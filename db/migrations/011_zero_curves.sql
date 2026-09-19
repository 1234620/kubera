-- 011 — Zero-coupon curves, implied borrow forwards, key-rate risk.
--
-- Method and the reasoning: docs/13-curve-construction.md.
--
-- This is the layer that turns the project from "computed metrics" into "built a
-- model": a discount curve everything can be priced off, the SLB fee curve read
-- as a forward curve, and curve risk decomposed by tenor instead of collapsed
-- into one number.

-- The curve's inputs, kept so a curve can always be traced back to the prints it
-- was built from. `observed_on` differs from `as_of_date` because daily G-Sec
-- prints are sparse and the curve pools a trailing window.
CREATE TABLE curve_observation (
    as_of_date      DATE NOT NULL,
    isin            TEXT NOT NULL REFERENCES gsec (isin),
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('GS', 'TB')),
    security_code   TEXT NOT NULL,
    observed_on     DATE NOT NULL,
    settlement_date DATE NOT NULL,
    tenor_years     NUMERIC(10, 6) NOT NULL CHECK (tenor_years > 0),
    dirty_price     NUMERIC(18, 6) NOT NULL CHECK (dirty_price > 0),
    ytm_pct         NUMERIC(12, 6) NOT NULL,
    nse_ytm_pct     NUMERIC(12, 6),
    source_query    TEXT NOT NULL,
    PRIMARY KEY (as_of_date, isin)
);

CREATE INDEX curve_observation_date_idx ON curve_observation (as_of_date, tenor_years);

-- Both curves live in one table with `method` as part of the key, so they can be
-- read side by side. That comparison is the point: the bootstrap is exact where
-- something traded, NSS is smooth and extrapolates, and neither dominates.
CREATE TABLE zero_curve_point (
    as_of_date       DATE NOT NULL,
    method           TEXT NOT NULL CHECK (method IN ('BOOTSTRAP', 'NSS')),
    tenor_years      NUMERIC(10, 4) NOT NULL,
    discount_factor  NUMERIC(14, 10) NOT NULL
        CHECK (discount_factor > 0 AND discount_factor <= 1),
    zero_rate_pct    NUMERIC(12, 6) NOT NULL,
    -- Forward from half this tenor out to it: the rate implied for the second
    -- half of the period, which is what "the market expects rates to rise" means
    -- mechanically.
    forward_rate_pct NUMERIC(12, 6),
    -- TRUE where the method had to reach past its observations.
    is_extrapolated  BOOLEAN NOT NULL DEFAULT FALSE,
    source_query     TEXT NOT NULL,
    PRIMARY KEY (as_of_date, method, tenor_years)
);

CREATE INDEX zero_curve_date_idx ON zero_curve_point (as_of_date, method);

-- The fitted parameters, not just the curve they generate. This is the reason to
-- prefer a parametric form: beta0 is the long level, beta0+beta1 the short rate,
-- beta2/beta3 the two curvatures, and rmse_bps says whether to trust any of it.
CREATE TABLE nss_fit (
    as_of_date     DATE PRIMARY KEY,
    beta0          NUMERIC(12, 6) NOT NULL,
    beta1          NUMERIC(12, 6) NOT NULL,
    beta2          NUMERIC(12, 6) NOT NULL,
    beta3          NUMERIC(12, 6) NOT NULL,
    tau1           NUMERIC(10, 4) NOT NULL CHECK (tau1 > 0),
    tau2           NUMERIC(10, 4) NOT NULL,
    short_rate_pct NUMERIC(12, 6) NOT NULL,
    rmse_bps       NUMERIC(10, 3) NOT NULL,
    observations   INTEGER NOT NULL CHECK (observations >= 4),
    description    TEXT NOT NULL,
    -- tau2 > tau1 keeps the two humps in their roles; swapped, the parameters
    -- stop being interpretable even though the curve is identical.
    CHECK (tau2 > tau1)
);

-- The SLB fee curve read as a forward curve. A spot fee says a name is expensive
-- now; the forward says whether the market expects it to STAY expensive, which is
-- the actual trading question (docs/13 §4).
CREATE TABLE slb_implied_forward (
    trade_date          DATE NOT NULL,
    symbol              TEXT NOT NULL,
    contract_set        TEXT NOT NULL,
    near_tenor_days     INTEGER NOT NULL,
    far_tenor_days      INTEGER NOT NULL,
    near_fee_pct        NUMERIC(12, 4) NOT NULL,
    far_fee_pct         NUMERIC(12, 4) NOT NULL,
    implied_forward_pct NUMERIC(12, 4) NOT NULL,
    signal              TEXT NOT NULL CHECK (signal IN (
        'RESOLVING', 'PERSISTENT', 'WORSENING', 'ANOMALOUS', 'FLAT'
    )),
    source_query        TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol, contract_set, near_tenor_days, far_tenor_days),
    CHECK (far_tenor_days > near_tenor_days)
);

CREATE INDEX slb_implied_forward_date_idx ON slb_implied_forward (trade_date, signal);

-- Curve risk on the repo collateral, bucketed. A parallel DV01 cannot tell a
-- ten-year position from a barbell of twos and thirties with the same total, and
-- a book that is DV01-neutral overall can still be badly exposed to a steepening.
CREATE TABLE key_rate_dv01_daily (
    as_of_date      DATE NOT NULL,
    isin            TEXT NOT NULL REFERENCES gsec (isin),
    key_tenor_years NUMERIC(8, 4) NOT NULL,
    -- Signed so positive means the position loses value when that part of the
    -- curve rises, which is the convention a risk report reads.
    dv01_inr        NUMERIC(20, 6) NOT NULL,
    source_query    TEXT NOT NULL,
    PRIMARY KEY (as_of_date, isin, key_tenor_years)
);

CREATE INDEX key_rate_dv01_date_idx ON key_rate_dv01_daily (as_of_date);
