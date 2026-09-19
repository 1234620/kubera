# Securities Lending & Repo Financing Desk Analytics

A working stock-borrow-loan (SBL) and repo financing book built on **real, public
Indian market data** — NSE Clearing's Securities Lending & Borrowing (SLB) daily
files and NSE's Wholesale Debt Market G-Sec master.

It does what a financing desk's analytics stack does: ingests the market files,
models the book in Postgres, prices both legs, scores specialness, attributes
financing spread P&L, runs repo collateral and margin mechanics, and allocates
internal funding cost across desks through a Funds Transfer Pricing model.

```
NSE archives ──▶ ingest ──▶ Postgres ──▶ SQL analytics ──▶ FastAPI ──▶ dashboard
  (SLB + WDM)    (parsers)   (star-ish)   (CTEs, windows)            └▶ Power BI
```

## Why this project exists

Two job descriptions, read literally:

| JD line | Where it lives here |
| --- | --- |
| "trade life cycle of Securities Lending & Repo Financing" | [`docs/01-domain-slb-lifecycle.md`](docs/01-domain-slb-lifecycle.md), [`docs/02-domain-repo.md`](docs/02-domain-repo.md), the `slb_trade` state machine |
| "bond analysis and bond pricing concepts" | [`src/slbdesk/bonds/`](src/slbdesk/bonds), [`docs/03-domain-bond-math.md`](docs/03-domain-bond-math.md) |
| "good python/sql programming skills" | [`db/queries/`](db/queries) — window functions and CTEs, no `SELECT *` |
| "Funding Transfer Pricing" (EFG JD, by name) | [`src/slbdesk/analytics/ftp.py`](src/slbdesk/analytics/ftp.py), [`docs/04-domain-ftp.md`](docs/04-domain-ftp.md) |

## What it computes

**Securities lending**
- Financing spread P&L per trade and per book, decomposed into fee spread, term
  spread and reinvestment
- Borrow cost and funding cost per position
- Utilisation (on-loan ÷ lendable) from NSE eligible-security and open-position files
- **Specialness score** — a percentile-and-z-blend of lending fee against the
  security's own history and the GC cohort, with a GC / warm / special / hard-to-borrow
  classification
- Term structure of lending fees across the 12 monthly series

**Bond leg (Indian G-Secs)**
- Accrued interest on the Indian 30/360 convention, clean ⇄ dirty price
- YTM by Newton solve on the dirty price
- Macaulay duration, modified duration, convexity, DV01/PV01

**Repo**
- Security-specific haircuts, collateral valuation, purchase price vs repurchase price
- Variation margin, margin call generation, borrowing-limit shortfall
- Repo interest accrual on ACT/365

**Funds Transfer Pricing**
- Matched-maturity transfer rate off a funding curve, plus term liquidity premium
- Net interest margin split into a desk spread and a treasury spread so the two
  sides add back to the book's actual P&L

## Stack

Postgres 16 · Python 3.12 · FastAPI · vanilla-JS dashboard · Power BI · Docker
Compose · GitHub Actions.

No ORM, no build step, no login page. Reasons in [`docs/adr/`](docs/adr).

## Quick start

```bash
make up          # postgres + api in docker
make migrate     # apply db/migrations in order
make ingest      # pull the latest NSE SLB + WDM files
make analytics   # refresh derived tables
make test        # pytest
open http://localhost:8000
```

## Documentation

Read in this order:

1. [Project charter](docs/00-project-charter.md) — scope, non-goals, stage plan
2. [SLB trade lifecycle](docs/01-domain-slb-lifecycle.md)
3. [Repo mechanics](docs/02-domain-repo.md)
4. [Bond math](docs/03-domain-bond-math.md)
5. [Funds Transfer Pricing](docs/04-domain-ftp.md)
6. [Data sources & file formats](docs/05-data-sources.md)
7. [Data model](docs/06-data-model.md)
8. [Analytics specification](docs/07-analytics-spec.md)
9. [API](docs/08-api-spec.md) · [Frontend](docs/09-frontend.md) · [Runbook](docs/10-runbook.md)
10. [Glossary](docs/11-glossary.md) · [Interview notes](docs/12-interview-notes.md)

## Data provenance

Every number traces to a public file. Nothing is invented except the synthetic
*book* (our own trades) — the *market* is real. See
[`docs/05-data-sources.md`](docs/05-data-sources.md) for endpoints, column maps
and the fixtures in [`tests/fixtures/`](tests/fixtures).

## Author

**Ahmed Moosani** — [@AhmedMoosani](https://github.com/AhmedMoosani).
See [`CONTRIBUTORS.md`](CONTRIBUTORS.md).

## Licence

MIT. Market data belongs to NSE / NSE Clearing and is used for education only.
