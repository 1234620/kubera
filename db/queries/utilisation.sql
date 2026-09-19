-- utilisation.sql — supply and demand per security.
--
-- Purpose : how much of a name's borrowable inventory is already lent out, and
--           how long it would take shorts to buy it back.
-- Inputs  : slb_open_position, cash_quote_daily
-- Grain   : one row per (trade_date, symbol)
-- Writes  : slb_utilisation_daily
-- Units   : quantities in shares; utilisation_pct and days_to_cover unitless
--
-- HONEST LIMITATION, and the reason is_estimated exists as a real column:
-- lendable supply is NOT published in India. NSE publishes what is ON LOAN, and
-- the eligible universe, but never the inventory made available to lend. So:
--
--   * days_to_cover     = on_loan / 20-day average traded volume   -- observable
--   * lendable_qty_est  = 20-day average DELIVERY quantity x 40    -- ESTIMATED
--   * utilisation_pct   = on_loan / lendable_qty_est x 100         -- ESTIMATED
--
-- Delivery volume is the flow of shares into settled custody, which is the best
-- observable proxy for inventory sitting in real holders' accounts. Its weakness
-- is that it measures flow, not stock, so the multiple is a calibration constant
-- and not a measurement. Every row is therefore flagged is_estimated, the API
-- passes the flag through, and the dashboard labels the cell. days_to_cover needs
-- no estimate at all, which is why it is reported alongside rather than hidden
-- behind utilisation.
--
-- Window use: LAG for the daily change in open interest, and a trailing 30-day
-- RANGE frame for the volume baseline -- RANGE on the date rather than ROWS,
-- because a thin name does not trade every day and a row-count frame would reach
-- back an unpredictable distance in time.

INSERT INTO slb_utilisation_daily (
    trade_date,
    symbol,
    on_loan_qty,
    avg_volume_20d,
    avg_delivery_20d,
    lendable_qty_est,
    utilisation_pct,
    days_to_cover,
    on_loan_change_1d,
    utilisation_5d_avg,
    is_estimated,
    source_query
)
WITH on_loan AS (
    SELECT
        o.trade_date,
        o.symbol,
        SUM(o.outstanding_qty) AS on_loan_qty
    FROM slb_open_position AS o
    GROUP BY o.trade_date, o.symbol
),
volume AS (
    SELECT
        c.trade_date,
        c.symbol,
        AVG(c.traded_qty) OVER w   AS avg_volume_20d,
        AVG(c.delivery_qty) OVER w AS avg_delivery_20d
    FROM cash_quote_daily AS c
    WHERE c.series = 'EQ'
    WINDOW w AS (
        PARTITION BY c.symbol
        ORDER BY c.trade_date
        RANGE BETWEEN INTERVAL '30 days' PRECEDING AND CURRENT ROW
    )
),
joined AS (
    SELECT
        l.trade_date,
        l.symbol,
        l.on_loan_qty,
        v.avg_volume_20d,
        v.avg_delivery_20d,
        (v.avg_delivery_20d * 40)::NUMERIC(20, 2) AS lendable_qty_est
    FROM on_loan AS l
    LEFT JOIN volume AS v
        ON v.trade_date = l.trade_date
       AND v.symbol = l.symbol
),
scored AS (
    SELECT
        j.trade_date,
        j.symbol,
        j.on_loan_qty,
        j.avg_volume_20d,
        j.avg_delivery_20d,
        j.lendable_qty_est,
        (100.0 * j.on_loan_qty / NULLIF(j.lendable_qty_est, 0))::NUMERIC(10, 4)
            AS utilisation_pct,
        (j.on_loan_qty / NULLIF(j.avg_volume_20d, 0))::NUMERIC(12, 4)
            AS days_to_cover
    FROM joined AS j
)
SELECT
    s.trade_date,
    s.symbol,
    s.on_loan_qty,
    s.avg_volume_20d,
    s.avg_delivery_20d,
    s.lendable_qty_est,
    s.utilisation_pct,
    s.days_to_cover,
    s.on_loan_qty - LAG(s.on_loan_qty) OVER (
        PARTITION BY s.symbol ORDER BY s.trade_date
    ) AS on_loan_change_1d,
    AVG(s.utilisation_pct) OVER (
        PARTITION BY s.symbol
        ORDER BY s.trade_date
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
    )::NUMERIC(10, 4) AS utilisation_5d_avg,
    TRUE AS is_estimated,
    'utilisation.sql' AS source_query
FROM scored AS s
ON CONFLICT (trade_date, symbol) DO UPDATE SET
    on_loan_qty        = EXCLUDED.on_loan_qty,
    avg_volume_20d     = EXCLUDED.avg_volume_20d,
    avg_delivery_20d   = EXCLUDED.avg_delivery_20d,
    lendable_qty_est   = EXCLUDED.lendable_qty_est,
    utilisation_pct    = EXCLUDED.utilisation_pct,
    days_to_cover      = EXCLUDED.days_to_cover,
    on_loan_change_1d  = EXCLUDED.on_loan_change_1d,
    utilisation_5d_avg = EXCLUDED.utilisation_5d_avg,
    source_query       = EXCLUDED.source_query;
