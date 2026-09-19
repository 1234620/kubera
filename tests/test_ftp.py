"""Stage 6 check: the funding curve, FTP charges and the NIM decomposition.

The curve and charge logic are pure functions. The reconciliation identity needs a
database, because it is a property of the whole book.
"""

import datetime as dt
import math

import pytest

from slbdesk import db, queries
from slbdesk.analytics import ftp

psycopg = pytest.importorskip("psycopg")

AS_OF = dt.date(2026, 9, 18)


# --- curve interpolation --------------------------------------------------


NODES = [(30, 5.50), (91, 6.00), (365, 6.80)]


def test_interpolation_is_exact_at_the_nodes():
    for tenor, rate in NODES:
        assert ftp.interpolate_rate(NODES, tenor) == (rate, False)


def test_interpolation_is_log_linear_on_discount_factors():
    """Linear in r*t, not linear in r. A straight line through yields is not
    arbitrage-free between nodes."""
    tenor = 60
    rate, estimated = ftp.interpolate_rate(NODES, tenor)
    assert not estimated

    left_area = NODES[0][0] * NODES[0][1]
    right_area = NODES[1][0] * NODES[1][1]
    weight = (tenor - NODES[0][0]) / (NODES[1][0] - NODES[0][0])
    assert rate == pytest.approx((left_area + weight * (right_area - left_area)) / tenor)

    # And it is genuinely NOT the naive linear-in-yield answer.
    naive = NODES[0][1] + weight * (NODES[1][1] - NODES[0][1])
    assert rate != pytest.approx(naive)


def test_interpolated_discount_factors_are_monotone():
    """The economic property the interpolation exists to preserve."""
    previous = 1.0
    for tenor in range(31, 365, 7):
        rate, _ = ftp.interpolate_rate(NODES, tenor)
        factor = math.exp(-rate / 100 * tenor / 365)
        assert factor < previous
        previous = factor


def test_extrapolation_is_flat_and_flagged():
    """Extending a slope past the last observation invents a shape nobody measured."""
    short_rate, short_flag = ftp.interpolate_rate(NODES, 1)
    long_rate, long_flag = ftp.interpolate_rate(NODES, 5000)

    assert (short_rate, short_flag) == (NODES[0][1], True)
    assert (long_rate, long_flag) == (NODES[-1][1], True)


def test_interpolation_needs_observations():
    with pytest.raises(ValueError, match="no sovereign observations"):
        ftp.interpolate_rate([], 30)


# --- term liquidity premium ----------------------------------------------


def test_term_liquidity_premium_rises_with_tenor():
    """The component most often omitted, and the reason long lending looks dear."""
    premiums = [ftp.term_liquidity_bps(days) for days in (1, 30, 91, 182, 365)]
    assert premiums == sorted(premiums)
    assert premiums[0] == 0.0


def test_term_liquidity_premium_is_exact_at_its_nodes():
    for days, bps in ftp.TLP_CURVE:
        assert ftp.term_liquidity_bps(days) == bps


def test_term_liquidity_premium_is_flat_beyond_the_grid():
    assert ftp.term_liquidity_bps(10_000) == ftp.TLP_CURVE[-1][1]
    assert ftp.term_liquidity_bps(0) == ftp.TLP_CURVE[0][1]


# --- fee accrual ---------------------------------------------------------


def position(**overrides) -> dict:
    base = {
        "as_of_date": AS_OF,
        "trade_id": 1,
        "desk_id": "EQ_FIN",
        "symbol": "RELIANCE",
        "series_code": "XO",
        "side": "LEND",
        "quantity": 10_000,
        "fee_per_share": 4.0,
        "tenor_days": 100,
        "underlying_close": 1000.0,
        "recall_eligible": True,
    }
    return base | overrides


def test_fee_accrues_straight_line_over_the_contract():
    pnl = ftp.position_pnl(position())
    assert pnl["fee_pnl_inr"] == pytest.approx(10_000 * 4.0 / 100)
    assert pnl["notional_inr"] == pytest.approx(10_000 * 1000.0)


def test_a_borrow_accrues_a_negative_number():
    """Positive is a gain to OUR book. A borrow cost is negative, not a positive
    number you subtract -- half of all sign errors come from mixing the two."""
    lend = ftp.position_pnl(position(side="LEND"))
    borrow = ftp.position_pnl(position(side="BORROW"))

    assert lend["fee_pnl_inr"] > 0
    assert borrow["fee_pnl_inr"] < 0
    assert lend["fee_pnl_inr"] == pytest.approx(-borrow["fee_pnl_inr"])


def test_fee_annualisation_matches_the_sql_definition():
    pnl = ftp.position_pnl(position())
    assert pnl["fee_annualised_pct"] == pytest.approx(4.0 / 1000.0 * (365 / 100) * 100)


# --- FTP charges and netting ---------------------------------------------


def curve() -> dict:
    return {AS_OF: [(days, 6.0, False) for days in ftp.CURVE_GRID]}


def test_a_matched_pair_attracts_no_charge():
    """The point of netting: a matched book consumes almost no balance sheet.

    Charging both legs would make FTP a flat tax on turnover rather than a price
    for the resource consumed, and a matched book would look loss-making.
    """
    matched = [
        position(trade_id=1, side="BORROW"),
        position(trade_id=2, side="LEND"),
    ]
    assert ftp.ftp_charges(matched, curve()) == []


def test_a_directional_borrow_is_charged():
    charges = ftp.ftp_charges([position(side="BORROW", desk_id="DELTA_ONE")], curve())
    desks = {row["desk_id"]: row for row in charges}

    assert desks["DELTA_ONE"]["net_quantity"] == 10_000
    assert desks["DELTA_ONE"]["ftp_charge_inr"] < 0  # a cost to the desk


def test_every_charge_is_mirrored_to_treasury():
    """The internal ledger nets to zero by construction, which is what makes the
    NIM decomposition an identity rather than a restatement."""
    charges = ftp.ftp_charges([position(side="BORROW", desk_id="DELTA_ONE")], curve())

    assert {row["desk_id"] for row in charges} == {"DELTA_ONE", "TREASURY"}
    assert sum(row["ftp_charge_inr"] for row in charges) == pytest.approx(0.0)
    assert sum(row["net_notional_inr"] for row in charges) == pytest.approx(0.0)


def test_ftp_rate_is_base_plus_tlp_plus_contingent():
    charges = ftp.ftp_charges([position(side="BORROW", desk_id="DELTA_ONE")], curve())
    row = next(r for r in charges if r["desk_id"] == "DELTA_ONE")

    expected = (
        row["base_rate_pct"] + row["term_liquidity_bps"] / 100 + row["contingent_liq_bps"] / 100
    )
    assert row["ftp_rate_pct"] == pytest.approx(expected)
    assert row["ftp_charge_inr"] == pytest.approx(
        -row["net_notional_inr"] * row["ftp_rate_pct"] / 100 / 365
    )


def test_a_position_that_cannot_be_recalled_pays_the_liquidity_charge():
    """Driven by the NSE eligibility file, not by an assumption (docs/07 §9)."""
    recallable = ftp.ftp_charges(
        [position(side="BORROW", desk_id="DELTA_ONE", recall_eligible=True)], curve()
    )
    locked = ftp.ftp_charges(
        [position(side="BORROW", desk_id="DELTA_ONE", recall_eligible=False)], curve()
    )

    free = next(r for r in recallable if r["desk_id"] == "DELTA_ONE")
    charged = next(r for r in locked if r["desk_id"] == "DELTA_ONE")

    assert free["contingent_liq_bps"] == 0.0
    assert charged["contingent_liq_bps"] == ftp.CONTINGENT_LIQUIDITY_BPS
    assert charged["ftp_rate_pct"] > free["ftp_rate_pct"]


def test_longer_tenors_cost_more_than_shorter_ones():
    """Matched maturity exists so this is true; a single-pool rate makes maturity
    transformation look free (ADR 0005)."""
    rising = {days: rate for days, rate in [(1, 5.0), (365, 7.0)]}
    nodes = [(days, rate, False) for days, rate in rising.items()]

    short = ftp.ftp_charges(
        [position(side="BORROW", desk_id="DELTA_ONE", tenor_days=7)], {AS_OF: nodes}
    )
    long = ftp.ftp_charges(
        [position(side="BORROW", desk_id="DELTA_ONE", tenor_days=360)], {AS_OF: nodes}
    )

    short_rate = next(r for r in short if r["desk_id"] == "DELTA_ONE")["ftp_rate_pct"]
    long_rate = next(r for r in long if r["desk_id"] == "DELTA_ONE")["ftp_rate_pct"]
    assert long_rate > short_rate


# --- the identities, against the stored book -----------------------------


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no database: {exc}")
    with connection as open_conn:
        if not open_conn.execute("SELECT COUNT(*) FROM desk_pnl_daily").fetchone()[0]:
            pytest.skip("no desk P&L; run `make seed && make analytics`")
        yield open_conn
        open_conn.rollback()


def test_the_internal_ledger_nets_to_zero(conn):
    """Every charge debited to a desk is credited to Treasury."""
    assert not queries.fetch(conn, "validate_ftp_zero")


def test_the_decomposition_adds_back_to_nim(conn):
    """desk_spread + treasury_spread = book NIM, to the rupee.

    This is the test that makes the FTP model believable rather than decorative,
    and it is NOT free: the identity holds only because the internal transfer
    cancels. An earlier version gave Treasury a desk spread as well as a treasury
    spread, double-counting its own FTP credit, and this failed by exactly that
    amount.
    """
    assert not queries.fetch(conn, "validate_ftp_reconciliation")


def test_a_flat_position_carries_no_charge_in_the_stored_book(conn):
    assert not queries.fetch(conn, "validate_matched_book_pays_little_ftp")


def test_treasury_holds_no_positions_of_its_own(conn):
    """Treasury is the internal bank, not a trading desk."""
    rows = conn.execute(
        "SELECT SUM(positions), SUM(gross_notional_inr) FROM desk_pnl_daily WHERE desk_id = %s",
        ("TREASURY",),
    ).fetchone()
    assert rows[0] == 0
    assert float(rows[1]) == 0.0


def test_a_matched_desk_pays_no_ftp_and_keeps_its_fee_spread(conn):
    """EQ_FIN runs matched pairs, so its net notional is zero and its FTP is zero.

    This is the behaviour the netting design exists to produce, observed on the
    real stored book rather than on a constructed pair.
    """
    row = conn.execute(
        """
        SELECT gross_notional_inr, net_notional_inr, ftp_charge_inr, fee_pnl_inr,
               desk_spread_inr
        FROM desk_pnl_daily
        WHERE desk_id = 'EQ_FIN'
          AND as_of_date = (SELECT MAX(as_of_date) FROM desk_pnl_daily)
        """
    ).fetchone()
    if row is None:
        pytest.skip("no EQ_FIN row")

    gross, net, ftp_charge, fee, desk_spread = (float(value) for value in row)
    assert gross > 0
    assert net == pytest.approx(0.0)
    assert ftp_charge == pytest.approx(0.0)
    assert desk_spread == pytest.approx(fee)
