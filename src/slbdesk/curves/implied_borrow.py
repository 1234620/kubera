"""The SLB fee curve as a forward curve.

This is the piece that turns the specialness work from a set of metrics into a
model, and it is the same no-arbitrage argument that gives a forward interest
rate from two zero rates.

A security quotes a borrow fee at several tenors on the same day. Borrowing to
the far date must cost the same as borrowing to the near date and then rolling,
or there is a trade. So the near and far fees IMPLY the fee the market expects
for the period in between:

    (1 + f_near * t_near/365) * (1 + f_fwd * (t_far - t_near)/365)
        = (1 + f_far * t_far/365)

    f_fwd = [(1 + f_far*t_far/365) / (1 + f_near*t_near/365) - 1] * 365/(t_far - t_near)

Simple ACT/365 compounding, because that is how the fee is quoted and accrued
(rules/FINANCE.md). Compounding it would price a product nobody trades.

WHY IT MATTERS. A spot fee says a name is expensive right now. The forward says
whether the market thinks it will STAY expensive, which is the actual trading
question. PIIND on 18-Sep quotes 51.1% p.a. to 15 days and 19.3% to 43 days;
the implied forward over the intervening 28 days is about 2%. The market is
pricing the squeeze to be finished within a fortnight. A desk lending PIIND for
43 days at 19.3% is therefore earning almost all of it in the first two weeks --
and a desk that borrows 43 days because "the fee looks lower" has paid a large
front-loaded cost for a cheap tail.

The mirror of the same algebra is the BREAKEVEN: the forward fee at which
rolling the near contract matches the cost of having gone long outright. Quote
above it and the term trade wins; below and rolling wins.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this the two tenors are too close for the forward to mean anything: a
# small fee difference divided by a few days explodes.
MIN_GAP_DAYS = 5

# A forward this far below the near fee is the market pricing a resolution; this
# far above is it pricing escalation. Declared once, here.
RESOLVING_RATIO = 0.6
WORSENING_RATIO = 1.25


@dataclass(frozen=True)
class ImpliedForward:
    symbol: str
    contract_set: str
    near_tenor_days: int
    far_tenor_days: int
    near_fee_pct: float
    far_fee_pct: float
    implied_forward_pct: float
    signal: str

    @property
    def gap_days(self) -> int:
        return self.far_tenor_days - self.near_tenor_days

    def describe(self) -> str:
        return (
            f"{self.symbol}: {self.near_fee_pct:.1f}% to {self.near_tenor_days}d and "
            f"{self.far_fee_pct:.1f}% to {self.far_tenor_days}d implies "
            f"{self.implied_forward_pct:.1f}% over the {self.gap_days} days between "
            f"({self.signal})"
        )


def forward_fee(
    near_fee_pct: float, near_tenor_days: int, far_fee_pct: float, far_tenor_days: int
) -> float:
    """The implied forward borrow fee, in percent per annum, ACT/365 simple."""
    if far_tenor_days <= near_tenor_days:
        raise ValueError("far tenor must exceed near tenor")

    near_growth = 1 + near_fee_pct / 100.0 * near_tenor_days / 365.0
    far_growth = 1 + far_fee_pct / 100.0 * far_tenor_days / 365.0
    gap = far_tenor_days - near_tenor_days

    return (far_growth / near_growth - 1) * 365.0 / gap * 100.0


def breakeven_forward(
    near_fee_pct: float, near_tenor_days: int, far_fee_pct: float, far_tenor_days: int
) -> float:
    """The forward at which rolling the near contract ties with going long.

    Identical to `forward_fee` -- which is the point, and worth stating rather
    than leaving implicit. The implied forward IS the breakeven; calling it one
    or the other only reflects whether you are pricing or deciding.
    """
    return forward_fee(near_fee_pct, near_tenor_days, far_fee_pct, far_tenor_days)


def classify(near_fee_pct: float, implied_forward_pct: float) -> str:
    """What the forward says about the market's view of the squeeze."""
    if implied_forward_pct < 0:
        # FRONT_LOADED, not "anomalous". A negative forward happens whenever
        # f_far * t_far < f_near * t_near -- a steeply falling fee curve -- and
        # on this data it is 26% of pairs, which is far too many to be errors.
        # It means the entire cost of the borrow sits in the near window and the
        # market expects the tail to be nearly free.
        #
        # And it is NOT an arbitrage, which is the important part. The
        # no-arbitrage forward assumes you can roll the near contract AT the
        # forward. In SLB you cannot: rolling means trading a fresh contract at
        # a new market-determined fee, and NCL facilitates recall, repay and
        # rollover on a best-efforts basis only. So the forward here is an
        # EXPECTATION the market is pricing, not a rate anyone can lock.
        return "FRONT_LOADED"
    if near_fee_pct <= 0:
        return "FLAT"
    ratio = implied_forward_pct / near_fee_pct
    if ratio < RESOLVING_RATIO:
        return "RESOLVING"
    if ratio > WORSENING_RATIO:
        return "WORSENING"
    return "PERSISTENT"


def curve_forwards(quotes: list[dict]) -> list[ImpliedForward]:
    """Consecutive-tenor forwards along one security's fee curve.

    `quotes` are dicts with symbol, contract_set, tenor_days, fee_annualised_pct.

    Grouped by (symbol, contract_set) and NOT across sets: the regular and
    non-foreclosing series are separate curves with different corporate-action
    treatment, so a forward spanning them prices a contract that does not exist.
    This project has hit that trap in the SQL window frame, the specialness
    monotonicity check and the term-structure chart, so it is asserted here too.

    Consecutive pairs only. Every pair would be O(n^2) of mostly redundant
    numbers, and the informative forward is the one between adjacent quoted
    points.
    """
    curves: dict[tuple[str, str], list[dict]] = {}
    for quote in quotes:
        if quote["fee_annualised_pct"] is None or quote["fee_annualised_pct"] <= 0:
            continue
        key = (quote["symbol"], quote["contract_set"])
        curves.setdefault(key, []).append(quote)

    out: list[ImpliedForward] = []
    for (symbol, contract_set), points in curves.items():
        ordered = sorted(points, key=lambda item: item["tenor_days"])

        for near, far in zip(ordered, ordered[1:], strict=False):
            if far["tenor_days"] - near["tenor_days"] < MIN_GAP_DAYS:
                continue

            near_fee = float(near["fee_annualised_pct"])
            far_fee = float(far["fee_annualised_pct"])
            forward = forward_fee(near_fee, near["tenor_days"], far_fee, far["tenor_days"])

            out.append(
                ImpliedForward(
                    symbol=symbol,
                    contract_set=contract_set,
                    near_tenor_days=near["tenor_days"],
                    far_tenor_days=far["tenor_days"],
                    near_fee_pct=near_fee,
                    far_fee_pct=far_fee,
                    implied_forward_pct=forward,
                    signal=classify(near_fee, forward),
                )
            )
    return out
