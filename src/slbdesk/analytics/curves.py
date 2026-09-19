"""Build and store the zero curves, implied borrow forwards and key-rate risk.

The maths is in `slbdesk.curves`, which knows nothing about the database. This is
the layer that feeds it real prints and writes the results down.

Method, and the comparison between the two curve constructions:
docs/13-curve-construction.md.
"""

from __future__ import annotations

import datetime as dt

import psycopg

from slbdesk import bonds
from slbdesk.curves import implied_borrow, keyrate, nss, zero

# Daily G-Sec prints reaching the WDM file are sparse -- four or five on a
# typical day, which is not a curve -- so the curve pools a trailing window and
# keeps the most recent observation per ISIN.
LOOKBACK_DAYS = 10

# NSS has four betas, so four observations identify it EXACTLY -- and a fit with
# zero degrees of freedom returns an RMSE of 0.00 that means nothing at all. That
# is what happened on the first date in the backfill, and validate_nss_fit.sql
# caught it. `nss.fit` keeps 4 as its hard floor because that is the algebraic
# requirement; this is the statistical one, and they are different things.
MIN_OBSERVATIONS_TO_FIT = 8

# Where the curve is published. Money-market through long bond, matching the
# tenors an Indian G-Sec book is actually exposed at.
PUBLISH_GRID = (0.08, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0)

# Observations to build one day's curve from.
#
# The two instrument types need DIFFERENT joins, and this is the subtle part.
# `issue_name` carries the COUPON for a G-Sec but the MATURITY, as DDMMYY, for a
# T-bill. A bill's security code is only its original tenor -- `91D`, `182D`,
# `364D` -- so the code identifies 13 to 48 different bills in the master and is
# useless on its own. Joining bills on code alone matched a 15-day bill to a
# price belonging to a much longer one and bootstrapped a 149% short rate, which
# is how the problem announced itself.
OBSERVATIONS = """
WITH recent AS (
    SELECT t.*
    FROM gsec_trade_daily AS t
    WHERE t.trade_date BETWEEN %(as_of)s - %(lookback)s AND %(as_of)s
      AND t.instrument_type IN ('GS', 'TB')
      AND t.vwap_clean_price > 0
      AND t.weighted_ytm_pct > 0
),
matched AS (
    -- G-Secs: code plus coupon, because a code like CG2028 is shared by several
    -- bonds with different coupons.
    SELECT w.*, g.isin, g.coupon_pct, g.maturity_date, g.next_ip_date, g.coupon_freq
    FROM recent AS w
    JOIN gsec AS g
        ON g.instrument_type = 'GS'
       AND g.security_code = w.security_code
       AND ABS(
           g.coupon_pct - REGEXP_REPLACE(w.issue_name, '[^0-9.]', '', 'g')::NUMERIC
       ) < 1e-9
    WHERE w.instrument_type = 'GS'

    UNION ALL

    -- T-bills: the maturity parsed out of issue_name.
    SELECT w.*, g.isin, NULL::NUMERIC, g.maturity_date, NULL::DATE, NULL::SMALLINT
    FROM recent AS w
    JOIN gsec AS g
        ON g.instrument_type = 'TB'
       AND g.maturity_date = TO_DATE(w.issue_name, 'DDMMYY')
    WHERE w.instrument_type = 'TB'
      AND w.issue_name ~ '^[0-9]{6}$'
)
SELECT DISTINCT ON (isin)
    instrument_type, security_code, isin, coupon_pct, maturity_date,
    next_ip_date, coupon_freq, vwap_clean_price, weighted_ytm_pct,
    trade_date, settl_days
FROM matched
ORDER BY isin, trade_date DESC
"""

TRADING_DAYS = """
SELECT DISTINCT trade_date FROM gsec_trade_daily
WHERE instrument_type IN ('GS', 'TB') ORDER BY trade_date
"""

FEE_CURVE = """
SELECT trade_date, symbol, contract_set, tenor_days, fee_annualised_pct
FROM slb_fee_basis
WHERE fee_annualised_pct > 0
ORDER BY trade_date, symbol, tenor_days
"""

COLLATERAL = """
SELECT c.as_of_date, c.isin, c.nominal, g.coupon_pct, g.maturity_date,
       g.next_ip_date, g.coupon_freq
FROM collateral_position AS c
JOIN gsec AS g ON g.isin = c.isin
WHERE g.instrument_type = 'GS'
"""


def _upsert(conn: psycopg.Connection, statement: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(statement, rows)
    return len(rows)


def observations_for(conn: psycopg.Connection, as_of: dt.date) -> list[dict]:
    """Priced, dated observations ready for either curve construction."""
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(OBSERVATIONS, {"as_of": as_of, "lookback": dt.timedelta(days=LOOKBACK_DAYS)})
        rows = cur.fetchall()

    out = []
    for row in rows:
        settlement = row["trade_date"] + dt.timedelta(days=row["settl_days"])
        if settlement >= row["maturity_date"]:
            continue

        tenor_years = (row["maturity_date"] - settlement).days / 365.0
        clean = float(row["vwap_clean_price"])

        if row["instrument_type"] == "TB":
            # A bill has no coupon, so clean is dirty and the yield is the
            # simple ACT/365 discount yield.
            dirty = clean
            ytm = bonds.tbill_yield(clean, settlement, row["maturity_date"])
            instrument = zero.bill_instrument(
                f"{row['security_code']}/{row['maturity_date']}", dirty, tenor_years
            )
        else:
            freq = row["coupon_freq"] or 2
            coupon = float(row["coupon_pct"])
            period_start, _ = bonds.coupon_schedule(
                settlement, row["next_ip_date"], row["maturity_date"], freq
            )
            dirty = clean + bonds.accrued_interest(coupon, period_start, settlement)
            ytm = float(row["weighted_ytm_pct"])
            instrument = zero.bond_instrument(
                row["security_code"], dirty, coupon, tenor_years, freq
            )

        out.append(
            {
                "as_of_date": as_of,
                "isin": row["isin"],
                "instrument_type": row["instrument_type"],
                "security_code": row["security_code"],
                "observed_on": row["trade_date"],
                "settlement_date": settlement,
                "tenor_years": tenor_years,
                "dirty_price": dirty,
                "ytm_pct": ytm,
                "nse_ytm_pct": float(row["weighted_ytm_pct"]),
                "instrument": instrument,
                "source_query": "analytics/curves.py",
            }
        )
    return out


UPSERT_OBSERVATION = """
INSERT INTO curve_observation (
    as_of_date, isin, instrument_type, security_code, observed_on,
    settlement_date, tenor_years, dirty_price, ytm_pct, nse_ytm_pct, source_query
) VALUES (
    %(as_of_date)s, %(isin)s, %(instrument_type)s, %(security_code)s,
    %(observed_on)s, %(settlement_date)s, %(tenor_years)s, %(dirty_price)s,
    %(ytm_pct)s, %(nse_ytm_pct)s, %(source_query)s
)
ON CONFLICT (as_of_date, isin) DO UPDATE SET
    observed_on     = EXCLUDED.observed_on,
    settlement_date = EXCLUDED.settlement_date,
    tenor_years     = EXCLUDED.tenor_years,
    dirty_price     = EXCLUDED.dirty_price,
    ytm_pct         = EXCLUDED.ytm_pct,
    nse_ytm_pct     = EXCLUDED.nse_ytm_pct
"""

UPSERT_CURVE = """
INSERT INTO zero_curve_point (
    as_of_date, method, tenor_years, discount_factor, zero_rate_pct,
    forward_rate_pct, is_extrapolated, source_query
) VALUES (
    %(as_of_date)s, %(method)s, %(tenor_years)s, %(discount_factor)s,
    %(zero_rate_pct)s, %(forward_rate_pct)s, %(is_extrapolated)s, %(source_query)s
)
ON CONFLICT (as_of_date, method, tenor_years) DO UPDATE SET
    discount_factor  = EXCLUDED.discount_factor,
    zero_rate_pct    = EXCLUDED.zero_rate_pct,
    forward_rate_pct = EXCLUDED.forward_rate_pct,
    is_extrapolated  = EXCLUDED.is_extrapolated
"""

UPSERT_NSS = """
INSERT INTO nss_fit (
    as_of_date, beta0, beta1, beta2, beta3, tau1, tau2,
    short_rate_pct, rmse_bps, observations, description
) VALUES (
    %(as_of_date)s, %(beta0)s, %(beta1)s, %(beta2)s, %(beta3)s, %(tau1)s,
    %(tau2)s, %(short_rate_pct)s, %(rmse_bps)s, %(observations)s, %(description)s
)
ON CONFLICT (as_of_date) DO UPDATE SET
    beta0 = EXCLUDED.beta0, beta1 = EXCLUDED.beta1,
    beta2 = EXCLUDED.beta2, beta3 = EXCLUDED.beta3,
    tau1 = EXCLUDED.tau1, tau2 = EXCLUDED.tau2,
    short_rate_pct = EXCLUDED.short_rate_pct,
    rmse_bps = EXCLUDED.rmse_bps,
    observations = EXCLUDED.observations,
    description = EXCLUDED.description
"""

UPSERT_FORWARD = """
INSERT INTO slb_implied_forward (
    trade_date, symbol, contract_set, near_tenor_days, far_tenor_days,
    near_fee_pct, far_fee_pct, implied_forward_pct, signal, source_query
) VALUES (
    %(trade_date)s, %(symbol)s, %(contract_set)s, %(near_tenor_days)s,
    %(far_tenor_days)s, %(near_fee_pct)s, %(far_fee_pct)s,
    %(implied_forward_pct)s, %(signal)s, %(source_query)s
)
ON CONFLICT (trade_date, symbol, contract_set, near_tenor_days, far_tenor_days)
DO UPDATE SET
    near_fee_pct        = EXCLUDED.near_fee_pct,
    far_fee_pct         = EXCLUDED.far_fee_pct,
    implied_forward_pct = EXCLUDED.implied_forward_pct,
    signal              = EXCLUDED.signal
"""

UPSERT_KEY_RATE = """
INSERT INTO key_rate_dv01_daily (
    as_of_date, isin, key_tenor_years, dv01_inr, source_query
) VALUES (%(as_of_date)s, %(isin)s, %(key_tenor_years)s, %(dv01_inr)s, %(source_query)s)
ON CONFLICT (as_of_date, isin, key_tenor_years) DO UPDATE SET
    dv01_inr = EXCLUDED.dv01_inr
"""


def refresh_curves(conn: psycopg.Connection) -> tuple[int, int, int]:
    """Bootstrap and fit a curve for every trading day.

    Returns (observations, curve points, NSS fits). Both methods are published
    for the same dates so they can be read against each other -- the bootstrap is
    exact where something traded, NSS is smooth and covers the gaps, and the
    comparison is the honest way to present either.
    """
    dates = [row[0] for row in conn.execute(TRADING_DAYS).fetchall()]

    observation_rows: list[dict] = []
    curve_rows: list[dict] = []
    fit_rows: list[dict] = []

    for as_of in dates:
        observations = observations_for(conn, as_of)
        if len(observations) < MIN_OBSERVATIONS_TO_FIT:
            continue

        observation_rows.extend(
            {key: value for key, value in row.items() if key != "instrument"}
            for row in observations
        )

        curve = zero.bootstrap([row["instrument"] for row in observations])
        longest_node = curve.nodes[-1][0] if curve.nodes else 0.0
        for tenor in PUBLISH_GRID:
            if not curve.nodes:
                break
            curve_rows.append(
                {
                    "as_of_date": as_of,
                    "method": "BOOTSTRAP",
                    "tenor_years": tenor,
                    "discount_factor": curve.discount_factor(tenor),
                    "zero_rate_pct": curve.zero_rate(tenor),
                    "forward_rate_pct": (
                        curve.forward_rate(tenor / 2, tenor) if tenor > 0.08 else None
                    ),
                    # The bootstrap holds its last zero rate flat beyond its
                    # final node rather than extending a slope, so anything past
                    # it is an extrapolation and says so.
                    "is_extrapolated": tenor > longest_node,
                    "source_query": "analytics/curves.py",
                }
            )

        fitted = nss.fit([(row["tenor_years"], row["ytm_pct"]) for row in observations])
        longest_observed = max(row["tenor_years"] for row in observations)
        for tenor in PUBLISH_GRID:
            curve_rows.append(
                {
                    "as_of_date": as_of,
                    "method": "NSS",
                    "tenor_years": tenor,
                    "discount_factor": fitted.discount_factor(tenor),
                    "zero_rate_pct": fitted.zero_rate(tenor),
                    "forward_rate_pct": (
                        fitted.forward_rate(tenor / 2, tenor) if tenor > 0.08 else None
                    ),
                    "is_extrapolated": tenor > longest_observed,
                    "source_query": "analytics/curves.py",
                }
            )

        fit_rows.append(
            {
                "as_of_date": as_of,
                "beta0": fitted.beta0,
                "beta1": fitted.beta1,
                "beta2": fitted.beta2,
                "beta3": fitted.beta3,
                "tau1": fitted.tau1,
                "tau2": fitted.tau2,
                "short_rate_pct": fitted.short_rate,
                "rmse_bps": fitted.rmse_bps,
                "observations": fitted.observations,
                "description": fitted.describe(),
            }
        )

    return (
        _upsert(conn, UPSERT_OBSERVATION, observation_rows),
        _upsert(conn, UPSERT_CURVE, curve_rows),
        _upsert(conn, UPSERT_NSS, fit_rows),
    )


def refresh_implied_forwards(conn: psycopg.Connection) -> int:
    """Read every security's fee curve as a forward curve."""
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(FEE_CURVE)
        quotes = cur.fetchall()

    by_date: dict[dt.date, list[dict]] = {}
    for quote in quotes:
        by_date.setdefault(quote["trade_date"], []).append(quote)

    rows = []
    for trade_date, day_quotes in by_date.items():
        for forward in implied_borrow.curve_forwards(day_quotes):
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": forward.symbol,
                    "contract_set": forward.contract_set,
                    "near_tenor_days": forward.near_tenor_days,
                    "far_tenor_days": forward.far_tenor_days,
                    "near_fee_pct": forward.near_fee_pct,
                    "far_fee_pct": forward.far_fee_pct,
                    "implied_forward_pct": forward.implied_forward_pct,
                    "signal": forward.signal,
                    "source_query": "analytics/curves.py",
                }
            )
    return _upsert(conn, UPSERT_FORWARD, rows)


def refresh_key_rates(conn: psycopg.Connection) -> int:
    """Bucket the repo collateral's curve risk by key tenor.

    Priced off the NSS curve rather than the bootstrap, because key-rate shocks
    need a rate at every cash-flow date and the bootstrap is silent between its
    nodes. Buckets are additive across the portfolio, so a desk-level curve
    exposure is a plain sum.
    """
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(COLLATERAL)
        positions = cur.fetchall()

    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT as_of_date, beta0, beta1, beta2, beta3, tau1, tau2, rmse_bps,"
            " observations FROM nss_fit"
        )
        fits = {
            row["as_of_date"]: nss.NSSFit(
                beta0=float(row["beta0"]),
                beta1=float(row["beta1"]),
                beta2=float(row["beta2"]),
                beta3=float(row["beta3"]),
                tau1=float(row["tau1"]),
                tau2=float(row["tau2"]),
                rmse_bps=float(row["rmse_bps"]),
                observations=row["observations"],
            )
            for row in cur.fetchall()
        }

    rows = []
    for position in positions:
        fitted = fits.get(position["as_of_date"])
        if fitted is None:
            continue

        as_of = position["as_of_date"]
        maturity = position["maturity_date"]
        if maturity <= as_of:
            continue

        freq = position["coupon_freq"] or 2
        coupon = float(position["coupon_pct"])
        years = (maturity - as_of).days / 365.0
        scale = float(position["nominal"]) / 100.0

        flows = zero.bond_instrument("x", 0.0, coupon, years, freq)["cash_flows"]
        buckets = keyrate.key_rate_dv01(flows, fitted.zero_rate)

        for key_tenor, dv01 in buckets.items():
            if abs(dv01) < 1e-12:
                continue
            rows.append(
                {
                    "as_of_date": as_of,
                    "isin": position["isin"],
                    "key_tenor_years": key_tenor,
                    "dv01_inr": dv01 * scale,
                    "source_query": "analytics/curves.py",
                }
            )
    return _upsert(conn, UPSERT_KEY_RATE, rows)


def refresh(conn: psycopg.Connection) -> dict[str, int]:
    observations, points, fits = refresh_curves(conn)
    return {
        "curve observations": observations,
        "zero curve points": points,
        "nss fits": fits,
        "slb implied forwards": refresh_implied_forwards(conn),
        "key rate dv01": refresh_key_rates(conn),
    }
