# 06 — Data model

Postgres 16. Four layers, one schema (`public`) — separate schemas would be
ceremony at this size. Naming mirrors the source file so provenance is obvious.

```
reference ──▶ market ──▶ book ──▶ analytics (derived)
                          ▲
                        curves
```

## Layer 1 — Reference

### `security`
One row per SLB-eligible equity.

| Column | Type | Note |
| --- | --- | --- |
| `symbol` | `TEXT PK` | NSE symbol, e.g. `RELIANCE` |
| `security_name` | `TEXT` | From the bhavcopy |
| `isin` | `TEXT` | From the foreclosure / VaR file |
| `first_seen` / `last_seen` | `DATE` | Delistings show up as a stale `last_seen` |

### `slb_series`
**Populated from the data, not hardcoded.** A series code alone does not give a
tenor — the reverse-leg date does, and it is republished every day.

| Column | Type | Note |
| --- | --- | --- |
| `series_code` | `TEXT` | `XN`, `07`, `O1`, … |
| `reverse_leg_date` | `DATE` | Observed in the bhavcopy |
| `contract_set` | `TEXT` | `REGULAR` (`01`–`12`), `NON_FORECLOSING` (`X*`), `ROLLOVER` (everything else) |
| `PRIMARY KEY (series_code, reverse_leg_date)` | | A code is reused every year |

`contract_set` is derived by pattern, and the classifier is one function with the
rule from [`01-domain-slb-lifecycle.md`](01-domain-slb-lifecycle.md) §3 in a
comment above it.

### `gsec`
Central-government paper from `wdmlist`, `SECTYPE IN ('GS','TB')`.

| Column | Type | Note |
| --- | --- | --- |
| `security_code` | `TEXT` | `CG2036` |
| `isin` | `TEXT PK` | |
| `instrument_type` | `TEXT` | `GS` or `TB` |
| `coupon_pct` | `NUMERIC(8,4)` | Parsed from `ISSUE_NAME`, cross-checked against `ISSUE_DESC`; `NULL` for T-bills |
| `issue_date`, `maturity_date` | `DATE` | |
| `last_ip_date`, `next_ip_date` | `DATE` | The real coupon period — the reason stub handling is correct |
| `coupon_freq` | `SMALLINT` | 2 for `Half Yearly`; read, not assumed |
| `face_value` | `NUMERIC(18,4)` | 100 |

### `desk`
`desk_id`, `desk_name`, `desk_type` (`FINANCING`, `REPO`, `ARB`, `TREASURY`).
Four rows. See [`04-domain-ftp.md`](04-domain-ftp.md) §6.

## Layer 2 — Market (one table per source file)

| Table | Source | Grain | Natural key |
| --- | --- | --- | --- |
| `slb_quote_daily` | `SLBM_BC_*.DAT` | day × symbol × series | `(trade_date, symbol, series_code)` |
| `slb_open_position` | `slb_openpos_*.csv` | day × symbol × series | `(trade_date, symbol, series_code)` |
| `slb_eligibility` | `SLB_ELG_SEC_*.csv` | day × symbol × series | `(as_of_date, symbol, series_code)` |
| `slb_foreclosure` | `Forclosure_SLB_*.CSV` | event | `(symbol, record_date, action_desc)` |
| `slb_var_margin` | `C_VAR1_SLB_*.DAT` | day × ISIN | `(as_of_date, isin)` |
| `gsec_trade_daily` | `trd*_sett.csv` | day × security × settl days | `(trade_date, security_code, settl_days)` |

`slb_quote_daily` columns: `prev_close_fee`, `open_fee`, `high_fee`, `low_fee`,
`close_fee`, `year_high_fee`, `year_low_fee`, `traded_qty`, `traded_value_inr`,
`num_trades`, `vwaf`, `reverse_leg_date`. All fees `NUMERIC(12,4)` in **₹/share for
the contract period**; `vwaf = traded_value_inr / traded_qty` computed on load.

Every market table carries `source_file TEXT` and `loaded_at TIMESTAMPTZ`. When a
number on the dashboard looks wrong the first question is which file it came from,
and this answers it without a re-run.

## Layer 3 — Book (synthetic, seeded)

### `slb_trade`
The position. One row per economic trade; the state machine from
[`01-domain-slb-lifecycle.md`](01-domain-slb-lifecycle.md) §10 lives in `status`.

| Column | Type | Note |
| --- | --- | --- |
| `trade_id` | `BIGSERIAL PK` | |
| `original_trade_id` | `BIGINT` | Self-FK; survives rollovers, so total tenure is checkable |
| `desk_id` | `TEXT FK` | |
| `symbol`, `series_code` | `TEXT FK` | |
| `side` | `TEXT` | `LEND` or `BORROW` |
| `quantity` | `BIGINT` | Shares |
| `trade_date` | `DATE` | T |
| `first_leg_settle_date` | `DATE` | T+1; **the accrual start** |
| `reverse_leg_date` | `DATE` | The series date |
| `fee_per_share` | `NUMERIC(12,4)` | Contract-period fee, as quoted |
| `status` | `TEXT` | `MATCHED`, `SETTLED_L`, `ROLLED`, `UNWOUND`, `FORECLOSED`, `CLOSED`, `CLOSED_OUT` |

`CHECK (reverse_leg_date > first_leg_settle_date)` and a trigger-free check that
the 12-month tenure cap holds against `original_trade_id` — expressed as a
constraint-style query in `db/queries/validate_tenure.sql`, run by `make analytics`,
because an exclusion constraint across a self-join is more machinery than this
needs.

### `slb_trade_leg`
**Append-only fee history.** A recall, a repay, a rollover or a re-rate writes a new
leg; it never mutates `slb_trade.fee_per_share`. This is what makes it possible to
attribute P&L to a re-rate versus to the original trade, which is the whole point.

`leg_id`, `trade_id`, `effective_from`, `effective_to`, `quantity`,
`fee_per_share`, `leg_type` (`OPEN`, `RERATE`, `PARTIAL_REPAY`, `RECALL`,
`ROLLOVER`, `CLOSE`), `reason`.

`effective_to` is `NULL` on the live leg. A `DATERANGE` and a `GIST` exclusion
constraint would enforce non-overlap; a `NUMERIC` check plus one validation query
is enough here and reads more clearly. *`ponytail:` no temporal constraint — add a
`DATERANGE` exclusion if leg editing ever becomes interactive.*

### `repo_trade`
`repo_id`, `desk_id`, `direction` (`REPO` = we borrow cash, `REVERSE` = we lend
cash), `isin` (collateral), `nominal`, `trade_date`, `start_date`, `end_date`,
`repo_rate_pct`, `haircut`, `purchase_price`, `repurchase_price`, `status`.

Both prices are **stored**, not recomputed on read: the haircut and the collateral's
dirty value on day one are historical facts, and recomputing them from today's
prices silently rewrites history.

### `collateral_position` and `margin_call`
`collateral_position`: `as_of_date`, `repo_id`, `isin`, `nominal`,
`mtm_clean_price`, `accrued_interest`, `dirty_value`, `haircut`,
`post_haircut_value`, `haircut_source` (`NSE_VAR` or `TENOR_MODEL`).

`margin_call`: `call_id`, `as_of_date`, `repo_id`, `desk_id`, `exposure`,
`collateral_value`, `shortfall`, `call_amount` (shortfall rounded up to the minimum
transfer amount), `due_at TIMESTAMPTZ` (09:00 next business day, per CCIL),
`status` (`OPEN`, `MET`, `BREACHED`).

## Layer 4 — Curves and analytics

### `funding_curve_point`
`as_of_date`, `tenor_days`, `base_rate_pct`, `term_liquidity_premium_bps`,
`source` (`TBILL`, `GSEC`, `INTERPOLATED`), `is_estimated BOOLEAN`.

### `gc_rate_daily`
`as_of_date`, `gc_fee_per_share_median`, `gc_fee_annualised_pct`, `cohort_size`.
The GC benchmark from [`07-analytics-spec.md`](07-analytics-spec.md) §3.

### Derived tables
Materialised by `make analytics`, each from exactly one SQL file, each with the
query's name in a `source_query` column so the number is traceable to the code that
made it:

`slb_utilisation_daily`, `slb_specialness_daily`, `slb_position_pnl_daily`,
`gsec_analytics_daily`, `ftp_charge_daily`, `desk_pnl_daily`.

Materialised tables rather than views because the specialness query has three
window functions over a growing history and the dashboard has to load in under a
second. *`ponytail:` full refresh each run — switch to incremental on `trade_date`
if the history passes a few years.*

## Indexes

Every `trade_date` / `as_of_date` column, every natural key as a unique index (which
is what `ON CONFLICT` needs anyway), plus `(symbol, trade_date)` on
`slb_quote_daily` because the trailing-window specialness baseline partitions by
symbol and orders by date. That composite is the one index that is a performance
decision rather than a correctness one, and the plan is recorded in the query
header.

## Why no ORM

[`adr/0002-no-orm.md`](adr/0002-no-orm.md). Short version: the SQL is the
deliverable.
