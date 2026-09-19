"""Key-rate durations: curve risk instead of single-yield risk.

A single DV01 off a bond's own YTM answers "what if the whole curve moves 1bp in
parallel". Real curves do not move in parallel -- they steepen, flatten and
twist -- and a book that is DV01-neutral overall can still be badly exposed to a
steepening. Key-rate durations decompose the risk by where on the curve the move
happens.

The construction: shock the zero curve at one key tenor and let the shock decay
linearly to zero at the neighbouring key tenors -- a "tent". Reprice, take the
difference. Sum the buckets and you get the parallel DV01 back, which is the
check that the decomposition is complete.

    key tenor k       shocked by 1bp
    neighbours        shocked by 0
    in between        linearly interpolated

The tents partition the curve, so a parallel shift is exactly the sum of all
tents. That property is asserted in tests/test_curves.py, and it is what makes
these numbers additive across a portfolio.
"""

from __future__ import annotations

from collections.abc import Callable

# Money-market through long-bond. These are the tenors an Indian G-Sec book is
# actually exposed at, and they match the funding curve's published grid at the
# short end so the two can be read together.
KEY_TENORS_YEARS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0)

ONE_BP = 0.0001


def tent_weight(years: float, key_index: int, keys: tuple[float, ...] = KEY_TENORS_YEARS) -> float:
    """How much of a shock at `keys[key_index]` reaches maturity `years`.

    1 at the key tenor, 0 at each neighbour, linear between. Outside the first
    and last key the weight is held at 1, so the ends of the curve are not
    silently unhedged -- a 40-year bond must still register against the 30-year
    bucket.
    """
    key = keys[key_index]
    lower = keys[key_index - 1] if key_index > 0 else None
    upper = keys[key_index + 1] if key_index + 1 < len(keys) else None

    if years == key:
        return 1.0
    if years < key:
        if lower is None:
            return 1.0
        return max(0.0, (years - lower) / (key - lower))
    if upper is None:
        return 1.0
    return max(0.0, (upper - years) / (upper - key))


def present_value(
    cash_flows: list[tuple[float, float]],
    discount: Callable[[float], float],
) -> float:
    return sum(amount * discount(years) for years, amount in cash_flows)


def key_rate_dv01(
    cash_flows: list[tuple[float, float]],
    zero_rate: Callable[[float], float],
    keys: tuple[float, ...] = KEY_TENORS_YEARS,
) -> dict[float, float]:
    """DV01 per key tenor, in price units per 1bp, for one set of cash flows.

    `zero_rate(years)` returns a continuously-compounded rate in percent, so it
    takes either a bootstrapped curve or an NSS fit -- both expose exactly that.
    Signed so a positive number means the position LOSES value when that part of
    the curve rises, which is the convention a risk report reads.
    """
    import math

    def discount(shift: dict[int, float] | None = None) -> Callable[[float], float]:
        def factor(years: float) -> float:
            rate = zero_rate(years) / 100.0
            if shift:
                for key_index, size in shift.items():
                    rate += size * tent_weight(years, key_index, keys)
            return math.exp(-rate * years)

        return factor

    base = present_value(cash_flows, discount())

    out: dict[float, float] = {}
    for key_index, key in enumerate(keys):
        bumped = present_value(cash_flows, discount({key_index: ONE_BP}))
        out[key] = base - bumped
    return out


def simultaneous_shock_dv01(
    cash_flows: list[tuple[float, float]],
    zero_rate: Callable[[float], float],
    keys: tuple[float, ...] = KEY_TENORS_YEARS,
) -> float:
    """DV01 from shocking EVERY key tenor at once by 1bp.

    Because the tents sum to 1 at every maturity, shocking them all together is
    exactly a parallel shift -- so this must equal `parallel_dv01` to machine
    precision. That is the identity which proves the tents partition the curve.

    It is NOT the same as summing the individual bucket DV01s: those are computed
    one shock at a time and repricing is non-linear in the rate, so their sum
    matches the parallel figure only to first order. The gap is the curve
    equivalent of the difference between duration and duration-plus-convexity,
    and it is small (a few parts in 10^5) but real.
    """
    import math

    def discount(shift: float) -> Callable[[float], float]:
        def factor(years: float) -> float:
            rate = zero_rate(years) / 100.0
            weight = sum(tent_weight(years, i, keys) for i in range(len(keys)))
            return math.exp(-(rate + shift * weight) * years)

        return factor

    return present_value(cash_flows, discount(0.0)) - present_value(cash_flows, discount(ONE_BP))


def parallel_dv01(
    cash_flows: list[tuple[float, float]],
    zero_rate: Callable[[float], float],
) -> float:
    """DV01 from a genuine parallel 1bp shift, for comparison with the bucket sum."""
    import math

    def discount(shift: float) -> Callable[[float], float]:
        return lambda years: math.exp(-(zero_rate(years) / 100.0 + shift) * years)

    return present_value(cash_flows, discount(0.0)) - present_value(cash_flows, discount(ONE_BP))
