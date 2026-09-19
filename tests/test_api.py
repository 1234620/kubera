"""Stage 7 check: the API contract.

Runs the real app against a real Postgres via TestClient, so the lifespan pool
and every SQL statement are exercised. In CI the database holds only the
committed fixtures, so most endpoints return few or no rows -- these checks are
about the contract (status, shape, types, units), not about the data.
"""

import datetime as dt

import pytest

from slbdesk import db

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture(scope="module")
def client():
    try:
        db.connect().close()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no database: {exc}")

    from slbdesk.api.main import app

    with TestClient(app) as test_client:
        yield test_client


# Every read-only endpoint, with arguments that work on an empty database.
LIST_ENDPOINTS = [
    "/api/data-coverage",
    "/api/securities?limit=5",
    "/api/securities?search=REL",
    "/api/series",
    "/api/desks",
    "/api/slb/quotes?limit=5",
    "/api/slb/term-structure?symbol=RELIANCE",
    "/api/slb/utilisation?limit=5",
    "/api/slb/specialness?limit=5",
    "/api/slb/specialness?classification=SPECIAL",
    "/api/slb/specialness?include_stale=false",
    "/api/slb/gc-rate",
    "/api/book/positions?limit=5",
    "/api/book/positions?desk=EQ_FIN",
    "/api/book/pnl",
    "/api/book/pnl?desk=TREASURY",
    "/api/bonds?limit=5",
    "/api/bonds?instrument_type=GS",
    "/api/repo/trades",
    "/api/repo/trades?status=OPEN",
    "/api/repo/collateral",
    "/api/repo/margin-calls",
    "/api/repo/margin-calls?status=OPEN",
    "/api/ftp/curve",
    "/api/ftp/charges?limit=5",
    "/api/ftp/decomposition",
]


# --- contract -------------------------------------------------------------


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_every_endpoint_answers_with_a_list(client, path):
    """Includes each optional filter, because a nullable parameter with no type
    context is an AmbiguousParameter error in Postgres, not a silent no-op."""
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


def test_desks_are_the_four_we_model(client):
    desks = {row["desk_id"] for row in client.get("/api/desks").json()}
    assert desks == {"EQ_FIN", "REPO", "DELTA_ONE", "TREASURY"}


def test_openapi_documents_every_route(client):
    """/docs is why this project does not hand-write API documentation."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/book/kpis" in paths
    assert "/api/slb/specialness" in paths
    assert "/api/bonds/price" in paths


# --- units and types ------------------------------------------------------


def test_money_is_a_json_number_not_a_string(client):
    """docs/08 promises JSON numbers.

    Without the NUMERIC->float loader in api/main.py, psycopg returns Decimal and
    Pydantic serialises it to a quoted string, so every money field would arrive
    as "3946218034.89" and the frontend would have to parse it.
    """
    body = client.get("/api/book/kpis").json()
    for field in ("gross_notional_inr", "net_spread_inr", "net_financing_spread_bps"):
        if body.get(field) is not None:
            assert isinstance(body[field], (int, float)), field
            assert not isinstance(body[field], str), field


def test_kpis_expose_the_estimate_and_reconciliation_flags(client):
    """Flags reach the UI rather than staying internal (docs/08)."""
    body = client.get("/api/book/kpis").json()
    assert isinstance(body["utilisation_is_estimated"], bool)
    assert isinstance(body["ftp_reconciles"], bool)


def test_specialness_returns_both_components_not_just_the_blend(client):
    """ "87" is not an answer; the percentile and the z-score are."""
    rows = client.get("/api/slb/specialness?limit=1").json()
    if not rows:
        pytest.skip("no specialness rows loaded")

    row = rows[0]
    for field in ("xs_percentile", "own_z", "specialness_score", "classification"):
        assert field in row
    for field in ("is_stale", "days_since_last_trade", "quote_date"):
        assert field in row


def test_ftp_curve_flags_extrapolated_nodes(client):
    rows = client.get("/api/ftp/curve").json()
    if not rows:
        pytest.skip("no curve loaded")
    assert all(isinstance(row["is_estimated"], bool) for row in rows)
    assert [row["tenor_days"] for row in rows] == sorted(row["tenor_days"] for row in rows)


# --- errors ---------------------------------------------------------------


def test_unknown_trade_is_404(client):
    assert client.get("/api/book/trade/99999999").status_code == 404


def test_unknown_isin_is_404(client):
    assert client.get("/api/bonds/NOTANISIN/analytics").status_code == 404


def test_limit_is_bounded(client):
    assert client.get("/api/slb/specialness?limit=5000").status_code == 422
    assert client.get("/api/slb/specialness?limit=0").status_code == 422


def test_a_bad_date_is_422(client):
    assert client.get("/api/slb/specialness?as_of=not-a-date").status_code == 422


# --- the ad-hoc pricer ----------------------------------------------------


PRICER = (
    "/api/bonds/price?coupon_pct=7.33&maturity_date=2036-06-07"
    "&settlement_date=2026-09-19&next_ip_date=2026-12-07"
)


def test_pricer_turns_a_clean_price_into_a_yield(client):
    body = client.get(f"{PRICER}&clean_price=100.25").json()

    assert body["accrued_interest"] > 0
    assert body["dirty_price"] == pytest.approx(100.25 + body["accrued_interest"])
    # Priced below par on a 7.33% coupon, so the yield sits above the coupon.
    assert body["ytm_pct"] > 7.0
    assert body["modified_duration"] < body["macaulay_duration"]
    assert body["convexity"] > 0
    assert body["dv01_per_100_face"] > 0


def test_pricer_round_trips(client):
    """price(ytm(clean)) == clean. The identity that says both directions agree."""
    forward = client.get(f"{PRICER}&clean_price=100.25").json()
    back = client.get(f"{PRICER}&ytm_pct={forward['ytm_compounded_pct']}").json()
    assert back["clean_price"] == pytest.approx(100.25, abs=1e-6)


def test_pricer_reads_the_coupon_period_from_the_schedule(client):
    body = client.get(f"{PRICER}&clean_price=100.25").json()
    assert body["coupon_period_start"] == "2026-06-07"
    assert body["next_coupon_date"] == "2026-12-07"
    assert body["remaining_coupons"] == 20


def test_pricer_needs_exactly_one_of_price_or_yield(client):
    assert client.get(PRICER).status_code == 422
    assert client.get(f"{PRICER}&clean_price=100&ytm_pct=7").status_code == 422


def test_pricer_rejects_settlement_after_maturity(client):
    response = client.get(
        "/api/bonds/price?coupon_pct=7.0&maturity_date=2026-01-01"
        "&settlement_date=2026-09-19&clean_price=100"
    )
    assert response.status_code == 422


def test_pricer_writes_nothing(client):
    """It computes and returns; the API is read-only."""
    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM gsec_analytics_daily").fetchone()[0]
    client.get(f"{PRICER}&clean_price=101.5")
    with db.connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM gsec_analytics_daily").fetchone()[0]
    assert before == after


# --- filters actually filter ----------------------------------------------


def test_desk_filter_narrows_the_blotter(client):
    everything = client.get("/api/book/positions?limit=1000").json()
    if not everything:
        pytest.skip("no positions loaded")

    one_desk = client.get("/api/book/positions?desk=EQ_FIN&limit=1000").json()
    assert all(row["desk_id"] == "EQ_FIN" for row in one_desk)
    assert len(one_desk) <= len(everything)


def test_as_of_filter_selects_a_single_date(client):
    rows = client.get("/api/book/pnl").json()
    if not rows:
        pytest.skip("no desk P&L loaded")

    as_of = rows[0]["as_of_date"]
    same_day = client.get(f"/api/ftp/decomposition?as_of={as_of}").json()
    assert all(row["as_of_date"] == as_of for row in same_day)


def test_min_score_filter_is_applied(client):
    rows = client.get("/api/slb/specialness?min_score=75&limit=1000").json()
    assert all(row["specialness_score"] >= 75 for row in rows)


def test_include_stale_false_drops_stale_rows(client):
    rows = client.get("/api/slb/specialness?include_stale=false&limit=1000").json()
    assert all(not row["is_stale"] for row in rows)


def test_dates_serialise_as_iso(client):
    body = client.get("/api/health").json()
    if body["latest_ingest_date"]:
        dt.date.fromisoformat(body["latest_ingest_date"])
