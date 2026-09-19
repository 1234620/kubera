"""Bond pricing and risk for Indian G-Secs and T-bills.

Pure functions taking floats and dates. No Bond class with mutable state, no
market-data lookups, no database -- which is what makes every one of these
testable against a hand-computed value (ADR: rules/CODING.md).

Conventions, stated rather than assumed (docs/03-domain-bond-math.md):
  * accrued interest 30/360, the RBI/FIMMDA convention for G-Secs
  * semi-annual coupons, so discounting is at y/2 over 2n periods
  * YTM is solved from the DIRTY price, because that is the settlement amount
  * prices are per 100 face unless a face value is passed
  * DV01 is unsigned per 100 face; the sign is applied at position level, where
    we know whether we are long or short
"""

from __future__ import annotations

import datetime as dt

from slbdesk.bonds.daycount import add_months, days_30_360

FACE = 100.0


def coupon_schedule(
    settlement: dt.date,
    next_ip_date: dt.date,
    maturity_date: dt.date,
    freq: int = 2,
) -> tuple[dt.date, list[dt.date]]:
    """The coupon period we are inside, and every coupon date still to come.

    Returns (period_start, remaining_coupon_dates).

    The security master is a snapshot, so its `Next IP Dt` can sit on either side
    of the settlement date we are pricing for: stale when pricing today, but ahead
    of it when pricing a historical trade date. The schedule therefore rolls in
    BOTH directions until the settlement date genuinely falls inside the period.

    Rolling only forward is a real bug and a quiet one: pricing an August trade
    against a September snapshot puts period_start after settlement, which makes
    accrued interest negative and the dirty price lower than the clean price. The
    `dirty_price >= clean_price` CHECK on gsec_analytics_daily is what caught it.

    Reading the period from the file rather than subtracting six months from
    maturity is what makes stub periods correct.
    """
    months = 12 // freq
    period_end = next_ip_date
    period_start = add_months(period_end, -months)

    while period_end <= settlement:
        period_start = period_end
        period_end = add_months(period_end, months)

    while period_start > settlement:
        period_end = period_start
        period_start = add_months(period_start, -months)

    remaining = []
    date = period_end
    while date < maturity_date:
        remaining.append(date)
        date = add_months(date, months)
    remaining.append(maturity_date)

    return period_start, remaining


def accrued_interest(
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    face: float = FACE,
) -> float:
    """Coupon earned since the last payment, 30/360.

    accrued = coupon_rate/100 x face x days_30_360 / 360

    Consistent with the semi-annual coupon by construction: a full 180-day
    30/360 period gives exactly coupon/2.
    """
    return coupon_pct / 100.0 * face * days_30_360(period_start, settlement) / 360.0


def dirty_from_clean(clean_price: float, accrued: float) -> float:
    """The amount that actually changes hands."""
    return clean_price + accrued


def clean_from_dirty(dirty_price: float, accrued: float) -> float:
    """The quoted price. Excluding accrued is why a quote does not saw-tooth."""
    return dirty_price - accrued


def _discount_terms(
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    remaining: list[dt.date],
    freq: int,
    face: float,
) -> list[tuple[float, float]]:
    """(time in periods, cash flow) for each remaining payment.

    The time to the next coupon is `1 - w`, where w is the fraction of the current
    coupon period already elapsed. Dropping that mid-period shift -- discounting
    as if settlement fell on a coupon date -- is the single most common bond
    pricing error, and it is worth tens of basis points five months into a period.
    """
    period_end = remaining[0]
    elapsed = days_30_360(period_start, settlement)
    full = days_30_360(period_start, period_end)
    w = elapsed / full if full else 0.0

    coupon = coupon_pct / 100.0 * face / freq
    terms = [(k + 1 - w, coupon) for k in range(len(remaining))]
    # Principal repaid with the final coupon.
    terms[-1] = (terms[-1][0], coupon + face)
    return terms


def price_from_ytm(
    ytm_pct: float,
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    remaining: list[dt.date],
    freq: int = 2,
    face: float = FACE,
) -> float:
    """DIRTY price at a given yield. Semi-annual compounding for freq=2."""
    i = ytm_pct / 100.0 / freq
    terms = _discount_terms(coupon_pct, period_start, settlement, remaining, freq, face)
    return sum(cash / (1 + i) ** tau for tau, cash in terms)


def risk_measures(
    ytm_pct: float,
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    remaining: list[dt.date],
    freq: int = 2,
    face: float = FACE,
) -> dict[str, float]:
    """Price and the three risk numbers, from one pass over the cash flows.

    Computed as analytic derivatives in period space and then converted to annual
    yield terms, which avoids the several incompatible textbook conventions for
    convexity:

        i        = y / (m x 100),  tau = time in periods
        P        = sum CF (1+i)^-tau
        D_mod    =  (1/(m P)) sum tau CF (1+i)^(-tau-1)
        C        = (1/(m^2 P)) sum tau (tau+1) CF (1+i)^(-tau-2)
        D_mac    = D_mod x (1+i)
        DV01     = P x D_mod x 1e-4

    So dP/P is approximately -D_mod x dy + 0.5 x C x dy^2, with dy in decimal.
    Convexity is positive for a plain bond, so duration alone overstates the loss
    from a yield rise and understates the gain from a fall.
    """
    i = ytm_pct / 100.0 / freq
    terms = _discount_terms(coupon_pct, period_start, settlement, remaining, freq, face)

    price = 0.0
    first = 0.0
    second = 0.0
    for tau, cash in terms:
        discounted = cash / (1 + i) ** tau
        price += discounted
        first += tau * discounted / (1 + i)
        second += tau * (tau + 1) * discounted / (1 + i) ** 2

    modified = first / (freq * price)
    return {
        "dirty_price": price,
        "macaulay_duration": modified * (1 + i),
        "modified_duration": modified,
        "convexity": second / (freq**2 * price),
        "dv01": price * modified * 1e-4,
    }


def solve_ytm(
    dirty_price: float,
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    remaining: list[dt.date],
    freq: int = 2,
    face: float = FACE,
    tolerance: float = 1e-10,
) -> float:
    """Yield to maturity, in percent per annum, solved from the DIRTY price.

    Newton-Raphson, seeded at the current-yield approximation and using the
    analytic derivative dP/dy = -P x D_mod, which risk_measures already computes.
    Price is monotonic and smooth in yield, so this converges in three or four
    steps. Bisection is the fallback for the pathological cases -- a deep
    discount, a near-maturity stub -- not the main path.

    Solving from the clean price instead gives a wrong yield, because the clean
    price is not the amount settled.
    """
    years = max((remaining[-1] - settlement).days / 365.0, 1 / 365.0)
    coupon = coupon_pct / 100.0 * face
    # Current yield plus the straight-line pull to par: close enough to seed.
    guess = (coupon + (face - dirty_price) / years) / dirty_price * 100.0
    guess = min(max(guess, 0.01), 50.0)

    for _ in range(50):
        measures = risk_measures(guess, coupon_pct, period_start, settlement, remaining, freq, face)
        error = measures["dirty_price"] - dirty_price
        if abs(error) < tolerance:
            return guess

        # dP/dy in percent-yield units, so the step lands in the same units.
        slope = -measures["dirty_price"] * measures["modified_duration"] / 100.0
        if slope == 0:
            break
        step = error / slope
        guess = min(max(guess - step, 1e-6), 100.0)

    low, high = 0.0, 100.0
    for _ in range(200):
        mid = (low + high) / 2
        priced = price_from_ytm(mid, coupon_pct, period_start, settlement, remaining, freq, face)
        if abs(priced - dirty_price) < tolerance:
            return mid
        if priced > dirty_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def simple_yield_act365(
    dirty_price: float,
    final_cash_flow: float,
    settlement: dt.date,
    maturity: dt.date,
) -> float:
    """Money-market yield: no compounding, ACT/365.

    y = (CF/P - 1) x 365/days x 100
    """
    days = (maturity - settlement).days
    return (final_cash_flow / dirty_price - 1) * 365.0 / days * 100.0


def quoted_ytm(
    dirty_price: float,
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    remaining: list[dt.date],
    freq: int = 2,
    face: float = FACE,
) -> float:
    """Yield on the convention the market actually quotes.

    Once a bond is inside its FINAL coupon period there is one cash flow left, and
    the market quotes it like a money-market instrument -- simple ACT/360-style
    interest, ACT/365 here -- rather than compounding a single payment.

    This is not a theoretical preference; it was measured. Against NSE's own
    published weighted YTM, compounding every bond left three outliers, all of
    them in their last coupon period. Switching those to the simple convention
    reproduced NSE to 0.02bp or better. See tests/test_bonds.py.

    Everything with two or more coupons left goes to the compounded solver, so
    `solve_ytm` keeps its exact round-trip identity with `price_from_ytm`.
    """
    if len(remaining) == 1:
        final = coupon_pct / 100.0 * face / freq + face
        return simple_yield_act365(dirty_price, final, settlement, remaining[0])

    return solve_ytm(dirty_price, coupon_pct, period_start, settlement, remaining, freq, face)


def dv01_numerical(
    ytm_pct: float,
    coupon_pct: float,
    period_start: dt.date,
    settlement: dt.date,
    remaining: list[dt.date],
    freq: int = 2,
    face: float = FACE,
) -> float:
    """DV01 by repricing at +/-1bp, needing no duration.

    Kept because it is the independent check on the analytic DV01: if the two
    disagree, the duration formula is wrong. Tested to agree to 1e-6.
    """
    args = (coupon_pct, period_start, settlement, remaining, freq, face)
    up = price_from_ytm(ytm_pct + 0.01, *args)
    down = price_from_ytm(ytm_pct - 0.01, *args)
    return (down - up) / 2


# --- T-bills: no coupon, ACT/365 -----------------------------------------


def tbill_price(
    ytm_pct: float, settlement: dt.date, maturity: dt.date, face: float = FACE
) -> float:
    """Discount instrument: price = face / (1 + y x days/365)."""
    days = (maturity - settlement).days
    return face / (1 + ytm_pct / 100.0 * days / 365.0)


def tbill_yield(price: float, settlement: dt.date, maturity: dt.date, face: float = FACE) -> float:
    """Simple ACT/365 yield implied by a discount price."""
    days = (maturity - settlement).days
    return (face - price) / price * (365.0 / days) * 100.0
