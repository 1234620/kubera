"""Stage 0 check: the committed NSE fixtures still have the shape docs/05 claims.

These are real exchange files. If NSE changes a layout, the parsers written in
stage 1 will be wrong, and this is the test that says so first.
"""

import csv
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def rows(name: str) -> list[list[str]]:
    with (FIXTURES / name).open(newline="") as fh:
        return [[c.strip() for c in r] for r in csv.reader(fh)]


def test_slb_bhavcopy_layout():
    """17 columns, no header, fee OHLC bracketed by the year high/low (docs/05 §2.1)."""
    data = rows("SLBM_BC_18092026.DAT")
    assert len(data) == 227
    assert {len(r) for r in data} == {17}

    for r in data:
        prev_close, open_, high, low, close = (float(r[i]) for i in range(5, 10))
        year_high, year_low = float(r[13]), float(r[14])
        qty, value = int(r[11]), float(r[12])

        assert low <= open_ <= high and low <= close <= high
        assert year_low <= low and high <= year_high
        assert prev_close > 0 and qty > 0 and value > 0
        assert r[10] == ""  # column 11 is unused in every row we have seen
        assert r[15] == "18-SEP-2026"

    # Traded value is quantity x the fee actually dealt, so value/qty is the VWAF
    # and it must sit inside the day's range.
    for r in data:
        vwaf = float(r[12]) / int(r[11])
        assert float(r[8]) - 1e-6 <= vwaf <= float(r[7]) + 1e-6


def test_slb_open_positions_layout():
    data = rows("slb_openpos_18092026.csv")
    assert data[0] == ["Sr no", "Security", "Series", "Outstanding Quantity at the end of the day"]
    body = [r for r in data[1:] if r]
    assert len(body) == 802
    assert all(int(r[3]) > 0 for r in body)


def test_open_interest_exceeds_traded_pairs():
    """The market is thin: far more pairs hold open interest than trade on a day.

    This is why docs/07 requires an explicit staleness guard on the specialness
    score rather than assuming every open position has a fresh print.
    """
    traded = {(r[1], r[2]) for r in rows("SLBM_BC_18092026.DAT")}
    open_pairs = {(r[1], r[2]) for r in rows("slb_openpos_18092026.csv")[1:] if r}
    assert len(open_pairs) > 3 * len(traded)


def test_eligibility_flags_are_e_or_d():
    data = rows("SLB_ELG_SEC_18092026_sample.csv")
    assert data[0][:3] == ["Sr.No.", "Symbol", "Series"]
    for r in data[1:]:
        if r:
            assert set(r[3:6]) <= {"E", "D"}


def test_foreclosure_report_layout():
    data = rows("Forclosure_SLB_20260918.CSV")
    assert data[0][0] == "SECURITY"
    assert data[0][-1] == "CORPORATE ACTION DESCRIPTION"
    assert len(data) > 1


def test_gsec_master_has_coupon_dates():
    """docs/03 relies on Last IP Dt / Next IP Dt for correct stub accrual."""
    data = rows("wdmlist_18092026_gsec.csv")
    header = data[0]
    assert header[6] == "Last IP Dt" and header[7] == "Next IP Dt"
    body = [r for r in data[1:] if r]
    assert len(body) == 120
    assert all(r[0] == "GS" for r in body)
    assert all(r[6] and r[7] for r in body)
    assert all(r[8] == "Half Yearly" for r in body)


def test_gsec_trades_have_weighted_ytm():
    """The YTM validation target in docs/03 §11 must actually be in the file."""
    data = rows("trd1809_sett.csv")
    assert data[0][-1] == "Weighted YTM"
    assert data[0][4] == "Settl Days"
    gs = [r for r in data[1:] if r and r[1] == "GS"]
    assert gs, "no G-Sec rows to validate against"
    assert all(r[4] == "1" for r in gs)  # G-Secs settle T+1
    assert all(0 < float(r[12]) < 20 for r in gs)
