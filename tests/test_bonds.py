"""Stage 4 check: bond math against hand-computed values and against NSE itself.

No database and no network: the market validation runs off the committed G-Sec
master and daily-trade fixtures, so CI checks our yields against the exchange's
own published figures on every push.
"""

import datetime as dt
import pathlib

import pytest

from slbdesk import bonds
from slbdesk.ingest import parse

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TRADE_DATE = dt.date(2026, 9, 18)


# --- day count ------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # A whole semi-annual period is 180 days, which is what makes accrued
        # interest reconcile exactly with coupon/2.
        ((2026, 6, 7), (2026, 12, 7), 180),
        ((2026, 1, 1), (2027, 1, 1), 360),
        ((2026, 1, 1), (2026, 2, 1), 30),
        ((2026, 1, 15), (2026, 3, 15), 60),
        # End-of-month adjustments: d1 = 31 becomes 30.
        ((2026, 1, 31), (2026, 2, 28), 28),
        ((2026, 1, 30), (2026, 3, 31), 60),
        ((2026, 1, 31), (2026, 3, 31), 60),
        # d2 = 31 is only pulled back when d1 is already 30.
        ((2026, 1, 15), (2026, 3, 31), 76),
    ],
)
def test_days_30_360(start, end, expected):
    assert bonds.days_30_360(dt.date(*start), dt.date(*end)) == expected


def test_act365_is_actual_days():
    assert bonds.year_fraction_act365(dt.date(2026, 1, 1), dt.date(2027, 1, 1)) == pytest.approx(
        365 / 365
    )
    # A leap year has 366 actual days, and ACT/365 does not pretend otherwise.
    assert bonds.year_fraction_act365(dt.date(2028, 1, 1), dt.date(2029, 1, 1)) == pytest.approx(
        366 / 365
    )


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        ((2026, 8, 31), 6, (2027, 2, 28)),  # clamped, not an error
        ((2028, 8, 31), 6, (2029, 2, 28)),
        ((2026, 1, 31), 1, (2026, 2, 28)),
        ((2026, 6, 7), 6, (2026, 12, 7)),
        ((2026, 12, 7), 6, (2027, 6, 7)),
        ((2026, 6, 7), -6, (2025, 12, 7)),
    ],
)
def test_add_months_clamps_the_day(start, months, expected):
    assert bonds.add_months(dt.date(*start), months) == dt.date(*expected)


# --- accrued interest and the clean/dirty pair ----------------------------


def test_accrued_over_a_full_period_is_half_the_coupon():
    """The 30/360 consistency check: 180/360 of an 8.33% annual coupon."""
    accrued = bonds.accrued_interest(8.33, dt.date(2026, 6, 7), dt.date(2026, 12, 7))
    assert accrued == pytest.approx(8.33 / 2)


def test_accrued_is_zero_on_a_coupon_date():
    assert bonds.accrued_interest(7.5, dt.date(2026, 6, 7), dt.date(2026, 6, 7)) == 0.0


def test_accrued_is_linear_in_time():
    """Three months into a semi-annual period is a quarter of the annual coupon."""
    accrued = bonds.accrued_interest(8.0, dt.date(2026, 6, 7), dt.date(2026, 9, 7))
    assert accrued == pytest.approx(2.0)


def test_clean_dirty_round_trip():
    accrued = bonds.accrued_interest(7.33, dt.date(2026, 6, 7), dt.date(2026, 9, 19))
    dirty = bonds.dirty_from_clean(100.2169, accrued)
    assert dirty > 100.2169
    assert bonds.clean_from_dirty(dirty, accrued) == pytest.approx(100.2169)


# --- pricing --------------------------------------------------------------


def schedule(settlement, next_ip, maturity, freq=2):
    return bonds.coupon_schedule(settlement, next_ip, maturity, freq)


def test_coupon_schedule_reads_the_real_period():
    start, remaining = schedule(dt.date(2026, 9, 19), dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    assert start == dt.date(2026, 6, 7)
    assert remaining[0] == dt.date(2026, 12, 7)
    assert remaining[-1] == dt.date(2036, 6, 7)
    # Ten years of semi-annual coupons from Dec-2026 to Jun-2036 inclusive.
    assert len(remaining) == 20


def test_coupon_schedule_rolls_a_stale_next_ip_forward():
    """The master's Next IP Dt can predate the settlement we are pricing for."""
    start, remaining = schedule(dt.date(2026, 9, 19), dt.date(2026, 3, 7), dt.date(2030, 9, 7))
    assert start <= dt.date(2026, 9, 19) < remaining[0]


def test_coupon_schedule_rolls_backward_for_a_historical_settlement():
    """The master is a snapshot, so its Next IP Dt can be AHEAD of settlement.

    Pricing a 14-Aug trade against an 18-Sep snapshot (next coupon Mar-2027) must
    roll the period back to Mar-Sep 2026, not leave period_start in September --
    which would make accrued interest negative and the dirty price lower than the
    clean price.
    """
    settlement = dt.date(2026, 8, 14)
    start, remaining = schedule(settlement, dt.date(2027, 3, 9), dt.date(2035, 9, 9))

    assert start == dt.date(2026, 3, 9)
    assert remaining[0] == dt.date(2026, 9, 9)
    assert start <= settlement < remaining[0]
    assert bonds.accrued_interest(7.40, start, settlement) > 0


def test_accrued_interest_is_never_negative_across_a_coupon_period():
    """The invariant behind the dirty >= clean constraint."""
    next_ip, maturity = dt.date(2027, 3, 9), dt.date(2035, 9, 9)
    day = dt.date(2026, 1, 1)
    while day < dt.date(2027, 6, 1):
        start, _ = schedule(day, next_ip, maturity)
        assert bonds.accrued_interest(7.40, start, day) >= 0, day
        day += dt.timedelta(days=1)


def test_price_at_par_when_yield_equals_coupon():
    """On a coupon date with y = c, the dirty price is exactly face."""
    settlement = dt.date(2026, 6, 7)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    price = bonds.price_from_ytm(8.0, 8.0, start, settlement, remaining)
    assert price == pytest.approx(100.0, abs=1e-8)


def test_price_moves_inversely_to_yield():
    settlement = dt.date(2026, 9, 19)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    args = (7.0, start, settlement, remaining)
    assert bonds.price_from_ytm(6.0, *args) > bonds.price_from_ytm(7.0, *args)
    assert bonds.price_from_ytm(7.0, *args) > bonds.price_from_ytm(8.0, *args)


def test_mid_period_discounting_is_applied():
    """Discounting as if settlement fell on a coupon date is the classic error.

    The same bond priced part-way through its coupon period must not give the
    same dirty price as one priced on the period start.
    """
    next_ip, maturity = dt.date(2026, 12, 7), dt.date(2036, 6, 7)
    on_date = dt.date(2026, 6, 7)
    mid = dt.date(2026, 9, 19)

    start_a, rem_a = schedule(on_date, next_ip, maturity)
    start_b, rem_b = schedule(mid, next_ip, maturity)

    assert bonds.price_from_ytm(7.0, 8.0, start_a, on_date, rem_a) != pytest.approx(
        bonds.price_from_ytm(7.0, 8.0, start_b, mid, rem_b)
    )


# --- yield solving --------------------------------------------------------


@pytest.mark.parametrize("yield_pct", [0.5, 2.0, 5.0, 6.87, 8.0, 12.0, 25.0])
def test_ytm_round_trips_against_the_pricer(yield_pct):
    """ytm(price(y)) == y. The identity that says the solver inverts the pricer."""
    settlement = dt.date(2026, 9, 19)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))

    price = bonds.price_from_ytm(yield_pct, 7.33, start, settlement, remaining)
    solved = bonds.solve_ytm(price, 7.33, start, settlement, remaining)
    assert solved == pytest.approx(yield_pct, abs=1e-8)


def test_ytm_round_trips_on_a_short_bond():
    settlement = dt.date(2026, 9, 19)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2027, 6, 7))
    price = bonds.price_from_ytm(6.5, 5.74, start, settlement, remaining)
    assert bonds.solve_ytm(price, 5.74, start, settlement, remaining) == pytest.approx(
        6.5, abs=1e-8
    )


def test_premium_and_discount_bracket_the_coupon():
    settlement = dt.date(2026, 6, 7)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    # Above par means the yield is below the coupon, and the other way round.
    assert bonds.solve_ytm(110.0, 8.0, start, settlement, remaining) < 8.0
    assert bonds.solve_ytm(92.0, 8.0, start, settlement, remaining) > 8.0


def test_quoted_ytm_switches_to_simple_in_the_final_period():
    """Measured against NSE, not assumed: see test_reproduces_nse_weighted_ytm."""
    settlement = dt.date(2026, 9, 17)
    maturity = dt.date(2026, 11, 15)
    start, remaining = schedule(settlement, maturity, maturity)
    assert len(remaining) == 1

    dirty = 101.0
    quoted = bonds.quoted_ytm(dirty, 5.74, start, settlement, remaining)
    simple = bonds.simple_yield_act365(dirty, 5.74 / 2 + 100, settlement, maturity)
    assert quoted == pytest.approx(simple)
    # And it genuinely differs from compounding a single payment.
    assert quoted != pytest.approx(bonds.solve_ytm(dirty, 5.74, start, settlement, remaining))


# --- duration, convexity, DV01 -------------------------------------------


def test_zero_coupon_macaulay_duration_equals_its_maturity():
    """The identity that pins the duration formula down."""
    settlement = dt.date(2026, 6, 7)
    maturity = dt.date(2036, 6, 7)
    start, remaining = schedule(settlement, maturity, maturity)

    measures = bonds.risk_measures(7.0, 0.0, start, settlement, remaining)
    assert measures["macaulay_duration"] == pytest.approx(10.0, abs=1e-9)


def test_coupons_pull_duration_below_maturity():
    settlement = dt.date(2026, 6, 7)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    measures = bonds.risk_measures(7.0, 7.0, start, settlement, remaining)
    # A 10-year 7% G-Sec sits nearer 7 years than 10.
    assert 6.5 < measures["macaulay_duration"] < 7.5


def test_modified_duration_is_macaulay_discounted_once():
    settlement = dt.date(2026, 9, 19)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    m = bonds.risk_measures(6.8, 7.33, start, settlement, remaining)
    assert m["modified_duration"] == pytest.approx(m["macaulay_duration"] / (1 + 0.068 / 2))


def test_analytic_dv01_matches_repricing():
    """The independent check: if these disagree, the duration formula is wrong."""
    settlement = dt.date(2026, 9, 19)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2036, 6, 7))
    args = (7.33, start, settlement, remaining)

    analytic = bonds.risk_measures(6.8, *args)["dv01"]
    numerical = bonds.dv01_numerical(6.8, *args)
    assert analytic == pytest.approx(numerical, abs=1e-6)


def test_convexity_is_positive_and_corrects_the_duration_estimate():
    """Duration alone overstates the loss from a rise and understates the gain.

    Convexity is the second-order term that fixes both, which is exactly what
    matters when sizing a margin call on a repo collateral pool.
    """
    settlement = dt.date(2026, 9, 19)
    start, remaining = schedule(settlement, dt.date(2026, 12, 7), dt.date(2046, 6, 7))
    args = (7.33, start, settlement, remaining)

    base = bonds.risk_measures(6.8, *args)
    assert base["convexity"] > 0

    shift = 0.01  # 100bp, where the convexity term is material
    actual = bonds.price_from_ytm(6.8 + shift * 100, *args)
    duration_only = base["dirty_price"] * (1 - base["modified_duration"] * shift)
    with_convexity = base["dirty_price"] * (
        1 - base["modified_duration"] * shift + 0.5 * base["convexity"] * shift**2
    )

    assert duration_only < actual  # overstates the loss
    assert abs(with_convexity - actual) < abs(duration_only - actual)


def test_longer_bonds_have_more_duration_and_convexity():
    settlement = dt.date(2026, 6, 7)
    short = schedule(settlement, dt.date(2026, 12, 7), dt.date(2031, 6, 7))
    long = schedule(settlement, dt.date(2026, 12, 7), dt.date(2046, 6, 7))

    a = bonds.risk_measures(7.0, 7.0, short[0], settlement, short[1])
    b = bonds.risk_measures(7.0, 7.0, long[0], settlement, long[1])
    assert b["modified_duration"] > a["modified_duration"]
    assert b["convexity"] > a["convexity"]


# --- T-bills --------------------------------------------------------------


def test_tbill_price_and_yield_round_trip():
    settlement, maturity = dt.date(2026, 9, 19), dt.date(2026, 12, 18)
    price = bonds.tbill_price(6.25, settlement, maturity)
    assert price < 100
    assert bonds.tbill_yield(price, settlement, maturity) == pytest.approx(6.25)


def test_tbill_discount_widens_with_tenor():
    settlement = dt.date(2026, 9, 19)
    near = bonds.tbill_price(6.25, settlement, dt.date(2026, 12, 18))
    far = bonds.tbill_price(6.25, settlement, dt.date(2027, 9, 18))
    assert far < near


# --- against the exchange itself -----------------------------------------


def nse_comparison() -> list[tuple[str, float, float]]:
    """(security, our quoted YTM, NSE's weighted YTM) for every G-Sec that traded.

    Runs across all 28 committed daily settlement files, not one day: a single day
    carries only a handful of G-Sec prints, which is too few to test a percentage
    against.

    Joined on security code AND coupon, because a code like CG2028 is shared by
    several bonds with different coupons -- joining on the code alone silently
    prices the wrong bond.
    """
    master = parse.parse_gsec_master((FIXTURES / "wdmlist_18092026_gsec.csv").read_bytes())
    trades = [
        row
        for path in sorted((FIXTURES / "gsec_trades").glob("trd*_sett.csv"))
        for row in parse.parse_gsec_trades_csv(path.read_bytes())
    ]

    by_key = {
        (row["security_code"], round(row["coupon_pct"], 4)): row
        for row in master
        if row["coupon_pct"] is not None
    }

    out = []
    for trade in trades:
        if trade["instrument_type"] != "GS":
            continue
        coupon = round(float(trade["issue_name"].rstrip("%")), 4)
        bond = by_key.get((trade["security_code"], coupon))
        if bond is None:
            continue

        settlement = trade["trade_date"] + dt.timedelta(days=trade["settl_days"])
        if settlement >= bond["maturity_date"]:
            continue

        start, remaining = bonds.coupon_schedule(
            settlement, bond["next_ip_date"], bond["maturity_date"], bond["coupon_freq"]
        )
        accrued = bonds.accrued_interest(coupon, start, settlement)
        dirty = bonds.dirty_from_clean(trade["vwap_clean_price"], accrued)
        ours = bonds.quoted_ytm(dirty, coupon, start, settlement, remaining, bond["coupon_freq"])
        out.append((trade["security_code"], ours, trade["weighted_ytm_pct"]))
    return out


def test_nse_comparison_has_something_to_compare():
    assert len(nse_comparison()) >= 100


def test_reproduces_nse_weighted_ytm():
    """The real test: does our implementation agree with the exchange?

    docs/03 §11 sets the bar at 90% of G-Sec trades within 2bp. It actually
    reaches 99.2% with a median error of 0.000bp, so the assertion is kept at the
    documented bar and the observed figure is left in this docstring rather than
    hard-coded -- a test that pins a number it happens to hit today fails on the
    next month of data for no good reason.

    This test is also what discovered the final-coupon-period convention: every
    outlier under pure compounding was a bond with one cash flow left, and
    switching those to a simple ACT/365 yield reproduced NSE to 0.02bp.
    """
    comparison = nse_comparison()
    errors = sorted((ours - theirs) * 100 for _, ours, theirs in comparison)
    within_2bp = sum(1 for error in errors if abs(error) <= 2)

    outliers = [
        (code, round(ours, 4), theirs)
        for code, ours, theirs in comparison
        if abs(ours - theirs) * 100 > 2
    ]
    assert within_2bp / len(errors) >= 0.90, outliers
    assert abs(errors[len(errors) // 2]) < 0.5  # median error under half a bp
