"""Stage 10 check: zero-coupon curve construction, NSS, forwards, key rates.

Pure maths, no database. The assertions are identities and recovery tests -- a
bootstrap must reprice its own inputs, an NSS fit must recover parameters it was
generated from, forwards must compound back to the spot, and key-rate buckets
must sum to the parallel DV01. Those either hold or the implementation is wrong;
there is nothing to eyeball.
"""

import math

import pytest

from slbdesk.curves import implied_borrow as ib
from slbdesk.curves import keyrate as kr
from slbdesk.curves import nss
from slbdesk.curves import zero as bs

# --- discount instruments -------------------------------------------------


def test_a_bill_gives_its_discount_factor_directly():
    """One cash flow, so df = price/face with nothing to solve."""
    curve = bs.bootstrap([bs.bill_instrument("364D", price=94.5, years=1.0)])
    assert curve.discount_factor(1.0) == pytest.approx(0.945)
    assert not curve.rejected


def test_zero_rate_inverts_the_discount_factor():
    curve = bs.bootstrap([bs.bill_instrument("364D", price=94.5, years=1.0)])
    rate = curve.zero_rate(1.0)
    assert rate == pytest.approx(-math.log(0.945) * 100)
    assert curve.discount_factor(1.0) == pytest.approx(math.exp(-rate / 100))


def test_discount_factors_fall_with_tenor():
    curve = bs.bootstrap(
        [
            bs.bill_instrument("91D", price=98.6, years=0.25),
            bs.bill_instrument("182D", price=97.1, years=0.5),
            bs.bill_instrument("364D", price=94.3, years=1.0),
        ]
    )
    factors = [curve.discount_factor(t) for t in (0.1, 0.25, 0.5, 0.75, 1.0, 2.0)]
    assert factors == sorted(factors, reverse=True)
    assert all(0 < f <= 1 for f in factors)


# --- the bootstrap reprices its inputs ------------------------------------


def flat_curve_bond(coupon_pct: float, years: float, rate_pct: float) -> dict:
    """A bond priced exactly off a flat continuously-compounded curve."""
    instrument = bs.bond_instrument("x", 0.0, coupon_pct, years)
    price = sum(amount * math.exp(-rate_pct / 100 * t) for t, amount in instrument["cash_flows"])
    return bs.bond_instrument(f"{coupon_pct}%/{years}y", price, coupon_pct, years)


def test_bootstrap_recovers_a_flat_curve():
    """Priced off a flat 7% curve, the bootstrap must hand back 7% at every node.

    This is the strongest single check on the sequential solve: if the coupon
    discounting or the schedule were off, the recovered rates would drift with
    maturity instead of staying flat.
    """
    instruments = [
        bs.bill_instrument("bill", price=math.exp(-0.07 * 0.5) * 100, years=0.5),
        flat_curve_bond(7.0, 1.0, 7.0),
        flat_curve_bond(6.0, 2.0, 7.0),
        flat_curve_bond(8.0, 5.0, 7.0),
        flat_curve_bond(7.5, 10.0, 7.0),
    ]
    curve = bs.bootstrap(instruments)

    assert not curve.rejected, curve.rejected
    for years, _ in curve.nodes:
        assert curve.zero_rate(years) == pytest.approx(7.0, abs=1e-6)


def test_bootstrap_reprices_every_accepted_instrument():
    """The bootstrap's defining property."""
    instruments = [
        bs.bill_instrument("bill", price=97.0, years=0.5),
        flat_curve_bond(7.0, 1.0, 6.6),
        flat_curve_bond(6.5, 2.0, 6.8),
        flat_curve_bond(7.2, 5.0, 7.0),
    ]
    curve = bs.bootstrap(instruments)
    accepted = {years for years, _ in curve.nodes}

    for instrument in instruments:
        if instrument["years"] not in accepted:
            continue
        priced = sum(amount * curve.discount_factor(t) for t, amount in instrument["cash_flows"])
        assert priced == pytest.approx(instrument["dirty_price"], rel=1e-9)


def test_par_rate_returns_the_coupon_of_a_par_bond():
    """Closes the loop: zeros -> par should land back on the input coupon."""
    curve = bs.bootstrap(
        [
            bs.bill_instrument("bill", price=math.exp(-0.07 * 0.5) * 100, years=0.5),
            flat_curve_bond(7.0, 5.0, 7.0),
        ]
    )
    # On a flat 7% continuous curve the 5-year par coupon is the equivalent
    # semi-annual rate, slightly above 7% because of the compounding convention.
    par = curve.par_rate(5.0, frequency=2)
    assert par == pytest.approx(200 * (math.exp(0.07 / 2) - 1), abs=0.02)


# --- the bootstrap defends itself -----------------------------------------


def test_a_bad_print_is_rejected_not_propagated():
    """A bootstrap is sequential, so accepting one bad node corrupts every
    longer tenor. Rejections are reported rather than silently dropped."""
    instruments = [
        bs.bill_instrument("good", price=97.0, years=0.5),
        # Priced far above the sum of its own cash flows: impossible.
        bs.bond_instrument("fat-fingered", 400.0, 7.0, 2.0),
        flat_curve_bond(7.0, 5.0, 6.9),
    ]
    curve = bs.bootstrap(instruments)

    labels = {label for label, _ in curve.rejected}
    assert "fat-fingered" in labels
    assert all(0 < df <= 1 for _, df in curve.nodes)


def test_a_duplicate_maturity_is_rejected():
    curve = bs.bootstrap(
        [
            bs.bill_instrument("a", price=97.0, years=0.5),
            bs.bill_instrument("b", price=96.9, years=0.5),
        ]
    )
    assert len(curve.nodes) == 1
    assert any("duplicates" in reason for _, reason in curve.rejected)


def test_an_empty_curve_refuses_to_discount():
    with pytest.raises(ValueError, match="empty curve"):
        bs.ZeroCurve().discount_factor(1.0)


# --- forwards -------------------------------------------------------------


def test_forwards_compound_back_to_the_spot():
    """The no-arbitrage identity: rolling the forwards must equal going long.

    df(t2) = df(t1) * exp(-f(t1,t2) * (t2-t1))
    """
    curve = bs.bootstrap(
        [
            bs.bill_instrument("a", price=98.5, years=0.25),
            bs.bill_instrument("b", price=96.8, years=1.0),
            flat_curve_bond(7.0, 5.0, 6.95),
        ]
    )

    forward = curve.forward_rate(1.0, 5.0)
    implied = curve.discount_factor(1.0) * math.exp(-forward / 100 * 4.0)
    assert implied == pytest.approx(curve.discount_factor(5.0), rel=1e-12)


def test_forward_exceeds_spot_on_an_upward_curve():
    """An upward-sloping spot curve implies forwards above it -- that is what
    'the market expects rates to rise' means mechanically."""
    curve = bs.bootstrap(
        [
            bs.bill_instrument("a", price=math.exp(-0.05 * 0.5) * 100, years=0.5),
            bs.bill_instrument("b", price=math.exp(-0.07 * 1.0) * 100, years=1.0),
        ]
    )
    assert curve.forward_rate(0.5, 1.0) > curve.zero_rate(1.0) > curve.zero_rate(0.5)


def test_forward_rejects_a_reversed_window():
    curve = bs.bootstrap([bs.bill_instrument("a", price=97.0, years=1.0)])
    with pytest.raises(ValueError, match="must exceed"):
        curve.forward_rate(2.0, 1.0)


# --- Nelson-Siegel-Svensson ----------------------------------------------


KNOWN = nss.NSSFit(
    beta0=7.4,
    beta1=-2.1,
    beta2=1.8,
    beta3=-0.9,
    tau1=1.4,
    tau2=11.0,
    rmse_bps=0.0,
    observations=0,
)


def test_nss_recovers_parameters_it_generated():
    """Generate yields from known parameters, fit, and check the CURVE matches.

    The curve is asserted rather than the parameters themselves: NSS is famously
    only weakly identified -- different (beta, tau) combinations produce nearly
    the same curve -- so demanding the exact betas back would be testing an
    artefact of the grid, not correctness. What must hold is that the fitted
    curve reproduces the generating one.
    """
    maturities = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30]
    observations = [(t, KNOWN.zero_rate(t)) for t in maturities]

    fitted = nss.fit(observations)
    assert fitted.rmse_bps < 2.0

    for t in maturities:
        assert fitted.zero_rate(t) == pytest.approx(KNOWN.zero_rate(t), abs=0.03)


def test_nss_short_rate_is_beta0_plus_beta1():
    """z(0) = b0 + b1, because the curvature loadings vanish at zero and the
    slope loading tends to one. The limit is guarded, not left as 0/0."""
    assert KNOWN.short_rate == pytest.approx(KNOWN.beta0 + KNOWN.beta1)
    assert KNOWN.zero_rate(1e-9) == pytest.approx(KNOWN.beta0 + KNOWN.beta1, abs=1e-6)
    assert KNOWN.zero_rate(0.0) == pytest.approx(KNOWN.beta0 + KNOWN.beta1)


def test_nss_tends_to_beta0_at_the_long_end():
    assert KNOWN.zero_rate(200.0) == pytest.approx(KNOWN.beta0, abs=0.15)


def test_nss_fits_a_real_shaped_curve():
    """Yields shaped like the observed Indian curve: 5.3% at 3 months rising to
    7.7% at 40 years, with a hump. The fit has to be tight enough to price off."""
    observed = [
        (0.11, 5.34),
        (0.25, 5.55),
        (0.5, 5.80),
        (1.0, 6.05),
        (2.0, 6.30),
        (3.0, 6.45),
        (5.0, 6.60),
        (7.0, 6.75),
        (9.0, 7.07),
        (14.0, 7.05),
        (20.0, 7.30),
        (29.0, 7.59),
        (40.0, 7.67),
    ]
    fitted = nss.fit(observed)

    # What matters is that the CURVE tracks the observations, because that is
    # what gets priced off. beta0 is deliberately NOT pinned to a range: it is
    # the asymptote as t goes to infinity, and this curve is still rising at its
    # longest observation, so the data genuinely does not determine where it
    # levels off. Asserting a range on it would test the grid, not the fit.
    assert fitted.rmse_bps < 12.0, fitted.describe()
    for years, observed_pct in observed:
        assert fitted.zero_rate(years) == pytest.approx(observed_pct, abs=0.25)

    assert fitted.short_rate < fitted.zero_rate(30.0)  # upward, as observed
    assert fitted.tau2 > fitted.tau1  # the two humps keep their roles


def test_nss_discount_factors_are_monotone_and_bounded():
    fitted = nss.fit([(t, KNOWN.zero_rate(t)) for t in (0.5, 1, 2, 5, 10, 20, 30)])
    factors = [fitted.discount_factor(t) for t in (0.5, 1, 2, 5, 10, 20, 30)]
    assert factors == sorted(factors, reverse=True)
    assert all(0 < f < 1 for f in factors)


def test_nss_forwards_compound_back_to_the_spot():
    fitted = nss.fit([(t, KNOWN.zero_rate(t)) for t in (0.5, 1, 2, 5, 10, 20, 30)])
    forward = fitted.forward_rate(2.0, 10.0)
    implied = fitted.discount_factor(2.0) * math.exp(-forward / 100 * 8.0)
    assert implied == pytest.approx(fitted.discount_factor(10.0), rel=1e-12)


def test_nss_needs_enough_observations_to_identify_four_betas():
    with pytest.raises(ValueError, match="at least 4"):
        nss.fit([(1.0, 6.0), (2.0, 6.2), (5.0, 6.5)])


def test_nss_describes_itself_in_words():
    """The reason to prefer a parametric form: the parameters mean something."""
    described = nss.fit([(t, KNOWN.zero_rate(t)) for t in (0.5, 1, 2, 5, 10, 20, 30)]).describe()
    assert "upward" in described
    assert "curvature" in described


# --- implied borrow forwards ---------------------------------------------


def test_implied_forward_compounds_back_to_the_far_fee():
    """Simple ACT/365, because that is how the fee is quoted and accrued."""
    near, far = 51.11, 19.31
    forward = ib.forward_fee(near, 15, far, 43)

    near_growth = 1 + near / 100 * 15 / 365
    forward_growth = 1 + forward / 100 * 28 / 365
    assert near_growth * forward_growth == pytest.approx(1 + far / 100 * 43 / 365)


def test_the_piind_squeeze_prices_as_resolving():
    """The real 18-Sep quote, and the headline result of the whole module.

    51.1% to 15 days against 19.3% to 43 days implies only about 2% over the 28
    days between: the market is pricing the squeeze to be over within a
    fortnight. A spot fee cannot say that.
    """
    forward = ib.forward_fee(51.11, 15, 19.31, 43)
    assert 0 < forward < 5
    assert ib.classify(51.11, forward) == "RESOLVING"


def test_a_flat_fee_curve_implies_the_same_forward():
    """No term structure, no information: the forward sits on the spot."""
    forward = ib.forward_fee(8.0, 30, 8.0, 90)
    assert forward == pytest.approx(8.0, abs=0.2)
    assert ib.classify(8.0, forward) == "PERSISTENT"


def test_a_rising_fee_curve_prices_escalation():
    forward = ib.forward_fee(5.0, 30, 12.0, 90)
    assert forward > 12.0
    assert ib.classify(5.0, forward) == "WORSENING"


def test_a_negative_forward_means_front_loaded_not_arbitrage():
    """A steeply falling fee curve, not a data error and not free money.

    It happens whenever f_far*t_far < f_near*t_near, which on real data is about
    a quarter of all pairs. The whole cost sits in the near window. It is not
    arbitrageable because rolling the near contract in SLB means trading a fresh
    contract at a new market-determined fee -- NCL facilitates rollover on a
    best-efforts basis -- so the forward is an expectation, not a lockable rate.
    """
    forward = ib.forward_fee(40.0, 30, 2.0, 90)
    assert forward < 0
    assert ib.classify(40.0, forward) == "FRONT_LOADED"


def test_the_forward_is_the_breakeven():
    assert ib.breakeven_forward(20.0, 15, 9.0, 60) == ib.forward_fee(20.0, 15, 9.0, 60)


def test_curve_forwards_never_span_contract_sets():
    """The two sets are separate curves with different corporate-action
    treatment, so a forward across them prices a contract that does not exist."""
    quotes = [
        {"symbol": "X", "contract_set": "REGULAR", "tenor_days": 15, "fee_annualised_pct": 30.0},
        {
            "symbol": "X",
            "contract_set": "NON_FORECLOSING",
            "tenor_days": 45,
            "fee_annualised_pct": 10.0,
        },
    ]
    assert ib.curve_forwards(quotes) == []


def test_curve_forwards_walks_consecutive_tenors():
    quotes = [
        {"symbol": "X", "contract_set": "REGULAR", "tenor_days": t, "fee_annualised_pct": f}
        for t, f in [(15, 40.0), (45, 20.0), (105, 12.0)]
    ]
    forwards = ib.curve_forwards(quotes)

    assert [(f.near_tenor_days, f.far_tenor_days) for f in forwards] == [(15, 45), (45, 105)]
    assert all(f.symbol == "X" for f in forwards)


def test_curve_forwards_skips_tenors_too_close_to_be_meaningful():
    """A small fee difference over three days divides into a nonsense forward."""
    quotes = [
        {"symbol": "X", "contract_set": "REGULAR", "tenor_days": 15, "fee_annualised_pct": 30.0},
        {"symbol": "X", "contract_set": "REGULAR", "tenor_days": 17, "fee_annualised_pct": 31.0},
    ]
    assert ib.curve_forwards(quotes) == []


# --- key-rate durations --------------------------------------------------


def flat_zero(rate_pct: float):
    return lambda years: rate_pct


def bond_flows(coupon_pct: float, years: float, frequency: int = 2):
    return bs.bond_instrument("x", 0.0, coupon_pct, years, frequency)["cash_flows"]


def test_tent_weights_partition_the_curve():
    """Every maturity's weights sum to 1, which is what makes a parallel shift
    the exact sum of the individual tents."""
    for years in (0.1, 0.25, 0.4, 1.0, 3.0, 7.5, 15.0, 30.0, 45.0):
        total = sum(kr.tent_weight(years, i) for i in range(len(kr.KEY_TENORS_YEARS)))
        assert total == pytest.approx(1.0, abs=1e-9), years


def test_a_tent_is_one_at_its_key_and_zero_at_its_neighbours():
    keys = kr.KEY_TENORS_YEARS
    index = keys.index(5.0)
    assert kr.tent_weight(5.0, index) == 1.0
    assert kr.tent_weight(keys[index - 1], index) == 0.0
    assert kr.tent_weight(keys[index + 1], index) == 0.0


def test_shocking_every_tent_at_once_is_exactly_a_parallel_shift():
    """The identity that proves the tents partition the curve.

    Weights sum to 1 at every maturity, so a simultaneous 1bp shock of all tents
    IS a parallel 1bp shift -- exact to machine precision, no tolerance needed.
    """
    flows = bond_flows(7.0, 10.0)
    curve = flat_zero(6.8)
    assert kr.simultaneous_shock_dv01(flows, curve) == pytest.approx(
        kr.parallel_dv01(flows, curve), rel=1e-12
    )


def test_key_rate_buckets_sum_to_the_parallel_dv01_to_first_order():
    """The completeness check: no risk may go missing from the decomposition.

    Only to FIRST ORDER, and that is not a fudge. The buckets are computed one
    shock at a time and repricing is non-linear in the rate, so their sum differs
    from a single parallel shift by a second-order term -- the curve equivalent of
    the gap between duration and duration-plus-convexity. A few parts in 10^5 on
    a ten-year bond. Demanding exactness here would be asserting something untrue;
    the exact identity is the simultaneous-shock test above.
    """
    flows = bond_flows(7.0, 10.0)
    curve = flat_zero(6.8)

    buckets = kr.key_rate_dv01(flows, curve)
    total = sum(buckets.values())
    parallel = kr.parallel_dv01(flows, curve)

    assert total == pytest.approx(parallel, rel=1e-3)
    assert abs(total - parallel) / parallel < 1e-4  # second-order, not a leak


def test_risk_concentrates_where_the_cash_flows_are():
    """A 10-year bond's risk sits in the 10-year bucket, not the 2-year one.

    This is the whole reason for key rates: a parallel DV01 cannot tell a
    10-year position from a barbell of 2s and 30s with the same total.
    """
    buckets = kr.key_rate_dv01(bond_flows(7.0, 10.0), flat_zero(6.8))
    assert max(buckets, key=lambda key: buckets[key]) == 10.0
    assert buckets[10.0] > buckets[2.0]
    assert buckets[10.0] > buckets[30.0]


def test_a_zero_coupon_bond_loads_one_bucket_pair_only():
    """No coupons, so risk sits in the tents bracketing its single maturity."""
    buckets = kr.key_rate_dv01([(5.0, 100.0)], flat_zero(7.0))
    loaded = {key for key, value in buckets.items() if abs(value) > 1e-9}
    assert loaded == {5.0}


def test_key_rates_distinguish_a_barbell_from_a_bullet():
    """The exposure a single DV01 hides."""
    bullet = kr.key_rate_dv01([(10.0, 100.0)], flat_zero(7.0))
    barbell = kr.key_rate_dv01([(2.0, 50.0), (30.0, 50.0)], flat_zero(7.0))

    assert bullet[10.0] > 0 and barbell[10.0] == pytest.approx(0.0, abs=1e-9)
    assert barbell[2.0] > 0 and barbell[30.0] > 0


def test_key_rate_dv01_works_off_an_nss_fit():
    """Both curve types expose zero_rate(years), so risk is curve-agnostic."""
    fitted = nss.fit([(t, KNOWN.zero_rate(t)) for t in (0.5, 1, 2, 5, 10, 20, 30)])
    buckets = kr.key_rate_dv01(bond_flows(7.0, 10.0), fitted.zero_rate)

    assert sum(buckets.values()) == pytest.approx(
        kr.parallel_dv01(bond_flows(7.0, 10.0), fitted.zero_rate), rel=1e-3
    )
    # And exactly, via the simultaneous shock.
    assert kr.simultaneous_shock_dv01(bond_flows(7.0, 10.0), fitted.zero_rate) == pytest.approx(
        kr.parallel_dv01(bond_flows(7.0, 10.0), fitted.zero_rate), rel=1e-12
    )
