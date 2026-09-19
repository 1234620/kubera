# 05 — Data sources, endpoints and file formats

Everything here was verified by fetching the live files, not read off a blog. The
column maps below were confirmed against real data; where a field was *inferred*
rather than documented by NSE, the doc says how it was verified.

## 1. Discovery endpoint

NSE publishes a JSON manifest of the day's report files per segment:

```
GET https://www.nseindia.com/api/daily-reports?key=SLBS
GET https://www.nseindia.com/api/daily-reports?key=WDM
```

Response has three keys — `PreviousDay`, `CurrentDay`, `FutureDay` — each a list of
`{displayName, fileActlName, filePath, tradingDate, fileSize}`. Concatenating
`filePath + fileActlName` gives the archive URL. **Use this rather than guessing
filenames**: it tells you the trading date, so holidays resolve themselves.

### Access requirements (this is the fiddly part)

`nseindia.com` sits behind Akamai. A bare `curl` gets a 403. What works:

1. A browser `User-Agent`.
2. A **cookie warm-up**: `GET` an ordinary HTML page first
   (`/market-data/securities-lending-and-borrowing`), keep the cookie jar, then hit
   the API with that jar.
3. A `Referer` header pointing at an nseindia.com page.

Archive downloads from `nsearchives.nseindia.com` need the same jar and a referer
of `https://www.nseindia.com/all-reports`. `src/slbdesk/ingest/nse.py` wraps this
in one `httpx.Client` that warms up once and is reused.

## 2. SLB files

Base: `https://nsearchives.nseindia.com/`

| File | Path | Content |
| --- | --- | --- |
| `SLBM_BC_DDMMYYYY.DAT` | `archives/slbs/bhavcopy/` | Daily lending-fee OHLC per security × series |
| `slb_openpos_DDMMYYYY.csv` | `archives/slbs/open_pos/` | End-of-day open interest per security × series |
| `SLB_ELG_SEC_DDMMYYYY.csv` | `archives/slbs/seclist/` | Eligible securities with per-series normal/recall/repay flags |
| `Forclosure_SLB_YYYYMMDD.CSV` | `content/slbs/` | Corporate-action foreclosures (note: NSE's own spelling, and the date format flips to `YYYYMMDD`) |
| `C_VAR1_SLB_DDMMYYYY_1.DAT` | `archives/slbs/var/` | Begin-day VaR / margin rates per ISIN — our haircut input |

### 2.1 `SLBM_BC_*.DAT` — the SLB bhavcopy

Comma-separated, **no header**, fixed-width padded fields, 17 columns, one row per
`(symbol, series)` that traded. Prices are zero-padded to 10 characters. All fee
columns are **rupees per share for the contract period** — not annualised.

| # | Field | Example | Notes |
| --- | --- | --- | --- |
| 1 | Security name | `ABB INDIA LIMITED` | 30 chars, space-padded |
| 2 | Symbol | `ABB` | 10 chars, space-padded |
| 3 | Series | `XN` | See [`01-domain-slb-lifecycle.md`](01-domain-slb-lifecycle.md) §3 |
| 4 | Reverse-leg settlement date | `03-NOV-2026` | `%d-%b-%Y`, uppercase month |
| 5 | Market type | `N` | Normal |
| 6 | Previous close fee | `0000017.28` | **Verified**: equals column 10 of the prior trading day for 93/93 overlapping rows |
| 7 | Open fee | `0000012.00` | |
| 8 | High fee | `0000012.00` | |
| 9 | Low fee | `0000012.00` | |
| 10 | Close fee | `0000012.00` | |
| 11 | *(unused)* | `    ` | Empty in every row of every file sampled |
| 12 | Traded quantity | `875` | Shares |
| 13 | Traded value | `10500.00` | Rupees; equals Σ(qty × fee), so value/qty is the VWAF |
| 14 | Year high fee | `0000017.28` | **Verified**: ≥ column 8 in every row |
| 15 | Year low fee | `0000001.00` | **Verified**: ≤ column 9 in every row |
| 16 | Trade date | `18-SEP-2026` | |
| 17 | Number of trades | `12` | |

Volume note: a typical day has ~230 rows. This is a thin market, which is itself a
finding worth putting in the dashboard — most `(symbol, series)` pairs do not trade
on a given day, so *staleness* has to be handled explicitly rather than assumed away.

**Derived on load**: `vwaf = traded_value / traded_quantity` (the value-weighted
average fee), and `fee_annualised_pct`, computed in
[`07-analytics-spec.md`](07-analytics-spec.md) §2 — the raw file gives you neither.

### 2.2 `slb_openpos_*.csv` — open positions

Header: `Sr no,Security,Series,Outstanding Quantity at the end of the day`

~800 rows/day, so open interest exists in roughly 3.5× more `(symbol, series)`
pairs than traded today. Confirms the staleness point. This is the numerator of
utilisation and, differenced day over day with `LAG()`, the flow.

### 2.3 `SLB_ELG_SEC_*.csv` — eligible securities

Header: `Sr.No.,Symbol,Series,Normal Eligibility,Recall Eligibility,Repay Eligibility,Market Type`

~32,600 rows/day = ~1,158 symbols × 28 series. Flags are `E` (eligible) or `D`
(disabled). Observed series set: `01`–`12`, `X1`–`X9`, `XO`, `XN`, `XD`, plus
rollover series (`O1`, `O2`, `ON`, `OD`, …) and occasional short-dated series
covering fewer symbols.

The **recall/repay flags drive the contingent liquidity charge** in the FTP model
(a position that cannot be recalled early consumes more liquidity), so this file is
not just reference data.

### 2.4 `Forclosure_SLB_*.CSV` — corporate-action foreclosures

Header: `SECURITY,SERIES**,ISIN,ANNOUNCEMENT DATE,BOOK CLOSURE START DATE,RECORD DATE,EX DATE,FORECLOSURE DATE,SHUT PERIOD START DATE,SHUT PERIOD END DATE,NEXT TRADE DATE,FORECLOSURE SETTLEMENT NO,CORPORATE ACTION DESCRIPTION`

`SERIES**` is usually the literal string `ALL`. The description field is a padded
short code followed by the long text (`BONUS 1:1    BONUS 1:1`) — split on runs of
two or more spaces and keep both. Small file, a handful of rows, and every row is a
forced unwind: a P&L event, not reference data.

The file **ends with a legend block** of short rows, which the parser must skip on
field count:

```
SERIES**
OLD,Series-01-12
NEW,Series-X1-XD
ALL,Both "OLD" and "NEW"
```

That legend is NSE confirming the contract-set split from
[`01-domain-slb-lifecycle.md`](01-domain-slb-lifecycle.md) §3 — `OLD` is the
force-foreclosing regular set, `NEW` is the non-foreclosing `X` set.

### 2.5 `C_VAR1_SLB_*.DAT` — VaR begin-day file

CSV, ~5 MB, **no header**, with record-type-prefixed rows:

- `10` — header: `10,DDMMYYYY,0.0000,<record count>`
- `20` — per security × series:
  `20,<symbol>,<series_code>,<ISIN>,<var_pct>,<unused>,<applicable_var_pct>,<elm_pct>,<additional_margin_pct>,<total_margin_pct>`

Example: `20,360ONE,01,INE466L01038,12.80,0.00,12.80,3.50,0.00,16.30`.

Three things here were **wrong in the first draft of this document** and were
corrected against the real file:

1. **Column 3 is the series code, not a serial number.** The file carries 84,389
   record-20 rows for 1,176 symbols — every symbol repeated across **73 series**.
2. **The total is `applicable_var + elm + additional`**, and that identity holds
   *exactly* for all 84,389 rows. The parser raises on violation, which is how a
   layout change gets caught. Column 6 is 0.00 in every row; column 9 is non-zero
   in 3,606 rows and is an additional/ad-hoc margin.
3. **`applicable_var` is not the computed VaR.** It is floored at a regulatory
   minimum, so it can exceed column 5 — ABBOTINDIA computes 8.21% but applies
   9.00%. The applicable figure is the one that matters for a haircut.

Total margin is **identical across all 73 series** for a given symbol (verified
across every symbol in the file), so the parser de-duplicates to **one row per
symbol** — 1,176 rows instead of 84,389.

**Bonus: this file is the complete series master.** The bhavcopy only shows series
that traded (11 on a sample day) and the eligibility file only those currently live
(29), but the VaR file lists all 73: 12 regular, 12 non-foreclosing, 48 rollover and
`R3`. That is exactly the count NCL's FAQ implies — twelve monthly series in each of
two contract sets plus "forty-eight contracts for rollover" — so it independently
confirms the taxonomy. `parse_slb_series_universe` extracts it as a by-product.

### 2.6 `sec_bhavdata_full_*.csv` — cash market close and volume

Path: `products/content/`. Discovered via `GET /api/daily-reports?key=CM` as
"Full Bhavcopy and Security Deliverable data".

Header: `SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE,
LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES,
DELIV_QTY, DELIV_PER` — note the values carry a leading space after each comma.

**This file is not optional.** None of the SLB files contain an underlying share
price, and without one a lending fee cannot be annualised
([`07-analytics-spec.md`](07-analytics-spec.md) §1). It also supplies the traded
volume that days-to-cover needs, so one file covers both gaps.

Two traps, both found by testing rather than reading:

- **Do not filter to `SERIES = 'EQ'`.** A security under surveillance trades in the
  `BE` segment while remaining SLB-eligible. On the sample date **HFCL** is exactly
  that: present in the SLB bhavcopy, absent from the EQ rows. An EQ-only filter
  silently loses the price for the names most likely to be special. `series` is kept
  as part of the natural key so a lookup can prefer `EQ` and fall back.
- **`DELIV_QTY` and `DELIV_PER` are `-`, not empty**, in the `BE` segment. They must
  parse to `NULL`, not 0 — a 0% delivery ratio is a meaningful and wrong number.

`TURNOVER_LACS` is in **lakhs** (1 lakh = 100,000) and is converted to rupees on
load, for the same reason the debt file's crore column is.

## 3. WDM (debt) files — the bond leg

| File | Path | Content |
| --- | --- | --- |
| `wdmlist_DDMMYYYY.csv` | `content/historical/WDM/YYYY/MON/` | Full debt security master |
| `dly<DDMM>YYYY.zip` | `content/debt/` | Daily report bundle |

### 3.1 `wdmlist_*.csv` — security master

Header: `SECTYPE,SECURITY,ISSUE_NAME,ISSUE_DESC,ISSUE_DATE,MAT_DATE,Last IP Dt,Next IP Dt,Cpn Freq,Last Traded Date,Last Traded Price (in Rs.),ISIN NO.,STATUS`

~10,650 rows. `SECTYPE` breakdown on a sample day: `SG` 5,515 (state government),
`DB` 1,476 (debentures), `GZ` 1,241, `CP` 822 (commercial paper), `PT` 690,
`ID` 127 (institutional debt), **`GS` 120 (central government dated securities)**,
`TB` 84 (T-bills), `BB` 77, `PF` 68.

We take `SECTYPE IN ('GS','TB')` — 120 G-Secs and 84 T-bills, which is the real
central-government curve.

Example row:
```
GS,CG2036,8.33%,GOI LOAN 8.33% 2036,07-Jun-2006,07-Jun-2036,07-Jun-2026,07-Dec-2026,Half Yearly,12-Nov-2025,111.5522,IN0020060045,Listed
```

The coupon rate lives in `ISSUE_NAME` (`8.33%`) and again in `ISSUE_DESC`; parse
from `ISSUE_NAME` and cross-check against `ISSUE_DESC`. Crucially, **`Last IP Dt`
and `Next IP Dt` are given**, so the accrued-interest calculation reads the actual
coupon period from the file instead of assuming a clean six-month offset — which is
what makes the stub periods correct. `Cpn Freq` is `Half Yearly` for every `GS` row
sampled, but the parser reads it rather than hardcoding it.

Note `Last Traded Price` can be years stale (`12-Nov-2025` on an actively quoted
bond) because WDM is not where G-Secs mostly trade — NDS-OM is. Use the master for
*terms*, and the daily trade file for *prices*.

### 3.2 `dly*.zip` → `trd<DDMM>_sett.csv` — daily trades

Zip contents: `trd<DDMM>_sett.csv`, `trd<DDMM>.xls`, `add<YYYYMMDD>.csv`
(additions), `mat<YYYYMMDD>.csv` (maturities), `dmr<DDMM>.docx`, `Legend.doc`.

Header of the settlement CSV:
`Trade date,Sectype,Security,Issue Name,Settl Days,Trade Type,No.of Trades,Traded Value (Rs.Cr.),Low Price/Rate,High Price/Rate,Last Traded Price/Rate,Value Weighted Average Price/Rate,Weighted YTM`

```
18-Sep-2026,GS,CG2026,7.33%,1,NR,3,300.00,100.2169,100.2169,100.2169,100.2169,5.2400
```

`Settl Days` = 1 → **T+1**, the G-Sec convention. Prices are **clean** per ₹100
face. `Traded Value` is in **₹ crore** (1 crore = 10 million) — the parser converts
to rupees on load and the column is named `traded_value_inr`, because a unit error
here is a factor of 10⁷.

**`Weighted YTM` is the validation target.** Our own YTM, computed from the file's
`Value Weighted Average Price/Rate` plus accrued interest from the master's coupon
dates, must reproduce it. That is the check in
[`03-domain-bond-math.md`](03-domain-bond-math.md) §11.

Also worth noting: `add*.csv` and `mat*.csv` are free issuance and redemption
feeds, which is how the security master stays current without re-downloading 10,650
rows daily.

## 4. What is *not* available publicly, and what we do instead

| Wanted | Status | Substitute |
| --- | --- | --- |
| Lendable supply per security | Not published | Modelled from MWPL and free-float proxies; documented and flagged as an estimate in the API response |
| GC equity lending rate | No single benchmark | The value-weighted median fee of the liquid cohort, defined in [`07-analytics-spec.md`](07-analytics-spec.md) |
| TREPS / market repo rates | Published by CCIL but no stable machine endpoint found | Short end of the funding curve from T-bill yields in the WDM file; the curve build documents the substitution |
| Our own book | Private by nature | Seeded generator, `db/seed/` |

Marking an estimate as an estimate is part of the deliverable. `is_estimated`
is a real column on the tables that need it.

## 5. Ingestion contract

- **Landing**: raw bytes written unmodified to `data/raw/<YYYY-MM-DD>/<filename>`.
  Never parse over the wire without landing first; when a layout changes you want
  the original bytes.
- **Idempotent**: re-running a date overwrites the landing file and upserts on the
  natural key. Running the whole backfill twice changes nothing.
- **Holidays**: mostly a 404, which is treated as "no data" rather than an error —
  no hardcoded holiday calendar. **But not always.** On an exchange holiday NSE
  does *not* 404 the cash bhavcopy: it serves a **stale copy under the holiday's
  filename**. `sec_bhavdata_full_14092026.csv` returns 200 with **11-Sep-2026**
  rows in it. The begin-day VaR file behaves similarly, because it is published
  ahead for future settlement days.

  A backfill that trusted the filename would load one day's prices under several
  dates and quietly corrupt every fee annualisation and every mark. So the
  ingester **trusts the date inside the file, never the date in its name**: it
  checks the cash bhavcopy's own `DATE1` against the requested date and skips the
  whole date if they disagree. `is_trading_day()` in
  `src/slbdesk/ingest/__main__.py`, checked in `tests/test_parsers.py`.
- **Politeness**: one client, sequential requests, 250 ms gap, three retries with
  exponential backoff on 5xx and on the Akamai 403 (which is usually a cookie
  expiry and is fixed by re-warming).
- **Fixtures**: one real copy of every file in `tests/fixtures/`, so parser checks
  run in CI with no network. The eligible-securities fixture is truncated to 400
  rows and the VaR fixture is omitted for size; both are noted in the check.
