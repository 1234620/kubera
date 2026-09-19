-- validate_tenure.sql — SEBI caps total SLB tenure at 12 months from the
-- ORIGINAL trade, however many times a position is rolled (docs/01 §6).
--
-- Returns rollover chains that breach the cap. An empty result is a pass.
-- Expressed as a query rather than a constraint because the rule spans a
-- self-join, and an exclusion constraint across one is more machinery than this
-- needs (docs/06).

SELECT
    t.trade_id,
    t.original_trade_id,
    t.symbol,
    o.trade_date        AS original_trade_date,
    t.reverse_leg_date  AS final_reverse_leg_date,
    (t.reverse_leg_date - o.trade_date) AS total_tenure_days
FROM slb_trade AS t
JOIN slb_trade AS o
    ON o.trade_id = t.original_trade_id
WHERE t.reverse_leg_date > o.trade_date + 365;
