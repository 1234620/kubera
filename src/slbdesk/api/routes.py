"""The endpoints. Spec and conventions: docs/08-api-spec.md.

Every query is a bound-parameter SELECT against an already-aggregated table, so
each handler is a few lines. SQL is never built by string formatting; parameters
are always `%()s`.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from slbdesk import bonds, queries

router = APIRouter()

Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0)]


def fetch(sql: str, params: dict | None = None) -> list[dict]:
    from slbdesk.api import main

    if main.pool is None:  # pragma: no cover - only before lifespan startup
        raise HTTPException(503, "database pool not started")
    with main.pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def one(sql: str, params: dict | None = None, missing: str = "not found") -> dict:
    rows = fetch(sql, params)
    if not rows:
        raise HTTPException(404, missing)
    return rows[0]


def named(name: str, params: dict | None = None) -> list[dict]:
    """Run a query from db/queries, so the SQL stays a reviewable artefact."""
    return fetch(queries.sql(name), params)


# --- meta -----------------------------------------------------------------


@router.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    row = one("SELECT MAX(trade_date) AS latest FROM slb_quote_daily")
    return {"status": "ok", "db": "ok", "latest_ingest_date": row["latest"]}


@router.get("/data-coverage", tags=["meta"])
def data_coverage() -> list[dict]:
    """What you open first when a chart looks wrong."""
    return fetch("""
        SELECT 'slb_quote_daily' AS table_name, MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date, COUNT(*) AS rows, MAX(loaded_at) AS last_loaded
        FROM slb_quote_daily
        UNION ALL SELECT 'slb_open_position', MIN(trade_date), MAX(trade_date), COUNT(*),
               MAX(loaded_at) FROM slb_open_position
        UNION ALL SELECT 'slb_eligibility', MIN(as_of_date), MAX(as_of_date), COUNT(*),
               MAX(loaded_at) FROM slb_eligibility
        UNION ALL SELECT 'slb_var_margin', MIN(as_of_date), MAX(as_of_date), COUNT(*),
               MAX(loaded_at) FROM slb_var_margin
        UNION ALL SELECT 'cash_quote_daily', MIN(trade_date), MAX(trade_date), COUNT(*),
               MAX(loaded_at) FROM cash_quote_daily
        UNION ALL SELECT 'gsec_trade_daily', MIN(trade_date), MAX(trade_date), COUNT(*),
               MAX(loaded_at) FROM gsec_trade_daily
        ORDER BY table_name
    """)


# --- reference ------------------------------------------------------------


@router.get("/securities", tags=["reference"])
def securities(search: str | None = None, limit: Limit = 100) -> list[dict]:
    return fetch(
        """
        SELECT symbol, security_name, isin, first_seen, last_seen
        FROM security
        WHERE %(search)s::TEXT IS NULL OR symbol ILIKE '%%' || %(search)s || '%%'
        ORDER BY symbol
        LIMIT %(limit)s
        """,
        {"search": search, "limit": limit},
    )


@router.get("/series", tags=["reference"])
def series(as_of: dt.date | None = None) -> list[dict]:
    return fetch(
        """
        SELECT series_code, reverse_leg_date, contract_set, first_seen, last_seen
        FROM slb_series
        WHERE %(as_of)s::DATE IS NULL OR (%(as_of)s BETWEEN first_seen AND last_seen)
        ORDER BY reverse_leg_date, series_code
        """,
        {"as_of": as_of},
    )


@router.get("/desks", tags=["reference"])
def desks() -> list[dict]:
    return fetch("SELECT desk_id, desk_name, desk_type FROM desk ORDER BY desk_id")


# --- SLB market -----------------------------------------------------------


@router.get("/slb/quotes", tags=["slb"])
def quotes(
    symbol: str | None = None,
    series: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: Limit = 200,
) -> list[dict]:
    """Lending fees. Note `fee_*` are rupees per share FOR THE CONTRACT PERIOD."""
    return fetch(
        """
        SELECT trade_date, symbol, series_code, contract_set, reverse_leg_date,
               tenor_days, fee_per_share, vwaf, fee_annualised_pct,
               underlying_close, traded_qty, num_trades
        FROM slb_fee_basis
        WHERE (%(symbol)s::TEXT IS NULL OR symbol = %(symbol)s)
          AND (%(series)s::TEXT IS NULL OR series_code = %(series)s)
          AND (%(date_from)s::DATE IS NULL OR trade_date >= %(date_from)s)
          AND (%(date_to)s::DATE IS NULL OR trade_date <= %(date_to)s)
        ORDER BY trade_date DESC, symbol, tenor_days
        LIMIT %(limit)s
        """,
        {
            "symbol": symbol,
            "series": series,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
    )


@router.get("/slb/term-structure", tags=["slb"])
def term_structure(symbol: str, as_of: dt.date | None = None) -> list[dict]:
    """The fee curve by tenor. Upward-sloping means the market expects the borrow
    to stay tight; downward means a transient event with a resolution date."""
    resolved = as_of or one("SELECT MAX(trade_date) AS d FROM slb_quote_daily")["d"]
    return named("term_structure", {"symbol": symbol, "as_of": resolved})


@router.get("/slb/utilisation", tags=["slb"])
def utilisation(
    symbol: str | None = None, as_of: dt.date | None = None, limit: Limit = 200
) -> list[dict]:
    """`utilisation_pct` is ESTIMATED -- lendable supply is not published in
    India. `days_to_cover` needs no estimate and is the figure to trust."""
    return fetch(
        """
        SELECT trade_date, symbol, on_loan_qty, avg_volume_20d, avg_delivery_20d,
               lendable_qty_est, utilisation_pct, days_to_cover, on_loan_change_1d,
               utilisation_5d_avg, is_estimated
        FROM slb_utilisation_daily
        WHERE trade_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(trade_date) FROM slb_utilisation_daily))
          AND (%(symbol)s::TEXT IS NULL OR symbol = %(symbol)s)
        ORDER BY utilisation_pct DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"as_of": as_of, "symbol": symbol, "limit": limit},
    )


@router.get("/slb/specialness", tags=["slb"])
def specialness(
    as_of: dt.date | None = None,
    min_score: float = 0,
    classification: str | None = None,
    symbol: str | None = None,
    include_stale: bool = True,
    limit: Limit = 200,
) -> list[dict]:
    """Both components are returned alongside the blend: "87" is not an answer,
    "94th percentile at this tenor and 3.2 sigma above its own mean" is."""
    return fetch(
        """
        SELECT trade_date, symbol, series_code, contract_set, tenor_bucket, tenor_days,
               fee_per_share, fee_annualised_pct, gc_fee_annualised_pct,
               spread_to_gc_bps, xs_percentile, baseline_mean_pct, baseline_sd_pct,
               baseline_obs, own_z, own_z_cdf, specialness_score, classification,
               quote_date, days_since_last_trade, is_stale
        FROM slb_specialness_daily
        WHERE trade_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(trade_date) FROM slb_specialness_daily))
          AND specialness_score >= %(min_score)s
          AND (%(classification)s::TEXT IS NULL OR classification = %(classification)s)
          AND (%(symbol)s::TEXT IS NULL OR symbol = %(symbol)s)
          AND (%(include_stale)s::BOOLEAN OR NOT is_stale)
        ORDER BY specialness_score DESC
        LIMIT %(limit)s
        """,
        {
            "as_of": as_of,
            "min_score": min_score,
            "classification": classification,
            "symbol": symbol,
            "include_stale": include_stale,
            "limit": limit,
        },
    )


@router.get("/slb/gc-rate", tags=["slb"])
def gc_rate(date_from: dt.date | None = None, date_to: dt.date | None = None) -> list[dict]:
    """The GC benchmark: quantity-weighted median fee of the day's liquid cohort.
    Flagged `is_estimated` below 20 cohort members."""
    return fetch(
        """
        SELECT as_of_date, contract_set, gc_fee_per_share_median,
               gc_fee_annualised_pct, cohort_size, is_estimated
        FROM gc_rate_daily
        WHERE (%(date_from)s::DATE IS NULL OR as_of_date >= %(date_from)s)
          AND (%(date_to)s::DATE IS NULL OR as_of_date <= %(date_to)s)
        ORDER BY as_of_date DESC, contract_set
        """,
        {"date_from": date_from, "date_to": date_to},
    )


# --- the book -------------------------------------------------------------


@router.get("/book/kpis", tags=["book"])
def book_kpis(as_of: dt.date | None = None) -> dict:
    return one(queries.sql("book_kpis"), {"as_of": as_of}, "no book data")


@router.get("/book/history", tags=["book"])
def book_history() -> list[dict]:
    """The daily series behind the KPI sparklines.

    Exists so the browser draws a series it is handed rather than aggregating
    positions itself: the frontend formats and draws, it does not compute
    (rules/FRONTEND.md).
    """
    return named("book_history")


@router.get("/book/positions", tags=["book"])
def positions(
    desk: str | None = None,
    symbol: str | None = None,
    as_of: dt.date | None = None,
    limit: Limit = 200,
    offset: Offset = 0,
) -> list[dict]:
    """The blotter. Joined to specialness so each position carries its own
    scarcity score, and to the FTP charge on the desk's NET position."""
    return fetch(
        """
        SELECT
            p.as_of_date, p.trade_id, p.desk_id, p.symbol, p.series_code, p.side,
            p.quantity, p.underlying_close, p.notional_inr, p.tenor_days,
            p.fee_per_share, p.fee_annualised_pct, p.fee_pnl_inr,
            s.specialness_score, s.classification, s.is_stale,
            f.net_notional_inr, f.ftp_rate_pct, f.ftp_charge_inr,
            f.curve_is_estimated
        FROM slb_position_pnl_daily AS p
        LEFT JOIN slb_specialness_daily AS s
            ON s.trade_date = p.as_of_date
           AND s.symbol = p.symbol
           AND s.series_code = p.series_code
        LEFT JOIN ftp_charge_daily AS f
            ON f.as_of_date = p.as_of_date
           AND f.desk_id = p.desk_id
           AND f.symbol = p.symbol
           AND f.series_code = p.series_code
        WHERE p.as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM slb_position_pnl_daily))
          AND (%(desk)s::TEXT IS NULL OR p.desk_id = %(desk)s)
          AND (%(symbol)s::TEXT IS NULL OR p.symbol = %(symbol)s)
        ORDER BY p.notional_inr DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"desk": desk, "symbol": symbol, "as_of": as_of, "limit": limit, "offset": offset},
    )


@router.get("/book/pnl", tags=["book"])
def book_pnl(
    desk: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> list[dict]:
    """Desk statements. `reconciles` says whether the decomposition adds back to
    NIM -- the API states whether its own numbers add up."""
    return fetch(
        """
        SELECT as_of_date, desk_id, positions, gross_notional_inr, net_notional_inr,
               fee_pnl_inr, ftp_charge_inr, net_spread_inr, nim_inr,
               desk_spread_inr, treasury_spread_inr, actual_cost_of_funds_inr,
               reconciles
        FROM desk_pnl_daily
        WHERE (%(desk)s::TEXT IS NULL OR desk_id = %(desk)s)
          AND (%(date_from)s::DATE IS NULL OR as_of_date >= %(date_from)s)
          AND (%(date_to)s::DATE IS NULL OR as_of_date <= %(date_to)s)
        ORDER BY as_of_date DESC, desk_id
        """,
        {"desk": desk, "date_from": date_from, "date_to": date_to},
    )


@router.get("/book/trade/{trade_id}", tags=["book"])
def trade(trade_id: int) -> dict:
    """A trade plus its full append-only fee history, which is what makes P&L
    attributable to a re-rate rather than to the original trade."""
    detail = one(
        """
        SELECT trade_id, original_trade_id, desk_id, symbol, series_code, side,
               quantity, trade_date, first_leg_settle_date, reverse_leg_date,
               fee_per_share, status
        FROM slb_trade WHERE trade_id = %(trade_id)s
        """,
        {"trade_id": trade_id},
        f"no trade {trade_id}",
    )
    detail["legs"] = fetch(
        """
        SELECT leg_id, leg_type, effective_from, effective_to, quantity,
               fee_per_share, reason
        FROM slb_trade_leg WHERE trade_id = %(trade_id)s ORDER BY effective_from, leg_id
        """,
        {"trade_id": trade_id},
    )
    return detail


# --- bonds ----------------------------------------------------------------


@router.get("/bonds", tags=["bonds"])
def bond_list(instrument_type: str | None = None, limit: Limit = 200) -> list[dict]:
    return fetch(
        """
        SELECT isin, security_code, instrument_type, issue_desc, coupon_pct,
               issue_date, maturity_date, last_ip_date, next_ip_date, coupon_freq
        FROM gsec
        WHERE %(instrument_type)s::TEXT IS NULL OR instrument_type = %(instrument_type)s
        ORDER BY maturity_date
        LIMIT %(limit)s
        """,
        {"instrument_type": instrument_type, "limit": limit},
    )


@router.get("/bonds/{isin}/analytics", tags=["bonds"])
def bond_analytics(isin: str, as_of: dt.date | None = None) -> dict:
    """`nse_ytm_pct` and `ytm_diff_bps` are stored alongside our own figure, so
    the response carries its own accuracy against the exchange."""
    return one(
        """
        SELECT trade_date, isin, security_code, coupon_pct, maturity_date,
               settlement_date, residual_years, clean_price, accrued_interest,
               dirty_price, ytm_pct, nse_ytm_pct, ytm_diff_bps, macaulay_duration,
               modified_duration, convexity, dv01_per_100_face, traded_value_inr
        FROM gsec_analytics_daily
        WHERE isin = %(isin)s
          AND (%(as_of)s::DATE IS NULL OR trade_date = %(as_of)s)
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        {"isin": isin, "as_of": as_of},
        f"no analytics for {isin}",
    )


@router.get("/bonds/price", tags=["bonds"])
def price_bond(
    coupon_pct: float,
    maturity_date: dt.date,
    settlement_date: dt.date,
    clean_price: float | None = None,
    ytm_pct: float | None = None,
    next_ip_date: dt.date | None = None,
    coupon_freq: int = 2,
    face_value: float = 100.0,
) -> dict:
    """Ad-hoc pricer: give it a clean price to get a yield, or a yield to get a price.

    Computes and returns; writes nothing. Accrued interest is 30/360 (the Indian
    G-Sec convention) and the quoted yield follows market convention -- simple
    ACT/365 once inside the final coupon period.
    """
    if (clean_price is None) == (ytm_pct is None):
        raise HTTPException(422, "give exactly one of clean_price or ytm_pct")
    if settlement_date >= maturity_date:
        raise HTTPException(422, "settlement must fall before maturity")

    next_ip = next_ip_date or maturity_date
    period_start, remaining = bonds.coupon_schedule(
        settlement_date, next_ip, maturity_date, coupon_freq
    )
    accrued = bonds.accrued_interest(coupon_pct, period_start, settlement_date, face_value)

    if clean_price is not None:
        dirty = bonds.dirty_from_clean(clean_price, accrued)
        yield_pct = bonds.quoted_ytm(
            dirty, coupon_pct, period_start, settlement_date, remaining, coupon_freq, face_value
        )
        compounded = bonds.solve_ytm(
            dirty, coupon_pct, period_start, settlement_date, remaining, coupon_freq, face_value
        )
    else:
        yield_pct = compounded = ytm_pct
        dirty = bonds.price_from_ytm(
            ytm_pct, coupon_pct, period_start, settlement_date, remaining, coupon_freq, face_value
        )
        clean_price = bonds.clean_from_dirty(dirty, accrued)

    # Risk is always taken off the compounded curve, so the derivatives stay
    # consistent with the function they differentiate.
    risk = bonds.risk_measures(
        compounded, coupon_pct, period_start, settlement_date, remaining, coupon_freq, face_value
    )
    return {
        "coupon_pct": coupon_pct,
        "settlement_date": settlement_date,
        "maturity_date": maturity_date,
        "coupon_period_start": period_start,
        "next_coupon_date": remaining[0],
        "remaining_coupons": len(remaining),
        "clean_price": clean_price,
        "accrued_interest": accrued,
        "dirty_price": dirty,
        "ytm_pct": yield_pct,
        "ytm_compounded_pct": compounded,
        "macaulay_duration": risk["macaulay_duration"],
        "modified_duration": risk["modified_duration"],
        "convexity": risk["convexity"],
        "dv01_per_100_face": risk["dv01"],
    }


# --- repo -----------------------------------------------------------------


@router.get("/repo/trades", tags=["repo"])
def repo_trades(desk: str | None = None, status: str | None = None) -> list[dict]:
    return fetch(
        """
        SELECT repo_id, desk_id, direction, isin, nominal, trade_date, start_date,
               end_date, repo_rate_pct, haircut, purchase_price, repurchase_price, status
        FROM repo_trade
        WHERE (%(desk)s::TEXT IS NULL OR desk_id = %(desk)s)
          AND (%(status)s::TEXT IS NULL OR status = %(status)s)
        ORDER BY start_date DESC, repo_id
        """,
        {"desk": desk, "status": status},
    )


@router.get("/repo/collateral", tags=["repo"])
def collateral(as_of: dt.date | None = None, repo_id: int | None = None) -> list[dict]:
    """Collateral is valued DIRTY, and the haircut is recomputed daily -- a haircut
    widening on unchanged prices is still a margin call."""
    return fetch(
        """
        SELECT as_of_date, repo_id, isin, nominal, mtm_clean_price, accrued_interest,
               dirty_value, haircut, post_haircut_value, haircut_source
        FROM collateral_position
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM collateral_position))
          AND (%(repo_id)s::INT IS NULL OR repo_id = %(repo_id)s)
        ORDER BY repo_id
        """,
        {"as_of": as_of, "repo_id": repo_id},
    )


@router.get("/repo/margin-calls", tags=["repo"])
def margin_calls(as_of: dt.date | None = None, status: str | None = None) -> list[dict]:
    """`due_at` is 09:00 the next business day, per CCIL's TREPS rule."""
    return fetch(
        """
        SELECT m.call_id, m.as_of_date, m.repo_id, m.desk_id, r.direction, r.isin,
               m.exposure, m.collateral_value, m.shortfall, m.call_amount,
               m.due_at, m.status
        FROM margin_call AS m
        JOIN repo_trade AS r ON r.repo_id = m.repo_id
        WHERE (%(as_of)s::DATE IS NULL OR m.as_of_date = %(as_of)s)
          AND (%(status)s::TEXT IS NULL OR m.status = %(status)s)
        ORDER BY m.as_of_date DESC, m.shortfall DESC
        """,
        {"as_of": as_of, "status": status},
    )


# --- FTP ------------------------------------------------------------------


@router.get("/curves/zero", tags=["curves"])
def zero_curve(as_of: dt.date | None = None, method: str | None = None) -> list[dict]:
    """The zero-coupon curve, both constructions.

    BOOTSTRAP is exact where something traded but silent between its nodes and
    flat beyond the last one. NSS is smooth and extrapolates but does not reprice
    every bond exactly. `is_extrapolated` marks where each one is reaching.
    """
    return fetch(
        """
        SELECT as_of_date, method, tenor_years, discount_factor, zero_rate_pct,
               forward_rate_pct, is_extrapolated
        FROM zero_curve_point
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM zero_curve_point))
          AND (%(method)s::TEXT IS NULL OR method = %(method)s)
        ORDER BY method, tenor_years
        """,
        {"as_of": as_of, "method": method},
    )


@router.get("/curves/nss", tags=["curves"])
def nss_parameters(date_from: dt.date | None = None) -> list[dict]:
    """The fitted Nelson-Siegel-Svensson parameters.

    The reason to prefer a parametric curve: beta0 is the long level, beta0+beta1
    the short rate, beta2/beta3 the two curvatures, and rmse_bps says whether to
    trust any of it. `description` is the curve in words.
    """
    return fetch(
        """
        SELECT as_of_date, beta0, beta1, beta2, beta3, tau1, tau2,
               short_rate_pct, rmse_bps, observations, description
        FROM nss_fit
        WHERE %(date_from)s::DATE IS NULL OR as_of_date >= %(date_from)s
        ORDER BY as_of_date DESC
        """,
        {"date_from": date_from},
    )


@router.get("/curves/observations", tags=["curves"])
def curve_observations(as_of: dt.date | None = None) -> list[dict]:
    """The prints each curve was built from, so a curve is always traceable.

    `nse_ytm_pct` is the exchange's own figure alongside ours -- for T-bills our
    simple ACT/365 yield reproduces it to a median 0.002bp.
    """
    return fetch(
        """
        SELECT as_of_date, isin, instrument_type, security_code, observed_on,
               settlement_date, tenor_years, dirty_price, ytm_pct, nse_ytm_pct
        FROM curve_observation
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM curve_observation))
        ORDER BY tenor_years
        """,
        {"as_of": as_of},
    )


@router.get("/slb/implied-forwards", tags=["slb"])
def implied_forwards(
    as_of: dt.date | None = None,
    signal: str | None = None,
    symbol: str | None = None,
    limit: Limit = 200,
) -> list[dict]:
    """The SLB fee curve read as a forward curve.

    A spot fee says a name is expensive now; the forward says whether the market
    expects it to STAY expensive. PIIND quoting 51.1% to 15 days and 19.3% to 43
    implies about 2% over the 28 days between -- the squeeze is priced to be over.

    FRONT_LOADED means a negative forward: the whole cost sits in the near
    window. It is NOT an arbitrage, because rolling an SLB contract means
    trading a fresh one at a new market fee.
    """
    return fetch(
        """
        SELECT trade_date, symbol, contract_set, near_tenor_days, far_tenor_days,
               near_fee_pct, far_fee_pct, implied_forward_pct, signal
        FROM slb_implied_forward
        WHERE trade_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(trade_date) FROM slb_implied_forward))
          AND (%(signal)s::TEXT IS NULL OR signal = %(signal)s)
          AND (%(symbol)s::TEXT IS NULL OR symbol = %(symbol)s)
        ORDER BY near_fee_pct DESC
        LIMIT %(limit)s
        """,
        {"as_of": as_of, "signal": signal, "symbol": symbol, "limit": limit},
    )


@router.get("/repo/key-rate-dv01", tags=["repo"])
def key_rate_dv01(as_of: dt.date | None = None) -> list[dict]:
    """Curve risk on the collateral, bucketed by key tenor.

    A parallel DV01 cannot tell a ten-year position from a barbell of twos and
    thirties with the same total, and a book that is DV01-neutral overall can
    still be badly exposed to a steepening. Buckets are additive, so a
    desk-level exposure is a plain sum.
    """
    return fetch(
        """
        SELECT as_of_date, key_tenor_years, SUM(dv01_inr) AS dv01_inr,
               COUNT(DISTINCT isin) AS positions
        FROM key_rate_dv01_daily
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM key_rate_dv01_daily))
        GROUP BY as_of_date, key_tenor_years
        ORDER BY key_tenor_years
        """,
        {"as_of": as_of},
    )


@router.get("/ftp/curve", tags=["ftp"])
def ftp_curve(as_of: dt.date | None = None) -> list[dict]:
    """`is_estimated` marks extrapolated nodes. The short end usually is: SLB
    tenors sit exactly where sovereign coverage is thinnest."""
    return fetch(
        """
        SELECT as_of_date, tenor_days, base_rate_pct, term_liquidity_premium_bps,
               (base_rate_pct + term_liquidity_premium_bps / 100.0)::NUMERIC(10, 4)
                   AS ftp_rate_pct,
               source, is_estimated
        FROM funding_curve_point
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM funding_curve_point))
        ORDER BY tenor_days
        """,
        {"as_of": as_of},
    )


@router.get("/ftp/charges", tags=["ftp"])
def ftp_charges(
    desk: str | None = None, as_of: dt.date | None = None, limit: Limit = 200
) -> list[dict]:
    """Charged on the NET position per desk, symbol and series: a matched book
    consumes almost no balance sheet and pays almost nothing."""
    return fetch(
        """
        SELECT as_of_date, desk_id, symbol, series_code, net_quantity,
               net_notional_inr, tenor_days, base_rate_pct, term_liquidity_bps,
               contingent_liq_bps, ftp_rate_pct, ftp_charge_inr, curve_is_estimated
        FROM ftp_charge_daily
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM ftp_charge_daily))
          AND (%(desk)s::TEXT IS NULL OR desk_id = %(desk)s)
        ORDER BY ABS(ftp_charge_inr) DESC
        LIMIT %(limit)s
        """,
        {"desk": desk, "as_of": as_of, "limit": limit},
    )


@router.get("/ftp/decomposition", tags=["ftp"])
def ftp_decomposition(as_of: dt.date | None = None) -> list[dict]:
    """The split that says whether a book made money because it was good or
    because funding was cheap. `reconciles` is the API stating its own integrity."""
    return fetch(
        """
        SELECT as_of_date, desk_id, positions, gross_notional_inr, net_notional_inr,
               fee_pnl_inr, ftp_charge_inr, desk_spread_inr, treasury_spread_inr,
               actual_cost_of_funds_inr, nim_inr, net_spread_inr, reconciles
        FROM desk_pnl_daily
        WHERE as_of_date = COALESCE(
                  %(as_of)s::DATE, (SELECT MAX(as_of_date) FROM desk_pnl_daily))
        ORDER BY desk_id
        """,
        {"as_of": as_of},
    )
