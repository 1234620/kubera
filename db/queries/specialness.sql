-- specialness.sql — how expensive is this borrow, relative to what?
--
-- Purpose : score every OPEN (symbol, series) pair 0-100 on how scarce the name
--           is, and classify it GC / WARM / SPECIAL / HARD_TO_BORROW.
-- Inputs  : slb_open_position (the universe), slb_fee_basis (the fee),
--           gc_rate_daily (the benchmark)
-- Grain   : one row per (trade_date, symbol, series_code)
-- Writes  : slb_specialness_daily
-- Units   : fee_annualised_pct percent p.a.; spread_to_gc_bps basis points;
--           xs_percentile and own_z_cdf in [0,1]; specialness_score in [0,100]
--
-- THE POINT: specialness is always relative. An absolute fee of 2 rupees means
-- nothing without a cohort and a history, so the score blends two views that
-- answer different questions, and BOTH are stored so the number can be defended:
--
--   A. xs_percentile -- where does this fee sit against every other name at the
--      same tenor TODAY? Robust to the whole market repricing, which is its job.
--   B. own_z         -- how far is it from what THIS name normally costs?
--      Catches a name that is cheap in absolute terms but expensive for itself.
--
--   specialness_score = 100 x (0.60 x xs_percentile + 0.40 x cdf(own_z))
--
-- Design notes that are easy to get wrong and are deliberate here:
--
--   * The universe is OPEN POSITIONS, not today's prints. Only ~230 of ~800 open
--     (symbol, series) pairs trade on a given day, so scoring only what printed
--     would ignore three quarters of the book. The fee is carried forward from
--     the last print via a LATERAL lookup, and days_since_last_trade / is_stale
--     expose exactly how old it is.
--   * The baseline frame EXCLUDES today ('1 day' PRECEDING). Including today
--     damps the very spike the z-score exists to detect.
--   * The baseline uses RANGE over dates, not ROWS. A thin name does not print
--     daily, so a 20-ROW frame reaches back an unpredictable number of weeks.
--   * own_z is clamped to +/-5: a name coming off a run of identical fees has a
--     near-zero standard deviation and produces an absurd z without the clamp.
--   * Thresholds and weights are named once, here, and nowhere else -- not in
--     Python, not in the API, not in the frontend (docs/07 §4).

INSERT INTO slb_specialness_daily (
    trade_date, symbol, series_code, contract_set, tenor_bucket, tenor_days,
    fee_per_share, fee_annualised_pct, gc_fee_annualised_pct, spread_to_gc_bps,
    xs_percentile, baseline_mean_pct, baseline_sd_pct, baseline_obs,
    own_z, own_z_cdf, specialness_score, classification,
    quote_date, days_since_last_trade, is_stale, source_query
)
WITH params AS (
    SELECT
        0.60::NUMERIC AS weight_cross_sectional,
        0.40::NUMERIC AS weight_own_history,
        3             AS stale_after_days,
        5.0::NUMERIC  AS z_clamp,
        2             AS min_baseline_obs,
        50.0::NUMERIC AS warm_from,
        75.0::NUMERIC AS special_from,
        90.0::NUMERIC AS hard_to_borrow_from
),
-- Every pair we actually hold exposure to, with the most recent fee at or before
-- that day. LATERAL because this is a per-row "latest before" lookup, which is
-- what the (symbol, trade_date) index on slb_quote_daily exists for.
universe AS (
    SELECT
        o.trade_date,
        o.symbol,
        o.series_code,
        o.contract_set,
        q.trade_date AS quote_date,
        q.tenor_days,
        q.fee_per_share,
        q.fee_annualised_pct
    FROM slb_open_position AS o
    JOIN LATERAL (
        SELECT
            b.trade_date,
            b.tenor_days,
            b.fee_per_share,
            b.fee_annualised_pct
        FROM slb_fee_basis AS b
        WHERE b.symbol = o.symbol
          AND b.series_code = o.series_code
          AND b.trade_date <= o.trade_date
          AND b.fee_annualised_pct > 0
        ORDER BY b.trade_date DESC
        LIMIT 1
    ) AS q ON TRUE
),
bucketed AS (
    SELECT
        u.*,
        CASE
            WHEN u.tenor_days <= 45  THEN '0-45d'
            WHEN u.tenor_days <= 135 THEN '46-135d'
            WHEN u.tenor_days <= 270 THEN '136-270d'
            ELSE '271d+'
        END AS tenor_bucket
    FROM universe AS u
),
-- A. Cross-sectional: rank within the same contract set and tenor bucket today.
cross_sectional AS (
    SELECT
        b.*,
        PERCENT_RANK() OVER (
            PARTITION BY b.trade_date, b.contract_set, b.tenor_bucket
            ORDER BY b.fee_annualised_pct
        ) AS xs_percentile
    FROM bucketed AS b
),
-- B. Own history: this symbol's own fee level over the previous 30 calendar days,
-- excluding today. Computed off the print history, not the carried-forward
-- universe, so a stale pair does not pollute its own baseline with repeats.
own_history AS (
    SELECT
        b.trade_date,
        b.symbol,
        AVG(b.fee_annualised_pct) OVER w         AS baseline_mean_pct,
        STDDEV_SAMP(b.fee_annualised_pct) OVER w AS baseline_sd_pct,
        COUNT(*) OVER w                          AS baseline_obs
    FROM slb_fee_basis AS b
    WHERE b.fee_annualised_pct > 0
    WINDOW w AS (
        PARTITION BY b.symbol
        ORDER BY b.trade_date
        RANGE BETWEEN INTERVAL '30 days' PRECEDING AND INTERVAL '1 day' PRECEDING
    )
),
-- One baseline per (symbol, day): the print history has a row per series, and
-- they share the same window, so any one of them carries the same values.
baseline AS (
    SELECT DISTINCT
        h.trade_date,
        h.symbol,
        h.baseline_mean_pct,
        h.baseline_sd_pct,
        h.baseline_obs
    FROM own_history AS h
),
scored AS (
    SELECT
        x.trade_date,
        x.symbol,
        x.series_code,
        x.contract_set,
        x.tenor_bucket,
        x.tenor_days,
        x.fee_per_share,
        x.fee_annualised_pct,
        x.quote_date,
        (x.trade_date - x.quote_date) AS days_since_last_trade,
        x.xs_percentile,
        bl.baseline_mean_pct,
        bl.baseline_sd_pct,
        COALESCE(bl.baseline_obs, 0) AS baseline_obs,
        g.gc_fee_annualised_pct,
        CASE
            WHEN COALESCE(bl.baseline_obs, 0) < p.min_baseline_obs THEN NULL
            ELSE GREATEST(
                -p.z_clamp,
                LEAST(
                    p.z_clamp,
                    (x.fee_annualised_pct - bl.baseline_mean_pct)
                        / NULLIF(bl.baseline_sd_pct, 0)
                )
            )
        END AS own_z,
        p.*
    FROM cross_sectional AS x
    CROSS JOIN params AS p
    LEFT JOIN baseline AS bl
        ON bl.trade_date = x.quote_date
       AND bl.symbol = x.symbol
    LEFT JOIN gc_rate_daily AS g
        ON g.as_of_date = x.quote_date
       AND g.contract_set = x.contract_set
),
blended AS (
    SELECT
        s.*,
        -- Normal CDF via the tanh approximation: max absolute error ~1e-4 over
        -- the clamped range, which is far finer than the 0-100 score resolution,
        -- and Postgres ships no erf().
        CASE
            WHEN s.own_z IS NULL THEN NULL
            ELSE 0.5 * (
                1 + TANH(0.7978845608 * s.own_z * (1 + 0.044715 * s.own_z * s.own_z))
            )
        END AS own_z_cdf
    FROM scored AS s
),
-- Blend once, into a named column, so the classification reads off a value
-- instead of repeating the expression three times.
final AS (
    SELECT
        b.*,
        (
            100 * CASE
                -- With no usable history the cross-sectional view carries the
                -- whole score rather than the row being dropped: a new name
                -- still has a borrow cost.
                WHEN b.own_z_cdf IS NULL THEN b.xs_percentile
                ELSE b.weight_cross_sectional * b.xs_percentile
                   + b.weight_own_history * b.own_z_cdf
            END
        )::NUMERIC(8, 4) AS specialness_score
    FROM blended AS b
)
SELECT
    f.trade_date,
    f.symbol,
    f.series_code,
    f.contract_set,
    f.tenor_bucket,
    f.tenor_days,
    f.fee_per_share,
    f.fee_annualised_pct,
    f.gc_fee_annualised_pct,
    ((f.fee_annualised_pct - f.gc_fee_annualised_pct) * 100)::NUMERIC(14, 2)
        AS spread_to_gc_bps,
    f.xs_percentile,
    f.baseline_mean_pct,
    f.baseline_sd_pct,
    f.baseline_obs,
    f.own_z,
    f.own_z_cdf,
    f.specialness_score,
    CASE
        WHEN f.specialness_score >= f.hard_to_borrow_from THEN 'HARD_TO_BORROW'
        WHEN f.specialness_score >= f.special_from        THEN 'SPECIAL'
        WHEN f.specialness_score >= f.warm_from           THEN 'WARM'
        ELSE 'GC'
    END AS classification,
    f.quote_date,
    f.days_since_last_trade,
    (f.days_since_last_trade > f.stale_after_days) AS is_stale,
    'specialness.sql' AS source_query
FROM final AS f
ON CONFLICT (trade_date, symbol, series_code) DO UPDATE SET
    fee_per_share         = EXCLUDED.fee_per_share,
    fee_annualised_pct    = EXCLUDED.fee_annualised_pct,
    gc_fee_annualised_pct = EXCLUDED.gc_fee_annualised_pct,
    spread_to_gc_bps      = EXCLUDED.spread_to_gc_bps,
    xs_percentile         = EXCLUDED.xs_percentile,
    baseline_mean_pct     = EXCLUDED.baseline_mean_pct,
    baseline_sd_pct       = EXCLUDED.baseline_sd_pct,
    baseline_obs          = EXCLUDED.baseline_obs,
    own_z                 = EXCLUDED.own_z,
    own_z_cdf             = EXCLUDED.own_z_cdf,
    specialness_score     = EXCLUDED.specialness_score,
    classification        = EXCLUDED.classification,
    quote_date            = EXCLUDED.quote_date,
    days_since_last_trade = EXCLUDED.days_since_last_trade,
    is_stale              = EXCLUDED.is_stale,
    source_query          = EXCLUDED.source_query;
