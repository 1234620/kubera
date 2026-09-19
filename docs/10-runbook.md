# 10 — Runbook

## Prerequisites

Docker and Docker Compose. That is all — Python and Postgres run in containers.
For local development without Docker: Python 3.12 and Postgres 16.

## First run

```bash
git clone <repo> && cd slb-repo-desk
cp .env.example .env
make up          # postgres + api
make migrate     # apply db/migrations in filename order
make ingest      # fetch the last 30 trading days from NSE
make seed        # generate the synthetic book
make analytics   # refresh derived tables and run validation queries
open http://localhost:8000
```

`make ingest` takes a few minutes: one request per file per day, sequential, with a
250 ms gap because hammering a public exchange archive is rude and gets you blocked.

## Make targets

| Target | What it does |
| --- | --- |
| `up` / `down` | Compose up/down |
| `logs` | Tail the api and db containers |
| `migrate` | Apply pending migrations, record them in `schema_migration` |
| `ingest` | Fetch and load. `DAYS=60 make ingest` to backfill further |
| `ingest-date` | `DATE=2026-09-18 make ingest-date` for one day |
| `seed` | Generate the synthetic book from real quotes. Idempotent; truncates and regenerates. Seeded, so it reproduces |
| `analytics` | Refresh derived tables, then run every validation query |
| `test` | Full suite in the container, so the Postgres tests run. `test-local` runs on the host and skips them |
| `lint` | `ruff check` and `ruff format --check` |
| `psql` | Open a shell on the database |
| `reset` | Drop and recreate the database. Asks first |

## Environment

`.env`, from `.env.example`:

```
POSTGRES_DB=slbdesk
POSTGRES_USER=slbdesk
POSTGRES_PASSWORD=slbdesk          # localhost only; see note
POSTGRES_HOST=db
POSTGRES_PORT=5432
API_PORT=8000
INGEST_DAYS=30
```

The password is a development default and the compose file does not publish the
Postgres port outside the Docker network. If this is ever deployed anywhere real,
it needs a real secret and this file is not where it goes.

## Migrations

`db/migrations/NNN_description.sql`, applied in filename order by
`scripts/migrate.py`, tracked in `schema_migration (version, applied_at, checksum)`.

- Forward-only. No down migrations — for a solo project, `make reset` and replay is
  faster and less error-prone than maintaining reversals nobody tests.
- A migration whose checksum no longer matches the recorded one **fails the run**
  rather than silently diverging. Edit a new migration, never an applied one.

## Ingestion behaviour

- Raw bytes land in `data/raw/<YYYY-MM-DD>/` before anything is parsed. When NSE
  changes a layout, the original file is still there.
- Holidays: the manifest omits them and a direct fetch 404s. Both mean "no data",
  not an error. No holiday calendar to maintain.
- Re-running a date is safe and cheap: an already-landed file is reused rather than
  re-fetched, and the load upserts on the natural key. `--force` re-downloads.
- The G-Sec security master is fetched once per run for the newest date, not per
  day — it is a security master, not a time series.
- A 403 is almost always an expired Akamai cookie. The client re-warms and retries
  three times with exponential backoff before giving up on that file, and it
  reports which file failed rather than dying on the whole batch.

## Power BI

`powerbi/slb_desk.pbit` is a template — a `.pbit` carries the model and visuals
without the data, so the repo stays small and nothing private ships.

1. Open the template; it prompts for the Postgres host, port and database.
2. Use the `slbdesk_ro` role.
3. Import mode against the materialised analytics tables. Not DirectQuery: the
   tables are already aggregated and Import keeps the report responsive.
4. Refresh.

Pages: Book Overview, Specialness Explorer, Term Structure, Repo & Margin, FTP
Attribution. Measures are defined in the model, and `powerbi/measures.md` lists
each DAX measure against the SQL metric it mirrors — so the two never drift
silently.

## CI

`.github/workflows/ci.yml`:

1. `ruff check` and `ruff format --check`
2. Spin up a Postgres 16 **service container**, run every migration against it
3. `pytest` — parser checks against `tests/fixtures/`, bond math checks, FTP
   reconciliation, P&L attribution
4. Load the fixtures into the CI database and run every validation query in
   [`07-analytics-spec.md`](07-analytics-spec.md) §11

No network calls in CI. That is what the fixtures are for: if NSE is down, CI still
passes, and CI failing always means *our* code broke.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `403` from nseindia.com | Cookie not warmed, or missing referer | The client handles it; if it persists NSE has changed its edge rules — check `nse.py` headers |
| `404` on an archive file | Non-trading day, or not published yet for today | Expected. Check `GET /api/data-coverage` |
| `skipped - exchange holiday` | NSE served a stale cash bhavcopy under that date's filename | Expected and correct; the date is genuinely not a trading day |
| Empty specialness table | Fewer than 21 days of history, so the trailing baseline is null | `DAYS=60 make ingest` |
| Dashboard shows `—` for utilisation | `lendable_qty` estimate missing for that symbol | Expected for newly eligible names; the cell is labelled as an estimate |
| FTP reconciliation test fails | A position has no curve point at its tenor | Check `GET /api/ftp/curve` for a gap; the interpolator should not have one |
| Migration checksum mismatch | An applied migration was edited | Revert it and add a new migration |
