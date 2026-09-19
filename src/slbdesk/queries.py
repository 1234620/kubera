"""Read the SQL files in db/queries and run them.

The SQL lives in files, not in Python string literals, because it is a
deliverable in its own right (ADR 0001). This module is the thin thing that
loads them; parameters are always bound, never formatted in.
"""

from __future__ import annotations

import functools
import pathlib

import psycopg

QUERIES = pathlib.Path(__file__).parents[2] / "db" / "queries"

# Order matters: specialness reads gc_rate_daily, so the benchmark is built first.
REFRESH_ORDER = ("gc_benchmark", "utilisation", "specialness")

VALIDATIONS = (
    "validate_coverage",
    "validate_staleness",
    "validate_specialness_bounds",
    "validate_tenure",
    "validate_legs",
    "validate_ftp_zero",
    "validate_ftp_reconciliation",
    "validate_matched_book_pays_little_ftp",
)


@functools.cache
def sql(name: str) -> str:
    path = QUERIES / f"{name}.sql"
    if not path.is_file():
        raise FileNotFoundError(f"no such query: {path}")
    return path.read_text()


def run(conn: psycopg.Connection, name: str, params: dict | None = None) -> int:
    """Execute a writing query (a refresh). Returns rows affected."""
    with conn.cursor() as cur:
        cur.execute(sql(name), params)
        return cur.rowcount


def fetch(conn: psycopg.Connection, name: str, params: dict | None = None) -> list[dict]:
    """Execute a reading query and return rows as dicts."""
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql(name), params)
        return cur.fetchall()


def validate(conn: psycopg.Connection) -> dict[str, list[dict]]:
    """Run every validation query. A query returning rows is a failure.

    Validations return the offending rows rather than a boolean, so a failure
    says what is wrong instead of only that something is.
    """
    return {name: fetch(conn, name) for name in VALIDATIONS}
