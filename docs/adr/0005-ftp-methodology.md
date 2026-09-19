# 0005 — FTP: matched-maturity, contractual tenor by default

**Status**: accepted

## Context
Three FTP methodologies are in industry use: single-rate pool, multiple pool, and
matched-maturity. Separately, matched-maturity needs a tenor per position, and that
tenor can be contractual or behavioural.

## Decision
**Matched-maturity**, with the transfer rate as base curve + term liquidity premium
+ contingent liquidity charge. **Contractual** tenor to the reverse-leg settlement
date by default, with an optional behavioural override that shortens the tenor by the
historical mean early-unwind for names where both recall and repay are eligible.

## Consequences
- Isolates the desk's commercial spread from Treasury's maturity-transformation
  spread, which is the entire purpose. Single-pool FTP cannot do this — it charges an
  overnight and a twelve-month position the same rate, making maturity transformation
  look free.
- The decomposition reconciles: `desk_spread + treasury_spread = NIM`, asserted to ₹1
  in CI. Without that test the model is decorative.
- The contingent liquidity charge is driven by the NSE eligibility file's
  `recall_eligibility` flag rather than an assumption, so a position that genuinely
  cannot be unwound early is charged for it.
- Contractual is the conservative default; behavioural is more accurate but needs
  unwind history the synthetic book only partly provides. Both are implemented so the
  difference is demonstrable.
- Cost: needs a funding curve at every position tenor, so the curve interpolator
  must not have gaps. A missing node fails the reconciliation test, which is the
  intended way to find out.
