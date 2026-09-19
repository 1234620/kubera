# Roadmap

**All eleven stages are complete.** Each is tagged `stage-N`; `git log` carries the
reasoning for every one, including the bugs each stage found.

Ten stages. Each stage ends in a commit **and a push**, tagged `stage-N`.
A stage is not done until: code works, one runnable check passes, the matching
doc section is written, CI is green.

| Stage | Name | Deliverable | Tag |
| --- | --- | --- | --- |
| 0 | Skeleton | Repo, docs, rules, CI, real fixtures | `stage-0` ✅ |
| 1 | Ingestion | NSE client + parsers for 5 SLB files and the WDM G-Sec master, landing to `data/raw` | `stage-1` ✅ |
| 2 | Schema | Postgres migrations, loaders, referential integrity, a seeded synthetic book | `stage-2` ✅ |
| 3 | SLB analytics | `db/queries/` — utilisation, fee term structure, specialness, spread P&L | `stage-3` ✅ |
| 4 | Bond leg | Accrued interest, clean/dirty, YTM, duration, convexity, DV01 on real G-Secs | `stage-4` ✅ |
| 5 | Repo | Haircuts, collateral valuation, variation margin, margin calls | `stage-5` ✅ |
| 6 | FTP | Funding curve, matched-maturity transfer rate, term liquidity premium, desk allocation | `stage-6` ✅ |
| 7 | API | FastAPI over the analytics layer, OpenAPI docs | `stage-7` ✅ |
| 8 | Dashboard | Single-page desk blotter with gradients and animation | `stage-8` ✅ |
| 9 | Ship | Docker Compose, Power BI model, README screenshots, final CI | `stage-9` ✅ |
| 10 | Curve construction | Zero-coupon bootstrap, NSS fit, forwards, SLB implied forwards, key-rate DV01 | `stage-10` ✅ |

## Stage detail

### Stage 1 — Ingestion
- `nse.py`: one session, browser headers, cookie warm-up, retry, date→URL builder
- Parsers: SLB bhavcopy (fixed-width-ish CSV), open positions, eligible securities,
  foreclosure report, WDM security master, WDM daily trades
- Backfill N trading days; detect holidays by 404 *and* by the stale-file trap
  (NSE serves the previous day's cash bhavcopy under a holiday's filename)
- Check: parse each fixture, assert row counts and known values

### Stage 2 — Schema
- Reference: `security`, `slb_series`, `gsec`
- Market: `slb_quote_daily`, `slb_open_position`, `slb_eligibility`, `slb_foreclosure`, `gsec_trade_daily`
- Book: `desk`, `slb_trade`, `slb_trade_leg`, `repo_trade`, `collateral_position`, `margin_call`
- Curves: `funding_curve_point`, `gc_rate_daily`
- Idempotent loads (`ON CONFLICT DO UPDATE` on natural keys)

### Stage 3 — SLB analytics
Each metric is one `.sql` file with a header comment stating inputs, formula and units.

### Stage 4 — Bond leg
Pure functions in `src/slbdesk/bonds/`, no DB. Tested against hand-computed values
and against NSE's own published weighted YTM from `trd*_sett.csv`.

### Stage 5 — Repo
Haircut schedule by tenor and issuer class, collateral revaluation, shortfall →
margin call rows.

### Stage 6 — FTP
Curve build, transfer rate per position, NIM decomposition that reconciles to the
book P&L within a rupee.

### Stage 7 — API
Read-only endpoints. Pydantic response models. No auth.

### Stage 8 — Dashboard
`web/` served as static files by FastAPI. Chart.js from CDN. No bundler.

### Stage 9 — Ship
Compose file brings up Postgres + API + a one-shot ingest job.
