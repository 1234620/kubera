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

## What it found

The point of building it on real files rather than synthetic ones is that the
data pushes back. A few of the things that only surfaced by doing the work:

- **NSE does not always 404 a holiday.** It serves a *stale* cash bhavcopy under
  the holiday's own filename — `sec_bhavdata_full_14092026.csv` returns 200 with
  11-Sep rows. A backfill trusting the filename loads one day's prices under
  several dates. The ingester trusts the date inside the file.
- **The begin-day VaR file's third column is the series code, not a serial.** It
  repeats all 1,176 securities across 73 series, which makes it the only complete
  series master — 12 regular + 12 non-foreclosing + 48 rollover + `R3`, exactly
  matching the NCL circular.
- **NSE quotes a simple ACT/365 yield inside the final coupon period.** Pure
  compounding left three outliers against NSE's published weighted YTM and every
  one was a bond with one cash flow left. Implementing the convention took
  agreement from 97.7% to **99.2% within 2bp, median error 0.000bp**.
- **A code like `CG2028` is shared by several bonds with different coupons.**
  Joining on the code alone prices the wrong bond — it produced repo collateral
  shortfalls of 7.5%, which is far too large for daily variation margin on
  G-Secs, and that implausibility is what exposed it.
- **`issue_name` means two different things.** It is the coupon for a G-Sec and
  the maturity date, as DDMMYY, for a T-bill — whose security code is only its
  original tenor and so identifies dozens of different bills. Joining bills on
  code alone bootstrapped a **149% short rate**, which is how the problem
  announced itself.
- **Lendable supply is not published in India at all.** So utilisation is an
  estimate, every row says so, and `days_to_cover` — which needs no estimate —
  is reported beside it rather than behind it.

## What it computes

**Securities lending**
- Financing spread P&L per position and per desk, split into fee accrual and
  funding drag
- Borrow cost and funding cost per position
- Utilisation (on-loan ÷ lendable) from NSE eligible-security and open-position files
- **Specialness score** — a blend of the cross-sectional percentile of the
  annualised fee within its tenor bucket and a z-score against the security's own
  trailing baseline, classified GC / warm / special / hard-to-borrow. Both
  components are stored, because "87" is not an answer.
- Term structure of lending fees across the monthly series

**Curve construction** (the quant layer)
- A **zero-coupon curve** bootstrapped from T-bills and G-Secs, solved as a fixed
  point because interpolated coupons depend on the factor being solved
- A **Nelson-Siegel-Svensson** parametric fit — separable least squares, no
  optimiser dependency — averaging 5.5bp RMSE over ~38 instruments a day
- Both published side by side: the bootstrap is exact where something traded, NSS
  is smooth and extrapolates, and `is_extrapolated` marks where each is reaching
- **Forward rates** from the curve, and the **SLB fee curve read as a forward
  curve** — PIIND at 51% to 15 days and 19% to 43 days implies 2% for the 28 days
  between, so the market is pricing that squeeze to be over within a fortnight
- **Key-rate DV01** bucketed by tenor, because a parallel DV01 cannot tell a
  10-year position from a barbell of 2s and 30s

**Bond leg (Indian G-Secs)**
- Accrued interest on the Indian 30/360 convention, clean ⇄ dirty price
- YTM by Newton solve on the dirty price
- Macaulay duration, modified duration, convexity, DV01/PV01

**Repo**
- Security-specific haircuts, collateral valuation, purchase price vs repurchase price
- Variation margin, margin call generation, borrowing-limit shortfall
- Repo interest accrual on ACT/365

**Funds Transfer Pricing**
- Matched-maturity transfer rate off a funding curve, plus a term liquidity
  premium and a contingent liquidity charge driven by the NSE recall-eligibility
  flag
- Charged on the **net** position per desk and name, so a matched book pays
  almost nothing and a directional one pays for the balance sheet it uses
- Net interest margin split into a desk spread and a treasury spread that add
  back to the book — asserted to the rupee on every refresh

On the seeded book this is the whole argument for the model:

| desk | positions | gross | net | fee ₹/day | FTP ₹/day | net ₹/day |
| --- | --- | --- | --- | --- | --- | --- |
| `EQ_FIN` | 376 | ₹789 cr | **₹0** | +131,352 | **0** | +131,352 |
| `DELTA_ONE` | 101 | ₹201 cr | ₹201 cr | −273,310 | **−320,549** | −593,859 |
| `TREASURY` | 0 | — | −₹201 cr | 0 | +320,549 | +639,868 |

The matched book keeps its spread. The directional one pays **more in funding
than it pays in borrow fees** — so the dominant cost of that arbitrage position
is balance sheet, not borrow, and nothing else surfaces that.

## Stack

Postgres 16 · Python 3.12 · FastAPI · vanilla-JS dashboard · Power BI · Docker
Compose · GitHub Actions.

No ORM, no migration framework, no frontend build step, no login page. Six
dependencies in total. Reasons in [`docs/adr/`](docs/adr).

## Quick start

Docker is the only prerequisite.

```bash
cp .env.example .env
make up          # postgres + api
make bootstrap   # migrate, ingest 30 trading days, seed the book, refresh analytics
open http://localhost:8000
```

`make bootstrap` takes about a minute: one request per file per trading day
against a public archive, deliberately paced. `make help` lists every target.

```bash
make test        # full suite in the container, including the Postgres checks
make test-ci     # the same suite against a throwaway fixture-only database
make analytics   # refresh derived tables and run the eight validation queries
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
10. [Curve construction](docs/13-curve-construction.md) — bootstrap, NSS, forwards, key rates
11. [Glossary](docs/11-glossary.md) · [Interview notes](docs/12-interview-notes.md)

## How it is checked

232 tests, and the ones that matter are not unit tests:

| Check | Where |
| --- | --- |
| Our G-Sec yields reproduce **NSE's own published weighted YTM** — 132/133 within 2bp, median error 0.000bp | `tests/test_bonds.py` |
| Our T-bill yields reproduce NSE's to a **median 0.002bp** | `docs/13` §1 |
| The bootstrap **reprices every input it accepts** | `tests/test_curves.py` |
| Simultaneous key-rate shocks equal a parallel shift **exactly** | `tests/test_curves.py` |
| The FTP decomposition **adds back to book NIM** to within ₹1 | `db/queries/validate_ftp_reconciliation.sql` |
| The internal FTP ledger **nets to zero** across desks | `db/queries/validate_ftp_zero.sql` |
| Rollover chains respect **SEBI's 12-month tenure cap** | `db/queries/validate_tenure.sql` |
| Average fee rises monotonically across the specialness bands — emergent, not fitted | `tests/test_analytics.py` |
| Analytic DV01 agrees with repricing at ±1bp to 1e-6 | `tests/test_bonds.py` |
| The loader's natural keys match the actual primary keys in `pg_index` | `tests/test_load.py` |
| The dashboard obeys its own rules — no build step, no login, no colour literal outside tokens | `tests/test_frontend.py` |

Eight validation queries run on every `make analytics` and fail the build rather
than letting a wrong number reach the dashboard. CI runs the whole suite against
a real Postgres with **no network**, using committed fixtures — so if NSE is down,
CI still passes, and CI failing always means our code broke.

Every one of those checks has caught a real bug at least once. The commit
messages say which.

## Data provenance

Every number traces to a public file. Nothing is invented except the synthetic
*book* (our own trades) — the *market* is real. See
[`docs/05-data-sources.md`](docs/05-data-sources.md) for endpoints, column maps
and the fixtures in [`tests/fixtures/`](tests/fixtures).

Three limits, stated rather than buried: the book is synthetic, `lendable_qty` is
an estimate because nobody publishes it, and everything is end-of-day.

## Author

**Ahmed Moosani** — [@AhmedMoosani](https://github.com/AhmedMoosani).
See [`CONTRIBUTORS.md`](CONTRIBUTORS.md).

## Licence

MIT. Market data belongs to NSE / NSE Clearing and is used for education only.
