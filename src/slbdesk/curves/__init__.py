"""Zero-coupon curve construction and curve risk.

See docs/13-curve-construction.md. The bootstrap is exact at observed tenors;
NSS is the smooth fit that covers the gaps. Both expose `zero_rate(years)` and
`discount_factor(years)`, so everything downstream is curve-agnostic.
"""

from slbdesk.curves.implied_borrow import (
    ImpliedForward,
    breakeven_forward,
    classify,
    curve_forwards,
    forward_fee,
)
from slbdesk.curves.keyrate import (
    KEY_TENORS_YEARS,
    key_rate_dv01,
    parallel_dv01,
    simultaneous_shock_dv01,
    tent_weight,
)
from slbdesk.curves.nss import NSSFit, fit
from slbdesk.curves.zero import ZeroCurve, bill_instrument, bond_instrument, bootstrap

__all__ = [
    "KEY_TENORS_YEARS",
    "ImpliedForward",
    "NSSFit",
    "ZeroCurve",
    "bill_instrument",
    "bond_instrument",
    "bootstrap",
    "breakeven_forward",
    "classify",
    "curve_forwards",
    "fit",
    "forward_fee",
    "key_rate_dv01",
    "parallel_dv01",
    "simultaneous_shock_dv01",
    "tent_weight",
]
