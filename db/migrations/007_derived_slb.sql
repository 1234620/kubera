-- 007 — Derived SLB analytics: the fee basis view and the materialised metrics.
--
-- Formulas, units and thresholds: docs/07-analytics-spec.md.
-- Materialised rather than views because specialness runs three window functions
-- over a growing history and the dashboard has to load in under a second.
-- ponytail: full refresh each run; switch to incremental on trade_date if the
-- history ever passes a few years.

-- The one conversion everything else depends on. NSE quotes the lending fee in
-- rupees per share FOR THE WHOLE CONTRACT PERIOD, so comparing a 1-month series
-- to a 12-month series without annualising is meaningless (docs/07 §1).
--
-- A view, not a table: it is one indexed join over a few thousand rows a day, and
-- materialising it would add a refresh step for nothing.
CREATE VIEW slb_fee_basis AS
WITH quote AS (
    SELECT
        q.trade_date,
        q.symbol,
        q.series_code,
        q.contract_set,
        q.reverse_leg_date,
        q.close_fee,
        q.vwaf,
        q.traded_qty,
        q.num_trades,
        -- The fee accrues from FIRST-LEG SETTLEMENT, which is T+1, not from trade
        -- date. Using trade date overstates the tenor by a day on every quote.
        -- Exchange holidays are not modelled here, only the weekend roll.
        q.trade_date + CASE EXTRACT(DOW FROM q.trade_date)
            WHEN 5 THEN 3   -- Friday  -> Monday
            WHEN 6 THEN 2   -- Saturday -> Monday
            ELSE 1
        END AS first_leg_settle_date
    FROM slb_quote_daily AS q
)
SELECT
    q.trade_date,
    q.symbol,
    q.series_code,
    q.contract_set,
    q.reverse_leg_date,
    q.first_leg_settle_date,
    (q.reverse_leg_date - q.first_leg_settle_date)          AS tenor_days,
    q.close_fee                                             AS fee_per_share,
    q.vwaf,
    q.traded_qty,
    q.num_trades,
    c.close_price                                           AS underlying_close,
    (q.traded_qty * c.close_price)::NUMERIC(20, 2)          AS traded_notional_inr,
    -- fee_annualised_pct = fee/price x 365/tenor x 100, ACT/365.
    (
        q.close_fee / c.close_price
        * (365.0 / NULLIF(q.reverse_leg_date - q.first_leg_settle_date, 0))
        * 100
    )::NUMERIC(12, 4)                                       AS fee_annualised_pct
FROM quote AS q
JOIN cash_quote_daily AS c
    ON c.trade_date = q.trade_date
   AND c.symbol = q.symbol
   -- Prefer the EQ segment but fall back: a surveillance-segment (BE) security
   -- can be SLB-eligible while absent from EQ (docs/05 §2.6).
   AND c.series = (
        SELECT p.series
        FROM cash_quote_daily AS p
        WHERE p.trade_date = q.trade_date AND p.symbol = q.symbol
        ORDER BY (p.series <> 'EQ'), p.series
        LIMIT 1
   )
WHERE q.reverse_leg_date > q.first_leg_settle_date
  AND c.close_price > 0;

-- Supply and demand. utilisation needs lendable supply, which is NOT published in
-- India, so it is estimated and flagged; days_to_cover and the delivery ratio are
-- fully observable and carry no estimate (docs/07 §2).
CREATE TABLE slb_utilisation_daily (
    trade_date         DATE NOT NULL,
    symbol             TEXT NOT NULL,
    on_loan_qty        BIGINT NOT NULL,
    avg_volume_20d     NUMERIC(20, 2),
    avg_delivery_20d   NUMERIC(20, 2),
    lendable_qty_est   NUMERIC(20, 2),
    utilisation_pct    NUMERIC(10, 4),
    days_to_cover      NUMERIC(12, 4),
    on_loan_change_1d  BIGINT,
    utilisation_5d_avg NUMERIC(10, 4),
    is_estimated       BOOLEAN NOT NULL DEFAULT TRUE,
    source_query       TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);

CREATE INDEX slb_utilisation_date_idx ON slb_utilisation_daily (trade_date);

-- The headline metric. Both components are stored, not just the blend: "87" is
-- not an answer, "94th percentile at this tenor and 2.1 sigma above its own
-- 30-day mean" is (docs/07 §4, ADR 0004).
CREATE TABLE slb_specialness_daily (
    trade_date            DATE NOT NULL,
    symbol                TEXT NOT NULL,
    series_code           TEXT NOT NULL,
    contract_set          TEXT NOT NULL,
    tenor_bucket          TEXT NOT NULL,
    tenor_days            INTEGER NOT NULL,
    fee_per_share         NUMERIC(12, 4) NOT NULL,
    fee_annualised_pct    NUMERIC(12, 4) NOT NULL,
    gc_fee_annualised_pct NUMERIC(12, 4),
    spread_to_gc_bps      NUMERIC(14, 2),
    xs_percentile         NUMERIC(8, 6) NOT NULL,
    baseline_mean_pct     NUMERIC(12, 4),
    baseline_sd_pct       NUMERIC(12, 4),
    baseline_obs          INTEGER NOT NULL,
    own_z                 NUMERIC(10, 4),
    own_z_cdf             NUMERIC(8, 6),
    specialness_score     NUMERIC(8, 4) NOT NULL,
    classification        TEXT NOT NULL
        CHECK (classification IN ('GC', 'WARM', 'SPECIAL', 'HARD_TO_BORROW')),
    -- Only ~230 of ~800 open (symbol, series) pairs print on a given day, so a
    -- score can be computed off a carried-forward fee. That is flagged, never
    -- hidden: "no recent print" is information about liquidity (docs/07 §4).
    quote_date            DATE NOT NULL,
    days_since_last_trade INTEGER NOT NULL,
    is_stale              BOOLEAN NOT NULL,
    source_query          TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol, series_code)
);

CREATE INDEX slb_specialness_date_idx ON slb_specialness_daily (trade_date);
CREATE INDEX slb_specialness_score_idx ON slb_specialness_daily (trade_date, specialness_score DESC);
