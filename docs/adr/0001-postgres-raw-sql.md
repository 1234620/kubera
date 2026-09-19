# 0001 — Postgres with raw SQL files

**Status**: accepted

## Context
The analytics could live in Python (pandas), in SQL, or split. Two of the three job
descriptions this project targets ask for SQL skill explicitly.

## Decision
Analytics live in Postgres as `.sql` files in `db/queries/`, executed with bound
parameters. Python does ingestion, bond math, and HTTP — not aggregation.

## Consequences
- The SQL is reviewable as a deliverable: CTEs, window functions, explicit joins.
- Window functions do the work that would otherwise be a Python loop over a result
  set: `PERCENT_RANK` for cross-sectional specialness, a trailing `AVG ... ROWS
  BETWEEN 20 PRECEDING AND 1 PRECEDING` for the own-history baseline, `LAG` for
  daily flow, running `SUM` for desk P&L.
- Bond math stays in Python, because Newton–Raphson in SQL would be a party trick.
- Cost: query text is not type-checked. Mitigated by running every query against a
  real Postgres in CI.
