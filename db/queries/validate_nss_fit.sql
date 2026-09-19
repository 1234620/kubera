-- validate_nss_fit.sql — the parametric fit has to be usable.
--
-- Returns any day whose fit is too loose to price off, or whose parameters have
-- lost their meaning. An empty result is a pass.
--
-- The RMSE bound is the important one. NSS will always return *a* curve; the
-- residual is the only thing that says whether it describes the market. 25bp is
-- generous for a sovereign curve -- the observed fits run around 8bp -- and
-- anything beyond it means the observations disagree with each other more than
-- the functional form can absorb, which is a data problem, not a fit problem.

SELECT
    as_of_date,
    rmse_bps,
    observations,
    tau1,
    tau2,
    short_rate_pct,
    beta0,
    CASE
        WHEN rmse_bps > 25      THEN 'fit too loose to price off'
        WHEN observations < 6   THEN 'too few bonds to identify four betas well'
        WHEN tau2 <= tau1       THEN 'decay parameters swapped, so uninterpretable'
        ELSE 'implausible level'
    END AS problem
FROM nss_fit
WHERE rmse_bps > 25
   OR observations < 6
   OR tau2 <= tau1
   OR beta0 <= 0
   OR beta0 > 25;
