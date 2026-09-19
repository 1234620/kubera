"""Stage 5 check: haircuts, collateral valuation and margin calls.

The valuation and call logic are pure functions, so most of this needs no
database. The last section checks the stored book against a real Postgres.
"""

import datetime as dt

import pytest

from slbdesk import bonds, repo

# A 10-year 7.40% G-Sec, five months into its coupon period.
COUPON = 7.40
NEXT_IP = dt.date(2027, 3, 9)
MATURITY = dt.date(2035, 9, 9)
AS_OF = dt.date(2026, 8, 14)


def position(**overrides) -> dict:
    base = {
        "as_of_date": AS_OF,
        "repo_id": 1,
        "desk_id": "REPO",
        "direction": "REPO",
        "isin": "IN0020050012",
        "nominal": 500_000_000.0,
        "start_date": AS_OF,
        "end_date": AS_OF + dt.timedelta(days=91),
        "repo_rate_pct": 6.50,
        "purchase_price": 0.0,
        "coupon_pct": COUPON,
        "next_ip_date": NEXT_IP,
        "maturity_date": MATURITY,
        "coupon_freq": 2,
        "mtm_clean_price": 102.1602,
        "price_date": AS_OF,
    }
    return base | overrides


# --- haircut bands --------------------------------------------------------


@pytest.mark.parametrize(
    ("residual_years", "expected"),
    [
        (0.1, 0.0050),
        (1.0, 0.0050),  # band is inclusive at the top
        (1.01, 0.0150),
        (5.0, 0.0150),
        (5.01, 0.0250),
        (10.0, 0.0250),
        (10.01, 0.0400),
        (40.0, 0.0400),
    ],
)
def test_haircut_bands(residual_years, expected):
    assert repo.haircut_for_gsec(residual_years) == expected


def test_haircut_never_falls_as_maturity_lengthens():
    """A longer bond has more DV01, so the same yield move costs more.

    A haircut that fell with tenor would under-collateralise exactly the
    positions that need it most.
    """
    haircuts = [repo.haircut_for_gsec(y) for y in [0.5, 2, 7, 15, 30, 40]]
    assert haircuts == sorted(haircuts)


# --- collateral valuation -------------------------------------------------


def test_collateral_is_valued_dirty_not_clean():
    """The convention that matters: clean valuation under-collateralises.

    Five months into a 7.40% coupon period the accrued interest is over three
    points, so valuing this collateral clean would leave the cash lender short by
    roughly 3% of the position (docs/02 §1).
    """
    valued = repo.revalue(position())

    assert valued["accrued_interest"] > 3.0
    clean_only = position()["nominal"] / 100 * position()["mtm_clean_price"]
    assert valued["dirty_value"] > clean_only
    # The gap is the accrued coupon on the whole nominal, not a rounding artefact.
    assert valued["dirty_value"] - clean_only == pytest.approx(
        position()["nominal"] / 100 * valued["accrued_interest"]
    )


def test_dirty_value_is_clean_plus_accrued_on_the_nominal():
    valued = repo.revalue(position())
    expected = (
        position()["nominal"] / 100 * (position()["mtm_clean_price"] + valued["accrued_interest"])
    )
    assert valued["dirty_value"] == pytest.approx(expected)


def test_post_haircut_value_applies_the_model_haircut():
    valued = repo.revalue(position())
    residual = (MATURITY - AS_OF).days / 365.0

    assert valued["haircut"] == repo.haircut_for_gsec(residual)
    assert valued["haircut_source"] == "TENOR_MODEL"
    assert valued["post_haircut_value"] == pytest.approx(
        valued["dirty_value"] * (1 - valued["haircut"])
    )


def test_a_widening_haircut_alone_reduces_collateral():
    """Both the price and the haircut move; recomputing only prices misses calls."""
    ten_year = repo.revalue(position())
    thirty_year = repo.revalue(position(maturity_date=dt.date(2056, 9, 9)))

    # Same clean price, longer bond, so a wider haircut and less usable value per
    # rupee of dirty value.
    assert thirty_year["haircut"] > ten_year["haircut"]
    assert (
        thirty_year["post_haircut_value"] / thirty_year["dirty_value"]
        < ten_year["post_haircut_value"] / ten_year["dirty_value"]
    )


def test_accrued_interest_matches_the_bond_math():
    """The repo valuation must not have its own idea of accrued interest."""
    valued = repo.revalue(position())
    period_start, _ = bonds.coupon_schedule(AS_OF, NEXT_IP, MATURITY, 2)
    assert valued["accrued_interest"] == pytest.approx(
        bonds.accrued_interest(COUPON, period_start, AS_OF)
    )


# --- margin calls ---------------------------------------------------------


def fully_collateralised() -> tuple[dict, dict]:
    """A position priced so that day one is exactly flat, as the seeder does."""
    pos = position()
    valued = repo.revalue(pos)
    return pos | {"purchase_price": valued["post_haircut_value"]}, valued


def test_no_call_on_a_fully_collateralised_position():
    pos, valued = fully_collateralised()
    assert repo.margin_call(pos, valued) is None


def test_no_call_inside_the_threshold():
    """The threshold is a percentage of exposure, not a flat rupee amount.

    A flat amount cannot serve a book running from 5 crore to 50 crore: one lakh
    is 0.2% of the smallest position and 0.02% of the largest, so it fires on any
    tick. This asserts a shortfall just inside the percentage is ignored.
    """
    pos, valued = fully_collateralised()
    exposure = pos["purchase_price"]
    just_inside = exposure * (repo.THRESHOLD_PCT_OF_EXPOSURE / 100.0) * 0.9

    assert repo.margin_call(pos, valued | {"post_haircut_value": exposure - just_inside}) is None


def test_call_raised_once_the_threshold_is_breached():
    pos, valued = fully_collateralised()
    exposure = pos["purchase_price"]
    breach = exposure * (repo.THRESHOLD_PCT_OF_EXPOSURE / 100.0) * 2

    call = repo.margin_call(pos, valued | {"post_haircut_value": exposure - breach})
    assert call is not None
    assert call["shortfall"] == pytest.approx(breach)
    assert call["exposure"] == pytest.approx(exposure)
    assert call["status"] == "OPEN"


def test_call_amount_rounds_up_to_the_minimum_transfer():
    pos, valued = fully_collateralised()
    exposure = pos["purchase_price"]
    shortfall = repo.MINIMUM_TRANSFER_INR * 2.3

    call = repo.margin_call(pos, valued | {"post_haircut_value": exposure - shortfall})
    assert call["call_amount"] == repo.MINIMUM_TRANSFER_INR * 3
    # Never for less than the shortfall itself.
    assert call["call_amount"] >= call["shortfall"]
    assert call["call_amount"] % repo.MINIMUM_TRANSFER_INR == 0


def test_exposure_accretes_at_the_repo_rate_on_act_365():
    """repurchase = purchase x (1 + rate/100 x days/365). ACT/365, not 30/360."""
    pos, valued = fully_collateralised()
    thirty_days_on = pos | {"as_of_date": pos["start_date"] + dt.timedelta(days=30)}

    call = repo.margin_call(thirty_days_on, valued | {"post_haircut_value": 0.0})
    expected = pos["purchase_price"] * (1 + pos["repo_rate_pct"] / 100 * 30 / 365)
    assert call["exposure"] == pytest.approx(expected)


def test_accretion_grows_the_exposure_over_time():
    pos, valued = fully_collateralised()
    flat = valued | {"post_haircut_value": 0.0}

    day_1 = repo.margin_call(pos | {"as_of_date": pos["start_date"]}, flat)
    day_90 = repo.margin_call(pos | {"as_of_date": pos["start_date"] + dt.timedelta(days=90)}, flat)
    assert day_90["exposure"] > day_1["exposure"]


# --- the CCIL deadline ---------------------------------------------------


@pytest.mark.parametrize(
    ("as_of", "expected_day"),
    [
        ((2026, 9, 16), (2026, 9, 17)),  # Wednesday -> Thursday
        ((2026, 9, 18), (2026, 9, 21)),  # Friday -> Monday
        ((2026, 9, 19), (2026, 9, 21)),  # Saturday -> Monday
        ((2026, 9, 20), (2026, 9, 21)),  # Sunday -> Monday
    ],
)
def test_margin_call_is_due_at_9am_the_next_business_day(as_of, expected_day):
    """CCIL's TREPS rule: a shortfall must be met by 09:00 next business day."""
    due = repo.next_business_day_9am(dt.date(*as_of))
    assert due.date() == dt.date(*expected_day)
    assert due.hour == 9
    assert due.date().weekday() < 5
