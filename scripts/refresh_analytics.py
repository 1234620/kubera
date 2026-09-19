"""Refresh the derived analytics tables, then validate them.

    python scripts/refresh_analytics.py

Exits non-zero if any validation query returns rows, so a broken number fails the
build rather than reaching the dashboard.
"""

from __future__ import annotations

import sys
import time

from slbdesk import db, queries
from slbdesk import repo as repo_analytics
from slbdesk.analytics import ftp, gsec


def main() -> int:
    with db.connect() as conn:
        for name in queries.REFRESH_ORDER:
            started = time.monotonic()
            affected = queries.run(conn, name)
            conn.commit()
            print(f"  {name:22s} {affected:>9,} rows  {time.monotonic() - started:5.1f}s")

        # Not SQL: the YTM solve is Newton-Raphson (ADR 0001).
        started = time.monotonic()
        affected = gsec.refresh(conn)
        conn.commit()
        print(f"  {'gsec analytics':22s} {affected:>9,} rows  {time.monotonic() - started:5.1f}s")

        started = time.monotonic()
        valued, calls, unvaluable = repo_analytics.refresh(conn)
        conn.commit()
        print(f"  {'repo collateral':22s} {valued:>9,} rows  {time.monotonic() - started:5.1f}s")
        print(f"  {'margin calls':22s} {calls:>9,} rows")
        if unvaluable:
            print(f"  WARNING: {unvaluable} repo(s) have collateral that never priced")

        started = time.monotonic()
        curve, pnl, charges, desks = ftp.refresh(conn)
        conn.commit()
        print(f"  {'funding curve':22s} {curve:>9,} rows  {time.monotonic() - started:5.1f}s")
        print(f"  {'position p&l':22s} {pnl:>9,} rows")
        print(f"  {'ftp charges':22s} {charges:>9,} rows")
        print(f"  {'desk p&l':22s} {desks:>9,} rows")

        failures = {name: rows for name, rows in queries.validate(conn).items() if rows}

    print()
    for name in queries.VALIDATIONS:
        if name in failures:
            rows = failures[name]
            print(f"FAIL {name}: {len(rows)} offending row(s)")
            for row in rows[:5]:
                print(f"       {row}")
            if len(rows) > 5:
                print(f"       ... and {len(rows) - 5} more")
        else:
            print(f"ok   {name}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
