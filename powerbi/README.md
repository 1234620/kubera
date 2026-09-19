# Power BI

**What is here and what is not.** This folder holds the model definition,
connection settings, the DAX measures and the exact SQL each table imports — but
**not a `.pbix` or `.pbit` file**. Power BI Desktop is Windows-only and a binary
template cannot be authored from outside it, so shipping a real one from this
repo is not possible. What is here is enough to rebuild the report in about ten
minutes, and every measure names the SQL metric it mirrors so the report and the
database cannot drift apart silently.

Building it is the one manual step in the project. Everything else is
`make bootstrap`.

## Connect

1. **Home → Get Data → PostgreSQL database**
2. Server `localhost:5432`, Database `slbdesk`
   The Compose file does not publish Postgres outside the Docker network, so
   either add `ports: ["5432:5432"]` to the `db` service or run Power BI on the
   Docker host with the port forwarded.
3. Credentials: the development values in `.env`. In anything real this would be
   a read-only role — see `docs/08-api-spec.md` on `slbdesk_ro`.
4. **Import mode, not DirectQuery.** The analytics tables are already aggregated
   by `make analytics`, so Import keeps the report instant and puts no load on
   the database. DirectQuery would re-run the aggregation on every slicer click.

## Tables to import

Import these and nothing else. They are the materialised outputs; importing the
raw market tables would pull a million rows to recompute what SQL already did.

| Table | Grain | Rows/day |
| --- | --- | --- |
| `desk_pnl_daily` | date × desk | 3 |
| `ftp_charge_daily` | date × desk × symbol × series | ~100 |
| `slb_position_pnl_daily` | date × trade | ~480 |
| `slb_specialness_daily` | date × symbol × series | ~680 |
| `slb_utilisation_daily` | date × symbol | ~370 |
| `gsec_analytics_daily` | date × ISIN | ~5 |
| `funding_curve_point` | date × tenor | 9 |
| `gc_rate_daily` | date × contract set | 2 |
| `margin_call` | date × repo | ~10 |
| `security`, `desk`, `slb_series`, `gsec` | reference | static |

`queries.sql` has a ready-to-paste `SELECT` for each, with the columns named
rather than `SELECT *`.

## Model

A date dimension is the only thing to add by hand, because none of the tables is
a clean date table on its own:

```
Date = CALENDAR(MIN('desk_pnl_daily'[as_of_date]), MAX('desk_pnl_daily'[as_of_date]))
```

Mark it as a date table, then build single-direction one-to-many relationships
from `Date[Date]` to each fact table's date column, and from `security[symbol]`
and `desk[desk_id]` to the facts. Leave cross-filtering single-direction:
bidirectional relationships here produce ambiguous paths between the position
table and the specialness table, which both key on symbol.

## Pages

1. **Book overview** — the KPI cards, on-loan over time, desk split
2. **Specialness explorer** — score matrix by symbol × tenor bucket, fee vs GC
3. **Term structure** — fee by tenor, sliced by symbol
4. **Repo & margin** — collateral value, haircut, shortfalls, DV01
5. **FTP attribution** — desk spread vs treasury spread, funding curve

Measures: [`measures.md`](measures.md).
