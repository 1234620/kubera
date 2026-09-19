-- book_history.sql — the daily series behind the KPI sparklines.
--
-- Purpose : one row per trading day with the book-level figures the top strip
--           charts, so the browser draws a series it is handed rather than
--           aggregating one itself (rules/FRONTEND.md).
-- Inputs  : slb_position_pnl_daily, desk_pnl_daily, slb_specialness_daily,
--           slb_utilisation_daily, margin_call
-- Grain   : one row per as_of_date, oldest first
-- Units   : *_inr rupees, *_pct percent, *_bps basis points

WITH positions AS (
    SELECT
        as_of_date,
        SUM(CASE WHEN side = 'LEND' THEN notional_inr ELSE 0 END) AS on_loan_inr,
        SUM(notional_inr)                                         AS gross_notional_inr,
        SUM(fee_annualised_pct * notional_inr) / NULLIF(SUM(notional_inr), 0)
            AS weighted_avg_fee_pct
    FROM slb_position_pnl_daily
    GROUP BY as_of_date
),
spreads AS (
    SELECT
        as_of_date,
        SUM(net_spread_inr) AS net_spread_inr
    FROM desk_pnl_daily
    GROUP BY as_of_date
),
utilisation AS (
    SELECT
        trade_date,
        SUM(utilisation_pct * on_loan_qty) / NULLIF(SUM(on_loan_qty), 0)
            AS book_utilisation_pct
    FROM slb_utilisation_daily
    WHERE utilisation_pct IS NOT NULL
    GROUP BY trade_date
),
specials AS (
    SELECT
        trade_date,
        COUNT(*) FILTER (WHERE specialness_score >= 75) AS special_count
    FROM slb_specialness_daily
    GROUP BY trade_date
),
calls AS (
    SELECT
        as_of_date,
        COUNT(*) FILTER (WHERE status = 'OPEN') AS open_margin_calls
    FROM margin_call
    GROUP BY as_of_date
)
SELECT
    p.as_of_date,
    p.on_loan_inr,
    p.gross_notional_inr,
    p.weighted_avg_fee_pct::NUMERIC(12, 4)  AS weighted_avg_fee_pct,
    COALESCE(s.net_spread_inr, 0)           AS net_spread_inr,
    (
        COALESCE(s.net_spread_inr, 0) * 365.0
        / NULLIF(p.gross_notional_inr, 0) * 10000
    )::NUMERIC(14, 2)                       AS net_financing_spread_bps,
    u.book_utilisation_pct::NUMERIC(10, 4)  AS book_utilisation_pct,
    COALESCE(sp.special_count, 0)           AS special_count,
    COALESCE(c.open_margin_calls, 0)        AS open_margin_calls
FROM positions AS p
LEFT JOIN spreads     AS s  ON s.as_of_date = p.as_of_date
LEFT JOIN utilisation AS u  ON u.trade_date = p.as_of_date
LEFT JOIN specials    AS sp ON sp.trade_date = p.as_of_date
LEFT JOIN calls       AS c  ON c.as_of_date = p.as_of_date
ORDER BY p.as_of_date;
