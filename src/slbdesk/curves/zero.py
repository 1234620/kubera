"""Bootstrap a zero-coupon curve from observable instruments.

The bootstrap is the exact construction: it solves discount factors one
instrument at a time so that each input reprices to its own market price, with
no functional form imposed. That exactness is its whole value and also its whole
weakness -- it says nothing between its nodes, it needs the inputs ordered and
reasonably complete, and one bad print propagates into every longer tenor.

So this project carries both. The bootstrap is the ground truth at the tenors
where something actually traded; NSS (see `nss.py`) is the smooth curve that
covers the gaps and extrapolates. `docs/13-curve-construction.md` compares them.

Order of business, shortest first:

  * a T-bill has ONE cash flow, so its discount factor is read straight off the
    price: df = price / face. This is why the T-bill prints matter so much --
    they pin the short end that the old funding curve was extrapolating flat.
  * a coupon bond's dirty price is the sum of its discounted cash flows. Every
    coupon before its maturity discounts at a factor already solved, so the
    final one is the only unknown and falls out by rearrangement.

Everything here is continuously compounded internally (`z = -ln(df)/t`), because
forwards and interpolation are linear in `ln(df)` and there is then exactly one
compounding convention to keep straight.

The module is `zero`, not `bootstrap`, deliberately: the package re-exports the
`bootstrap` FUNCTION, and a module of the same name would be shadowed by it on
`from slbdesk.curves import bootstrap`. That is a confusing failure to debug, so
the name is avoided rather than worked around.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FACE = 100.0

# A solved node whose zero rate jumps further than this from the previous one is
# a stale or fat-fingered print, and a bootstrap is sequential, so accepting it
# would corrupt every longer tenor.
MAX_NODE_JUMP_PCT = 3.0

# The interpolated coupons depend on the factor being solved, so each instrument
# is a fixed point. It converges in a few passes; the cap is a guard, not a
# budget.
FIXED_POINT_ITERATIONS = 50
FIXED_POINT_TOLERANCE = 1e-14


@dataclass
class ZeroCurve:
    """Discount factors at observed tenors, plus interpolation between them."""

    # (years, discount_factor), strictly increasing in years.
    nodes: list[tuple[float, float]] = field(default_factory=list)
    # Instruments the bootstrap refused, with the reason. Reported, not hidden.
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def discount_factor(self, years: float) -> float:
        """Interpolated df.

        LOG-LINEAR in the discount factor, which is linear in `z*t`. A straight
        line through discount factors is not arbitrage-free between nodes, and a
        straight line through zero rates is not either.

        Beyond the last node the last zero rate is held flat rather than
        extending a slope, because extending a slope invents curve shape nobody
        observed. Use the NSS fit when the gap matters.
        """
        if not self.nodes:
            raise ValueError("empty curve")
        if years <= 0:
            return 1.0

        first_years, first_df = self.nodes[0]
        if years <= first_years:
            # Flat zero rate in to t=0.
            rate = -math.log(first_df) / first_years
            return math.exp(-rate * years)

        last_years, last_df = self.nodes[-1]
        if years >= last_years:
            rate = -math.log(last_df) / last_years
            return math.exp(-rate * years)

        for (left_years, left_df), (right_years, right_df) in zip(
            self.nodes, self.nodes[1:], strict=False
        ):
            if left_years <= years <= right_years:
                weight = (years - left_years) / (right_years - left_years)
                log_df = math.log(left_df) + weight * (math.log(right_df) - math.log(left_df))
                return math.exp(log_df)
        raise AssertionError("unreachable: years is bracketed by the nodes")

    def zero_rate(self, years: float) -> float:
        """Continuously-compounded zero rate in percent: z = -ln(df)/t."""
        if years <= 0:
            raise ValueError("zero rate is undefined at t=0")
        return -math.log(self.discount_factor(years)) / years * 100.0

    def forward_rate(self, start_years: float, end_years: float) -> float:
        """Continuously-compounded forward between two tenors, in percent."""
        if end_years <= start_years:
            raise ValueError("end_years must exceed start_years")
        ratio = self.discount_factor(end_years) / self.discount_factor(start_years)
        return -math.log(ratio) / (end_years - start_years) * 100.0

    def par_rate(self, years: float, frequency: int = 2) -> float:
        """The coupon a bond of this maturity would need to price at par.

        Included because it closes the loop: a par curve derived from the
        bootstrapped zeros should land back on the observed bond yields, and
        that is the check that the bootstrap inverted correctly.
        """
        times = [k / frequency for k in range(1, int(round(years * frequency)) + 1)]
        if not times:
            raise ValueError("maturity is shorter than one coupon period")
        annuity = sum(self.discount_factor(t) for t in times) / frequency
        return (1 - self.discount_factor(times[-1])) / annuity * 100.0


def _solve_final_factor(
    curve: ZeroCurve, flows: list[tuple[float, float]], price: float
) -> float | None:
    """Solve the unknown discount factor at the last cash flow, by iteration.

    A bootstrap is usually described as a closed-form solve: discount the known
    coupons, rearrange for the last factor. That is only true when every earlier
    coupon date already has a node. In practice it does not -- a 5-year bond
    against nodes at 0.5, 1 and 2 years has coupons at 2.5 through 4.5 with
    nothing solved around them, so they have to be INTERPOLATED, and the
    interpolation depends on the very factor being solved.

    So it is a fixed point, not a division. Guess the unknown factor, insert it,
    re-discount the coupons through the interpolator that now spans them, and
    re-solve until it stops moving.

    Skipping this is a quiet error rather than a loud one: the curve looks
    plausible and simply fails to reprice its own inputs. It was caught by
    `test_bootstrap_reprices_every_accepted_instrument`, which asserts the
    bootstrap's one defining property, and the residuals were 0.6 paisa on a
    2-year and 7.8 paisa on a 5-year -- small enough to miss by eye, far too
    large for a curve.

    Convergence is fast (a handful of passes) because an intermediate coupon's
    interpolated factor depends only weakly on the endpoint.
    """
    final_years, final_amount = flows[-1]
    coupons = flows[:-1]

    if not coupons:
        # A discount instrument: one flow, so this really is a division.
        return price / final_amount

    guess = price / final_amount
    for _ in range(FIXED_POINT_ITERATIONS):
        probe = ZeroCurve(nodes=[*curve.nodes, (final_years, guess)])
        earlier = sum(amount * probe.discount_factor(t) for t, amount in coupons)

        residual = price - earlier
        if residual <= 0:
            return None

        updated = residual / final_amount
        if abs(updated - guess) < FIXED_POINT_TOLERANCE:
            return updated
        guess = updated

    return guess


def bootstrap(instruments: list[dict]) -> ZeroCurve:
    """Solve discount factors from instruments sorted by maturity.

    Each instrument is a dict with:
        label       -- for the rejection report
        years       -- time to maturity in years
        dirty_price -- the settlement amount, per 100 face
        cash_flows  -- [(years, amount)], the final one including principal.
                       A single entry means a discount instrument.

    A solved factor is rejected rather than stored when it is non-positive, above
    one, or implies a zero rate that jumps more than 300bp from the previous
    node. Those are the signatures of a stale or fat-fingered print, and the
    bootstrap's sequential nature means accepting one corrupts everything longer.
    """
    curve = ZeroCurve()

    for instrument in sorted(instruments, key=lambda item: item["years"]):
        label = instrument["label"]
        years = instrument["years"]
        price = instrument["dirty_price"]
        flows = sorted(instrument["cash_flows"])

        if years <= 0 or price <= 0 or not flows:
            curve.rejected.append((label, "non-positive maturity, price or cash flows"))
            continue

        final_years, final_amount = flows[-1]
        if curve.nodes and final_years <= curve.nodes[-1][0]:
            curve.rejected.append((label, "maturity duplicates or precedes an existing node"))
            continue

        solved = _solve_final_factor(curve, flows, price)
        if solved is None:
            curve.rejected.append((label, "coupons alone exceed the price"))
            continue
        if not 0 < solved <= 1:
            curve.rejected.append((label, f"implied df {solved:.4f} outside (0, 1]"))
            continue

        rate = -math.log(solved) / final_years * 100.0
        if curve.nodes:
            previous = curve.zero_rate(curve.nodes[-1][0])
            if abs(rate - previous) > MAX_NODE_JUMP_PCT:
                curve.rejected.append(
                    (label, f"zero rate jumps {rate - previous:+.2f}% from the previous node")
                )
                continue

        curve.nodes.append((final_years, solved))

    return curve


def bill_instrument(label: str, price: float, years: float, face: float = FACE) -> dict:
    """A discount instrument: one cash flow, so df = price/face directly."""
    return {
        "label": label,
        "years": years,
        "dirty_price": price,
        "cash_flows": [(years, face)],
    }


def bond_instrument(
    label: str,
    dirty_price: float,
    coupon_pct: float,
    years: float,
    frequency: int = 2,
    face: float = FACE,
) -> dict:
    """A coupon bond, with its schedule laid back from maturity.

    Laying coupons back from maturity rather than forward from today is what
    keeps the final flow exactly on the maturity date; building forward leaves a
    stub at the wrong end and shifts every discount factor slightly.
    """
    coupon = coupon_pct / 100.0 * face / frequency
    step = 1.0 / frequency

    times = []
    remaining = years
    while remaining > 1e-9:
        times.append(remaining)
        remaining -= step
    times.reverse()

    flows = [(t, coupon) for t in times]
    flows[-1] = (times[-1], coupon + face)
    return {
        "label": label,
        "years": years,
        "dirty_price": dirty_price,
        "cash_flows": flows,
    }
