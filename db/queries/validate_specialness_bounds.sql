-- validate_specialness_bounds.sql — the score and its parts must be in range,
-- and the classification must match the score it claims to describe.
--
-- An empty result is a pass. Thresholds are declared in specialness.sql; this
-- asserts the stored classification agrees with them, so the two cannot drift.

SELECT
    s.trade_date,
    s.symbol,
    s.series_code,
    s.specialness_score,
    s.xs_percentile,
    s.own_z,
    s.classification,
    CASE
        WHEN s.specialness_score < 0 OR s.specialness_score > 100 THEN 'score out of [0,100]'
        WHEN s.xs_percentile < 0 OR s.xs_percentile > 1           THEN 'percentile out of [0,1]'
        WHEN ABS(s.own_z) > 5.0001                                THEN 'own_z not clamped'
        ELSE 'classification does not match score'
    END AS problem
FROM slb_specialness_daily AS s
WHERE s.specialness_score < 0
   OR s.specialness_score > 100
   OR s.xs_percentile < 0
   OR s.xs_percentile > 1
   OR ABS(COALESCE(s.own_z, 0)) > 5.0001
   OR s.classification <> CASE
        WHEN s.specialness_score >= 90 THEN 'HARD_TO_BORROW'
        WHEN s.specialness_score >= 75 THEN 'SPECIAL'
        WHEN s.specialness_score >= 50 THEN 'WARM'
        ELSE 'GC'
   END;
