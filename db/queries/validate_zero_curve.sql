-- validate_zero_curve.sql — a discount curve has to be a discount curve.
--
-- Returns any curve point that breaks a structural property. An empty result is
-- a pass. These are not style checks: a non-monotone discount curve implies a
-- negative forward rate, which prices an instrument nobody would sell.

WITH ordered AS (
    SELECT
        as_of_date,
        method,
        tenor_years,
        discount_factor,
        zero_rate_pct,
        LAG(discount_factor) OVER w AS previous_factor,
        LAG(tenor_years) OVER w     AS previous_tenor
    FROM zero_curve_point
    WINDOW w AS (PARTITION BY as_of_date, method ORDER BY tenor_years)
)
SELECT
    o.as_of_date,
    o.method,
    o.tenor_years,
    o.discount_factor,
    o.zero_rate_pct,
    CASE
        WHEN o.discount_factor > o.previous_factor
            THEN 'discount factor rises with tenor (negative forward)'
        WHEN o.zero_rate_pct <= 0  THEN 'non-positive zero rate'
        WHEN o.zero_rate_pct > 25  THEN 'implausible zero rate'
        ELSE 'discount factor outside (0, 1]'
    END AS problem
FROM ordered AS o
WHERE (o.previous_factor IS NOT NULL AND o.discount_factor > o.previous_factor)
   OR o.zero_rate_pct <= 0
   OR o.zero_rate_pct > 25
   OR o.discount_factor <= 0
   OR o.discount_factor > 1;
