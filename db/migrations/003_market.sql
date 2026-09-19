-- 003 — Market data. One table per source file, named after it so provenance is
-- obvious. Every table carries source_file and loaded_at: when a number looks
-- wrong, the first question is which file it came from.
--
-- Column maps and the verification behind them: docs/05-data-sources.md.

-- SLBM_BC_*.DAT. Fees are rupees per share for the whole contract period, NOT
-- annualised -- see docs/07 §1 before comparing across series.
CREATE TABLE slb_quote_daily (
    trade_date       DATE NOT NULL,
    symbol           TEXT NOT NULL,
    series_code      TEXT NOT NULL,
    contract_set     TEXT NOT NULL,
    security_name    TEXT,
    reverse_leg_date DATE NOT NULL,
    market_type      TEXT,
    prev_close_fee   NUMERIC(12, 4) NOT NULL,
    open_fee         NUMERIC(12, 4) NOT NULL,
    high_fee         NUMERIC(12, 4) NOT NULL,
    low_fee          NUMERIC(12, 4) NOT NULL,
    close_fee        NUMERIC(12, 4) NOT NULL,
    year_high_fee    NUMERIC(12, 4) NOT NULL,
    year_low_fee     NUMERIC(12, 4) NOT NULL,
    traded_qty       BIGINT NOT NULL,
    traded_value_inr NUMERIC(18, 4) NOT NULL,
    num_trades       INTEGER NOT NULL,
    vwaf             NUMERIC(12, 4) NOT NULL,   -- traded_value / traded_qty
    source_file      TEXT NOT NULL,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, symbol, series_code),
    CHECK (reverse_leg_date > trade_date),
    CHECK (low_fee <= high_fee AND traded_qty > 0)
);

-- The specialness baseline partitions by symbol and orders by date, so this
-- composite is a deliberate performance index, not just a key (docs/06).
CREATE INDEX slb_quote_symbol_date_idx ON slb_quote_daily (symbol, trade_date);
CREATE INDEX slb_quote_date_idx ON slb_quote_daily (trade_date);

-- slb_openpos_*.csv. The numerator of utilisation; LAG() over it gives the flow.
CREATE TABLE slb_open_position (
    trade_date      DATE NOT NULL,
    symbol          TEXT NOT NULL,
    series_code     TEXT NOT NULL,
    contract_set    TEXT NOT NULL,
    outstanding_qty BIGINT NOT NULL CHECK (outstanding_qty > 0),
    source_file     TEXT NOT NULL,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, symbol, series_code)
);

CREATE INDEX slb_open_pos_date_idx ON slb_open_position (trade_date);
CREATE INDEX slb_open_pos_symbol_date_idx ON slb_open_position (symbol, trade_date);

-- SLB_ELG_SEC_*.csv. recall_eligible drives the FTP contingent liquidity charge:
-- a position that cannot be unwound early consumes liquidity (docs/07 §9).
CREATE TABLE slb_eligibility (
    as_of_date      DATE NOT NULL,
    symbol          TEXT NOT NULL,
    series_code     TEXT NOT NULL,
    normal_eligible BOOLEAN NOT NULL,
    recall_eligible BOOLEAN NOT NULL,
    repay_eligible  BOOLEAN NOT NULL,
    market_type     TEXT,
    source_file     TEXT NOT NULL,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of_date, symbol, series_code)
);

CREATE INDEX slb_eligibility_date_idx ON slb_eligibility (as_of_date);

-- Forclosure_SLB_*.CSV (NSE's spelling). Every row is a forced unwind at a
-- pro-rata fee: a P&L event, not reference data (docs/01 §7).
CREATE TABLE slb_foreclosure (
    symbol                    TEXT NOT NULL,
    record_date               DATE NOT NULL,
    action_desc               TEXT NOT NULL,
    series_scope              TEXT,
    isin                      TEXT,
    announcement_date         DATE,
    ex_date                   DATE,
    foreclosure_date          DATE,
    shut_period_start         DATE,
    shut_period_end           DATE,
    next_trade_date           DATE,
    foreclosure_settlement_no TEXT,
    action_code               TEXT,
    source_file               TEXT NOT NULL,
    loaded_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, record_date, action_desc)
);

-- C_VAR1_SLB_*.DAT, de-duplicated to one row per symbol: the source repeats every
-- symbol across all 73 series with identical margins (docs/05 §2.5).
-- total_margin_pct is the equity haircut.
CREATE TABLE slb_var_margin (
    as_of_date            DATE NOT NULL,
    symbol                TEXT NOT NULL,
    isin                  TEXT,
    var_pct               NUMERIC(8, 4) NOT NULL,
    applicable_var_pct    NUMERIC(8, 4) NOT NULL,
    elm_pct               NUMERIC(8, 4) NOT NULL,
    additional_margin_pct NUMERIC(8, 4) NOT NULL,
    total_margin_pct      NUMERIC(8, 4) NOT NULL,
    source_file           TEXT NOT NULL,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of_date, symbol),
    -- Holds exactly for all 84,389 rows of the source file.
    CHECK (applicable_var_pct + elm_pct + additional_margin_pct = total_margin_pct)
);

-- The full series universe, a by-product of the VaR file and the only complete
-- list: the bhavcopy shows only series that traded, eligibility only those live.
CREATE TABLE slb_series_universe (
    as_of_date   DATE NOT NULL,
    series_code  TEXT NOT NULL,
    contract_set TEXT NOT NULL,
    source_file  TEXT NOT NULL,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of_date, series_code)
);

-- sec_bhavdata_full_*.csv. No SLB file carries an underlying price, so without
-- this nothing can be annualised (docs/07 §1). series is in the key because a
-- surveillance-segment (BE) security can be SLB-eligible while absent from EQ.
CREATE TABLE cash_quote_daily (
    trade_date   DATE NOT NULL,
    symbol       TEXT NOT NULL,
    series       TEXT NOT NULL,
    prev_close   NUMERIC(18, 4) NOT NULL,
    close_price  NUMERIC(18, 4) NOT NULL,
    avg_price    NUMERIC(18, 4) NOT NULL,
    traded_qty   BIGINT NOT NULL,
    turnover_inr NUMERIC(20, 2) NOT NULL,   -- source is in lakhs; converted on load
    num_trades   INTEGER NOT NULL,
    delivery_qty BIGINT,                    -- '-' in the BE segment, so NULL not 0
    delivery_pct NUMERIC(8, 4),
    source_file  TEXT NOT NULL,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, symbol, series)
);

CREATE INDEX cash_quote_date_idx ON cash_quote_daily (trade_date);
CREATE INDEX cash_quote_symbol_date_idx ON cash_quote_daily (symbol, trade_date);

-- trd*_sett.csv from the dly*.zip bundle. Prices are CLEAN per 100 face.
-- weighted_ytm_pct is the validation target for our own solver (docs/03 §11).
CREATE TABLE gsec_trade_daily (
    trade_date       DATE NOT NULL,
    security_code    TEXT NOT NULL,
    settl_days       SMALLINT NOT NULL,
    instrument_type  TEXT NOT NULL,
    issue_name       TEXT,
    trade_type       TEXT,
    num_trades       INTEGER NOT NULL,
    traded_value_inr NUMERIC(20, 2) NOT NULL,  -- source is in crore; converted on load
    low_price        NUMERIC(18, 4) NOT NULL,
    high_price       NUMERIC(18, 4) NOT NULL,
    last_price       NUMERIC(18, 4) NOT NULL,
    vwap_clean_price NUMERIC(18, 4) NOT NULL,
    weighted_ytm_pct NUMERIC(10, 4) NOT NULL,
    source_file      TEXT NOT NULL,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, security_code, settl_days),
    CHECK (low_price <= vwap_clean_price AND vwap_clean_price <= high_price)
);

CREATE INDEX gsec_trade_date_idx ON gsec_trade_daily (trade_date);
