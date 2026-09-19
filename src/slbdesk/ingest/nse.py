"""Fetch NSE daily files.

nseindia.com sits behind Akamai: a bare request gets a 403. What works is a
browser User-Agent, a cookie warm-up against an ordinary HTML page, and a
Referer. See docs/05-data-sources.md §1.

Archive URLs are built from date templates rather than the JSON manifest, so a
backfill is one request per file instead of one manifest call per day. A
non-trading day simply 404s, which is what `fetch` returns None for — no holiday
calendar to maintain.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import time

import httpx

ARCHIVE = "https://nsearchives.nseindia.com/"
WARMUP_URL = "https://www.nseindia.com/market-data/securities-lending-and-borrowing"
MANIFEST_URL = "https://www.nseindia.com/api/daily-reports"

# Landing root. Raw bytes are written here unmodified before anything parses
# them, so a layout change leaves the original file to look at.
LANDING = pathlib.Path(__file__).parents[3] / "data" / "raw"

HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://www.nseindia.com/all-reports",
}

# path template -> local filename. {ddmmyyyy}, {yyyymmdd}, {ddmm}, {yyyy}, {MON}
# are substituted from the trading date.
SOURCES = {
    "slb_bhavcopy": "archives/slbs/bhavcopy/SLBM_BC_{ddmmyyyy}.DAT",
    "slb_open_positions": "archives/slbs/open_pos/slb_openpos_{ddmmyyyy}.csv",
    "slb_eligibility": "archives/slbs/seclist/SLB_ELG_SEC_{ddmmyyyy}.csv",
    # NSE's own spelling, and this one file uses YYYYMMDD while its siblings use DDMMYYYY.
    "slb_foreclosure": "content/slbs/Forclosure_SLB_{yyyymmdd}.CSV",
    "slb_var": "archives/slbs/var/C_VAR1_SLB_{ddmmyyyy}_1.DAT",
    "cash_bhavcopy": "products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
    "gsec_master": "content/historical/WDM/{yyyy}/{MON}/wdmlist_{ddmmyyyy}.csv",
    "gsec_trades": "content/debt/dly{ddmmyyyy}.zip",
}


def url_for(source: str, date: dt.date) -> str:
    return ARCHIVE + SOURCES[source].format(
        ddmmyyyy=date.strftime("%d%m%Y"),
        yyyymmdd=date.strftime("%Y%m%d"),
        ddmm=date.strftime("%d%m"),
        yyyy=date.strftime("%Y"),
        MON=date.strftime("%b").upper(),
    )


class NseClient:
    """One warmed-up session. Reuse it; each new session costs a warm-up round trip."""

    def __init__(self, gap_seconds: float = 0.25) -> None:
        self._client = httpx.Client(headers=HEADERS, timeout=60.0, follow_redirects=True)
        self._gap = gap_seconds
        self._warm = False

    def __enter__(self) -> NseClient:
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def _warmup(self) -> None:
        self._client.get(WARMUP_URL)
        self._warm = True

    def fetch(self, url: str) -> bytes | None:
        """Return the file's bytes, or None if NSE does not have it (holiday, not yet published).

        A 403 is almost always an expired Akamai cookie, so it re-warms and retries.
        """
        if not self._warm:
            self._warmup()

        for attempt in range(3):
            time.sleep(self._gap)
            response = self._client.get(url)

            if response.status_code == 200:
                return response.content
            if response.status_code == 404:
                return None
            if response.status_code == 403:
                self._warmup()
            elif response.status_code < 500:
                response.raise_for_status()

            time.sleep(2**attempt)

        response.raise_for_status()
        return None

    def latest_trading_date(self) -> dt.date:
        """The most recent date NSE has published SLB files for."""
        if not self._warm:
            self._warmup()
        time.sleep(self._gap)
        manifest = self._client.get(MANIFEST_URL, params={"key": "SLBS"}).json()
        for key in ("CurrentDay", "PreviousDay"):
            entries = manifest.get(key) or []
            if entries:
                return dt.datetime.strptime(entries[0]["tradingDate"], "%d-%b-%Y").date()
        raise RuntimeError("SLBS manifest carried no trading date")

    def land(self, source: str, date: dt.date, force: bool = False) -> pathlib.Path | None:
        """Fetch one source for one date and write the raw bytes to data/raw/<date>/.

        An already-landed file is reused unless `force`, so re-running a backfill
        costs nothing. Returns None when NSE has no such file.
        """
        directory = LANDING / date.isoformat()
        path = directory / pathlib.Path(url_for(source, date)).name

        if path.exists() and not force:
            return path

        payload = self.fetch(url_for(source, date))
        if payload is None:
            return None

        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path


def trading_dates(end: dt.date, days: int) -> list[dt.date]:
    """Weekdays ending at `end`, most recent first.

    Weekends are skipped because they are never trading days; exchange holidays
    are left to resolve themselves as 404s.
    """
    dates: list[dt.date] = []
    day = end
    while len(dates) < days:
        if day.weekday() < 5:
            dates.append(day)
        day -= dt.timedelta(days=1)
    return dates
