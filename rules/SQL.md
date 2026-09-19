# SQL rules

The SQL in this repo is meant to be read by an interviewer. Write it that way.

## Hard rules
- **Never `SELECT *`** outside an ad-hoc console session. Name every column.
- **Every analytics query is a file** in `db/queries/`, not a string in Python.
- **Every file opens with a header comment**: purpose, inputs, grain of the
  output, units of each measure, and the formula in words.
- **CTEs over nested subqueries.** One idea per CTE, named for what it holds
  (`daily_fee`, `gc_cohort`, `fee_zscore`), not `t1`, `a`, `tmp`.
- **Window functions over self-joins** for anything ranked, lagged or cumulative.
  This project's natural fits:
  - `PERCENT_RANK() OVER (PARTITION BY trade_date, series_code ORDER BY fee_close)`
    → cross-sectional specialness
  - `AVG(fee_close) OVER (PARTITION BY symbol ORDER BY trade_date
    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)` → the security's own 1-month
    baseline, excluding today
  - `LAG(outstanding_qty) OVER (PARTITION BY symbol, series_code ORDER BY trade_date)`
    → daily change in open interest
  - `SUM(...) OVER (PARTITION BY desk_id ORDER BY trade_date)` → running desk P&L
  - `FIRST_VALUE`/`LAST_VALUE` with an explicit frame for term-structure endpoints
- **Explicit joins** with the condition on `ON`, never in `WHERE`.
- **Explicit casts** on money: `NUMERIC(18,4)`. Never let a rate divide into an
  integer and hope.
- **Idempotent loads**: `INSERT ... ON CONFLICT (natural_key) DO UPDATE`.

## Style
Keywords uppercase, identifiers lowercase, one column per line in a `SELECT`
longer than three columns, trailing commas never.

```sql
-- db/queries/utilisation.sql
-- Grain: one row per (trade_date, symbol).
-- utilisation_pct = on-loan quantity / lendable quantity * 100
WITH on_loan AS (
    SELECT
        trade_date,
        symbol,
        SUM(outstanding_qty) AS on_loan_qty
    FROM slb_open_position
    GROUP BY trade_date, symbol
)
SELECT
    o.trade_date,
    o.symbol,
    o.on_loan_qty,
    l.lendable_qty,
    ROUND(100.0 * o.on_loan_qty / NULLIF(l.lendable_qty, 0), 2) AS utilisation_pct
FROM on_loan AS o
JOIN lendable AS l
    ON l.trade_date = o.trade_date
   AND l.symbol = o.symbol;
```

## Performance
Index the natural keys and every `trade_date`. `EXPLAIN ANALYZE` anything that
touches more than a million rows, and record the plan in the query's header
comment if you changed the query because of it.
