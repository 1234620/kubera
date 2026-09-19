"""Price every G-Sec that traded and store its risk measures.

The one piece of analytics that is not SQL: solving for YTM is Newton-Raphson, so
it belongs in Python, and the bond math is pure functions that know nothing about
the database (docs/03-domain-bond-math.md).
"""

from __future__ import annotations

import datetime as dt

import psycopg

from slbdesk import bonds

# Joined on security code AND coupon: a code like CG2028 is shared by several
# bonds with different coupons, so joining on the code alone silently prices the
# wrong bond.
#
# The coupon is stripped with REGEXP_REPLACE rather than REPLACE(.., '%', '')
# deliberately: psycopg only unescapes '%%' when the query is given parameters,
# so a literal percent sign in a parameterless query is a trap that silently
# matches nothing. Keeping percent signs out of the SQL avoids the whole class.
TRADES = """
SELECT
    t.trade_date,
    t.security_code,
    t.settl_days,
    t.vwap_clean_price,
    t.weighted_ytm_pct,
    t.traded_value_inr,
    g.isin,
    g.coupon_pct,
    g.next_ip_date,
    g.maturity_date,
    g.coupon_freq
FROM gsec_trade_daily AS t
JOIN gsec AS g
    ON g.security_code = t.security_code
   AND g.instrument_type = 'GS'
   AND ABS(g.coupon_pct - REGEXP_REPLACE(t.issue_name, '[^0-9.]', '', 'g')::NUMERIC) < 1e-9
WHERE t.instrument_type = 'GS'
  AND t.weighted_ytm_pct > 0
  AND t.vwap_clean_price > 0
"""

UPSERT = """
INSERT INTO gsec_analytics_daily (
    trade_date, isin, security_code, coupon_pct, maturity_date, settlement_date,
    residual_years, clean_price, accrued_interest, dirty_price, ytm_pct,
    nse_ytm_pct, ytm_diff_bps, macaulay_duration, modified_duration, convexity,
    dv01_per_100_face, traded_value_inr, source_query
) VALUES (
    %(trade_date)s, %(isin)s, %(security_code)s, %(coupon_pct)s, %(maturity_date)s,
    %(settlement_date)s, %(residual_years)s, %(clean_price)s, %(accrued_interest)s,
    %(dirty_price)s, %(ytm_pct)s, %(nse_ytm_pct)s, %(ytm_diff_bps)s,
    %(macaulay_duration)s, %(modified_duration)s, %(convexity)s,
    %(dv01_per_100_face)s, %(traded_value_inr)s, %(source_query)s
)
ON CONFLICT (trade_date, isin) DO UPDATE SET
    clean_price       = EXCLUDED.clean_price,
    accrued_interest  = EXCLUDED.accrued_interest,
    dirty_price       = EXCLUDED.dirty_price,
    ytm_pct           = EXCLUDED.ytm_pct,
    nse_ytm_pct       = EXCLUDED.nse_ytm_pct,
    ytm_diff_bps      = EXCLUDED.ytm_diff_bps,
    macaulay_duration = EXCLUDED.macaulay_duration,
    modified_duration = EXCLUDED.modified_duration,
    convexity         = EXCLUDED.convexity,
    dv01_per_100_face = EXCLUDED.dv01_per_100_face,
    traded_value_inr  = EXCLUDED.traded_value_inr,
    source_query      = EXCLUDED.source_query
"""


def analyse(trade: dict) -> dict | None:
    """Price one traded G-Sec. None if it is past maturity or not priceable."""
    settlement = trade["trade_date"] + dt.timedelta(days=trade["settl_days"])
    if settlement >= trade["maturity_date"]:
        return None

    coupon = float(trade["coupon_pct"])
    freq = trade["coupon_freq"] or 2

    period_start, remaining = bonds.coupon_schedule(
        settlement, trade["next_ip_date"], trade["maturity_date"], freq
    )
    clean = float(trade["vwap_clean_price"])
    accrued = bonds.accrued_interest(coupon, period_start, settlement)
    dirty = bonds.dirty_from_clean(clean, accrued)

    # The quoted yield follows market convention -- simple ACT/365 inside the
    # final coupon period -- so it is comparable to NSE's published figure.
    ytm = bonds.quoted_ytm(dirty, coupon, period_start, settlement, remaining, freq)
    # Risk is always measured off the compounded curve, so the derivatives are
    # consistent with the pricing function they differentiate.
    risk = bonds.risk_measures(
        bonds.solve_ytm(dirty, coupon, period_start, settlement, remaining, freq),
        coupon,
        period_start,
        settlement,
        remaining,
        freq,
    )

    nse = float(trade["weighted_ytm_pct"])
    return {
        "trade_date": trade["trade_date"],
        "isin": trade["isin"],
        "security_code": trade["security_code"],
        "coupon_pct": coupon,
        "maturity_date": trade["maturity_date"],
        "settlement_date": settlement,
        "residual_years": round((trade["maturity_date"] - settlement).days / 365.0, 4),
        "clean_price": clean,
        "accrued_interest": accrued,
        "dirty_price": dirty,
        "ytm_pct": ytm,
        "nse_ytm_pct": nse,
        "ytm_diff_bps": (ytm - nse) * 100,
        "macaulay_duration": risk["macaulay_duration"],
        "modified_duration": risk["modified_duration"],
        "convexity": risk["convexity"],
        "dv01_per_100_face": risk["dv01"],
        "traded_value_inr": trade["traded_value_inr"],
        "source_query": "analytics/gsec.py",
    }


def refresh(conn: psycopg.Connection) -> int:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(TRADES)
        trades = cur.fetchall()

    priced = [row for row in (analyse(trade) for trade in trades) if row]
    with conn.cursor() as cur:
        cur.executemany(UPSERT, priced)
    return len(priced)
