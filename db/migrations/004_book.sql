-- 004 — Our own book. Synthetic and seeded; the market above is real.
-- Lifecycle and state machine: docs/01-domain-slb-lifecycle.md §10.

CREATE TABLE slb_trade (
    trade_id              BIGSERIAL PRIMARY KEY,
    -- Survives rollovers, so the 12-month total-tenure cap is checkable.
    original_trade_id     BIGINT REFERENCES slb_trade (trade_id),
    desk_id               TEXT NOT NULL REFERENCES desk (desk_id),
    symbol                TEXT NOT NULL REFERENCES security (symbol),
    series_code           TEXT NOT NULL,
    side                  TEXT NOT NULL CHECK (side IN ('LEND', 'BORROW')),
    quantity              BIGINT NOT NULL CHECK (quantity > 0),
    trade_date            DATE NOT NULL,
    -- T+1. The accrual starts here, not at trade date: starting at trade date
    -- overstates the fee by a day on every trade in the book (docs/07 §5).
    first_leg_settle_date DATE NOT NULL,
    reverse_leg_date      DATE NOT NULL,
    -- As quoted: rupees per share for the contract period, not annualised.
    fee_per_share         NUMERIC(12, 4) NOT NULL CHECK (fee_per_share >= 0),
    status                TEXT NOT NULL CHECK (status IN (
        'MATCHED', 'SETTLED_L', 'ROLLED', 'UNWOUND', 'FORECLOSED', 'CLOSED', 'CLOSED_OUT'
    )),
    CHECK (first_leg_settle_date > trade_date),
    CHECK (reverse_leg_date > first_leg_settle_date)
);

CREATE INDEX slb_trade_desk_idx ON slb_trade (desk_id);
CREATE INDEX slb_trade_symbol_idx ON slb_trade (symbol, series_code);
CREATE INDEX slb_trade_original_idx ON slb_trade (original_trade_id);
CREATE INDEX slb_trade_status_idx ON slb_trade (status);

-- Append-only fee history. A recall, repay, rollover or re-rate writes a new leg
-- and never mutates slb_trade.fee_per_share, which is what makes it possible to
-- attribute P&L to a re-rate rather than to the original trade.
--
-- ponytail: no DATERANGE exclusion constraint -- non-overlap is asserted by
-- db/queries/validate_legs.sql instead. Add the GIST constraint if leg editing
-- ever becomes interactive.
CREATE TABLE slb_trade_leg (
    leg_id         BIGSERIAL PRIMARY KEY,
    trade_id       BIGINT NOT NULL REFERENCES slb_trade (trade_id),
    leg_type       TEXT NOT NULL CHECK (leg_type IN (
        'OPEN', 'RERATE', 'PARTIAL_REPAY', 'RECALL', 'ROLLOVER', 'CLOSE'
    )),
    effective_from DATE NOT NULL,
    effective_to   DATE,              -- NULL on the live leg
    quantity       BIGINT NOT NULL CHECK (quantity > 0),
    fee_per_share  NUMERIC(12, 4) NOT NULL,
    reason         TEXT,
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX slb_trade_leg_trade_idx ON slb_trade_leg (trade_id, effective_from);

-- Repo. purchase_price and repurchase_price are STORED, not recomputed on read:
-- the haircut and the collateral's dirty value on day one are historical facts,
-- and recomputing them from today's prices rewrites history (docs/06).
CREATE TABLE repo_trade (
    repo_id          BIGSERIAL PRIMARY KEY,
    desk_id          TEXT NOT NULL REFERENCES desk (desk_id),
    -- REPO: we borrow cash and deliver collateral. REVERSE: we lend cash.
    direction        TEXT NOT NULL CHECK (direction IN ('REPO', 'REVERSE')),
    isin             TEXT NOT NULL REFERENCES gsec (isin),
    nominal          NUMERIC(18, 2) NOT NULL CHECK (nominal > 0),
    trade_date       DATE NOT NULL,
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    repo_rate_pct    NUMERIC(8, 4) NOT NULL,       -- ACT/365
    haircut          NUMERIC(8, 6) NOT NULL CHECK (haircut >= 0 AND haircut < 1),
    purchase_price   NUMERIC(18, 4) NOT NULL,
    repurchase_price NUMERIC(18, 4) NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('OPEN', 'MATURED', 'CLOSED_EARLY')),
    CHECK (end_date > start_date),
    CHECK (repurchase_price >= purchase_price)
);

CREATE INDEX repo_trade_desk_idx ON repo_trade (desk_id);
CREATE INDEX repo_trade_dates_idx ON repo_trade (start_date, end_date);

-- Daily revaluation. Both the price AND the haircut move; a haircut widening on
-- unchanged prices is still a margin call (docs/02 §3).
CREATE TABLE collateral_position (
    as_of_date         DATE NOT NULL,
    repo_id            BIGINT NOT NULL REFERENCES repo_trade (repo_id),
    isin               TEXT NOT NULL REFERENCES gsec (isin),
    nominal            NUMERIC(18, 2) NOT NULL,
    mtm_clean_price    NUMERIC(18, 4) NOT NULL,
    accrued_interest   NUMERIC(18, 4) NOT NULL,
    dirty_value        NUMERIC(18, 4) NOT NULL,
    haircut            NUMERIC(8, 6) NOT NULL,
    post_haircut_value NUMERIC(18, 4) NOT NULL,
    haircut_source     TEXT NOT NULL CHECK (haircut_source IN ('NSE_VAR', 'TENOR_MODEL')),
    PRIMARY KEY (as_of_date, repo_id)
);

CREATE TABLE margin_call (
    call_id          BIGSERIAL PRIMARY KEY,
    as_of_date       DATE NOT NULL,
    repo_id          BIGINT NOT NULL REFERENCES repo_trade (repo_id),
    desk_id          TEXT NOT NULL REFERENCES desk (desk_id),
    exposure         NUMERIC(18, 4) NOT NULL,
    collateral_value NUMERIC(18, 4) NOT NULL,
    shortfall        NUMERIC(18, 4) NOT NULL CHECK (shortfall > 0),
    -- shortfall rounded up to the minimum transfer amount
    call_amount      NUMERIC(18, 4) NOT NULL,
    -- 09:00 the next business day, per CCIL's TREPS rule (docs/02 §3).
    due_at           TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('OPEN', 'MET', 'BREACHED')),
    UNIQUE (as_of_date, repo_id)
);

CREATE INDEX margin_call_status_idx ON margin_call (status, as_of_date);
