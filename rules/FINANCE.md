# Finance rules

Wrong-but-fast is worthless here. These are the conventions this repo commits to.
Any code that departs from them must say so in a comment.

## Units and signs
- Lending fees from NSE are quoted **in rupees per share for the whole period of
  the contract**, not annualised. Annualise explicitly and label the column
  `fee_annualised_pct`; never mix the two in one column.
- Rates are stored as **percent** (`7.25` means 7.25%), converted to decimal only
  inside a formula. Column names ending `_pct` are percent; `_bps` are basis points.
- P&L sign convention: **positive is a gain to our book.** A borrow cost is
  therefore negative. Every P&L column comment states whose book.
- Quantities are shares (integer). Values are rupees (`NUMERIC(18,4)`).

## Day counts
- **Indian G-Sec accrued interest: 30/360.** RBI/FIMMDA convention. Twelve 30-day
  months, 360-day year.
- **Repo interest and money-market: ACT/365.** Indian rupee market convention.
- **SLB fee accrual: ACT/365** over actual calendar days between first-leg
  settlement and reverse-leg settlement.
- Never use a library default without checking which convention it assumed.

## Bond math
- G-Secs pay **semi-annual** coupons. Discount at `y/2` over `2n` periods.
- YTM is solved from the **dirty price** (settlement amount), not the clean price.
- Modified duration = Macaulay duration ÷ (1 + y/m).
- DV01 = dirty price × modified duration × 0.0001, per 100 face. State the face
  value in every DV01 column name or comment.
- Convexity is reported **per annum, undiscounted by 100** — i.e. the raw second
  derivative scaled by price, so `ΔP/P ≈ −D_mod·Δy + ½·C·Δy²`.

## SLB specifics (NSE Clearing rules, not invented)
- Tenure is up to 12 months; there are 12 fixed monthly series whose reverse-leg
  settlement date is a fixed day of the month, published per series.
- Two parallel contract sets exist: series `01`–`12` are force-foreclosed on
  AGM/EGM; series `X1`–`XD` are not. Rollover series are separate again. Never
  aggregate across contract sets without saying so.
- First leg settles **T+1**; the reverse leg settles on the series date.
- Rollovers may not extend total tenure beyond 12 months from the original trade.
- Corporate actions: dividend is debited from borrower and credited to lender;
  stock splits adjust quantity; everything else foreclosed on ex-date with a
  pro-rata fee return.

## Specialness
Specialness is **relative**, always. A fee of ₹2 means nothing without the cohort
and the history. The score blends:
1. cross-sectional percentile of today's fee within the same series, and
2. z-score of today's fee against the security's own trailing 20-day mean.

Both components and the final score are stored, so the number can be defended.
Thresholds for GC / warm / special / hard-to-borrow are declared in
`docs/07-analytics-spec.md` and referenced from the SQL — not duplicated.

## Repo
- Haircut is applied to market value: `purchase_price = dirty_value × (1 − haircut)`.
- Repurchase price = purchase price × (1 + repo_rate × days/365).
- Collateral is revalued daily on mark-to-market price *and* current haircut;
  a shortfall against outstanding borrowing raises a margin call.

## FTP
- Default methodology is **matched-maturity**: each position is charged the
  funding curve at its own tenor, plus a term liquidity premium.
- The decomposition must reconcile: `desk_spread + treasury_spread = actual NIM`.
  There is a test that asserts this to the rupee.
