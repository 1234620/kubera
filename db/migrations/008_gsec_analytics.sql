-- 008 — G-Sec analytics: priced and risk-measured from the bond math.
--
-- Formulas: docs/03-domain-bond-math.md. Computed in Python rather than SQL
-- because the YTM solve is Newton-Raphson, and Newton-Raphson in SQL would be a
-- party trick (ADR 0001).
--
-- nse_ytm_pct and ytm_diff_bps are stored alongside our own figure on purpose:
-- the table carries its own accuracy against the exchange, so the dashboard can
-- show it and nobody has to take the pricing on trust.

CREATE TABLE gsec_analytics_daily (
    trade_date        DATE NOT NULL,
    isin              TEXT NOT NULL REFERENCES gsec (isin),
    security_code     TEXT NOT NULL,
    coupon_pct        NUMERIC(8, 4) NOT NULL,
    maturity_date     DATE NOT NULL,
    settlement_date   DATE NOT NULL,
    residual_years    NUMERIC(10, 4) NOT NULL,
    clean_price       NUMERIC(18, 6) NOT NULL,
    accrued_interest  NUMERIC(18, 6) NOT NULL,
    dirty_price       NUMERIC(18, 6) NOT NULL,
    ytm_pct           NUMERIC(12, 6) NOT NULL,
    nse_ytm_pct       NUMERIC(12, 6),
    ytm_diff_bps      NUMERIC(12, 4),
    macaulay_duration NUMERIC(12, 6) NOT NULL,
    modified_duration NUMERIC(12, 6) NOT NULL,
    convexity         NUMERIC(14, 6) NOT NULL,
    -- Unsigned, per 100 face. The sign is applied at position level, where we
    -- know whether we are long or short (rules/FINANCE.md).
    dv01_per_100_face NUMERIC(14, 8) NOT NULL,
    traded_value_inr  NUMERIC(20, 2),
    source_query      TEXT NOT NULL,
    PRIMARY KEY (trade_date, isin),
    CHECK (dirty_price >= clean_price),
    CHECK (macaulay_duration >= modified_duration),
    CHECK (convexity > 0)
);

CREATE INDEX gsec_analytics_date_idx ON gsec_analytics_daily (trade_date);
CREATE INDEX gsec_analytics_isin_idx ON gsec_analytics_daily (isin, trade_date);
