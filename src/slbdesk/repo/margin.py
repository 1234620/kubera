"""Daily collateral revaluation and margin calls.

Mechanics: docs/02-domain-repo.md. Formulas and the haircut bands:
docs/07-analytics-spec.md §8.

Split of labour: SQL builds the (repo, date) grid and carries the last known
clean price forward; Python does the accrued interest and the valuation, because
accrued interest needs the coupon schedule from the bond math.
"""

from __future__ import annotations

import datetime as dt

import psycopg

from slbdesk import bonds

# G-Sec haircut by residual maturity. Shaped like CCIL's approach -- a
# security-specific VaR over a 5-day holding horizon, stepped up for illiquidity
# -- rather than plugged: a longer bond has more DV01, so the same yield move
# costs more, so it needs more over-collateralisation (docs/07 §8).
GSEC_HAIRCUT_BANDS = (
    (1.0, 0.0050),
    (5.0, 0.0150),
    (10.0, 0.0250),
    (float("inf"), 0.0400),
)

# Desk conventions, not regulation.
#
# The threshold is a PERCENTAGE of exposure, not a flat rupee figure. A flat
# amount cannot work across a book whose positions run from 5 crore to 50 crore:
# 1 lakh is 0.2% of the smallest and 0.02% of the largest, so it fires on any
# price tick and half of all days generate a call. A percentage threshold is also
# what a real CSA uses, for the same reason.
THRESHOLD_PCT_OF_EXPOSURE = 0.25
# Calls are rounded UP to this, so nobody moves an odd number of rupees.
MINIMUM_TRANSFER_INR = 1_000_000.0

# CCIL's TREPS rule: a borrowing-limit shortfall must be met by 09:00 the next
# business day (docs/02 §3).
MARGIN_CALL_DUE_HOUR = 9


def haircut_for_gsec(residual_years: float) -> float:
    """Haircut as a decimal, from the residual-maturity band."""
    for upper, haircut in GSEC_HAIRCUT_BANDS:
        if residual_years <= upper:
            return haircut
    raise AssertionError("unreachable: the last band is unbounded")


def next_business_day_9am(date: dt.date) -> dt.datetime:
    day = date + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(MARGIN_CALL_DUE_HOUR))


# One row per (open repo, trading day it was live), with the most recent clean
# price at or before that day. LATERAL because not every G-Sec trades every day:
# a repo still has to be revalued on a day its collateral did not print, and the
# honest thing to use is the last observed price.
#
# The price is carried forward from gsec_analytics_daily, which is keyed by ISIN,
# rather than from gsec_trade_daily, which is keyed by security code. That is not
# a convenience: a code like CG2028 is shared by several bonds with different
# coupons, so a lookup on the code alone revalues a repo with the WRONG BOND's
# price. An earlier version of this query did exactly that and produced
# collateral shortfalls of up to 7.5% -- far too large for daily variation margin,
# which is what gave it away. Reusing the analytics table also keeps the repo
# valuation and the bond analytics from drifting apart.
POSITIONS = """
WITH trading_day AS (
    SELECT DISTINCT trade_date FROM cash_quote_daily
)
SELECT
    d.trade_date            AS as_of_date,
    r.repo_id,
    r.desk_id,
    r.direction,
    r.isin,
    r.nominal,
    r.start_date,
    r.end_date,
    r.repo_rate_pct,
    r.purchase_price,
    g.coupon_pct,
    g.next_ip_date,
    g.maturity_date,
    g.coupon_freq,
    p.clean_price           AS mtm_clean_price,
    p.trade_date            AS price_date
FROM repo_trade AS r
JOIN gsec AS g
    ON g.isin = r.isin
JOIN trading_day AS d
    ON d.trade_date >= r.start_date
   AND d.trade_date <= r.end_date
JOIN LATERAL (
    SELECT a.trade_date, a.clean_price
    FROM gsec_analytics_daily AS a
    WHERE a.isin = r.isin
      AND a.trade_date <= d.trade_date
    ORDER BY a.trade_date DESC
    LIMIT 1
) AS p ON TRUE
"""

UPSERT_COLLATERAL = """
INSERT INTO collateral_position (
    as_of_date, repo_id, isin, nominal, mtm_clean_price, accrued_interest,
    dirty_value, haircut, post_haircut_value, haircut_source
) VALUES (
    %(as_of_date)s, %(repo_id)s, %(isin)s, %(nominal)s, %(mtm_clean_price)s,
    %(accrued_interest)s, %(dirty_value)s, %(haircut)s, %(post_haircut_value)s,
    %(haircut_source)s
)
ON CONFLICT (as_of_date, repo_id) DO UPDATE SET
    mtm_clean_price    = EXCLUDED.mtm_clean_price,
    accrued_interest   = EXCLUDED.accrued_interest,
    dirty_value        = EXCLUDED.dirty_value,
    haircut            = EXCLUDED.haircut,
    post_haircut_value = EXCLUDED.post_haircut_value,
    haircut_source     = EXCLUDED.haircut_source
"""

UPSERT_CALL = """
INSERT INTO margin_call (
    as_of_date, repo_id, desk_id, exposure, collateral_value,
    shortfall, call_amount, due_at, status
) VALUES (
    %(as_of_date)s, %(repo_id)s, %(desk_id)s, %(exposure)s, %(collateral_value)s,
    %(shortfall)s, %(call_amount)s, %(due_at)s, %(status)s
)
ON CONFLICT (as_of_date, repo_id) DO UPDATE SET
    exposure         = EXCLUDED.exposure,
    collateral_value = EXCLUDED.collateral_value,
    shortfall        = EXCLUDED.shortfall,
    call_amount      = EXCLUDED.call_amount,
    due_at           = EXCLUDED.due_at,
    status           = EXCLUDED.status
"""


def revalue(position: dict) -> dict:
    """Value one repo's collateral on one day.

    Collateral is valued DIRTY -- clean price plus accrued interest -- because
    that is what the bond is worth on the day. Valuing it clean systematically
    under-collateralises the cash lender by the accrued coupon, which five months
    into a 7.5% G-Sec coupon period is over three points (docs/02 §1).

    Both the price and the haircut move: a haircut widening on unchanged prices is
    still a margin call, which is why the haircut is recomputed here rather than
    carried from the trade.
    """
    as_of = position["as_of_date"]
    coupon = float(position["coupon_pct"])
    freq = position["coupon_freq"] or 2

    period_start, _ = bonds.coupon_schedule(
        as_of, position["next_ip_date"], position["maturity_date"], freq
    )
    accrued = bonds.accrued_interest(coupon, period_start, as_of)

    clean = float(position["mtm_clean_price"])
    nominal = float(position["nominal"])
    dirty_value = nominal / 100.0 * (clean + accrued)

    residual_years = (position["maturity_date"] - as_of).days / 365.0
    haircut = haircut_for_gsec(residual_years)

    return {
        "as_of_date": as_of,
        "repo_id": position["repo_id"],
        "isin": position["isin"],
        "nominal": nominal,
        "mtm_clean_price": clean,
        "accrued_interest": accrued,
        "dirty_value": dirty_value,
        "haircut": haircut,
        "post_haircut_value": dirty_value * (1 - haircut),
        "haircut_source": "TENOR_MODEL",
    }


def margin_call(position: dict, collateral: dict) -> dict | None:
    """The call, if the collateral no longer covers the accreted obligation.

    The obligation accretes at the repo rate on ACT/365:

        accreted = purchase_price x (1 + rate/100 x elapsed_days/365)

    net_exposure = accreted - post_haircut_collateral. Positive means the party
    holding the cash-lender side is under-collateralised, so on a REVERSE we make
    the call and on a REPO we receive one. Which way it goes is read from
    repo_trade.direction rather than duplicated here.
    """
    elapsed = (position["as_of_date"] - position["start_date"]).days
    accreted = float(position["purchase_price"]) * (
        1 + float(position["repo_rate_pct"]) / 100.0 * elapsed / 365.0
    )

    net_exposure = accreted - collateral["post_haircut_value"]
    if net_exposure <= accreted * THRESHOLD_PCT_OF_EXPOSURE / 100.0:
        return None

    return {
        "as_of_date": position["as_of_date"],
        "repo_id": position["repo_id"],
        "desk_id": position["desk_id"],
        "exposure": accreted,
        "collateral_value": collateral["post_haircut_value"],
        "shortfall": net_exposure,
        # Rounded UP to the minimum transfer amount: a call is never made for
        # less than the MTA, and never for less than the shortfall.
        "call_amount": -(-net_exposure // MINIMUM_TRANSFER_INR) * MINIMUM_TRANSFER_INR,
        "due_at": next_business_day_9am(position["as_of_date"]),
        "status": "OPEN",
    }


UNVALUED = """
SELECT COUNT(*) FROM repo_trade AS r
WHERE NOT EXISTS (
    SELECT 1 FROM gsec_analytics_daily AS a
    WHERE a.isin = r.isin AND a.trade_date <= r.end_date
)
"""


def refresh(conn: psycopg.Connection) -> tuple[int, int, int]:
    """Revalue every live repo on every trading day.

    Returns (valuations, calls, unvaluable repos). The last figure is reported
    rather than swallowed: a repo whose collateral has never printed a price
    cannot be marked, and silently dropping it would understate the book.
    """
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(POSITIONS)
        positions = cur.fetchall()

    unvaluable = conn.execute(UNVALUED).fetchone()[0]

    collateral = [revalue(position) for position in positions]
    calls = [
        call
        for position, valued in zip(positions, collateral, strict=True)
        if (call := margin_call(position, valued)) is not None
    ]

    with conn.cursor() as cur:
        cur.executemany(UPSERT_COLLATERAL, collateral)
        if calls:
            cur.executemany(UPSERT_CALL, calls)
    return len(collateral), len(calls), unvaluable
