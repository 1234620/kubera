"""Fetch NSE files, land the raw bytes, parse them, report what came back.

    python -m slbdesk.ingest --days 30
    python -m slbdesk.ingest --date 2026-09-18

Stage 1 lands and parses. Loading into Postgres arrives in stage 2, which is why
this prints counts rather than writing rows.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import sys

from slbdesk.ingest import parse
from slbdesk.ingest.nse import NseClient, trading_dates

# (source file, dataset, parser). Several datasets may come from one file: the
# VaR file carries both the margin rates and the full series universe.
DAILY = [
    ("slb_bhavcopy", "slb_quote_daily", lambda payload, _: parse.parse_slb_bhavcopy(payload)),
    ("slb_open_positions", "slb_open_position", parse.parse_slb_open_positions),
    ("slb_eligibility", "slb_eligibility", parse.parse_slb_eligibility),
    ("slb_foreclosure", "slb_foreclosure", lambda payload, _: parse.parse_slb_foreclosure(payload)),
    ("slb_var", "slb_var_margin", parse.parse_slb_var),
    ("slb_var", "slb_series", parse.parse_slb_series_universe),
    ("cash_bhavcopy", "cash_quote_daily", lambda payload, _: parse.parse_cash_bhavcopy(payload)),
    ("gsec_trades", "gsec_trade_daily", parse.parse_gsec_trades),
]

# A security master, not a time series: fetched once per run for the newest date.
MASTERS = [("gsec_master", "gsec", lambda payload, _: parse.parse_gsec_master(payload))]


def is_trading_day(payload: bytes, date: dt.date) -> bool:
    """Whether the cash bhavcopy NSE served is actually for the date we asked for.

    On an exchange holiday NSE does not 404 the cash bhavcopy -- it serves a
    *stale* copy under the holiday's filename. `sec_bhavdata_full_14092026.csv`
    returns 11-Sep-2026 rows. Without this check a backfill loads one day's prices
    under several dates, which would quietly corrupt every fee annualisation and
    every mark. So trust the date inside the file, never the date in its name.
    """
    rows = parse.parse_cash_bhavcopy(payload)
    return bool(rows) and rows[0]["trade_date"] == date


def ingest_date(
    client: NseClient,
    date: dt.date,
    datasets: list,
    counts: collections.Counter,
    force: bool,
) -> list[str] | None:
    """Land and parse one date.

    Returns the sources NSE had no file for, or None if the date turned out not to
    be a trading day.
    """
    missing: list[str] = []
    payloads: dict[str, bytes] = {}

    for source in dict.fromkeys(source for source, _, _ in datasets):
        path = client.land(source, date, force=force)
        if path is None:
            missing.append(source)
            continue

        payload = path.read_bytes()
        if source == "cash_bhavcopy" and not is_trading_day(payload, date):
            return None
        payloads[source] = payload

    for source, dataset, parser in datasets:
        if source in payloads:
            counts[dataset] += len(parser(payloads[source], date))

    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="slbdesk.ingest", description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=30, help="trading days to backfill")
    group.add_argument("--date", help="a single date, YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="re-download files already landed")
    args = ap.parse_args(argv)

    counts: collections.Counter = collections.Counter()
    skipped: list[dt.date] = []

    with NseClient() as client:
        if args.date:
            dates = [dt.date.fromisoformat(args.date)]
        else:
            latest = client.latest_trading_date()
            print(f"latest published trading date: {latest}")
            dates = trading_dates(latest, args.days)

        for date in dates:
            missing = ingest_date(client, date, DAILY, counts, args.force)
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
            ingest_date(client, max(dates), MASTERS, counts, args.force)

    print(f"\nparsed {len(dates) - len(skipped)} of {len(dates)} dates")
    for dataset, count in sorted(counts.items()):
        print(f"  {dataset:22s} {count:>9,} rows")

    return 0 if counts else 1


if __name__ == "__main__":
    sys.exit(main())
