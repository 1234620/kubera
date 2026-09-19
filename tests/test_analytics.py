"""Stage 3 check: the SLB analytics queries, against a real Postgres.

Loads the committed fixtures for one date into a transaction, refreshes the
derived tables, asserts the invariants, and rolls back. The properties tested
here hold on any data, so CI can verify them without a network or a backfill.

A few checks need real history (the 30-day baseline) and skip unless the
developer has actually ingested a month.
"""

import datetime as dt
import pathlib

import pytest

from slbdesk import db, queries
from slbdesk.ingest import load, parse

psycopg = pytest.importorskip("psycopg")

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DATE = dt.date(2026, 9, 18)


@pytest.fixture
def loaded():
    """Fixtures for one date, refreshed analytics, rolled back afterwards."""
    try:
        connection = db.connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no database: {exc}")

    with connection as conn:
        files = {
            "slb_quote_daily": parse.parse_slb_bhavcopy(
                (FIXTURES / "SLBM_BC_18092026.DAT").read_bytes()
            ),
            "cash_quote_daily": parse.parse_cash_bhavcopy(
                (FIXTURES / "sec_bhavdata_full_18092026.csv").read_bytes()
            ),
            "slb_open_position": parse.parse_slb_open_positions(
                (FIXTURES / "slb_openpos_18092026.csv").read_bytes(), DATE
            ),
        }
        for table, rows in files.items():
            load.upsert(conn, table, rows, extra={"source_file": "fixture"})

        for name in queries.REFRESH_ORDER:
            queries.run(conn, name)

        yield conn
        conn.rollback()


def rows(conn, statement: str, params=None) -> list[dict]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(statement, params)
        return cur.fetchall()


def date_count(conn) -> int:
    return conn.execute("SELECT COUNT(DISTINCT trade_date) FROM slb_quote_daily").fetchone()[0]


# --- the fee basis: the conversion everything depends on -------------------


def test_fee_annualisation_arithmetic(loaded):
    """fee/price x 365/tenor x 100, ACT/365, tenor from FIRST-LEG SETTLEMENT.

    Recomputed in Python from the same inputs. If this drifts, every downstream
    number is wrong by the same factor and nothing else would notice.

    Compared on an ABSOLUTE tolerance of half the column's last stored digit,
    not a relative one: fee_annualised_pct is NUMERIC(12,4), so 0.01bp is the
    intended resolution and a fee of 0.0007% p.a. is stored as exactly that.
    A relative tolerance would demand precision the column deliberately drops.
    """
    for row in rows(
        loaded,
        """
        SELECT fee_per_share, underlying_close, tenor_days, fee_annualised_pct
        FROM slb_fee_basis WHERE trade_date = %s LIMIT 50
        """,
        (DATE,),
    ):
        expected = (
            float(row["fee_per_share"])
            / float(row["underlying_close"])
            * (365.0 / row["tenor_days"])
            * 100
        )
        assert float(row["fee_annualised_pct"]) == pytest.approx(expected, abs=5e-5)


def test_tenor_runs_from_settlement_not_trade_date(loaded):
    """Using trade date would overstate every tenor by a day (docs/07 §5)."""
    for row in rows(
        loaded,
        """
        SELECT trade_date, first_leg_settle_date, reverse_leg_date, tenor_days
        FROM slb_fee_basis WHERE trade_date = %s LIMIT 20
        """,
        (DATE,),
    ):
        assert row["first_leg_settle_date"] > row["trade_date"]
        assert row["first_leg_settle_date"].weekday() < 5  # T+1 rolls over a weekend
        assert row["tenor_days"] == (row["reverse_leg_date"] - row["first_leg_settle_date"]).days


def test_fee_basis_prices_the_surveillance_segment_too(loaded):
    """HFCL trades in BE, so an EQ-only join would drop it (docs/05 §2.6)."""
    quoted = {r["symbol"] for r in rows(loaded, "SELECT DISTINCT symbol FROM slb_quote_daily")}
    priced = {r["symbol"] for r in rows(loaded, "SELECT DISTINCT symbol FROM slb_fee_basis")}
    if "HFCL" in quoted:
        assert "HFCL" in priced


# --- the GC benchmark -----------------------------------------------------


def test_gc_benchmark_is_built_and_flags_a_thin_cohort(loaded):
    built = rows(loaded, "SELECT * FROM gc_rate_daily WHERE as_of_date = %s", (DATE,))
    assert built

    for row in built:
        assert row["cohort_size"] > 0
        assert float(row["gc_fee_annualised_pct"]) > 0
        # A median of a dozen observations is not a market rate.
        assert row["is_estimated"] == (row["cohort_size"] < 20)


def test_gc_benchmark_sits_below_the_specials(loaded):
    """A GC rate above the median of what is on loan would mean it is not GC."""
    gc = rows(
        loaded,
        """
        SELECT g.contract_set, g.gc_fee_annualised_pct AS gc,
               MAX(s.fee_annualised_pct) AS worst
        FROM gc_rate_daily AS g
        JOIN slb_specialness_daily AS s
            ON s.trade_date = g.as_of_date AND s.contract_set = g.contract_set
        WHERE g.as_of_date = %s
        GROUP BY g.contract_set, g.gc_fee_annualised_pct
        """,
        (DATE,),
    )
    assert gc
    for row in gc:
        assert float(row["gc"]) < float(row["worst"])


# --- specialness ----------------------------------------------------------


def test_specialness_covers_open_positions_not_just_todays_prints(loaded):
    """~230 of ~800 open pairs print on a given day; the rest still hold risk."""
    scored = conn_count(loaded, "slb_specialness_daily")
    printed = conn_count(loaded, "slb_quote_daily")
    assert scored > printed


def conn_count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_specialness_score_is_bounded_and_classified_consistently(loaded):
    for row in rows(loaded, "SELECT * FROM slb_specialness_daily WHERE trade_date = %s", (DATE,)):
        score = float(row["specialness_score"])
        assert 0 <= score <= 100
        assert 0 <= float(row["xs_percentile"]) <= 1
        if row["own_z"] is not None:
            assert abs(float(row["own_z"])) <= 5.0001  # clamped

        expected = (
            "HARD_TO_BORROW"
            if score >= 90
            else "SPECIAL"
            if score >= 75
            else "WARM"
            if score >= 50
            else "GC"
        )
        assert row["classification"] == expected


def test_specialness_falls_back_to_cross_section_without_history(loaded):
    """A name with no usable baseline still has a borrow cost; it is not dropped."""
    no_history = rows(
        loaded,
        """
        SELECT xs_percentile, specialness_score
        FROM slb_specialness_daily
        WHERE trade_date = %s AND own_z IS NULL
        LIMIT 20
        """,
        (DATE,),
    )
    assert no_history, "one day of fixtures should give no baseline at all"
    for row in no_history:
        assert float(row["specialness_score"]) == pytest.approx(
            100 * float(row["xs_percentile"]), abs=1e-3
        )


def test_staleness_flag_matches_the_quote_age(loaded):
    for row in rows(
        loaded,
        """
        SELECT trade_date, quote_date, days_since_last_trade, is_stale
        FROM slb_specialness_daily WHERE trade_date = %s
        """,
        (DATE,),
    ):
        assert row["days_since_last_trade"] == (row["trade_date"] - row["quote_date"]).days
        assert row["is_stale"] == (row["days_since_last_trade"] > 3)


def test_specialness_rises_with_the_fee_within_a_tenor_bucket(loaded):
    """Inside a bucket the score must be monotone in the fee, or it is not a
    ranking of scarcity at all."""
    bucket = rows(
        loaded,
        """
        SELECT fee_annualised_pct, specialness_score
        FROM slb_specialness_daily
        WHERE trade_date = %s
          AND tenor_bucket = (
              SELECT tenor_bucket FROM slb_specialness_daily
              WHERE trade_date = %s
              GROUP BY tenor_bucket ORDER BY COUNT(*) DESC LIMIT 1
          )
          AND own_z IS NULL
        ORDER BY fee_annualised_pct
        """,
        (DATE, DATE),
    )
    scores = [float(r["specialness_score"]) for r in bucket]
    assert scores == sorted(scores)


# --- utilisation ----------------------------------------------------------


def test_utilisation_is_flagged_as_an_estimate(loaded):
    """Lendable supply is not published in India, so it must never look measured."""
    built = rows(loaded, "SELECT * FROM slb_utilisation_daily WHERE trade_date = %s", (DATE,))
    assert built
    assert all(row["is_estimated"] for row in built)


def test_days_to_cover_needs_no_estimate(loaded):
    """on_loan / average volume is fully observable, which is why it is reported."""
    built = rows(
        loaded,
        """
        SELECT on_loan_qty, avg_volume_20d, days_to_cover
        FROM slb_utilisation_daily
        WHERE trade_date = %s AND days_to_cover IS NOT NULL
        LIMIT 50
        """,
        (DATE,),
    )
    assert built
    for row in built:
        expected = row["on_loan_qty"] / float(row["avg_volume_20d"])
        # NUMERIC(12,4), so compare at the column's own scale.
        assert float(row["days_to_cover"]) == pytest.approx(expected, abs=5e-5)


# --- term structure -------------------------------------------------------


def test_term_structure_slope_uses_an_explicit_window_frame(loaded):
    """LAST_VALUE's default frame would make every slope zero (docs/07 §6)."""
    symbol = loaded.execute(
        """
        SELECT symbol FROM slb_fee_basis
        WHERE trade_date = %s
        GROUP BY symbol, contract_set
        HAVING COUNT(*) >= 2
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """,
        (DATE,),
    ).fetchone()
    if symbol is None:
        pytest.skip("no symbol quoted at two tenors on this date")

    curve = queries.fetch(loaded, "term_structure", {"symbol": symbol[0], "as_of": DATE})
    assert len(curve) >= 2

    # The curve is ordered by tenor WITHIN each contract set: the two sets are
    # separate curves and interleaving them would be a meaningless chart.
    by_set: dict[str, list[int]] = {}
    for row in curve:
        by_set.setdefault(row["contract_set"], []).append(row["tenor_days"])
    for tenors in by_set.values():
        assert tenors == sorted(tenors)

    # A non-zero slope somewhere is the proof the explicit window frame took
    # effect; LAST_VALUE's default frame would return the current row and give
    # every point a slope of exactly zero.
    assert any(float(row["term_slope_bps"]) != 0 for row in curve)


# --- the validation queries are themselves the test -----------------------


def test_every_validation_query_passes(loaded):
    failures = {name: rows_ for name, rows_ in queries.validate(loaded).items() if rows_}
    # validate_coverage compares against the newest ingested date, which a
    # single-date fixture load cannot satisfy if a real backfill is also present.
    failures.pop("validate_coverage", None)
    assert not failures, failures


def test_coverage_validation_passes_on_a_real_backfill(loaded):
    if date_count(loaded) < 20:
        pytest.skip("needs a real backfill; run `make ingest`")
    assert not queries.fetch(loaded, "validate_coverage")


def test_average_fee_rises_across_the_classification_bands(loaded):
    """The bands must actually separate cheap borrows from expensive ones.

    This is the property that says the score means something: nothing in the
    query sorts by fee across buckets, so monotonicity here is emergent.
    """
    if date_count(loaded) < 20:
        pytest.skip("needs a real backfill; run `make ingest`")

    bands = rows(
        loaded,
        """
        SELECT classification, AVG(fee_annualised_pct) AS avg_fee
        FROM slb_specialness_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM slb_specialness_daily)
        GROUP BY classification
        """,
    )
    by_band = {r["classification"]: float(r["avg_fee"]) for r in bands}
    order = ["GC", "WARM", "SPECIAL", "HARD_TO_BORROW"]
    present = [by_band[b] for b in order if b in by_band]
    assert present == sorted(present)
