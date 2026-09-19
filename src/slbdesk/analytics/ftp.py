"""Funds Transfer Pricing: the internal price of the balance sheet a desk uses.

Methodology and the reasoning behind each choice: docs/04-domain-ftp.md.

For a securities financing desk this is not an overhead allocation -- funding IS
the product, so the FTP rate is cost of goods sold. The desk's economics are the
difference between the external fee it earns and the internal rate it is charged.

    FTP rate = base curve at the position's own tenor   (matched maturity)
             + term liquidity premium at that tenor
             + contingent liquidity charge

Matched maturity rather than a single pool, because a single rate charges an
overnight and a twelve-month position the same and so makes maturity
transformation look free (ADR 0005).
"""

from __future__ import annotations

import bisect
import datetime as dt

import psycopg

from slbdesk import bonds

# The bank's marginal cost of funds over the sovereign curve. A documented
# constant rather than a fitted one: we have no observable bank issuance spread
# in the public files, and a plugged number that says so is better than one
# dressed up as a measurement.
ISSUER_SPREAD_BPS = 45.0

# Term liquidity premium: the extra cost of borrowing long instead of rolling
# short. This is the component most commonly omitted from an FTP model and the
# most expensive to omit -- it is what makes long-dated lending correctly look
# expensive, and regulators pushed banks on it specifically after 2008.
TLP_CURVE = (
    (1, 0.0),
    (30, 8.0),
    (91, 18.0),
    (182, 28.0),
    (365, 40.0),
)

# Charged on a position in a series where recall is DISABLED: it cannot be
# unwound early, so it consumes a liquidity buffer that a recallable position
# does not. The driver is the NSE eligibility file, not an assumption.
CONTINGENT_LIQUIDITY_BPS = 15.0

# Treasury funds the book short and lends it long; this is the tenor it actually
# funds at. The gap between the FTP rate charged and the curve here is Treasury's
# maturity-transformation return, which is exactly what treasury_spread measures.
TREASURY_FUNDING_TENOR_DAYS = 30

# The curve is published on a standard grid so it is queryable and the API does
# not have to interpolate. These are the money-market tenors an SLB book actually
# needs -- positions run from 4 to 356 days.
CURVE_GRID = (1, 7, 14, 30, 60, 91, 182, 273, 365)

# Daily NDS-OM prints reaching the WDM file are sparse -- four bonds on a typical
# day, which is not a curve. Nodes are pooled over a trailing window so the shape
# is observed rather than invented.
CURVE_LOOKBACK_DAYS = 10

RECONCILE_TOLERANCE_INR = 1.0


# --- the curve ------------------------------------------------------------

# Sovereign observations to build the curve from. G-Secs come with a solved YTM
# already; T-bills are discount instruments and are yielded separately.
CURVE_NODES = """
SELECT
    a.residual_years,
    a.ytm_pct
FROM gsec_analytics_daily AS a
WHERE a.trade_date BETWEEN %(as_of)s - %(lookback)s AND %(as_of)s
  AND a.ytm_pct > 0
ORDER BY a.residual_years
"""

TRADING_DAYS = "SELECT DISTINCT trade_date FROM cash_quote_daily ORDER BY trade_date"


def interpolate_rate(nodes: list[tuple[float, float]], tenor_days: int) -> tuple[float, bool]:
    """Rate at a tenor, and whether it had to be extrapolated.

    Interpolation is LOG-LINEAR ON DISCOUNT FACTORS, not linear on yields. With
    df = exp(-r*t), linear in log(df) means linear in r*t, so the curve is
    arbitrage-free between nodes -- a straight line through yields is not.

    Beyond the observed ends the rate is held flat and the caller is told, so the
    row can be flagged is_estimated. Extending a slope past the last observation
    invents a curve shape nobody measured.
    """
    if not nodes:
        raise ValueError("no sovereign observations to build a curve from")

    tenors = [days for days, _ in nodes]
    if tenor_days <= tenors[0]:
        return nodes[0][1], tenor_days < tenors[0]
    if tenor_days >= tenors[-1]:
        return nodes[-1][1], tenor_days > tenors[-1]

    index = bisect.bisect_left(tenors, tenor_days)
    left_days, left_rate = nodes[index - 1]
    right_days, right_rate = nodes[index]

    # Interpolate r*t, then divide back out.
    left_area = left_rate * left_days
    right_area = right_rate * right_days
    weight = (tenor_days - left_days) / (right_days - left_days)
    return (left_area + weight * (right_area - left_area)) / tenor_days, False


def term_liquidity_bps(tenor_days: int) -> float:
    """TLP at a tenor, linearly interpolated between the declared nodes."""
    nodes = list(TLP_CURVE)
    if tenor_days <= nodes[0][0]:
        return nodes[0][1]
    if tenor_days >= nodes[-1][0]:
        return nodes[-1][1]

    for (left_days, left_bps), (right_days, right_bps) in zip(nodes, nodes[1:], strict=False):
        if left_days <= tenor_days <= right_days:
            weight = (tenor_days - left_days) / (right_days - left_days)
            return left_bps + weight * (right_bps - left_bps)
    raise AssertionError("unreachable: the grid is bracketed above")


def observed_nodes(conn: psycopg.Connection, as_of: dt.date) -> list[tuple[float, float]]:
    """Sovereign (tenor_days, yield) nodes, averaged per tenor and sorted."""
    rows = conn.execute(
        CURVE_NODES, {"as_of": as_of, "lookback": dt.timedelta(days=CURVE_LOOKBACK_DAYS)}
    ).fetchall()

    buckets: dict[int, list[float]] = {}
    for residual_years, ytm in rows:
        days = max(int(round(float(residual_years) * 365)), 1)
        buckets.setdefault(days, []).append(float(ytm))

    return sorted((days, sum(ytms) / len(ytms)) for days, ytms in buckets.items())


def build_curve(conn: psycopg.Connection) -> int:
    """Publish the funding curve on the standard grid, for every trading day."""
    dates = [row[0] for row in conn.execute(TRADING_DAYS).fetchall()]

    rows = []
    for as_of in dates:
        nodes = observed_nodes(conn, as_of)
        if not nodes:
            continue

        for tenor_days in CURVE_GRID:
            sovereign, extrapolated = interpolate_rate(nodes, tenor_days)
            rows.append(
                {
                    "as_of_date": as_of,
                    "tenor_days": tenor_days,
                    "base_rate_pct": sovereign + ISSUER_SPREAD_BPS / 100.0,
                    "term_liquidity_premium_bps": term_liquidity_bps(tenor_days),
                    # Every node is interpolated onto the grid from observations
                    # at other tenors, so none of them is a direct print.
                    "source": "INTERPOLATED",
                    "is_estimated": extrapolated,
                }
            )

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO funding_curve_point (
                as_of_date, tenor_days, base_rate_pct, term_liquidity_premium_bps,
                source, is_estimated
            ) VALUES (
                %(as_of_date)s, %(tenor_days)s, %(base_rate_pct)s,
                %(term_liquidity_premium_bps)s, %(source)s, %(is_estimated)s
            )
            ON CONFLICT (as_of_date, tenor_days) DO UPDATE SET
                base_rate_pct              = EXCLUDED.base_rate_pct,
                term_liquidity_premium_bps = EXCLUDED.term_liquidity_premium_bps,
                source                     = EXCLUDED.source,
                is_estimated               = EXCLUDED.is_estimated
            """,
            rows,
        )
    return len(rows)


def curve_lookup(conn: psycopg.Connection) -> dict[dt.date, list[tuple[int, float, bool]]]:
    """The published curve, as {date: [(tenor_days, base_rate_pct, is_estimated)]}."""
    rows = conn.execute(
        """
        SELECT as_of_date, tenor_days, base_rate_pct, is_estimated
        FROM funding_curve_point
        ORDER BY as_of_date, tenor_days
        """
    ).fetchall()

    out: dict[dt.date, list[tuple[int, float, bool]]] = {}
    for as_of, tenor_days, rate, estimated in rows:
        out.setdefault(as_of, []).append((tenor_days, float(rate), estimated))
    return out


def base_rate(curve: list[tuple[int, float, bool]], tenor_days: int) -> tuple[float, bool]:
    nodes = [(days, rate) for days, rate, _ in curve]
    rate, extrapolated = interpolate_rate(nodes, tenor_days)
    estimated = extrapolated or any(flag for days, _, flag in curve if abs(days - tenor_days) <= 1)
    return rate, estimated


# --- fee accrual ----------------------------------------------------------

# Every position live on a trading day, with the underlying close that gives it a
# notional. The fee accrues from first-leg settlement, so the position is not live
# before then.
POSITIONS = """
WITH trading_day AS (
    SELECT DISTINCT trade_date FROM cash_quote_daily
)
SELECT
    d.trade_date AS as_of_date,
    t.trade_id,
    t.desk_id,
    t.symbol,
    t.series_code,
    t.side,
    t.quantity,
    t.fee_per_share,
    (t.reverse_leg_date - t.first_leg_settle_date) AS tenor_days,
    c.close_price AS underlying_close,
    COALESCE(e.recall_eligible, FALSE) AS recall_eligible
FROM slb_trade AS t
JOIN trading_day AS d
    ON d.trade_date >= t.first_leg_settle_date
   AND d.trade_date < t.reverse_leg_date
JOIN cash_quote_daily AS c
    ON c.trade_date = d.trade_date
   AND c.symbol = t.symbol
   AND c.series = 'EQ'
LEFT JOIN slb_eligibility AS e
    ON e.as_of_date = d.trade_date
   AND e.symbol = t.symbol
   AND e.series_code = t.series_code
"""

UPSERT_POSITION_PNL = """
INSERT INTO slb_position_pnl_daily (
    as_of_date, trade_id, desk_id, symbol, series_code, side, quantity,
    underlying_close, notional_inr, tenor_days, fee_per_share,
    fee_annualised_pct, fee_pnl_inr, source_query
) VALUES (
    %(as_of_date)s, %(trade_id)s, %(desk_id)s, %(symbol)s, %(series_code)s,
    %(side)s, %(quantity)s, %(underlying_close)s, %(notional_inr)s,
    %(tenor_days)s, %(fee_per_share)s, %(fee_annualised_pct)s, %(fee_pnl_inr)s,
    %(source_query)s
)
ON CONFLICT (as_of_date, trade_id) DO UPDATE SET
    underlying_close   = EXCLUDED.underlying_close,
    notional_inr       = EXCLUDED.notional_inr,
    fee_annualised_pct = EXCLUDED.fee_annualised_pct,
    fee_pnl_inr        = EXCLUDED.fee_pnl_inr,
    source_query       = EXCLUDED.source_query
"""

UPSERT_FTP = """
INSERT INTO ftp_charge_daily (
    as_of_date, desk_id, symbol, series_code, net_quantity, net_notional_inr,
    tenor_days, base_rate_pct, term_liquidity_bps, contingent_liq_bps,
    ftp_rate_pct, ftp_charge_inr, curve_is_estimated, source_query
) VALUES (
    %(as_of_date)s, %(desk_id)s, %(symbol)s, %(series_code)s, %(net_quantity)s,
    %(net_notional_inr)s, %(tenor_days)s, %(base_rate_pct)s,
    %(term_liquidity_bps)s, %(contingent_liq_bps)s, %(ftp_rate_pct)s,
    %(ftp_charge_inr)s, %(curve_is_estimated)s, %(source_query)s
)
ON CONFLICT (as_of_date, desk_id, symbol, series_code) DO UPDATE SET
    net_quantity       = EXCLUDED.net_quantity,
    net_notional_inr   = EXCLUDED.net_notional_inr,
    base_rate_pct      = EXCLUDED.base_rate_pct,
    term_liquidity_bps = EXCLUDED.term_liquidity_bps,
    contingent_liq_bps = EXCLUDED.contingent_liq_bps,
    ftp_rate_pct       = EXCLUDED.ftp_rate_pct,
    ftp_charge_inr     = EXCLUDED.ftp_charge_inr,
    curve_is_estimated = EXCLUDED.curve_is_estimated,
    source_query       = EXCLUDED.source_query
"""


def position_pnl(position: dict) -> dict:
    """Daily fee accrual for one position.

    Straight-line over the contract, ACT/365, from first-leg settlement. Positive
    is a gain to our book, so a BORROW accrues a negative number.
    """
    quantity = position["quantity"]
    fee = float(position["fee_per_share"])
    price = float(position["underlying_close"])
    tenor = position["tenor_days"]

    sign = 1 if position["side"] == "LEND" else -1
    return {
        "as_of_date": position["as_of_date"],
        "trade_id": position["trade_id"],
        "desk_id": position["desk_id"],
        "symbol": position["symbol"],
        "series_code": position["series_code"],
        "side": position["side"],
        "quantity": quantity,
        "underlying_close": price,
        "notional_inr": quantity * price,
        "tenor_days": tenor,
        "fee_per_share": fee,
        "fee_annualised_pct": fee / price * (365.0 / tenor) * 100,
        "fee_pnl_inr": sign * quantity * fee / tenor,
        "source_query": "analytics/ftp.py",
    }


def ftp_charges(positions: list[dict], curves: dict) -> list[dict]:
    """FTP on the NET position per (date, desk, symbol, series).

    Netting is the point: a matched book consumes almost no balance sheet and
    should earn the fee spread nearly cleanly, while a directional book pays for
    what it uses. Charging both legs of a matched pair would make FTP a flat tax
    on turnover rather than a price for the resource consumed.
    """
    grouped: dict[tuple, dict] = {}
    for position in positions:
        key = (
            position["as_of_date"],
            position["desk_id"],
            position["symbol"],
            position["series_code"],
        )
        sign = -1 if position["side"] == "LEND" else 1  # net borrower is positive
        bucket = grouped.setdefault(
            key,
            {
                "net_quantity": 0,
                "price": float(position["underlying_close"]),
                "tenor_days": position["tenor_days"],
                "recall_eligible": True,
            },
        )
        bucket["net_quantity"] += sign * position["quantity"]
        bucket["tenor_days"] = max(bucket["tenor_days"], position["tenor_days"])
        if not position["recall_eligible"]:
            bucket["recall_eligible"] = False

    out = []
    for (as_of, desk_id, symbol, series_code), bucket in grouped.items():
        curve = curves.get(as_of)
        if curve is None or bucket["net_quantity"] == 0:
            continue

        tenor = bucket["tenor_days"]
        base, estimated = base_rate(curve, tenor)
        tlp = term_liquidity_bps(tenor)
        contingent = 0.0 if bucket["recall_eligible"] else CONTINGENT_LIQUIDITY_BPS

        ftp_rate = base + tlp / 100.0 + contingent / 100.0
        net_notional = bucket["net_quantity"] * bucket["price"]

        out.append(
            {
                "as_of_date": as_of,
                "desk_id": desk_id,
                "symbol": symbol,
                "series_code": series_code,
                "net_quantity": bucket["net_quantity"],
                "net_notional_inr": net_notional,
                "tenor_days": tenor,
                "base_rate_pct": base,
                "term_liquidity_bps": tlp,
                "contingent_liq_bps": contingent,
                "ftp_rate_pct": ftp_rate,
                # A net borrower consumes balance sheet and is charged; a net
                # lender has released it and is credited.
                "ftp_charge_inr": -net_notional * ftp_rate / 100.0 / 365.0,
                "curve_is_estimated": estimated,
                "source_query": "analytics/ftp.py",
            }
        )

    # Treasury is the other side of every charge. Mirroring them is what makes
    # the internal ledger net to zero -- and that is the property the
    # reconciliation actually tests, rather than a restatement of itself.
    mirrors = [
        row
        | {
            "desk_id": "TREASURY",
            "net_quantity": -row["net_quantity"],
            "net_notional_inr": -row["net_notional_inr"],
            "ftp_charge_inr": -row["ftp_charge_inr"],
        }
        for row in out
        if row["desk_id"] != "TREASURY"
    ]
    return out + mirrors


# The desk statement, and the decomposition that has to add up.
#
#   desk_spread     = fee earned externally + the FTP charged (negative when the
#                     desk consumes balance sheet)
#   treasury_spread = the FTP Treasury was credited + what it actually paid for
#                     the funding -- its return on maturity transformation
#
# Book-wide, desk_spread + treasury_spread collapses to fee income less
# Treasury's real cost of funds, because the internal transfer cancels. That
# cancellation is the non-trivial part, and db/queries/validate_ftp_*.sql assert
# both halves of it.
DESK_PNL = """
INSERT INTO desk_pnl_daily (
    as_of_date, desk_id, positions, gross_notional_inr, net_notional_inr,
    fee_pnl_inr, ftp_charge_inr, net_spread_inr, nim_inr, desk_spread_inr,
    treasury_spread_inr, actual_cost_of_funds_inr, reconciles, source_query
)
WITH fees AS (
    SELECT
        as_of_date,
        desk_id,
        COUNT(*)          AS positions,
        SUM(notional_inr) AS gross_notional_inr,
        SUM(fee_pnl_inr)  AS fee_pnl_inr
    FROM slb_position_pnl_daily
    GROUP BY as_of_date, desk_id
),
charges AS (
    SELECT
        f.as_of_date,
        f.desk_id,
        SUM(f.net_notional_inr) AS net_notional_inr,
        SUM(f.ftp_charge_inr)   AS ftp_charge_inr,
        -- What Treasury really pays: its own funding tenor, not the position's.
        -- Signed the same way as an FTP charge, so a consumer of funding shows a
        -- cost and Treasury's mirror shows the offsetting receipt.
        SUM(
            -f.net_notional_inr * COALESCE(short.base_rate_pct, f.base_rate_pct)
            / 100.0 / 365.0
        ) AS cost_at_treasury_tenor
    FROM ftp_charge_daily AS f
    LEFT JOIN funding_curve_point AS short
        ON short.as_of_date = f.as_of_date
       AND short.tenor_days = %(treasury_tenor)s
    GROUP BY f.as_of_date, f.desk_id
),
desks AS (
    SELECT
        COALESCE(fees.as_of_date, charges.as_of_date) AS as_of_date,
        COALESCE(fees.desk_id, charges.desk_id)       AS desk_id,
        COALESCE(fees.positions, 0)                   AS positions,
        COALESCE(fees.gross_notional_inr, 0)          AS gross_notional_inr,
        COALESCE(charges.net_notional_inr, 0)         AS net_notional_inr,
        COALESCE(fees.fee_pnl_inr, 0)                 AS fee_pnl_inr,
        COALESCE(charges.ftp_charge_inr, 0)           AS ftp_charge_inr,
        COALESCE(charges.cost_at_treasury_tenor, 0)   AS cost_at_treasury_tenor
    FROM fees
    FULL OUTER JOIN charges
        ON charges.as_of_date = fees.as_of_date
       AND charges.desk_id = fees.desk_id
),
split AS (
    SELECT
        d.*,
        -- Treasury is not a business desk, so it has NO desk spread: its entire
        -- result is the maturity transformation. Giving it one would count its
        -- FTP credit twice -- once here and once in treasury_spread -- and the
        -- decomposition would overstate the book by exactly that amount. That is
        -- precisely what validate_ftp_reconciliation.sql caught.
        CASE
            WHEN d.desk_id = 'TREASURY' THEN 0
            ELSE d.fee_pnl_inr + d.ftp_charge_inr
        END AS desk_spread_inr,
        -- And only Treasury runs the transformation; a business desk took no
        -- such position, so its treasury spread is zero.
        CASE
            WHEN d.desk_id = 'TREASURY'
            THEN d.ftp_charge_inr + d.cost_at_treasury_tenor
            ELSE 0
        END AS treasury_spread_inr,
        CASE WHEN d.desk_id = 'TREASURY' THEN d.cost_at_treasury_tenor ELSE 0 END
            AS actual_cost_of_funds_inr
    FROM desks AS d
),
-- The book-level identity, evaluated per date. It is the same answer for every
-- desk on a date, which is deliberate: the API can report whether the day's
-- numbers add up alongside any desk's row.
checked AS (
    SELECT
        s.*,
        SUM(s.desk_spread_inr + s.treasury_spread_inr) OVER (PARTITION BY s.as_of_date)
            AS decomposition_total,
        SUM(s.fee_pnl_inr + s.actual_cost_of_funds_inr) OVER (PARTITION BY s.as_of_date)
            AS book_nim
    FROM split AS s
)
SELECT
    c.as_of_date,
    c.desk_id,
    c.positions,
    c.gross_notional_inr,
    c.net_notional_inr,
    c.fee_pnl_inr,
    c.ftp_charge_inr,
    (c.desk_spread_inr + c.treasury_spread_inr) AS net_spread_inr,
    (c.fee_pnl_inr + c.actual_cost_of_funds_inr) AS nim_inr,
    c.desk_spread_inr,
    c.treasury_spread_inr,
    c.actual_cost_of_funds_inr,
    ABS(c.decomposition_total - c.book_nim) <= %(tolerance)s AS reconciles,
    'analytics/ftp.py' AS source_query
FROM checked AS c
ON CONFLICT (as_of_date, desk_id) DO UPDATE SET
    positions                = EXCLUDED.positions,
    gross_notional_inr       = EXCLUDED.gross_notional_inr,
    net_notional_inr         = EXCLUDED.net_notional_inr,
    fee_pnl_inr              = EXCLUDED.fee_pnl_inr,
    ftp_charge_inr           = EXCLUDED.ftp_charge_inr,
    net_spread_inr           = EXCLUDED.net_spread_inr,
    nim_inr                  = EXCLUDED.nim_inr,
    desk_spread_inr          = EXCLUDED.desk_spread_inr,
    treasury_spread_inr      = EXCLUDED.treasury_spread_inr,
    actual_cost_of_funds_inr = EXCLUDED.actual_cost_of_funds_inr,
    reconciles               = EXCLUDED.reconciles,
    source_query             = EXCLUDED.source_query
"""


def refresh(conn: psycopg.Connection) -> tuple[int, int, int, int]:
    """Build the curve, accrue fees, charge FTP, write desk P&L.

    Returns (curve points, position rows, ftp rows, desk rows).
    """
    curve_points = build_curve(conn)
    curves = curve_lookup(conn)

    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(POSITIONS)
        positions = cur.fetchall()

    pnl = [position_pnl(position) for position in positions]
    charges = ftp_charges(positions, curves)

    with conn.cursor() as cur:
        cur.executemany(UPSERT_POSITION_PNL, pnl)
        if charges:
            cur.executemany(UPSERT_FTP, charges)
        cur.execute(
            DESK_PNL,
            {
                "treasury_tenor": TREASURY_FUNDING_TENOR_DAYS,
                "tolerance": RECONCILE_TOLERANCE_INR,
            },
        )
        desk_rows = cur.rowcount

    return curve_points, len(pnl), len(charges), desk_rows


__all__ = [
    "CONTINGENT_LIQUIDITY_BPS",
    "CURVE_GRID",
    "ISSUER_SPREAD_BPS",
    "RECONCILE_TOLERANCE_INR",
    "TLP_CURVE",
    "TREASURY_FUNDING_TENOR_DAYS",
    "base_rate",
    "bonds",
    "build_curve",
    "ftp_charges",
    "interpolate_rate",
    "position_pnl",
    "refresh",
    "term_liquidity_bps",
]
