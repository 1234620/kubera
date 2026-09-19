"""Nelson-Siegel-Svensson: a parametric zero-coupon curve.

Why this and not only a bootstrap. A bootstrap reprices every input exactly but
says nothing between its nodes, needs the inputs ordered and complete, and
propagates one bad print into every longer tenor. NSS is the answer when bond
observations are scattered and sparse -- which is exactly what reaches the WDM
file, where a typical day carries four or five G-Sec prints. It is also the
standard tool: central banks publish curves with it, and its parameters are
interpretable rather than being a list of numbers.

    z(t) = b0
         + b1 * (1 - exp(-t/T1)) / (t/T1)
         + b2 * [(1 - exp(-t/T1)) / (t/T1) - exp(-t/T1)]
         + b3 * [(1 - exp(-t/T2)) / (t/T2) - exp(-t/T2)]

    b0  long-run level      -- where the curve goes as t grows
    b1  short-end spread    -- z(0) = b0 + b1, so this is slope
    b2  first curvature     -- a hump peaking near T1
    b3  second curvature    -- a second hump near T2, which is what Svensson
                               added to Nelson-Siegel so a curve can bend twice

FITTING, and the trick that keeps this dependency-free: for FIXED T1 and T2 the
model is *linear* in b0..b3. So the fit separates -- grid-search the two decay
parameters, and at each candidate solve the four betas by ordinary least squares
through a 4x4 normal equation. No optimiser, no scipy, no new dependency, and
the linear step is exact rather than iterative.

This fits OBSERVED YIELDS, not prices. Fitting prices is the more correct
objective because it weights by cash-flow sensitivity, and it is the honest
refinement to name; fitting yields is common practice, far simpler, and adequate
when the residuals come out at a basis point or two. `rmse_bps` is reported so
the quality is visible rather than assumed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Grid for the two decay parameters, in years. T1 controls the short hump and T2
# the long one; T2 > T1 is enforced so the two terms cannot swap roles and make
# the fitted parameters uninterpretable.
TAU1_GRID = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
TAU2_GRID = (3.0, 5.0, 7.5, 10.0, 12.5, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0)


@dataclass(frozen=True)
class NSSFit:
    beta0: float
    beta1: float
    beta2: float
    beta3: float
    tau1: float
    tau2: float
    rmse_bps: float
    observations: int

    def zero_rate(self, years: float) -> float:
        """Continuously-compounded zero rate at `years`, in percent."""
        return _zero_rate(self.betas, self.tau1, self.tau2, years)

    def discount_factor(self, years: float) -> float:
        """df(t) = exp(-z(t) * t), with z continuously compounded."""
        return math.exp(-self.zero_rate(years) / 100.0 * years)

    def forward_rate(self, start_years: float, end_years: float) -> float:
        """Continuously-compounded forward between two tenors, in percent.

        f = -ln(df(t2)/df(t1)) / (t2 - t1). This is the no-arbitrage forward: the
        rate that makes rolling short equal to going long.
        """
        if end_years <= start_years:
            raise ValueError("end_years must exceed start_years")
        ratio = self.discount_factor(end_years) / self.discount_factor(start_years)
        return -math.log(ratio) / (end_years - start_years) * 100.0

    @property
    def betas(self) -> tuple[float, float, float, float]:
        return (self.beta0, self.beta1, self.beta2, self.beta3)

    @property
    def short_rate(self) -> float:
        """The fitted instantaneous rate, z(0+) = b0 + b1."""
        return self.beta0 + self.beta1

    def describe(self) -> str:
        """The curve in words, which is the point of a parametric form."""
        slope = self.beta0 - self.short_rate
        shape = "upward" if slope > 0.1 else "inverted" if slope < -0.1 else "flat"
        return (
            f"{shape}: short {self.short_rate:.2f}% to long {self.beta0:.2f}%, "
            f"curvature {self.beta2:+.2f}/{self.beta3:+.2f}, "
            f"fit {self.rmse_bps:.1f}bp over {self.observations} bonds"
        )


def _basis(tau1: float, tau2: float, years: float) -> tuple[float, float, float, float]:
    """The four NSS loadings at one maturity.

    At t -> 0 the level loading is 1 and the slope loading also tends to 1, while
    both curvature loadings tend to 0, which is why z(0) = b0 + b1. Guarding the
    limit matters: `(1 - exp(-x))/x` is 0/0 at x = 0.
    """
    if years <= 0:
        return (1.0, 1.0, 0.0, 0.0)

    x1 = years / tau1
    x2 = years / tau2
    decay1 = math.exp(-x1)
    decay2 = math.exp(-x2)

    slope = (1 - decay1) / x1
    return (1.0, slope, slope - decay1, (1 - decay2) / x2 - decay2)


def _zero_rate(
    betas: tuple[float, float, float, float], tau1: float, tau2: float, years: float
) -> float:
    loadings = _basis(tau1, tau2, years)
    return sum(beta * loading for beta, loading in zip(betas, loadings, strict=True))


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. None if singular.

    Four unknowns, so an explicit solve is a dozen lines and avoids pulling in a
    linear-algebra dependency for one 4x4 system.
    """
    size = len(rhs)
    augmented = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]

        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            for col in range(column, size + 1):
                augmented[row][col] -= factor * augmented[column][col]

    solution = [0.0] * size
    for row in reversed(range(size)):
        total = augmented[row][size] - sum(
            augmented[row][col] * solution[col] for col in range(row + 1, size)
        )
        solution[row] = total / augmented[row][row]
    return solution


def _fit_betas(
    observations: list[tuple[float, float]], tau1: float, tau2: float
) -> tuple[list[float], float] | None:
    """Least-squares betas for fixed decay parameters, and the sum of squares.

    Builds and solves the normal equations X'X b = X'y directly. With four
    columns and a handful of rows that is cheaper and clearer than a QR, and the
    conditioning is fine because the loadings are all O(1).
    """
    gram = [[0.0] * 4 for _ in range(4)]
    moment = [0.0] * 4

    for years, yield_pct in observations:
        loadings = _basis(tau1, tau2, years)
        for i in range(4):
            moment[i] += loadings[i] * yield_pct
            for j in range(4):
                gram[i][j] += loadings[i] * loadings[j]

    betas = _solve(gram, moment)
    if betas is None:
        return None

    residual = sum(
        (_zero_rate(tuple(betas), tau1, tau2, years) - yield_pct) ** 2
        for years, yield_pct in observations
    )
    return betas, residual


def fit(observations: list[tuple[float, float]]) -> NSSFit:
    """Fit NSS to (maturity_years, yield_pct) pairs.

    Separable least squares: grid the two decay parameters, solve the betas
    exactly at each candidate, keep the best, then refine the grid locally around
    it. Needs at least four observations for four betas to be identified -- with
    fewer the system is underdetermined and a "fit" would be fiction.
    """
    usable = [(years, yield_pct) for years, yield_pct in observations if years > 0]
    if len(usable) < 4:
        raise ValueError(f"NSS needs at least 4 observations, got {len(usable)}")

    best: tuple[float, list[float], float, float] | None = None
    for tau1 in TAU1_GRID:
        for tau2 in TAU2_GRID:
            if tau2 <= tau1:
                continue
            solved = _fit_betas(usable, tau1, tau2)
            if solved is None:
                continue
            betas, residual = solved
            if best is None or residual < best[0]:
                best = (residual, betas, tau1, tau2)

    if best is None:
        raise ValueError("no NSS fit converged for these observations")

    # Local refinement: halve the spacing around the winning pair twice. The
    # coarse grid finds the basin; this sharpens it without a real optimiser.
    residual, betas, tau1, tau2 = best
    for step in (0.5, 0.25):
        for candidate1 in (tau1 * (1 - step), tau1, tau1 * (1 + step)):
            for candidate2 in (tau2 * (1 - step), tau2, tau2 * (1 + step)):
                if candidate2 <= candidate1 or candidate1 <= 0:
                    continue
                solved = _fit_betas(usable, candidate1, candidate2)
                if solved and solved[1] < residual:
                    residual, betas = solved[1], solved[0]
                    tau1, tau2 = candidate1, candidate2

    rmse_bps = math.sqrt(residual / len(usable)) * 100
    return NSSFit(*betas, tau1=tau1, tau2=tau2, rmse_bps=rmse_bps, observations=len(usable))
