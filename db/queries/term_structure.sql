-- term_structure.sql — the shape of the lending-fee curve for one security.
--
-- Purpose : read the market's view on how long a borrow will stay tight.
-- Inputs  : slb_fee_basis
-- Grain   : one row per (trade_date, symbol, series_code), ordered by tenor
-- Params  : %(symbol)s, %(as_of)s
-- Units   : fee_annualised_pct percent p.a.; term_slope_bps basis points
-- Read-only: the API executes this directly. Nothing to materialise -- it is a
-- handful of rows for one symbol on one day.
--
-- HOW TO READ IT, which is the whole value of the chart:
--   upward-sloping   -> the market expects the borrow to stay tight; lenders
--                       demand more to commit for longer.
--   downward-sloping -> today's demand is a transient event with an expected
--                       resolution date -- an index rebalance, a record date, a
--                       merger closing. That shape is a trade signal.
--
-- LAST_VALUE needs its frame stated explicitly. The default frame is
-- RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW, which silently makes
-- LAST_VALUE return the CURRENT row instead of the last one -- the classic
-- window-function trap, and the reason term_slope_bps would otherwise be zero.

WITH curve AS (
    SELECT
        b.trade_date,
        b.symbol,
        b.series_code,
        b.contract_set,
        b.tenor_days,
        b.reverse_leg_date,
        b.fee_per_share,
        b.fee_annualised_pct,
        b.num_trades
    FROM slb_fee_basis AS b
    WHERE b.symbol = %(symbol)s
      AND b.trade_date = %(as_of)s
      AND b.fee_annualised_pct > 0
),
endpoints AS (
    SELECT
        c.*,
        FIRST_VALUE(c.fee_annualised_pct) OVER w AS fee_shortest_pct,
        LAST_VALUE(c.fee_annualised_pct) OVER w  AS fee_longest_pct
    FROM curve AS c
    WINDOW w AS (
        PARTITION BY c.trade_date, c.contract_set
        ORDER BY c.tenor_days
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
)
SELECT
    e.trade_date,
    e.symbol,
    e.series_code,
    e.contract_set,
    e.tenor_days,
    e.reverse_leg_date,
    e.fee_per_share,
    e.fee_annualised_pct,
    e.num_trades,
    ((e.fee_longest_pct - e.fee_shortest_pct) * 100)::NUMERIC(14, 2) AS term_slope_bps
FROM endpoints AS e
ORDER BY e.contract_set, e.tenor_days;
