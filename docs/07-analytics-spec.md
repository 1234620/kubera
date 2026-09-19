# 07 — Analytics specification

Every metric the system produces, with its formula, units, grain, and the file that
implements it. If a number appears on the dashboard and is not in this document,
that is a bug in one of the two.

Notation: fees are `f` in **₹/share for the contract period**; `q` is quantity in
shares; `P` is the underlying share price in ₹; `d` is days.

---

## 1. Basis and units

The single most important conversion in this project. NSE quotes the SLB fee as
rupees per share **for the whole contract**, but every desk metric, every
comparison across tenors, and the entire FTP model need an **annualised rate on
notional**. Comparing a 1-month series fee to a 12-month series fee without
annualising is meaningless, and it is the mistake this section exists to prevent.

```
notional            = q × P                                  [₹]
fee_amount          = q × f                                   [₹]
fee_annualised_pct  = (f / P) × (365 / d) × 100               [% p.a.]
```

where `d = reverse_leg_date − first_leg_settle_date` in **actual calendar days**
(ACT/365), and `P` is the underlying cash-market close on the same date.

`P` is not in the SLB files. It comes from the NSE cash-market bhavcopy, which
the ingest layer also pulls for exactly this reason — without it, nothing here can
be annualised.

**Implementation**: `db/queries/fee_basis.sql`, grain day × symbol × series.
Output columns: `fee_per_share`, `underlying_close`, `tenor_days`,
`fee_annualised_pct`, `notional_inr`.

---

## 2. Utilisation

```
utilisation_pct = on_loan_qty / lendable_qty × 100
```

- **`on_loan_qty`** — `SUM(outstanding_qty)` from `slb_open_position` for the day,
  across all series. Real, from the file.
- **`lendable_qty`** — *not published anywhere*, and MWPL is not in any of the
  files we ingest, so the original plan to proxy free float from it was not
  actually available. What **is** observable is delivery volume — the flow of
  shares into settled custody — so the implemented estimate is
  `avg_delivery_qty_30d × 40`. Its weakness is stated rather than hidden: delivery
  measures *flow*, not *stock*, so the multiple is a calibration constant and not
  a measurement. **`is_estimated = TRUE` on every row**, the API carries the flag
  through, and the dashboard labels the cell.

  Because of that, `days_to_cover` is reported **alongside** utilisation rather
  than behind it: `on_loan ÷ avg_volume_30d` needs no estimate at all, so it is
  the number to trust when the two disagree. Saying which of your metrics is
  measured and which is modelled is part of the deliverable.

Interpretation: utilisation is the standard industry supply-and-demand gauge —
on-loan value over total lendable inventory. Rising utilisation against a shrinking
lendable base is the leading indicator that a name is about to go special; the fee
is the lagging confirmation.

**Implementation**: `db/queries/utilisation.sql` → `slb_utilisation_daily`.
Window use: `LAG()` over `(symbol ORDER BY trade_date)` for the daily change, a
5-row moving average for the trend, and a trailing **`RANGE BETWEEN INTERVAL
'30 days' PRECEDING`** frame for the volume baseline — `RANGE` on the date rather
than `ROWS`, because a thin name does not trade every day and a row-count frame
reaches back an unpredictable distance in time.

### 2.1 Days to cover

```
days_to_cover = on_loan_qty / avg_daily_cash_volume_30d
```

How many normal trading days it would take to buy back the borrowed stock. High
days-to-cover plus high utilisation is a squeeze setup.

Folded into `utilisation.sql` rather than given its own file: identical grain,
identical joins, so a second file would duplicate the query for one column.

---

## 3. The GC benchmark

Specialness is relative, so there has to be something to be relative *to*. Equity
SLB has no published GC rate, so we define one and document it rather than borrowing
a number from a different market.

**Definition**: the **quantity-weighted median** `fee_annualised_pct` across the
day's *liquid cohort* — rows where `num_trades ≥ 3` and `traded_qty ≥ 1000` — for
the same `contract_set`, restricted to the nearest three series by tenor.

Median, not mean, because the fee distribution is severely right-skewed: a handful
of hard-to-borrow names at 40% p.a. would drag a mean benchmark up and make
genuinely special names look ordinary. Quantity-weighted because a 100-share print
should not move the market benchmark.

`cohort_size` is stored alongside. Below 20 members the benchmark is flagged
`is_estimated`, because a median of twelve observations is not a market rate.

**Implementation**: `db/queries/gc_benchmark.sql` → `gc_rate_daily`.
Uses `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ...)`.

---

## 4. Specialness score

The headline metric. Two components, deliberately, so the number can be defended:
a name can look expensive *relative to the market today* or *relative to its own
history*, and those are different statements.

### Component A — cross-sectional percentile

```
xs_percentile = PERCENT_RANK() OVER (
    PARTITION BY trade_date, contract_set, series_bucket
    ORDER BY fee_annualised_pct
)
```

Where does this name's fee sit against every other name at the same tenor today?
Range 0–1. Robust to the whole market repricing, which is its job.

### Component B — own-history z-score

```
baseline_mean = AVG(fee_annualised_pct) OVER (
    PARTITION BY symbol
    ORDER BY trade_date
    RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 day' PRECEDING
)
baseline_sd   = STDDEV_SAMP(...) OVER (same frame)

own_z = (fee_annualised_pct − baseline_mean) / NULLIF(baseline_sd, 0)
```

The frame **excludes today** (`INTERVAL '1 day' PRECEDING`), so today's
observation is measured against a baseline it did not contribute to. Including it
damps exactly the spike you are trying to detect.

`RANGE` over dates, not `ROWS` over observations: a 20-`ROWS` frame on a name that
prints twice a week reaches back two months, and on a liquid name four weeks — so
the "20-day baseline" would silently mean a different period per security. A
30-calendar-day `RANGE` means the same thing for every name.

`own_z` is winsorised to ±5 before blending — a name coming off a 20-day run of
identical fees has a near-zero standard deviation, which produces an absurd z
without the clamp.

### The blend

```
specialness_score = 100 × (0.6 × xs_percentile + 0.4 × norm_cdf(own_z))
```

Postgres ships no `erf()`, so `norm_cdf` is the tanh approximation
`0.5 × (1 + tanh(0.7978845608·z·(1 + 0.044715·z²)))` — maximum absolute error
about 1e-4 across the clamped range, which is far finer than the score's own
0–100 resolution.

With **no usable baseline** (fewer than two prior observations) the cross-sectional
view carries the whole score rather than the row being dropped: a newly eligible
name still has a borrow cost, and dropping it would hide exactly the names most
likely to be scarce.

Range 0–100. `norm_cdf` maps the z-score onto 0–1 so the two components are on the
same scale before weighting. The 60/40 weighting favours the cross-sectional view
because it is computed from more observations on any given day; the weights are
named constants in the SQL header, not magic numbers in the middle of an
expression.

Both components **and** the final score are stored. "Score 87" is not an answer;
"87, because it's in the 94th percentile of 1-month fees and 2.1 standard
deviations above its own 20-day mean" is.

### Classification

| Band | Score | Reading |
| --- | --- | --- |
| `GC` | < 50 | General collateral; trades near the benchmark |
| `WARM` | 50 – 75 | Demand building; worth watching |
| `SPECIAL` | 75 – 90 | Scarce; fee materially above cohort and history |
| `HARD_TO_BORROW` | ≥ 90 | Squeeze territory |

Thresholds are declared **once**, here, and referenced from the SQL header. They
are not duplicated in Python, in the API, or in the frontend.

### The universe, and the staleness guard

The score is computed over **open positions**, not over today's prints. Only ~230
of ~800 open `(symbol, series)` pairs trade on a given day, so scoring what
printed would ignore three quarters of the exposure. The fee is carried forward
from the last print via a `LATERAL` "latest at or before this date" lookup — which
is what the `(symbol, trade_date)` index on `slb_quote_daily` exists for.

A fee that last printed nine days ago is not today's market, so every row carries
`quote_date` and `days_since_last_trade`, and anything older than **three days** is
returned with `is_stale = TRUE`. The dashboard greys those cells rather than
dropping them, because "no recent print" is itself information about liquidity.

The own-history baseline is computed from the **print** history, not the
carried-forward universe, so a stale pair cannot pollute its own baseline with
repeats of the same number.

**Implementation**: `db/queries/specialness.sql` → `slb_specialness_daily`.

---

## 5. Financing spread P&L

The desk's actual money, decomposed so each line is attributable to a decision
someone made.

### Fee accrual, per position, per day

```
tenor_days        = reverse_leg_date − first_leg_settle_date
daily_fee_accrual = q × f / tenor_days                            [₹/day]
fee_pnl_inr       = +daily_fee_accrual  if side = LEND
                  = −daily_fee_accrual  if side = BORROW
```

Straight-line over the contract, ACT/365, starting at **first-leg settlement** —
not trade date. Starting at trade date overstates the accrual by one day on every
trade in the book.

**Sign convention: positive is a gain to our book, always.** A borrow cost is a
negative number, not a positive number you subtract. Half the sign errors in P&L
code come from mixing those two styles in one file.

→ `slb_position_pnl_daily`, one row per (date, trade).

### The FTP charge, on the *net* position

This is the design decision worth defending. The charge applies to the **net**
position per `(date, desk, symbol, series)`, not to each leg:

```
net_quantity     = Σ BORROW − Σ LEND
net_notional_inr = net_quantity × underlying_close
ftp_charge_inr   = −net_notional × ftp_rate_pct / 100 / 365
```

Netting is the point. A matched book — borrow a name in, lend the same name and
series out, same size — consumes almost no balance sheet, so it should earn the
fee spread nearly cleanly. A directional book consumes real balance sheet and
must pay for it. Charging both legs of a matched pair would turn FTP into a flat
tax on turnover rather than a price for the resource actually consumed, and would
make a matched book look loss-making.

Every charge is **mirrored to `TREASURY`** with the opposite sign, so the internal
ledger nets to zero by construction. That cancellation is what makes the
decomposition in §9 an identity rather than a restatement.

→ `ftp_charge_daily`, one row per (date, desk, symbol, series).

### What the split actually shows

On the seeded book, this is the output — and it is the reason the model is worth
building:

| desk | positions | gross notional | net notional | fee ₹/day | FTP ₹/day | net ₹/day |
| --- | --- | --- | --- | --- | --- | --- |
| `EQ_FIN` | 376 | ₹789 cr | **₹0** | +131,352 | **0** | +131,352 |
| `DELTA_ONE` | 101 | ₹201 cr | ₹201 cr | −273,310 | **−320,549** | −593,859 |
| `TREASURY` | 0 | — | −₹201 cr | 0 | +320,549 | +639,868 |

`EQ_FIN` runs a perfectly matched book: ₹789 crore gross, zero net, so it pays no
FTP and keeps its whole fee spread. `DELTA_ONE` is directional, and **its funding
charge (₹320,549) is larger than the borrow fee it pays (₹273,310)** — so the
dominant cost of that arbitrage position is balance sheet, not borrow. Nothing but
an FTP model surfaces that.

### A note on term spread

An earlier draft of this document promised a three-way split into fee spread, term
spread and funding drag. Term spread — being paid for a maturity mismatch — is
**structurally zero for this book**, because the seeded matched pairs borrow and
lend the *same series*, so there is no mismatch to be paid for. Rather than
fabricate the line, the position-level attribution is the two components that
genuinely exist:

```
net_spread_inr = fee_pnl_inr + ftp_charge_inr
```

and the maturity-transformation return is reported where it actually accrues —
`treasury_spread_inr` on Treasury's row. Term spread would become a real line the
moment the book borrowed short and lent long, and the curve to price it is already
there.

## 6. Term structure of lending fees

Grain: day × symbol × tenor bucket. `fee_annualised_pct` by tenor, so the shape can
be read.

An **upward-sloping** fee curve says the market expects the borrow to stay tight —
lenders demand more to commit for longer. **Downward-sloping** says today's demand
is a transient event with an expected resolution date (an index rebalance, a record
date, a merger closing). That shape is a trade signal, and it is the chart that
makes the dashboard worth looking at.

Endpoints via `FIRST_VALUE` / `LAST_VALUE` with an explicit
`ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING` frame — the default
frame silently gives the wrong `LAST_VALUE`, which is the classic window-function
trap and is called out in the query header.

```
term_slope_bps = (fee_ann_longest − fee_ann_shortest) × 100
```

**Implementation**: `db/queries/term_structure.sql`.

---

## 7. Bond analytics

Pure functions, `src/slbdesk/bonds/`. Formulas in
[`03-domain-bond-math.md`](03-domain-bond-math.md); this is the register of outputs.

| Output | Unit | Function |
| --- | --- | --- |
| `accrued_interest` | ₹ per ₹100 face | `accrued_interest()` — 30/360 |
| `dirty_price` | ₹ per ₹100 face | `dirty_from_clean()` |
| `ytm_pct` | % p.a. semi-annual | `solve_ytm()` — Newton, bisection fallback |
| `macaulay_duration` | years | `macaulay_duration()` |
| `modified_duration` | years | `modified_duration()` |
| `convexity` | years² | `convexity()` |
| `dv01` | ₹ per ₹100 face per bp | `dv01()` — unsigned; sign applied at position level |

→ `gsec_analytics_daily`, joined to `gsec_trade_daily` for prices and `gsec` for terms.

---

## 8. Repo analytics

| Output | Formula | Unit |
| --- | --- | --- |
| `dirty_value` | `nominal / 100 × (clean_price + accrued_interest)` | ₹ |
| `haircut` | `slb_var_margin.total_margin_pct / 100` for equities; tenor-banded model for G-Secs | decimal |
| `post_haircut_value` | `dirty_value × (1 − haircut)` | ₹ |
| `repurchase_price` | `purchase_price × (1 + repo_rate_pct / 100 × d / 365)` | ₹ |
| `accreted_value` | `purchase_price × (1 + repo_rate_pct / 100 × d_elapsed / 365)` | ₹ |
| `net_exposure` | `accreted_value − post_haircut_value` | ₹ |
| `shortfall` | `net_exposure`, when it exceeds the threshold | ₹ |
| `call_amount` | `shortfall` rounded **up** to the minimum transfer amount (₹10 lakh) | ₹ |

The threshold is **0.25% of exposure**, not a flat rupee figure. A flat amount
cannot serve a book whose positions run from ₹5 crore to ₹50 crore: ₹1 lakh is
0.2% of the smallest and 0.02% of the largest, so it fires on any price tick and
half of all days generate a call. A percentage threshold is also what a real CSA
uses, for the same reason.

G-Sec haircut model, bands documented so the number is not a plug:

| Residual maturity | Haircut |
| --- | --- |
| ≤ 1 year | 0.50% |
| 1 – 5 years | 1.50% |
| 5 – 10 years | 2.50% |
| > 10 years | 4.00% |

Calibrated as roughly a 5-day 99% VaR from the tenor band's DV01 and observed yield
volatility — the same shape CCIL uses (security-specific VaR over a 5-day holding
period, stepped up for illiquidity). `haircut_source` records which path produced
each row.

`due_at` is 09:00 on the next business day, per the CCIL TREPS rule.

**Implementation**: `db/queries/repo_margin.sql`, `src/slbdesk/repo/`.

---

## 9. FTP

Formulas in [`04-domain-ftp.md`](04-domain-ftp.md).

```
ftp_rate_pct = base_curve(tenor_days) + tlp(tenor_days) + contingent_liquidity_bps/100
ftp_charge   = position_value × ftp_rate_pct / 100 / 365
```

Contingent liquidity charge: **15 bps** on the portion of a position in a series
where `recall_eligibility = 'D'` in `slb_eligibility`, zero otherwise. A position
that cannot be unwound early consumes liquidity a recallable one does not, and the
NSE file tells us which is which — so the driver is data, not an assumption.

Curve build: T-bill yields (`SECTYPE = 'TB'`) for tenors under a year, G-Sec yields
beyond, log-linear interpolation on discount factors, plus a documented constant
issuer spread over the risk-free curve. Nodes carry `source` and `is_estimated`.

Decomposition, which must reconcile:

```
desk_spread     = external_rate_pct − ftp_rate_pct
treasury_spread = ftp_rate_pct − actual_cost_of_funds_pct
```

`tests/test_ftp_reconciliation.py` asserts `desk_spread + treasury_spread = NIM`
book-wide to within ₹1, and that FTP charges across all desks including `TREASURY`
sum to zero. Both are in CI.

**Implementation**: `src/slbdesk/analytics/ftp.py`, `db/queries/ftp_desk_pnl.sql`.

---

## 10. Book-level KPIs

The dashboard's top strip. `db/queries/book_kpis.sql`, one row per `as_of_date`.

| KPI | Definition |
| --- | --- |
| `total_on_loan_value` | `Σ q × P` over live `LEND` positions |
| `total_borrowed_value` | `Σ q × P` over live `BORROW` positions |
| `net_financing_spread_bps` | `Σ net_spread × 365 / Σ notional × 10000` |
| `weighted_avg_fee_pct` | Notional-weighted `fee_annualised_pct` |
| `book_utilisation_pct` | Notional-weighted utilisation |
| `open_margin_calls` | `COUNT(*)` where `status = 'OPEN'` |
| `book_dv01` | `Σ` signed DV01 over repo collateral |
| `special_count` | Positions scoring ≥ 75 |

---

## 11. Validation queries

Not optional. `make analytics` runs these and fails loudly.

| Check | File |
| --- | --- |
| Rollover chains respect the 12-month tenure cap | `db/queries/validate_tenure.sql` |
| `slb_trade_leg` ranges do not overlap within a trade | `db/queries/validate_legs.sql` |
| FTP charges net to zero across desks | `db/queries/validate_ftp_zero.sql` |
| No position priced off a quote older than 3 trading days without `is_stale` | `db/queries/validate_staleness.sql` |
| Every market table has rows for the latest ingested date | `db/queries/validate_coverage.sql` |
| Score, percentile and z are in range and the classification matches the score | `db/queries/validate_specialness_bounds.sql` |
