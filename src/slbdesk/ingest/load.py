"""Load parsed rows into Postgres, idempotently.

Every load is an upsert on the table's natural key, so re-running a date or a
whole backfill changes nothing. Parsers hand over plain dicts whose keys are the
column names, which is why one generic function covers every table.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

# table -> natural key. These match the primary keys in db/migrations.
KEYS = {
    "slb_quote_daily": ("trade_date", "symbol", "series_code"),
    "slb_open_position": ("trade_date", "symbol", "series_code"),
    "slb_eligibility": ("as_of_date", "symbol", "series_code"),
    "slb_foreclosure": ("symbol", "record_date", "action_desc"),
    "slb_var_margin": ("as_of_date", "symbol"),
    "slb_series_universe": ("as_of_date", "series_code"),
    "cash_quote_daily": ("trade_date", "symbol", "series"),
    "gsec_trade_daily": ("trade_date", "security_code", "settl_days"),
    "gsec": ("isin",),
}


def upsert(
    conn: psycopg.Connection,
    table: str,
    rows: list[dict],
    extra: dict | None = None,
) -> int:
    """Upsert rows into `table` on its natural key.

    `extra` is merged in as a constant for every row -- used for source_file,
    which is the same for a whole file and not worth repeating in a million dicts.
    """
    if not rows:
        return 0

    extra = extra or {}
    columns = [*rows[0], *extra]
    key = KEYS[table]
    updatable = [c for c in columns if c not in key]

    statement = sql.SQL(
        "INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
        "ON CONFLICT ({key}) DO UPDATE SET {assignments}"
    ).format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
        key=sql.SQL(", ").join(map(sql.Identifier, key)),
        assignments=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in updatable
        ),
    )

    constants = tuple(extra.values())
    with conn.cursor() as cur:
        cur.executemany(statement, [tuple(row.values()) + constants for row in rows])
    return len(rows)


# Reference tables are derived from the market tables rather than loaded, so the
# universe cannot drift from what NSE actually published, and load order does not
# matter. Run after the market loads.
DERIVE_SECURITY = """
INSERT INTO security (symbol, security_name, isin, first_seen, last_seen)
SELECT
    q.symbol,
    MAX(q.security_name)   AS security_name,
    MAX(v.isin)            AS isin,
    MIN(q.trade_date)      AS first_seen,
    MAX(q.trade_date)      AS last_seen
FROM slb_quote_daily AS q
LEFT JOIN slb_var_margin AS v
    ON v.symbol = q.symbol
GROUP BY q.symbol
ON CONFLICT (symbol) DO UPDATE SET
    security_name = COALESCE(EXCLUDED.security_name, security.security_name),
    isin          = COALESCE(EXCLUDED.isin, security.isin),
    first_seen    = LEAST(security.first_seen, EXCLUDED.first_seen),
    last_seen     = GREATEST(security.last_seen, EXCLUDED.last_seen)
"""

# A series code is reused every year, so the contract is (code, reverse_leg_date).
# Both come from the bhavcopy: observed, never hardcoded.
DERIVE_SLB_SERIES = """
INSERT INTO slb_series (series_code, reverse_leg_date, contract_set, first_seen, last_seen)
SELECT
    series_code,
    reverse_leg_date,
    MIN(contract_set) AS contract_set,
    MIN(trade_date)   AS first_seen,
    MAX(trade_date)   AS last_seen
FROM slb_quote_daily
GROUP BY series_code, reverse_leg_date
ON CONFLICT (series_code, reverse_leg_date) DO UPDATE SET
    first_seen = LEAST(slb_series.first_seen, EXCLUDED.first_seen),
    last_seen  = GREATEST(slb_series.last_seen, EXCLUDED.last_seen)
"""


def derive_reference(conn: psycopg.Connection) -> dict[str, int]:
    counts = {}
    with conn.cursor() as cur:
        for table, statement in (
            ("security", DERIVE_SECURITY),
            ("slb_series", DERIVE_SLB_SERIES),
        ):
            cur.execute(statement)
            counts[table] = cur.rowcount
    return counts
