# 00 — Project charter

## Problem

A securities financing desk lends and borrows equities and finances them against
collateral. Its profit is a *spread*: what it charges a borrower minus what it pays
a lender minus what its own treasury charges it for the balance sheet it consumes.
To manage that spread the desk needs to know, per security and per position:

- what the market is paying to borrow this name today, and whether that is normal
- how much of the available supply is already lent out
- what the position costs to fund internally
- what the collateral is worth after a haircut, and whether more is needed
- how sensitive the bond side of the book is to a 1bp rate move

Nothing in that list is available from a single public screen. This project builds
the stack that produces it.

## Scope

**In scope**
- Ingestion of five NSE Clearing SLB daily files and two NSE WDM debt files
- A Postgres model of an SBL book: trades, legs, series, collateral, rebate rates
- SLB analytics: utilisation, fee term structure, specialness, financing spread P&L
- G-Sec bond analytics: accrued interest, clean/dirty, YTM, duration, convexity, DV01
- Repo mechanics: haircut schedule, collateral valuation, variation margin, margin calls
- A Funds Transfer Pricing engine allocating internal funding cost across desks
- A read-only FastAPI service and a single-page dashboard
- A Power BI model over the same Postgres tables

**Out of scope, deliberately**
- Authentication, multi-tenancy, user management. Localhost project.
- Order entry, connectivity to any exchange, anything that could place a trade
- Intraday or real-time data. Everything is end-of-day.
- A general-purpose pricing library. Bond math covers Indian fixed-coupon G-Secs
  and T-bills, not floaters, not callables, not inflation-linked.
- Machine learning. The specialness score is a transparent, defensible statistic,
  not a model nobody can explain in an interview.

## What is real and what is synthetic

| Layer | Source | Real? |
| --- | --- | --- |
| SLB lending fees, volumes, series dates | `SLBM_BC_*.DAT` | Real |
| SLB open interest per security/series | `slb_openpos_*.csv` | Real |
| SLB eligible securities and recall/repay flags | `SLB_ELG_SEC_*.csv` | Real |
| Corporate-action foreclosures | `Forclosure_SLB_*.CSV` | Real |
| Security-level VaR (haircut input) | `C_VAR1_SLB_*.DAT` | Real |
| G-Sec master: coupon, maturity, coupon dates, ISIN | `wdmlist_*.csv` | Real |
| G-Sec traded prices and weighted YTM | `trd*_sett.csv` | Real |
| **Our own book**: trades, desks, counterparties | generated | Synthetic |
| Funding curve | built from T-bill and G-Sec yields, spread-shifted | Derived |

The market is real; only *our positions* are invented, because a real desk's book
is not public. The generator is seeded and documented so results reproduce.

## Success criteria

1. `make up && make migrate && make ingest && make analytics` works from a clean
   clone on a machine with only Docker.
2. Every metric in [`07-analytics-spec.md`](07-analytics-spec.md) is computed by a
   named SQL file or a named pure function, and its formula is written down.
3. Bond math reproduces NSE's own published weighted YTM for at least 90% of
   G-Sec trades on a given day to within 2bp.
4. The FTP decomposition reconciles to book P&L to within ₹1.
5. CI runs migrations against a real Postgres service container and passes.
6. The dashboard loads in under a second against the seeded database.

## Stage plan

See [`../ROADMAP.md`](../ROADMAP.md). Ten stages, each pushed and tagged.
