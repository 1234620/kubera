-- validate_legs.sql — a trade's fee-history legs must not overlap in time.
--
-- Returns overlapping leg pairs. An empty result is a pass.
--
-- slb_trade_leg is append-only, so a recall, repay, rollover or re-rate adds a
-- row rather than mutating the trade. That only reconstructs a correct fee
-- history if the ranges tile without overlapping -- otherwise a position accrues
-- at two fees at once and the P&L attribution is wrong twice over.

SELECT
    a.trade_id,
    a.leg_id       AS leg_a,
    b.leg_id       AS leg_b,
    a.effective_from AS a_from,
    a.effective_to   AS a_to,
    b.effective_from AS b_from,
    b.effective_to   AS b_to
FROM slb_trade_leg AS a
JOIN slb_trade_leg AS b
    ON b.trade_id = a.trade_id
   AND b.leg_id > a.leg_id
WHERE DATERANGE(a.effective_from, a.effective_to, '[]')
   && DATERANGE(b.effective_from, b.effective_to, '[]');
