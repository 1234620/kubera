"""Parsers for the NSE daily files. One function per file, returning list[dict].

Plain dicts rather than dataclasses: stage 2 inserts them straight into SQL, so a
model layer in between would be an abstraction with one consumer.

Layouts and the verification behind them: docs/05-data-sources.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile

CONTRACT_SETS = {"REGULAR": "01-12", "NON_FORECLOSING": "X1-XD"}


def _date(value: str) -> dt.date:
    """NSE writes both '18-SEP-2026' and '18-Sep-2026'; strptime's %b is case-insensitive."""
    return dt.datetime.strptime(value.strip(), "%d-%b-%Y").date()


def _rows(payload: bytes | str, skip_header: bool = False) -> list[list[str]]:
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text)) if row]
    return rows[1:] if skip_header else rows


def classify_series(series_code: str) -> str:
    """Which of the three contract sets a series belongs to (docs/01 §3).

    `01`-`12` are force-foreclosed on AGM/EGM; `X1`-`X9`/`XO`/`XN`/`XD` are not.
    Everything else is one of the 48 rollover series, whose code encodes both a
    source and a target month.
    """
    if series_code.isdigit() and 1 <= int(series_code) <= 12:
        return "REGULAR"
    if series_code[0] == "X" and series_code[1:] in set("123456789OND"):
        return "NON_FORECLOSING"
    return "ROLLOVER"


def parse_slb_bhavcopy(payload: bytes) -> list[dict]:
    """SLB lending-fee OHLC, one row per (symbol, series) that traded.

    17 comma-separated fields, no header, prices zero-padded. Fees are rupees per
    share for the whole contract period -- not annualised. Column 11 is unused in
    every file observed, and column 6 is the previous day's close (verified in
    tests/test_fixtures.py).
    """
    out = []
    for row in _rows(payload):
        traded_qty = int(row[11])
        traded_value = float(row[12])
        out.append(
            {
                "trade_date": _date(row[15]),
                "symbol": row[1],
                "series_code": row[2],
                "contract_set": classify_series(row[2]),
                "security_name": row[0],
                "reverse_leg_date": _date(row[3]),
                "market_type": row[4],
                "prev_close_fee": float(row[5]),
                "open_fee": float(row[6]),
                "high_fee": float(row[7]),
                "low_fee": float(row[8]),
                "close_fee": float(row[9]),
                "traded_qty": traded_qty,
                "traded_value_inr": traded_value,
                "year_high_fee": float(row[13]),
                "year_low_fee": float(row[14]),
                "num_trades": int(row[16]),
                # Value-weighted average fee: the fee actually dealt, which the
                # file does not give directly.
                "vwaf": traded_value / traded_qty,
            }
        )
    return out


def parse_slb_open_positions(payload: bytes, trade_date: dt.date) -> list[dict]:
    """End-of-day open interest per (symbol, series). The file carries no date."""
    return [
        {
            "trade_date": trade_date,
            "symbol": row[1],
            "series_code": row[2],
            "contract_set": classify_series(row[2]),
            "outstanding_qty": int(row[3]),
        }
        for row in _rows(payload, skip_header=True)
    ]


def parse_slb_eligibility(payload: bytes, as_of_date: dt.date) -> list[dict]:
    """Per-series normal/recall/repay eligibility. 'E' eligible, 'D' disabled.

    recall_eligible drives the FTP contingent liquidity charge (docs/07 §9): a
    position that cannot be unwound early consumes liquidity a recallable one does
    not, and this file is what tells us which is which.
    """
    return [
        {
            "as_of_date": as_of_date,
            "symbol": row[1],
            "series_code": row[2],
            "normal_eligible": row[3] == "E",
            "recall_eligible": row[4] == "E",
            "repay_eligible": row[5] == "E",
            "market_type": row[6],
        }
        for row in _rows(payload, skip_header=True)
    ]


def parse_slb_foreclosure(payload: bytes) -> list[dict]:
    """Corporate actions forcing an unwind. Every row is a P&L event, not reference data.

    The description field is a padded short code followed by the long text
    ('BONUS 1:1    BONUS 1:1'); both are kept.

    The file ends with a legend block of short rows explaining the SERIES**
    column -- OLD is series 01-12, NEW is X1-XD, ALL is both -- which is NSE
    confirming the contract-set split in classify_series. Rows that are not the
    full 13 fields are that legend, and are skipped.
    """
    out = []
    for row in _rows(payload, skip_header=True):
        if len(row) != 13:
            continue
        parts = [p for p in row[12].split("  ") if p.strip()]
        out.append(
            {
                "symbol": row[0],
                "series_scope": row[1],
                "isin": row[2],
                "announcement_date": _date(row[3]),
                "record_date": _date(row[5]),
                "ex_date": _date(row[6]),
                "foreclosure_date": _date(row[7]),
                "shut_period_start": _date(row[8]) if row[8] else None,
                "shut_period_end": _date(row[9]) if row[9] else None,
                "next_trade_date": _date(row[10]) if row[10] else None,
                "foreclosure_settlement_no": row[11],
                "action_code": parts[0].strip(),
                "action_desc": parts[-1].strip(),
            }
        )
    return out


def parse_slb_var(payload: bytes, as_of_date: dt.date) -> list[dict]:
    """Begin-day margin rates -- the equity haircut input (docs/07 §8).

    Record `10` is a header, record `20` is per security. Column 3 is the *series
    code*, not a serial number, so the file repeats every security across all 73
    series; the total margin is identical across them (verified), so this
    de-duplicates to one row per symbol.

    Exact identity across all rows: applicable_var + elm + additional = total.
    Note applicable_var is floored at a regulatory minimum and so can exceed the
    computed VaR (ABBOTINDIA: computed 8.21, applicable 9.00).
    """
    seen: dict[str, dict] = {}
    for row in _rows(payload):
        if row[0] != "20":
            continue
        symbol = row[1]
        if symbol in seen:
            continue

        applicable_var, elm, additional, total = (float(row[i]) for i in (6, 7, 8, 9))
        if abs(applicable_var + elm + additional - total) > 1e-6:
            raise ValueError(f"VaR layout changed: {symbol} margins do not sum to {total}")

        seen[symbol] = {
            "as_of_date": as_of_date,
            "symbol": symbol,
            "isin": row[3].strip(),
            "var_pct": float(row[4]),
            "applicable_var_pct": applicable_var,
            "elm_pct": elm,
            "additional_margin_pct": additional,
            "total_margin_pct": total,
        }
    return list(seen.values())


def parse_slb_series_universe(payload: bytes, as_of_date: dt.date) -> list[dict]:
    """The full series universe, a by-product of the VaR file.

    The bhavcopy only shows series that traded and the eligibility file only shows
    those currently live, so this is the one file listing all 73 -- 12 regular,
    12 non-foreclosing and 48 rollover, per NCL circular 36465.
    """
    codes = {row[2] for row in _rows(payload) if row[0] == "20"}
    return [
        {"as_of_date": as_of_date, "series_code": code, "contract_set": classify_series(code)}
        for code in sorted(codes)
    ]


def parse_cash_bhavcopy(payload: bytes) -> list[dict]:
    """Cash-market close and volume, all series.

    Needed because the SLB files have no underlying price: annualising a lending
    fee requires the share price (docs/07 §1), and days-to-cover requires volume.
    Turnover is published in lakhs (1 lakh = 100,000) and converted here, because
    a unit slip there is a factor of 10^5.

    Deliberately *not* filtered to `EQ`. A security under surveillance trades in
    the `BE` segment while staying SLB-eligible -- HFCL is that case on this
    date -- so filtering to EQ silently loses the price for exactly the names most
    likely to be special. `series` is kept so the caller can prefer EQ and fall
    back; it is part of the natural key.
    """
    out = []
    for row in _rows(payload, skip_header=True):
        out.append(
            {
                "trade_date": _date(row[2]),
                "symbol": row[0],
                "series": row[1],
                "prev_close": float(row[3]),
                "close_price": float(row[8]),
                "avg_price": float(row[9]),
                "traded_qty": int(row[10]),
                "turnover_inr": float(row[11]) * 100_000,
                "num_trades": int(row[12]),
                "delivery_qty": int(row[13]) if row[13] not in ("", "-") else None,
                "delivery_pct": float(row[14]) if row[14] not in ("", "-") else None,
            }
        )
    return out


def parse_gsec_master(payload: bytes) -> list[dict]:
    """Central-government paper from the WDM security list: SECTYPE in (GS, TB).

    `Last IP Dt` / `Next IP Dt` are given, so accrued interest reads the real
    coupon period instead of assuming a clean six-month offset -- which is what
    makes stub periods correct (docs/03 §1). For T-bills the coupon columns are
    empty and ISSUE_NAME holds a maturity code rather than a rate.
    """
    freq = {"Half Yearly": 2, "Yearly": 1, "Quarterly": 4, "Monthly": 12}
    out = []
    for row in _rows(payload, skip_header=True):
        if row[0] not in ("GS", "TB"):
            continue
        coupon = row[2].rstrip("%")
        out.append(
            {
                "instrument_type": row[0],
                "security_code": row[1],
                "issue_desc": row[3],
                "coupon_pct": float(coupon) if row[2].endswith("%") else None,
                "issue_date": _date(row[4]),
                "maturity_date": _date(row[5]),
                "last_ip_date": _date(row[6]) if row[6] else None,
                "next_ip_date": _date(row[7]) if row[7] else None,
                "coupon_freq": freq.get(row[8]),
                "isin": row[11],
                "status": row[12],
            }
        )
    return out


def parse_gsec_trades(payload: bytes, trade_date: dt.date) -> list[dict]:
    """Daily debt trades, extracted from the `dly*.zip` bundle.

    Prices are clean per 100 face; `Settl Days` is 1 for G-Secs (T+1). Traded
    value is published in crore (1 crore = 10,000,000). `Weighted YTM` is the
    validation target for our own YTM solver (docs/03 §11).
    """
    member = f"trd{trade_date.strftime('%d%m')}_sett.csv"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        inner = archive.read(member)

    out = []
    for row in _rows(inner, skip_header=True):
        out.append(
            {
                "trade_date": _date(row[0]),
                "instrument_type": row[1],
                "security_code": row[2],
                "issue_name": row[3],
                "settl_days": int(row[4]),
                "trade_type": row[5],
                "num_trades": int(row[6]),
                "traded_value_inr": float(row[7]) * 10_000_000,
                "low_price": float(row[8]),
                "high_price": float(row[9]),
                "last_price": float(row[10]),
                "vwap_clean_price": float(row[11]),
                "weighted_ytm_pct": float(row[12]),
            }
        )
    return out
