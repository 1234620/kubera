# 08 — API

FastAPI, read-only, no auth. One `psycopg` connection pool, one module per
resource, Pydantic response models. Serves `web/` as static files at `/`.

Interactive docs at `/docs` — that is FastAPI's OpenAPI UI and it is the reason
this project does not hand-write API documentation.

## Conventions

- Dates are ISO `YYYY-MM-DD`. Omitting `as_of` means the latest ingested date.
- Every response carrying an estimate exposes `is_estimated`; every response
  carrying a possibly stale quote exposes `is_stale` and `days_since_last_trade`.
  These are not internal flags — they reach the UI and it labels them.
- Money is a JSON number in **rupees**. Rates are percent. Field names end `_inr`,
  `_pct` or `_bps` so a unit mistake is visible in the payload.
- Pagination: `limit` (default 100, max 1000) and `offset`. No cursor pagination
  until there is a table big enough to need it.
- Errors: FastAPI's default `{"detail": ...}`. 404 for an unknown symbol, 422 for a
  bad date. No custom error envelope.

## Endpoints

### Reference
```
GET /api/securities?search=&limit=
GET /api/series?as_of=
GET /api/desks
```

### Market
```
GET /api/slb/quotes?symbol=&series=&from=&to=
GET /api/slb/term-structure?symbol=&as_of=
    → [{series_code, tenor_days, fee_per_share, fee_annualised_pct, num_trades}]
GET /api/slb/utilisation?symbol=&as_of=
    → {symbol, on_loan_qty, lendable_qty, utilisation_pct, change_1d_pct, is_estimated}
GET /api/slb/specialness?as_of=&min_score=&classification=&limit=
    → [{symbol, series_code, specialness_score, xs_percentile, own_z,
        classification, fee_annualised_pct, is_stale, days_since_last_trade}]
GET /api/slb/gc-rate?from=&to=
```

### Book
```
GET /api/book/kpis?as_of=
GET /api/book/positions?desk=&status=&symbol=&limit=&offset=
    → [{trade_id, desk_id, symbol, series_code, side, quantity, notional_inr,
        fee_per_share, fee_annualised_pct, daily_fee_accrual_inr,
        ftp_charge_inr, net_spread_inr, specialness_score, classification}]
GET /api/book/pnl?desk=&from=&to=&group_by=day|desk|symbol
    → [{bucket, gross_spread_inr, fee_spread_inr, term_spread_inr,
        funding_drag_inr, net_spread_inr}]
GET /api/book/trade/{trade_id}
    → the trade plus its full slb_trade_leg history
```

### Bonds
```
GET /api/bonds?instrument_type=GS|TB&limit=
GET /api/bonds/{isin}/analytics?as_of=
    → {isin, security_code, coupon_pct, maturity_date, settlement_date,
       clean_price, accrued_interest, dirty_price, ytm_pct,
       macaulay_duration, modified_duration, convexity, dv01_per_100_face}
POST /api/bonds/price
    → body {coupon_pct, maturity_date, settlement_date, clean_price|ytm_pct,
            face_value, coupon_freq}
```

`POST /api/bonds/price` is the only non-`GET`. It computes and returns; it writes
nothing. It exists because an ad-hoc pricing calculator is the single most useful
thing to demo live, and it is also what the frontend's bond panel calls.

### Repo
```
GET /api/repo/trades?desk=&status=
GET /api/repo/collateral?as_of=&repo_id=
GET /api/repo/margin-calls?as_of=&status=
    → [{call_id, repo_id, desk_id, exposure_inr, collateral_value_inr,
        shortfall_inr, call_amount_inr, due_at, status}]
```

### FTP
```
GET /api/ftp/curve?as_of=
    → [{tenor_days, base_rate_pct, term_liquidity_premium_bps, source, is_estimated}]
GET /api/ftp/charges?desk=&from=&to=
GET /api/ftp/decomposition?as_of=&desk=
    → [{desk_id, nim_inr, desk_spread_inr, treasury_spread_inr, reconciles}]
```

`reconciles` is a boolean on the response. The API states whether its own numbers
add up rather than leaving the reader to check.

### Meta
```
GET /api/health          → {status, db, latest_ingest_date}
GET /api/data-coverage   → per market table: min date, max date, row count, last loaded_at
```

`/api/data-coverage` is what you open first when a chart looks wrong.

## Implementation notes

- Analytics queries are read from `db/queries/*.sql` at import and executed with
  bound parameters. No f-string SQL, anywhere — parameters are always `%s`.
- Endpoints that map to a materialised table are a single `SELECT` with a `WHERE`.
  There is no service layer; a repository pattern over six queries is an
  abstraction with one implementation.
- `src/slbdesk/api/main.py` mounts `web/` with `StaticFiles(html=True)` so
  `GET /` serves the dashboard and the API lives under `/api`. One process, one
  port, no CORS configuration, no reverse proxy.
- Read-only role: the API connects as `slbdesk_ro` with `SELECT` only. Ingestion and
  migrations use a separate role. Cheap, and it means a bug in an endpoint cannot
  corrupt the book.
