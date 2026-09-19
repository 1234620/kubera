-- 005 — Funding curve and the GC benchmark.
-- Methodology: docs/04-domain-ftp.md §3, docs/07-analytics-spec.md §3 and §9.

-- Base curve from T-bill yields under a year and G-Sec yields beyond, plus a
-- documented issuer spread. is_estimated is a real column, not a comment: a node
-- we interpolated or substituted must say so, and the API passes the flag to the UI.
CREATE TABLE funding_curve_point (
    as_of_date                 DATE NOT NULL,
    tenor_days                 INTEGER NOT NULL CHECK (tenor_days > 0),
    base_rate_pct              NUMERIC(8, 4) NOT NULL,
    term_liquidity_premium_bps NUMERIC(8, 2) NOT NULL DEFAULT 0,
    source                     TEXT NOT NULL
        CHECK (source IN ('TBILL', 'GSEC', 'INTERPOLATED')),
    is_estimated               BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (as_of_date, tenor_days)
);

-- The equity SLB market publishes no GC rate, so we define one: the
-- quantity-weighted median annualised fee of the day's liquid cohort. Median
-- because the fee distribution is severely right-skewed (docs/07 §3).
CREATE TABLE gc_rate_daily (
    as_of_date               DATE NOT NULL,
    contract_set             TEXT NOT NULL,
    gc_fee_per_share_median  NUMERIC(12, 4) NOT NULL,
    gc_fee_annualised_pct    NUMERIC(10, 4) NOT NULL,
    cohort_size              INTEGER NOT NULL,
    -- A median of a dozen observations is not a market rate.
    is_estimated             BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (as_of_date, contract_set)
);
