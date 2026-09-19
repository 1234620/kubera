-- 002 — Reference data.
--
-- Populated from the market files rather than hand-maintained, so the universe
-- cannot drift from what NSE actually published. See docs/06-data-model.md.

CREATE TABLE security (
    symbol        TEXT PRIMARY KEY,
    security_name TEXT,
    isin          TEXT,
    first_seen    DATE NOT NULL,
    last_seen     DATE NOT NULL
);

-- A series code alone does not give a tenor: the code is reused every year, and
-- the reverse-leg settlement date is what makes it a contract. The date is
-- observed in the bhavcopy, never hardcoded (docs/01 §3).
CREATE TABLE slb_series (
    series_code      TEXT NOT NULL,
    reverse_leg_date DATE NOT NULL,
    contract_set     TEXT NOT NULL
        CHECK (contract_set IN ('REGULAR', 'NON_FORECLOSING', 'ROLLOVER')),
    first_seen       DATE NOT NULL,
    last_seen        DATE NOT NULL,
    PRIMARY KEY (series_code, reverse_leg_date)
);

-- Central-government paper. Coupon dates come from the WDM master rather than a
-- six-month offset, which is what makes stub-period accrual correct (docs/03 §1).
CREATE TABLE gsec (
    isin            TEXT PRIMARY KEY,
    security_code   TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('GS', 'TB')),
    issue_desc      TEXT,
    coupon_pct      NUMERIC(8, 4),
    issue_date      DATE NOT NULL,
    maturity_date   DATE NOT NULL,
    last_ip_date    DATE,
    next_ip_date    DATE,
    coupon_freq     SMALLINT,
    face_value      NUMERIC(18, 4) NOT NULL DEFAULT 100,
    status          TEXT,
    -- A T-bill has no coupon; a dated security must have one and a schedule.
    CHECK (instrument_type = 'TB' OR (coupon_pct IS NOT NULL AND coupon_freq IS NOT NULL)),
    CHECK (maturity_date > issue_date)
);

CREATE INDEX gsec_maturity_idx ON gsec (maturity_date);

CREATE TABLE desk (
    desk_id   TEXT PRIMARY KEY,
    desk_name TEXT NOT NULL,
    desk_type TEXT NOT NULL CHECK (desk_type IN ('FINANCING', 'REPO', 'ARB', 'TREASURY'))
);

-- Every FTP charge debits a desk and credits TREASURY, so the internal ledger
-- nets to zero by construction (docs/04 §6).
INSERT INTO desk (desk_id, desk_name, desk_type) VALUES
    ('EQ_FIN',    'Equity Financing',      'FINANCING'),
    ('REPO',      'G-Sec Repo',            'REPO'),
    ('DELTA_ONE', 'Delta One / Index Arb', 'ARB'),
    ('TREASURY',  'Group Treasury',        'TREASURY');
