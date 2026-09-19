-- validate_coverage.sql — every market table must have data for the latest day.
--
-- Returns one row per table that is BEHIND the newest ingested date. An empty
-- result is a pass. A row means a file silently failed to load, which is the
-- failure mode that otherwise shows up as a chart quietly missing its last point.

WITH latest AS (
    SELECT MAX(trade_date) AS d FROM slb_quote_daily
),
coverage AS (
    SELECT 'slb_quote_daily'    AS table_name, MAX(trade_date) AS max_date FROM slb_quote_daily
    UNION ALL
    SELECT 'slb_open_position',  MAX(trade_date)  FROM slb_open_position
    UNION ALL
    SELECT 'slb_eligibility',    MAX(as_of_date)  FROM slb_eligibility
    UNION ALL
    SELECT 'slb_var_margin',     MAX(as_of_date)  FROM slb_var_margin
    UNION ALL
    SELECT 'cash_quote_daily',   MAX(trade_date)  FROM cash_quote_daily
    UNION ALL
    SELECT 'slb_utilisation_daily', MAX(trade_date) FROM slb_utilisation_daily
    UNION ALL
    SELECT 'slb_specialness_daily', MAX(trade_date) FROM slb_specialness_daily
    UNION ALL
    SELECT 'gc_rate_daily',      MAX(as_of_date)  FROM gc_rate_daily
)
SELECT
    c.table_name,
    c.max_date,
    l.d AS expected_max_date,
    (l.d - c.max_date) AS days_behind
FROM coverage AS c
CROSS JOIN latest AS l
WHERE c.max_date IS NULL OR c.max_date < l.d;
