"""Stage 2 check: the loader is idempotent and the schema actually rejects bad data.

Runs against a real migrated Postgres (CI provides one as a service container) and
skips if none is reachable. Every test works inside a transaction that is rolled
back, so the suite leaves the database exactly as it found it.
"""

import datetime as dt
import pathlib

import pytest

from slbdesk import db
from slbdesk.ingest import load, parse

psycopg = pytest.importorskip("psycopg")

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DATE = dt.date(2026, 9, 18)


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no database: {exc}")

    with connection:
        connection.execute("SELECT 1 FROM schema_migration LIMIT 1")
        yield connection
        connection.rollback()


def quotes(shift_years: int = 0) -> list[dict]:
    """Fixture quotes, optionally moved to a date the real backfill cannot occupy.

    The tests run against whatever the developer has already ingested, so a test
    that needs to count its own inserts has to own its own date.
    """
    rows = parse.parse_slb_bhavcopy((FIXTURES / "SLBM_BC_18092026.DAT").read_bytes())
    if not shift_years:
        return rows

    for row in rows:
        for field in ("trade_date", "reverse_leg_date"):
            row[field] = row[field].replace(year=row[field].year + shift_years)
    return rows


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


# --- idempotence ----------------------------------------------------------


def test_upsert_is_idempotent(conn):
    """Loading the same file twice must not duplicate a row.

    This is the property that makes `make ingest` safe to re-run, and it is worth
    a test because it depends on the natural key in KEYS matching the table's
    primary key -- two declarations that could silently drift apart.
    """
    rows = quotes(shift_years=10)
    before = count(conn, "slb_quote_daily")

    load.upsert(conn, "slb_quote_daily", rows, extra={"source_file": "a.DAT"})
    after_first = count(conn, "slb_quote_daily")

    load.upsert(conn, "slb_quote_daily", rows, extra={"source_file": "a.DAT"})
    after_second = count(conn, "slb_quote_daily")

    assert after_first == before + len(rows)
    assert after_second == after_first


def test_upsert_updates_a_changed_value(conn):
    """A re-published file must correct the row, not be silently ignored."""
    rows = quotes(shift_years=10)[:5]
    load.upsert(conn, "slb_quote_daily", rows, extra={"source_file": "a.DAT"})

    corrected = [dict(row) for row in rows]
    corrected[0]["close_fee"] = 99.9999

    load.upsert(conn, "slb_quote_daily", corrected, extra={"source_file": "b.DAT"})

    stored = conn.execute(
        "SELECT close_fee, source_file FROM slb_quote_daily "
        "WHERE trade_date = %s AND symbol = %s AND series_code = %s",
        (rows[0]["trade_date"], rows[0]["symbol"], rows[0]["series_code"]),
    ).fetchone()
    assert float(stored[0]) == pytest.approx(99.9999)
    assert stored[1] == "b.DAT"


def test_upsert_of_nothing_is_a_no_op(conn):
    assert load.upsert(conn, "slb_quote_daily", []) == 0


def test_every_loader_key_matches_the_primary_key(conn):
    """KEYS and the migrations must agree, or upsert silently duplicates rows."""
    for table, key in load.KEYS.items():
        actual = conn.execute(
            """
            SELECT a.attname
            FROM pg_index AS i
            JOIN pg_attribute AS a
                ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary
            ORDER BY a.attname
            """,
            (table,),
        ).fetchall()
        assert sorted(key) == [row[0] for row in actual], table


# --- the schema has to defend itself --------------------------------------


def test_reverse_leg_must_be_after_trade_date(conn):
    """docs/01 §4: a position runs from first-leg settlement to the reverse leg."""
    row = dict(quotes()[0])
    row["reverse_leg_date"] = row["trade_date"] - dt.timedelta(days=1)

    with pytest.raises(psycopg.errors.CheckViolation):
        load.upsert(conn, "slb_quote_daily", [row], extra={"source_file": "a.DAT"})


def test_var_margin_components_must_sum_to_the_total(conn):
    """The identity holds for all 84,389 rows of the source, so the DB enforces it."""
    rows = parse.parse_slb_var((FIXTURES / "C_VAR1_SLB_18092026_1_sample.DAT").read_bytes(), DATE)
    load.upsert(conn, "slb_var_margin", rows, extra={"source_file": "v.DAT"})

    broken = dict(rows[0])
    broken["total_margin_pct"] = 99.0
    with pytest.raises(psycopg.errors.CheckViolation):
        load.upsert(conn, "slb_var_margin", [broken], extra={"source_file": "v.DAT"})


def test_open_position_quantity_must_be_positive(conn):
    rows = parse.parse_slb_open_positions(
        (FIXTURES / "slb_openpos_18092026.csv").read_bytes(), DATE
    )
    broken = dict(rows[0])
    broken["outstanding_qty"] = 0
    with pytest.raises(psycopg.errors.CheckViolation):
        load.upsert(conn, "slb_open_position", [broken], extra={"source_file": "o.csv"})


def test_tbill_may_have_no_coupon_but_a_gsec_may_not(conn):
    """gsec's CHECK encodes that a dated security must carry a coupon schedule."""
    rows = parse.parse_gsec_master((FIXTURES / "wdmlist_18092026_gsec.csv").read_bytes())
    load.upsert(conn, "gsec", rows, extra={"source_file": "w.csv"})

    broken = dict(rows[0])
    broken["coupon_pct"] = None
    with pytest.raises(psycopg.errors.CheckViolation):
        load.upsert(conn, "gsec", [broken], extra={"source_file": "w.csv"})


# --- derived reference ----------------------------------------------------


def test_derive_reference_builds_the_universe_from_market_data(conn):
    """security and slb_series are derived, so they cannot drift from the files."""
    load.upsert(conn, "slb_quote_daily", quotes(), extra={"source_file": "a.DAT"})
    counts = load.derive_reference(conn)

    assert counts["security"] > 0
    assert counts["slb_series"] > 0

    # A series code means one reverse-leg date on a given day, and the contract
    # set must be one of the three from docs/01 §3.
    sets = conn.execute("SELECT DISTINCT contract_set FROM slb_series").fetchall()
    assert {row[0] for row in sets} <= {"REGULAR", "NON_FORECLOSING", "ROLLOVER"}

    symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM slb_quote_daily").fetchone()[0]
    assert count(conn, "security") == symbols


def test_derive_reference_is_idempotent(conn):
    load.upsert(conn, "slb_quote_daily", quotes(), extra={"source_file": "a.DAT"})
    load.derive_reference(conn)
    first = count(conn, "security"), count(conn, "slb_series")
    load.derive_reference(conn)
    assert (count(conn, "security"), count(conn, "slb_series")) == first
