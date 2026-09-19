"""Stage 1 check: parsers against the committed real NSE files. No network.

Every assertion here is a value read off the actual file, so a silent layout
change or a unit slip fails the build.
"""

import datetime as dt
import pathlib
import re

import pytest

from slbdesk.ingest import nse, parse

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DATE = dt.date(2026, 9, 18)


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- series classification ------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("01", "REGULAR"),
        ("12", "REGULAR"),
        ("X1", "NON_FORECLOSING"),
        ("X9", "NON_FORECLOSING"),
        ("XO", "NON_FORECLOSING"),
        ("XN", "NON_FORECLOSING"),
        ("XD", "NON_FORECLOSING"),
        # Rollover codes encode source and target month: JL is June -> July,
        # JA June -> August, with U1/U2 the non-foreclosing equivalents.
        ("JL", "ROLLOVER"),
        ("U1", "ROLLOVER"),
        ("O1", "ROLLOVER"),
        ("R3", "ROLLOVER"),
    ],
)
def test_classify_series(code, expected):
    assert parse.classify_series(code) == expected


def test_classify_series_rejects_out_of_range_digits():
    assert parse.classify_series("13") == "ROLLOVER"


# --- SLB bhavcopy ---------------------------------------------------------


def test_slb_bhavcopy():
    rows = parse.parse_slb_bhavcopy(load("SLBM_BC_18092026.DAT"))
    assert len(rows) == 227

    abb = next(r for r in rows if r["symbol"] == "ABB" and r["series_code"] == "XN")
    assert abb["trade_date"] == DATE
    assert abb["reverse_leg_date"] == dt.date(2026, 11, 3)
    assert abb["contract_set"] == "NON_FORECLOSING"
    assert abb["prev_close_fee"] == 17.28
    assert abb["close_fee"] == 12.00
    assert abb["traded_qty"] == 875
    assert abb["traded_value_inr"] == 10500.00
    assert abb["num_trades"] == 12
    assert abb["vwaf"] == pytest.approx(12.00)

    # VWAF must fall inside the day's fee range for every row.
    for row in rows:
        assert row["low_fee"] - 1e-6 <= row["vwaf"] <= row["high_fee"] + 1e-6


def test_slb_bhavcopy_reverse_leg_is_after_trade_date():
    for row in parse.parse_slb_bhavcopy(load("SLBM_BC_18092026.DAT")):
        assert row["reverse_leg_date"] > row["trade_date"]


def test_series_code_maps_to_one_reverse_leg_date():
    """The premise of slb_series: on a given day a code means exactly one date."""
    rows = parse.parse_slb_bhavcopy(load("SLBM_BC_18092026.DAT"))
    by_code: dict[str, set] = {}
    for row in rows:
        by_code.setdefault(row["series_code"], set()).add(row["reverse_leg_date"])
    assert all(len(dates) == 1 for dates in by_code.values())


# --- other SLB files ------------------------------------------------------


def test_slb_open_positions():
    rows = parse.parse_slb_open_positions(load("slb_openpos_18092026.csv"), DATE)
    assert len(rows) == 802
    assert all(r["outstanding_qty"] > 0 and r["trade_date"] == DATE for r in rows)

    first = next(r for r in rows if r["symbol"] == "360ONE" and r["series_code"] == "X1")
    assert first["outstanding_qty"] == 2


def test_slb_eligibility():
    rows = parse.parse_slb_eligibility(load("SLB_ELG_SEC_18092026_sample.csv"), DATE)
    assert rows
    assert all(isinstance(r["recall_eligible"], bool) for r in rows)

    # In the sampled file every series has normal trading on but recall and repay
    # switched off -- which is exactly the case the FTP liquidity charge prices.
    first = rows[0]
    assert first["symbol"] == "360ONE"
    assert first["normal_eligible"] is True
    assert first["recall_eligible"] is False
    assert first["repay_eligible"] is False


def test_slb_foreclosure():
    rows = parse.parse_slb_foreclosure(load("Forclosure_SLB_20260918.CSV"))
    # Five events, plus a trailing legend block the parser must skip.
    assert len(rows) == 5

    heg = next(r for r in rows if r["symbol"] == "HEG")
    assert heg["isin"] == "INE545A01024"
    assert heg["record_date"] == dt.date(2026, 9, 7)
    assert heg["ex_date"] == dt.date(2026, 9, 7)
    assert heg["action_code"] == "DEMERGER"
    assert heg["action_desc"] == "DEMERGER"

    # Other-than-dividend actions get a 7-day shut period (docs/01 §7).
    assert (heg["shut_period_end"] - heg["shut_period_start"]).days >= 7

    bonus = next(r for r in rows if r["symbol"] == "PGIL")
    assert bonus["action_code"] == "BONUS 1:1"


# --- cash market ----------------------------------------------------------


def test_cash_bhavcopy():
    rows = parse.parse_cash_bhavcopy(load("sec_bhavdata_full_18092026.csv"))
    assert len(rows) == 3508
    assert all(r["trade_date"] == DATE for r in rows)
    assert sum(r["series"] == "EQ" for r in rows) == 2647

    reliance = next(r for r in rows if r["symbol"] == "RELIANCE")
    assert reliance["close_price"] == 1226.40
    assert reliance["traded_qty"] == 15_122_715
    # Published as 186728.32 lakhs; a unit slip here is a factor of 100,000.
    assert reliance["turnover_inr"] == pytest.approx(18_672_832_000.0)
    assert reliance["delivery_pct"] == pytest.approx(77.01)


def test_cash_bhavcopy_covers_the_slb_universe():
    """Fees cannot be annualised without an underlying close, so coverage matters.

    HFCL is the case that forces this: it is SLB-eligible but trades in the `BE`
    surveillance segment, so an EQ-only filter would leave it unpriceable.
    """
    cash = parse.parse_cash_bhavcopy(load("sec_bhavdata_full_18092026.csv"))
    slb = {r["symbol"] for r in parse.parse_slb_bhavcopy(load("SLBM_BC_18092026.DAT"))}

    assert not slb - {r["symbol"] for r in cash}
    assert slb - {r["symbol"] for r in cash if r["series"] == "EQ"} == {"HFCL"}


def test_cash_bhavcopy_handles_missing_delivery_data():
    """The BE segment publishes '-' for delivery, which must become None, not 0."""
    rows = parse.parse_cash_bhavcopy(load("sec_bhavdata_full_18092026.csv"))
    hfcl = next(r for r in rows if r["symbol"] == "HFCL")
    assert hfcl["series"] == "BE"
    assert hfcl["close_price"] == 215.71
    assert hfcl["delivery_qty"] is None
    assert hfcl["delivery_pct"] is None


# --- G-Secs ---------------------------------------------------------------


def test_gsec_master():
    rows = parse.parse_gsec_master(load("wdmlist_18092026_gsec.csv"))
    assert len(rows) == 120
    assert all(r["instrument_type"] == "GS" for r in rows)

    bond = next(r for r in rows if r["security_code"] == "CG2036" and r["coupon_pct"] == 8.33)
    assert bond["isin"] == "IN0020060045"
    assert bond["maturity_date"] == dt.date(2036, 6, 7)
    assert bond["last_ip_date"] == dt.date(2026, 6, 7)
    assert bond["next_ip_date"] == dt.date(2026, 12, 7)
    assert bond["coupon_freq"] == 2


def test_gsec_master_coupon_agrees_with_description():
    """docs/05 §3.1: coupon comes from ISSUE_NAME and is cross-checked here.

    Compared numerically, because NSE writes the rate unpadded in both fields
    ('8.6%', not '8.60%').
    """
    for row in parse.parse_gsec_master(load("wdmlist_18092026_gsec.csv")):
        in_desc = re.search(r"(\d+\.?\d*)%", row["issue_desc"])
        assert in_desc, row["issue_desc"]
        assert float(in_desc.group(1)) == pytest.approx(row["coupon_pct"])


def test_gsec_master_coupon_period_brackets_the_trade_date():
    for row in parse.parse_gsec_master(load("wdmlist_18092026_gsec.csv")):
        assert row["last_ip_date"] < row["next_ip_date"]
        assert row["next_ip_date"] <= row["maturity_date"]


def test_gsec_trades():
    rows = parse.parse_gsec_trades(load("dly18092026.zip"), DATE)
    assert rows

    gs = [r for r in rows if r["instrument_type"] == "GS"]
    assert gs
    assert all(r["settl_days"] == 1 for r in gs)  # G-Secs settle T+1

    bond = next(r for r in gs if r["security_code"] == "CG2026")
    assert bond["vwap_clean_price"] == pytest.approx(100.2169)
    assert bond["weighted_ytm_pct"] == pytest.approx(5.24)
    # Published as 300.00 crore.
    assert bond["traded_value_inr"] == pytest.approx(3_000_000_000.0)

    for row in rows:
        assert row["low_price"] <= row["vwap_clean_price"] <= row["high_price"]


# --- URL construction -----------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected_tail"),
    [
        ("slb_bhavcopy", "archives/slbs/bhavcopy/SLBM_BC_18092026.DAT"),
        ("slb_open_positions", "archives/slbs/open_pos/slb_openpos_18092026.csv"),
        ("slb_eligibility", "archives/slbs/seclist/SLB_ELG_SEC_18092026.csv"),
        # This one file uses YYYYMMDD while its siblings use DDMMYYYY.
        ("slb_foreclosure", "content/slbs/Forclosure_SLB_20260918.CSV"),
        ("slb_var", "archives/slbs/var/C_VAR1_SLB_18092026_1.DAT"),
        ("cash_bhavcopy", "products/content/sec_bhavdata_full_18092026.csv"),
        ("gsec_master", "content/historical/WDM/2026/SEP/wdmlist_18092026.csv"),
        ("gsec_trades", "content/debt/dly18092026.zip"),
    ],
)
def test_url_for(source, expected_tail):
    assert nse.url_for(source, DATE) == nse.ARCHIVE + expected_tail


def test_trading_dates_skips_weekends():
    # 2026-09-18 is a Friday, so the five days back are Mon-Fri of that week.
    dates = nse.trading_dates(DATE, 5)
    assert dates == [
        dt.date(2026, 9, 18),
        dt.date(2026, 9, 17),
        dt.date(2026, 9, 16),
        dt.date(2026, 9, 15),
        dt.date(2026, 9, 14),
    ]
    assert all(d.weekday() < 5 for d in nse.trading_dates(DATE, 40))


# --- VaR / margin file ----------------------------------------------------


def test_slb_var_dedupes_to_one_row_per_symbol():
    """The file repeats every symbol across all 73 series with identical margins."""
    payload = load("C_VAR1_SLB_18092026_1_sample.DAT")
    rows = parse.parse_slb_var(payload, DATE)
    assert len(rows) == 4
    assert {r["symbol"] for r in rows} == {"ABB", "ABBOTINDIA", "360ONE", "RELIANCE"}

    one = next(r for r in rows if r["symbol"] == "360ONE")
    assert one["isin"] == "INE466L01038"
    assert one["applicable_var_pct"] == 12.80
    assert one["elm_pct"] == 3.50
    assert one["total_margin_pct"] == 16.30


def test_slb_var_margins_sum_exactly():
    for row in parse.parse_slb_var(load("C_VAR1_SLB_18092026_1_sample.DAT"), DATE):
        parts = row["applicable_var_pct"] + row["elm_pct"] + row["additional_margin_pct"]
        assert parts == pytest.approx(row["total_margin_pct"])


def test_slb_var_applicable_can_exceed_computed_var():
    """Applicable VaR is floored at a regulatory minimum, so it is not the raw VaR."""
    rows = parse.parse_slb_var(load("C_VAR1_SLB_18092026_1_sample.DAT"), DATE)
    abbott = next(r for r in rows if r["symbol"] == "ABBOTINDIA")
    assert abbott["var_pct"] == 8.21
    assert abbott["applicable_var_pct"] == 9.00


def test_slb_var_rejects_a_changed_layout():
    broken = b"10,18092026,0.0000,0000001\n20,FOO,01,INE000A01001,1.00,0.00,1.00,3.50,0.00,99.00\n"
    with pytest.raises(ValueError, match="layout changed"):
        parse.parse_slb_var(broken, DATE)


def test_series_universe_is_the_full_73():
    """The one file listing every series: 12 regular + 12 non-foreclosing + 48 rollover + R3."""
    rows = parse.parse_slb_series_universe(load("C_VAR1_SLB_18092026_1_sample.DAT"), DATE)
    assert len(rows) == 73

    by_set: dict[str, int] = {}
    for row in rows:
        by_set[row["contract_set"]] = by_set.get(row["contract_set"], 0) + 1
    assert by_set == {"REGULAR": 12, "NON_FORECLOSING": 12, "ROLLOVER": 49}


# --- holiday detection ----------------------------------------------------


def test_is_trading_day_rejects_a_stale_holiday_file():
    """NSE serves a stale cash bhavcopy under a holiday's filename instead of 404ing.

    The committed fixture is 18-Sep data, so asking whether it is 14-Sep -- an
    exchange holiday on which NSE really does serve 11-Sep rows -- must be False.
    """
    payload = load("sec_bhavdata_full_18092026.csv")
    assert parse.is_trading_day(payload, DATE) is True
    assert parse.is_trading_day(payload, dt.date(2026, 9, 14)) is False
