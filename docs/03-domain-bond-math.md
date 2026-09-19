# 03 — Bond analysis and pricing: Indian G-Secs

Sources: RBI handbook of statistics on the G-Sec market, FIMMDA valuation
methodology, standard fixed-income texts. Every formula below is implemented in
`src/slbdesk/bonds/` as a pure function and checked against a hand-computed value
plus NSE's own published weighted YTM.

## 0. Conventions this repo commits to

| Thing | Convention | Why |
| --- | --- | --- |
| Accrued interest on G-Secs | **30/360** | RBI/FIMMDA convention for Indian government securities: 360-day year, 30 days per completed month |
| Coupon frequency | **Semi-annual** | All fixed-coupon GoI dated securities |
| Repo / money market | **ACT/365** | Indian rupee money-market convention |
| Face value | **₹100** unless stated | Prices are quoted per ₹100 face |
| YTM input | **Dirty price** | The settlement amount, not the quote |
| T-bills | Discount instrument, ACT/365 simple yield | No coupon |

A function that departs from these says so in its docstring. A function that
silently assumes ACT/ACT because a library defaulted to it is a bug.

## 1. Accrued interest

Between coupon dates the buyer owes the seller the coupon earned so far.

```
accrued_interest = (coupon_rate / 100) × face × (days_30_360 / 360)
```

where `days_30_360` counts from the last interest payment date to the settlement
date under the 30/360 rule:

```
days = 360 × (y2 − y1) + 30 × (m2 − m1) + (d2 − d1)
```

with the standard end-of-month adjustments: if `d1 = 31` set `d1 = 30`; if
`d2 = 31` and `d1 = 30` set `d2 = 30`.

The G-Sec master file gives us `Last IP Dt` and `Next IP Dt` directly, so the
coupon period is read from the data rather than inferred by subtracting six months
— which matters for the stub periods on recently issued and soon-maturing paper.

## 2. Clean price and dirty price

```
dirty_price = clean_price + accrued_interest
clean_price = dirty_price − accrued_interest
```

The **clean price** is the quoted price — it excludes accrued interest, which is
why a bond's quoted price does not saw-tooth downward on every coupon date. The
**dirty price** (full price, invoice price) is what actually changes hands. NSE's
WDM files quote **clean**; every valuation in this repo converts to dirty before
discounting.

## 3. Price from yield

For a semi-annual bond with `n` remaining coupons, annual coupon rate `c`, annual
yield `y`, face `F`, and a settlement date that falls mid-period:

```
per_period_coupon = c × F / 200
per_period_yield  = y / 200
w = fraction of the current coupon period already elapsed (30/360)

               n     coupon                F
dirty_price =  Σ  ─────────────────  +  ──────────────
              k=1  (1+y/200)^(k−w)      (1+y/200)^(n−w)
```

The `−w` exponent shift is the mid-period adjustment. Dropping it — discounting as
if settlement were exactly on a coupon date — is the single most common bond
pricing error, and it can be worth tens of basis points of yield on a bond bought
five months into its coupon period.

## 4. Yield to maturity

YTM is the single discount rate that sets the present value of all remaining cash
flows equal to the dirty price. There is no closed form; we solve it.

Implementation: **Newton–Raphson** on `f(y) = price(y) − dirty_price`, with the
analytic derivative `f'(y) = −dirty_price × modified_duration` (we already need
that), seeded at the current-yield approximation and falling back to bisection on
`[0%, 50%]` if Newton fails to converge in 50 iterations. Tolerance 1e-10 on price.

Newton converges in three or four steps here because bond price is monotonic and
smooth in yield; the bisection fallback exists for the pathological cases (deep
discount, near-maturity stub) rather than as the main path.

**Interpretation and its caveats** — say these out loud in an interview: YTM assumes
the bond is held to maturity, every coupon is reinvested at the YTM itself, and
there is no default. The reinvestment assumption is the weak one; it is why
realised return differs from YTM even on a risk-free bond held to maturity.

## 5. Macaulay duration

The present-value-weighted average time to receive the bond's cash flows, in years.

```
                 n     t_k × PV(CF_k)
D_macaulay =  ─────────────────────────────
                     dirty_price
```

where `t_k` is the time to cash flow `k` in **years** and `PV(CF_k)` is that cash
flow discounted at the YTM. A 10-year 7% G-Sec has a Macaulay duration around
7 years, not 10 — coupons pull it in. A zero-coupon bond's Macaulay duration
equals its maturity exactly, which is the sanity check in the test.

## 6. Modified duration

The percentage price sensitivity to a parallel yield move.

```
D_mod = D_macaulay / (1 + y/m)
```

with `m = 2` for semi-annual. Then, to first order:

```
ΔP/P ≈ −D_mod × Δy          (Δy in decimal, so 1bp = 0.0001)
```

Modified duration is the number a risk report shows. It is also what Indian banks
feed into the **Basel III standardised market-risk capital charge**, which is why
it appears on the regulatory side of a desk's reporting and not only on the trading
side.

## 7. Convexity

Duration is a straight-line approximation to a curve. Convexity is the second-order
correction.

```
              1        n
convexity = ────── ×   Σ   t_k × (t_k + 1/m) × PV(CF_k)
            price     k=1

ΔP/P ≈ −D_mod × Δy + ½ × convexity × Δy²
```

Convexity is positive for a plain bond, so duration alone **overstates** the loss
from a yield rise and **understates** the gain from a yield fall. For a 1bp move the
convexity term is negligible; for 100bp on a long G-Sec it is material, and that
asymmetry is exactly what matters when sizing a margin call on a repo collateral
pool. Reported per annum with the `½` applied at use, not at computation.

## 8. DV01 / PV01

Rupee price change for a **1 basis point** yield move, per ₹100 face:

```
DV01 = dirty_price × D_mod × 0.0001
```

Scale to a position by multiplying by `face_value / 100`. Conventions worth
stating: some desks sign it (**PV01** positive = gain when rates fall), some report
it unsigned. This repo stores it **unsigned per ₹100 face** and applies the sign at
the position level, where we know whether we are long or short. Every DV01 column
name carries its face basis.

An alternative, sometimes preferred because it needs no duration: reprice at
`y + 0.0001` and `y − 0.0001` and take half the difference. That **numerical DV01**
is what the test compares the analytic DV01 against — agreement to 1e-6 is the
check that the duration formula is right.

## 9. T-bills

No coupon. Price is a discount off face:

```
price = face / (1 + y/100 × days/365)
y     = ((face − price) / price) × (365 / days) × 100
```

ACT/365. `wdmlist_*.csv` carries T-bills as `SECTYPE = TB`, with a maturity date
and no coupon rate; the parser routes them to the T-bill path rather than trying
to build a coupon schedule.

## 10. Where the bond leg touches the financing book

This is the join an interviewer will look for:

1. **Repo collateral valuation** — the haircut applies to the *dirty* value, which
   needs accrued interest, which needs the coupon schedule.
2. **Margin call sizing** — the collateral pool's DV01 tells you the rupee call from
   a 1bp move; convexity tells you it is asymmetric in a large one.
3. **Funding curve construction** — the FTP model's curve is bootstrapped from
   T-bill and G-Sec yields, so every point on it is an output of this module.
4. **Term mismatch** — a 3-month repo against a 10-year G-Sec is a duration
   mismatch that the repo rate does not price. Duration quantifies it.

## 11. Validation plan

| Check | How |
| --- | --- |
| 30/360 day count | Hand-computed cases including end-of-month edges |
| Accrued interest | Recompute a published G-Sec settlement amount |
| Zero-coupon identity | Macaulay duration == maturity |
| Analytic vs numerical DV01 | Agree to 1e-6 |
| YTM round-trip | `ytm(price(y)) == y` to 1e-8 |
| Against the market | Reproduce `Weighted YTM` in `trd*_sett.csv` from the file's own VWAP clean price for ≥90% of G-Sec rows to within 2bp |

That last one is the real test. It is the difference between "I implemented a
formula from a textbook" and "my implementation agrees with the exchange".
