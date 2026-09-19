-- book_kpis.sql — the dashboard's top strip.
--
-- Purpose : one row summarising the whole book on a date.
-- Inputs  : slb_position_pnl_daily, desk_pnl_daily, slb_specialness_daily,
--           slb_utilisation_daily, margin_call, gsec_analytics_daily
-- Grain   : one row per as_of_date
-- Params  : %(as_of)s -- NULL means the latest date with position P&L
-- Units   : *_inr rupees, *_pct percent, *_bps basis points
--
-- Every figure here is already aggregated in SQL. The browser formats and draws;
-- it does not compute (rules/FRONTEND.md).

WITH as_of AS (
    SELECT COALESCE(
        %(as_of)s::DATE,
        (SELECT MAX(as_of_date) FROM slb_position_pnl_daily)
    ) AS d
),
book AS (
    SELECT
        SUM(CASE WHEN p.side = 'LEND' THEN p.notional_inr ELSE 0 END)   AS on_loan_inr,
        SUM(CASE WHEN p.side = 'BORROW' THEN p.notional_inr ELSE 0 END) AS borrowed_inr,
        SUM(p.notional_inr)                                             AS gross_notional_inr,
        SUM(p.fee_pnl_inr)                                              AS fee_pnl_inr,
        COUNT(*)                                                        AS positions,
        -- Notional-weighted, so a 2 crore position does not count the same as a
        -- 40 crore one.
        SUM(p.fee_annualised_pct * p.notional_inr) / NULLIF(SUM(p.notional_inr), 0)
            AS weighted_avg_fee_pct
    FROM slb_position_pnl_daily AS p
    JOIN as_of ON as_of.d = p.as_of_date
),
desks AS (
    SELECT
        -- BUSINESS desks only. Summed across every desk the FTP charge is
        -- exactly zero, because Treasury is credited whatever the desks are
        -- debited -- correct as a ledger, useless as a KPI. What the strip wants
        -- is what the trading desks were actually charged.
        SUM(d.ftp_charge_inr) FILTER (WHERE d.desk_id <> 'TREASURY') AS ftp_charge_inr,
        -- Summed across ALL desks this is book NIM: the internal transfer
        -- cancels, leaving fee income less Treasury's real cost of funds.
        SUM(d.net_spread_inr)  AS net_spread_inr,
        BOOL_AND(d.reconciles) AS reconciles
    FROM desk_pnl_daily AS d
    JOIN as_of ON as_of.d = d.as_of_date
),
specials AS (
    SELECT
        COUNT(*) FILTER (WHERE s.specialness_score >= 75) AS special_count,
        COUNT(*) FILTER (WHERE s.specialness_score >= 90) AS hard_to_borrow_count,
        COUNT(*) FILTER (WHERE s.is_stale)                AS stale_count,
        COUNT(*)                                          AS scored_count
    FROM slb_specialness_daily AS s
    JOIN as_of ON as_of.d = s.trade_date
),
-- Weighted by what is actually on loan, so the book figure reflects exposure
-- rather than averaging a large and a tiny name equally.
utilisation AS (
    SELECT
        SUM(u.utilisation_pct * u.on_loan_qty) / NULLIF(SUM(u.on_loan_qty), 0)
            AS book_utilisation_pct,
        BOOL_OR(u.is_estimated) AS utilisation_is_estimated
    FROM slb_utilisation_daily AS u
    JOIN as_of ON as_of.d = u.trade_date
    WHERE u.utilisation_pct IS NOT NULL
),
calls AS (
    SELECT
        COUNT(*) FILTER (WHERE m.status = 'OPEN') AS open_margin_calls,
        COALESCE(SUM(m.call_amount) FILTER (WHERE m.status = 'OPEN'), 0)
            AS open_call_amount_inr
    FROM margin_call AS m
    JOIN as_of ON as_of.d = m.as_of_date
),
-- Signed at the position level, where we know the direction: a REPO delivers
-- collateral away, a REVERSE takes it in (rules/FINANCE.md).
collateral AS (
    SELECT
        COALESCE(SUM(
            CASE WHEN r.direction = 'REVERSE' THEN 1 ELSE -1 END
            * c.nominal / 100.0 * g.dv01_per_100_face
        ), 0) AS book_dv01_inr
    FROM collateral_position AS c
    JOIN as_of ON as_of.d = c.as_of_date
    JOIN repo_trade AS r ON r.repo_id = c.repo_id
    JOIN gsec_analytics_daily AS g
        ON g.isin = c.isin
       AND g.trade_date = c.as_of_date
)
SELECT
    as_of.d                                        AS as_of_date,
    COALESCE(book.positions, 0)                    AS positions,
    COALESCE(book.on_loan_inr, 0)                  AS on_loan_inr,
    COALESCE(book.borrowed_inr, 0)                 AS borrowed_inr,
    COALESCE(book.gross_notional_inr, 0)           AS gross_notional_inr,
    COALESCE(book.fee_pnl_inr, 0)                  AS fee_pnl_inr,
    COALESCE(desks.ftp_charge_inr, 0)              AS ftp_charge_inr,
    COALESCE(desks.net_spread_inr, 0)              AS net_spread_inr,
    -- Annualised on gross notional, in basis points: the daily spread scaled by
    -- 365 and expressed against the balance sheet it consumed.
    (
        COALESCE(desks.net_spread_inr, 0) * 365.0
        / NULLIF(book.gross_notional_inr, 0) * 10000
    )::NUMERIC(14, 2)                              AS net_financing_spread_bps,
    book.weighted_avg_fee_pct::NUMERIC(12, 4)      AS weighted_avg_fee_pct,
    utilisation.book_utilisation_pct::NUMERIC(10, 4) AS book_utilisation_pct,
    COALESCE(utilisation.utilisation_is_estimated, TRUE) AS utilisation_is_estimated,
    COALESCE(specials.special_count, 0)            AS special_count,
    COALESCE(specials.hard_to_borrow_count, 0)     AS hard_to_borrow_count,
    COALESCE(specials.stale_count, 0)              AS stale_count,
    COALESCE(specials.scored_count, 0)             AS scored_count,
    COALESCE(calls.open_margin_calls, 0)           AS open_margin_calls,
    calls.open_call_amount_inr,
    collateral.book_dv01_inr::NUMERIC(20, 4)       AS book_dv01_inr,
    COALESCE(desks.reconciles, TRUE)               AS ftp_reconciles
FROM as_of
CROSS JOIN book
CROSS JOIN desks
CROSS JOIN specials
CROSS JOIN utilisation
CROSS JOIN calls
CROSS JOIN collateral;
