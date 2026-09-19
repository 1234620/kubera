# 04 — Funds Transfer Pricing

Sources: Finastra *Funds Transfer Pricing* white paper and FTP primer, Moorad
Choudhry *Best-practice Funds Transfer Pricing Principles* (BTRM, 2017), industry
FTP guides. The EFG job description names FTP explicitly; this is the module that
answers it.

## 1. What FTP is and why a financing desk cares

A bank's Treasury is the internal bank. Every desk that uses funding buys it from
Treasury; every desk that raises funding sells it to Treasury. The price of that
internal transaction is the **transfer rate**, and setting it is Funds Transfer
Pricing.

Without FTP, a desk's P&L is an accident of where its funding happened to come
from. With FTP, three things become measurable:

1. **True desk profitability.** A financing book earning 40bp of spread while
   consuming 12-month funding priced at 55bp is destroying value, and only FTP
   shows it.
2. **Risk ownership.** Interest-rate and liquidity risk are transferred to Treasury
   at a price. The desk keeps credit and business risk. Each risk sits where it can
   be managed.
3. **Behaviour.** A desk charged for term liquidity stops writing long-dated trades
   it cannot fund, which is the whole point of the post-2008 regulatory interest in
   FTP.

For a securities lending and repo desk this is not academic. The desk's entire
product *is* funding. Its economics are the difference between the external rate it
earns and the internal rate it is charged, so the FTP curve is not an overhead
allocation — it is the cost of goods sold.

## 2. The three methodologies

| Method | How the rate is set | Verdict |
| --- | --- | --- |
| **Single rate** (pool) | One rate for all assets and all liabilities, usually a short money-market rate or the average cost of funds | Simple, and wrong for anything with a term. A 10-year asset and an overnight asset get the same charge, so maturity transformation looks free. Not used here. |
| **Multiple pool** | A handful of rates by broad maturity bucket | Better; still smears everything inside a bucket |
| **Matched maturity** | Each instrument is priced at the point on the funding curve matching its own repricing/maturity profile | Industry standard, and what this repo implements |

**Matched-maturity** is the default. It is also the only one that makes the
decomposition in §5 work, because it isolates the desk's spread from Treasury's
maturity-transformation spread.

## 3. The components of a transfer rate

```
FTP rate = base curve rate (at the instrument's tenor)
         + term liquidity premium (at that tenor)
         + contingent liquidity charge
         [+ basis / optionality adjustments]
```

**Base curve** — the bank's own marginal cost of funds by tenor. Built here from
Indian rupee benchmarks: TREPS/overnight at the short end, T-bill yields out to a
year, G-Sec yields beyond, with a documented issuer spread over the risk-free
curve. Interpolation is log-linear on discount factors, not linear on yields, so
the curve is arbitrage-free between nodes.

**Term liquidity premium (TLP)** — the extra cost of borrowing long rather than
rolling short. This is the component most commonly missed, and it is the one that
makes long-dated lending look expensive, correctly. It rises with tenor and widens
in stress. Modelled as a tenor-indexed spread curve, calibrated to the bank's own
term issuance spread over the base curve.

**Contingent liquidity charge** — the cost of holding a liquid asset buffer against
committed but undrawn exposures and against the possibility that a position cannot
be unwound. For an SLB book the natural driver is the *recall-disabled* portion of
the position: a series where recall eligibility is `D` cannot be unwound early, so
it consumes contingent liquidity that a recallable position does not. The NSE
eligible-securities file gives us that flag directly, which is a genuinely nice
data-driven touch rather than a plugged number.

## 4. Applying it to this book

Per position, per day:

```
funding_tenor_days = reverse_leg_settlement_date − valuation_date
ftp_rate_pct       = curve(funding_tenor_days) + tlp(funding_tenor_days) + clc(position)
ftp_charge         = position_value × ftp_rate_pct / 100 × 1 / 365
```

Applied daily on actual position value, so a position that shrinks through partial
repays is charged less — accrual, not a day-one point charge. Sign convention: an
asset (a borrow the desk is financing) is **charged**; a liability (cash the desk
has raised and passed to Treasury) is **credited**.

Whether to use **contractual** or **behavioural** tenor is a real modelling choice
and it is documented in [`adr/0005-ftp-methodology.md`](adr/0005-ftp-methodology.md).
This repo uses contractual tenor to the reverse-leg date as the default, with an
optional behavioural override that shortens the tenor by the historical mean early
unwind for names where recall and repay are both eligible. Contractual is the
conservative default; behavioural is the more accurate one, and stating that you
know the difference is the point.

## 5. The decomposition that has to reconcile

Net interest margin splits in two, and the two halves must add back:

```
desk_spread      = external rate earned − FTP rate charged
treasury_spread  = FTP rate charged − actual cost of funds raised

desk_spread + treasury_spread = actual net interest margin
```

The desk owns `desk_spread` — that is its commercial performance, free of any
funding-mix luck. Treasury owns `treasury_spread` — the return on deliberate
maturity transformation, which is a position Treasury took and should be measured on.

`tests/test_ftp_reconciliation.py` asserts this identity across the whole book to
within ₹1. If it does not reconcile, the FTP model is decorative. That test is the
reason to believe the module.

## 6. Desk allocation

The book is split across synthetic desks so the allocation has something to
allocate across:

| Desk | Business |
| --- | --- |
| `EQ_FIN` | Equity financing — the SLB lending book |
| `REPO` | G-Sec repo and reverse repo, matched book |
| `DELTA_ONE` | Index arbitrage and ETF market making, a *consumer* of borrow |
| `TREASURY` | The internal bank; the other side of every FTP charge |

Every FTP charge debits a desk and credits `TREASURY`, so the internal ledger nets
to zero by construction. `db/queries/ftp_desk_pnl.sql` proves it: the sum of all
FTP charges across desks including Treasury is zero, and that is asserted in CI.

## 7. What to say about this in an interview

- FTP transfers **liquidity and interest-rate risk** to Treasury at a price; the
  desk retains credit and business risk.
- **Matched-maturity** is the standard because single-pool FTP makes maturity
  transformation look free and therefore over-incentivises it.
- The **term liquidity premium** is the piece that is easy to omit and expensive to
  omit; regulators pushed banks on it specifically after 2008.
- FTP changes behaviour, which is a feature. A desk that is charged properly for
  12-month funding writes fewer 12-month trades at 30bp.
- The decomposition is the deliverable: it tells you whether a book made money
  because it was good or because funding was cheap.
