-- 009 — Funds Transfer Pricing and position P&L.
--
-- Methodology: docs/04-domain-ftp.md. Formulas: docs/07-analytics-spec.md §5, §9.
--
-- Three tables at three grains, because each number genuinely lives at a
-- different one: the fee accrues per position, the FTP charge applies to the NET
-- balance sheet a desk consumes in a name, and the NIM decomposition is a desk
-- statement.

-- Fee accrual, per position, per day. Straight-line over the contract on ACT/365
-- from FIRST-LEG SETTLEMENT -- not trade date, which would overstate the accrual
-- by one day on every trade in the book.
--
-- Sign convention: positive is a gain to OUR book, always. A borrow cost is a
-- negative number, not a positive number you subtract (rules/FINANCE.md).
CREATE TABLE slb_position_pnl_daily (
    as_of_date          DATE NOT NULL,
    trade_id            BIGINT NOT NULL REFERENCES slb_trade (trade_id),
    desk_id             TEXT NOT NULL REFERENCES desk (desk_id),
    symbol              TEXT NOT NULL,
    series_code         TEXT NOT NULL,
    side                TEXT NOT NULL,
    quantity            BIGINT NOT NULL,
    underlying_close    NUMERIC(18, 4) NOT NULL,
    notional_inr        NUMERIC(20, 2) NOT NULL,
    tenor_days          INTEGER NOT NULL,
    fee_per_share       NUMERIC(12, 4) NOT NULL,
    fee_annualised_pct  NUMERIC(12, 4) NOT NULL,
    fee_pnl_inr         NUMERIC(20, 4) NOT NULL,
    source_query        TEXT NOT NULL,
    PRIMARY KEY (as_of_date, trade_id)
);

CREATE INDEX slb_position_pnl_date_idx ON slb_position_pnl_daily (as_of_date);
CREATE INDEX slb_position_pnl_desk_idx ON slb_position_pnl_daily (desk_id, as_of_date);

-- The FTP charge, on the NET position a desk holds in a name and series.
--
-- Netting is the whole point. A matched book -- borrow a name in, lend the same
-- name out, same series, same size -- consumes almost no balance sheet, so it
-- should earn the fee spread almost cleanly. A directional book consumes real
-- balance sheet and must pay for it. Charging both legs of a matched pair would
-- make FTP a flat tax on turnover instead of a price for the resource actually
-- consumed, and it would make a matched book look loss-making.
CREATE TABLE ftp_charge_daily (
    as_of_date           DATE NOT NULL,
    desk_id              TEXT NOT NULL REFERENCES desk (desk_id),
    symbol               TEXT NOT NULL,
    series_code          TEXT NOT NULL,
    -- Positive = net borrower of stock, so a consumer of balance sheet.
    net_quantity         BIGINT NOT NULL,
    net_notional_inr     NUMERIC(20, 2) NOT NULL,
    tenor_days           INTEGER NOT NULL,
    base_rate_pct        NUMERIC(10, 4) NOT NULL,
    term_liquidity_bps   NUMERIC(10, 2) NOT NULL,
    contingent_liq_bps   NUMERIC(10, 2) NOT NULL,
    ftp_rate_pct         NUMERIC(10, 4) NOT NULL,
    ftp_charge_inr       NUMERIC(20, 4) NOT NULL,
    curve_is_estimated   BOOLEAN NOT NULL,
    source_query         TEXT NOT NULL,
    PRIMARY KEY (as_of_date, desk_id, symbol, series_code)
);

CREATE INDEX ftp_charge_date_idx ON ftp_charge_daily (as_of_date);
CREATE INDEX ftp_charge_desk_idx ON ftp_charge_daily (desk_id, as_of_date);

-- The desk statement, and the decomposition that has to add up:
--
--   desk_spread     = what the desk earned externally, minus the FTP it was charged
--   treasury_spread = the FTP charged, minus Treasury's actual cost of funds
--   desk_spread + treasury_spread = net interest margin
--
-- If that identity does not hold, the FTP model is decorative. `reconciles` is
-- stored on the row so the API can state whether its own numbers add up, and
-- tests/test_ftp.py asserts it to the rupee.
CREATE TABLE desk_pnl_daily (
    as_of_date          DATE NOT NULL,
    desk_id             TEXT NOT NULL REFERENCES desk (desk_id),
    positions           INTEGER NOT NULL,
    gross_notional_inr  NUMERIC(20, 2) NOT NULL,
    net_notional_inr    NUMERIC(20, 2) NOT NULL,
    fee_pnl_inr         NUMERIC(20, 4) NOT NULL,
    ftp_charge_inr      NUMERIC(20, 4) NOT NULL,
    net_spread_inr      NUMERIC(20, 4) NOT NULL,
    nim_inr             NUMERIC(20, 4) NOT NULL,
    desk_spread_inr     NUMERIC(20, 4) NOT NULL,
    treasury_spread_inr NUMERIC(20, 4) NOT NULL,
    reconciles          BOOLEAN NOT NULL,
    source_query        TEXT NOT NULL,
    PRIMARY KEY (as_of_date, desk_id)
);

CREATE INDEX desk_pnl_date_idx ON desk_pnl_daily (as_of_date);
