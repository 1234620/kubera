"""Fetch NSE files, land the raw bytes, parse them, upsert them into Postgres.

    python -m slbdesk.ingest --days 30
    python -m slbdesk.ingest --date 2026-09-18
    python -m slbdesk.ingest --days 5 --no-load     # parse only, no database

Idempotent throughout: an already-landed file is reused, and every load upserts on
the natural key, so re-running a date or the whole backfill changes nothing.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys

from slbdesk import db
from slbdesk.ingest import load, parse
from slbdesk.ingest.nse import NseClient, trading_dates

# (source file, target table, parser). Several tables may come from one file: the
# VaR file carries both the margin rates and the full series universe.
DAILY = [
    ("slb_bhavcopy", "slb_quote_daily", lambda payload, _: parse.parse_slb_bhavcopy(payload)),
    ("slb_open_positions", "slb_open_position", parse.parse_slb_open_positions),
    ("slb_eligibility", "slb_eligibility", parse.parse_slb_eligibility),
    ("slb_foreclosure", "slb_foreclosure", lambda payload, _: parse.parse_slb_foreclosure(payload)),
    ("slb_var", "slb_var_margin", parse.parse_slb_var),
    ("slb_var", "slb_series_universe", parse.parse_slb_series_universe),
    ("cash_bhavcopy", "cash_quote_daily", lambda payload, _: parse.parse_cash_bhavcopy(payload)),
    ("gsec_trades", "gsec_trade_daily", parse.parse_gsec_trades),
]

# A security master, not a time series: fetched once per run for the newest date.
MASTERS = [("gsec_master", "gsec", lambda payload, _: parse.parse_gsec_master(payload))]


def ingest_date(
    client: NseClient,
    date: dt.date,
    datasets: list,
    counts: collections.Counter,
    force: bool,
    conn=None,
) -> list[str] | None:
    """Land, parse and optionally load one date.

    Returns the sources NSE had no file for, or None if the date turned out not to
    be a trading day.
    """
    missing: list[str] = []
    payloads: dict[str, tuple[bytes, str]] = {}

    for source in dict.fromkeys(source for source, _, _ in datasets):
        path = client.land(source, date, force=force)
        if path is None:
            missing.append(source)
            continue

        payload = path.read_bytes()
        if source == "cash_bhavcopy" and not parse.is_trading_day(payload, date):
            return None
        payloads[source] = (payload, path.name)

    for source, table, parser in datasets:
        if source not in payloads:
            continue
        payload, filename = payloads[source]
        rows = parser(payload, date)
        counts[table] += len(rows)
        if conn is not None:
            load.upsert(conn, table, rows, extra={"source_file": filename})

    if conn is not None:
        conn.commit()
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="slbdesk.ingest", description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=30, help="trading days to backfill")
    group.add_argument("--date", help="a single date, YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="re-download files already landed")
    ap.add_argument("--no-load", action="store_true", help="parse only; do not touch the database")
    args = ap.parse_args(argv)

    counts: collections.Counter = collections.Counter()
    skipped: list[dt.date] = []
    conn = None if args.no_load else db.connect()

    with NseClient() as client:
        if args.date:
            dates = [dt.date.fromisoformat(args.date)]
        else:
            latest = client.latest_trading_date()
            print(f"latest published trading date: {latest}")
            dates = trading_dates(latest, args.days)

        for date in dates:
            missing = ingest_date(client, date, DAILY, counts, args.force, conn)
            if missing is None:
                skipped.append(date)
                print(f"{date}  skipped - exchange holiday (NSE served stale files)")
            elif len(missing) == len(dict.fromkeys(s for s, _, _ in DAILY)):
                skipped.append(date)
                print(f"{date}  no files - holiday or not yet published")
            else:
                note = f"  (missing: {', '.join(missing)})" if missing else ""
                print(f"{date}  ok{note}")

        if len(skipped) < len(dates):
            ingest_date(client, max(dates), MASTERS, counts, args.force, conn)

    verb = "parsed" if conn is None else "loaded"
    print(f"\n{verb} {len(dates) - len(skipped)} of {len(dates)} dates")
    for table, count in sorted(counts.items()):
        print(f"  {table:22s} {count:>9,} rows")

    if conn is not None:
        # Reference tables are derived from the market tables, so they run last
        # and load order stops mattering.
        for table, affected in load.derive_reference(conn).items():
            print(f"  {table:22s} {affected:>9,} rows (derived)")
        conn.commit()
        conn.close()

    return 0 if counts else 1


if __name__ == "__main__":
    sys.exit(main())
