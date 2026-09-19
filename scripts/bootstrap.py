"""Bring a clean database all the way up: migrate, ingest, seed, refresh.

    python scripts/bootstrap.py        # or: make bootstrap

Each step is the same module `make` would call individually, run in the one order
that works: the reference tables are derived from market data, the book is seeded
from real quotes, and the analytics read both. Exits on the first failure rather
than leaving a half-built database that looks populated.
"""

from __future__ import annotations

import os
import sys
import time

DAYS = os.environ.get("INGEST_DAYS", "30")


def step(name: str, run) -> None:
    print(f"\n=== {name}")
    started = time.monotonic()
    code = run()
    elapsed = time.monotonic() - started
    if code:
        print(f"FAILED after {elapsed:.0f}s: {name}")
        sys.exit(code)
    print(f"--- {name} ok ({elapsed:.0f}s)")


def main() -> int:
    import migrate  # noqa: PLC0415 -- sibling script, imported after sys.path is set

    from slbdesk.ingest.__main__ import main as ingest

    step("migrations", migrate.main)
    # The slowest step by far: one request per file per day against a public
    # archive, deliberately paced.
    step(f"ingest ({DAYS} trading days)", lambda: ingest(["--days", DAYS]))

    import refresh_analytics  # noqa: PLC0415
    import seed_book  # noqa: PLC0415

    step("seed synthetic book", seed_book.main)
    step("refresh analytics", refresh_analytics.main)

    print("\nReady. Open http://localhost:8000")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
