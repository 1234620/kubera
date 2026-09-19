"""Day count conventions.

Getting these wrong is a silent error: the number looks plausible and is wrong by
a few basis points. So each function names its convention and nothing here takes
a default from a library.

Conventions this repo commits to (rules/FINANCE.md):
  * Indian G-Sec accrued interest -- 30/360 (RBI/FIMMDA)
  * Rupee money market, repo, SLB fee accrual -- ACT/365
"""

from __future__ import annotations

import datetime as dt


def days_30_360(start: dt.date, end: dt.date) -> int:
    """Days between two dates on the 30/360 convention.

    Twelve 30-day months, a 360-day year. The two end-of-month adjustments are
    the part people leave out: a coupon paid on the 31st would otherwise accrue a
    day more than one paid on the 30th.
    """
    d1, d2 = start.day, end.day
    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30

    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)


def year_fraction_act365(start: dt.date, end: dt.date) -> float:
    """Actual calendar days over 365. Rupee money-market convention."""
    return (end - start).days / 365.0


def add_months(date: dt.date, months: int) -> dt.date:
    """Shift by whole months, clamping the day to the target month's length.

    31-Aug plus six months is 28-Feb (or 29-Feb in a leap year), not an error.
    Used to walk a coupon schedule forward.
    """
    total = date.month - 1 + months
    year = date.year + total // 12
    month = total % 12 + 1

    day = date.day
    while day > 28:
        try:
            return dt.date(year, month, day)
        except ValueError:
            day -= 1
    return dt.date(year, month, day)
