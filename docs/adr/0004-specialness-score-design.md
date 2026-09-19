# 0004 — Specialness score: transparent statistic, not a model

**Status**: accepted

## Context
Specialness could be a trained classifier over utilisation, short interest, fee
history and corporate-action proximity. It would probably score better on a
backtest.

## Decision
A two-component transparent statistic: cross-sectional `PERCENT_RANK` of the
annualised fee within the same tenor bucket, blended 60/40 with the normal CDF of a
z-score against the security's own trailing 20-day mean. Both components stored
alongside the blended score.

## Consequences
- Defensible in an interview and on a desk. "87" is not an answer; "94th percentile
  at this tenor and 2.1 sigma above its own 20-day mean" is.
- Computable entirely in SQL, so it stays where the data is.
- The trailing window **excludes today** (`1 PRECEDING`), so the baseline is not
  contaminated by the spike being detected.
- `own_z` is winsorised to ±5, because a name with a flat fee history has a
  near-zero standard deviation and produces an absurd z without the clamp.
- Cost: no learned interaction effects. Acceptable — an unexplainable score is worse
  than a slightly less accurate explainable one for this purpose.
