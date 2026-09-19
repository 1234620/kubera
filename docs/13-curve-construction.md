# 13 — Zero-coupon curve construction

The gap this closes. Up to stage 9 every rate in the project was either observed
directly or interpolated between observations, and every bond was priced off its
own yield to maturity. That is enough to compute metrics; it is not a model. A
yield to maturity is instrument-specific — two bonds with the same maturity and
different coupons have different YTMs — so you cannot discount an arbitrary cash
flow with it, and you cannot say anything about what the market expects.

A **zero-coupon curve** fixes that. One discount factor per maturity, applicable
to any cash flow, from which spot rates, forward rates and par rates all follow.

## 1. The inputs, and the trap in them

Built from what actually trades: **T-bills** (pure discount instruments) and
**dated G-Secs** (coupon bonds). On a sample day that is 38 unique instruments —
20 bills and 18 bonds — pooled over a trailing 10 days because daily NDS-OM
prints reaching the WDM file are sparse.

The trap cost an afternoon and is worth stating plainly. **`issue_name` means
two different things.** For a G-Sec it is the coupon (`7.33%`). For a T-bill it
is the **maturity date as DDMMYY** (`291026` = 29-Oct-2026). And a bill's
security code is only its original tenor — `91D`, `182D`, `364D` — so the code
identifies 13 to 48 different bills in the master and is useless on its own.

Joining bills on code alone matched a 15-day bill to a price belonging to a much
longer one and bootstrapped a **149% short rate**. The absurdity is what exposed
it; a subtler mismatch would have produced a plausible, wrong curve.

With the correct join our own T-bill yields reproduce NSE's published figures to
a **median of 0.002bp** — essentially exact, and a second independent validation
alongside the G-Sec YTM check in [`03-domain-bond-math.md`](03-domain-bond-math.md).

## 2. Bootstrap — exact, and brittle

Solve discount factors one instrument at a time, shortest first, so each input
reprices to its own market price.

- A **bill** has one cash flow, so `df = price / face`. No solve.
- A **bond**'s dirty price is the sum of its discounted flows, so the last
  factor falls out once the earlier ones are known.

**It is not a division, it is a fixed point.** That is the part textbooks gloss
over. The closed-form solve only works when every earlier coupon date already
has a node. A 5-year bond against nodes at 0.5, 1 and 2 years has coupons at 2.5
through 4.5 with nothing solved around them, so those have to be *interpolated* —
and the interpolation depends on the very factor being solved. So: guess, insert,
re-discount, re-solve, repeat. It converges in a handful of passes.

Skipping the iteration is a quiet error. The curve looks entirely plausible and
simply fails to reprice its own inputs — residuals of 0.6 paisa on a 2-year and
7.8 paisa on a 5-year, small enough to miss by eye and far too large for a curve.
`test_bootstrap_reprices_every_accepted_instrument` is what caught it, because it
asserts the bootstrap's one defining property.

**It defends itself.** A bootstrap is sequential, so one bad print corrupts every
longer tenor. A solved factor is rejected — and the rejection *reported* — when it
is outside `(0, 1]`, duplicates a node, or implies a zero rate jumping more than
300bp from the previous one.

## 3. Nelson-Siegel-Svensson — smooth, and approximate

```
z(t) = b0
     + b1 · (1−e^(−t/T1))/(t/T1)
     + b2 · [(1−e^(−t/T1))/(t/T1) − e^(−t/T1)]
     + b3 · [(1−e^(−t/T2))/(t/T2) − e^(−t/T2)]
```

`b0` is the long-run level, `b0+b1` the short rate, `b2` and `b3` two humps
decaying at `T1` and `T2`. Svensson's addition is the second hump, which lets a
curve bend twice.

**The fitting trick that keeps this dependency-free**: for fixed `T1, T2` the
model is *linear* in the betas. So the fit separates — grid-search the two decay
parameters, and at each candidate solve the four betas exactly through a 4×4
normal equation by Gaussian elimination. No optimiser, no SciPy, no new
dependency, and the linear step is exact rather than iterative.

It fits **observed yields, not prices**. Fitting prices is more correct because it
weights by cash-flow sensitivity; fitting yields is common practice, far simpler,
and adequate at these residuals. Named as the honest refinement rather than left
unsaid.

Observed fit quality across the backfill: **mean RMSE 5.5bp, worst 8.8bp** over
about 38 instruments a day.

**Four observations is identifiability, not credibility.** Four betas fit four
points *exactly* and report an RMSE of 0.00 that means nothing. That happened on
the first date of the backfill and `validate_nss_fit.sql` caught it, so the
analytics layer requires **eight** observations before fitting while `nss.fit`
keeps four as its algebraic floor. They are different constraints and the code
distinguishes them.

## 4. Both, and the comparison

Neither construction dominates, so both are published for the same dates in
`zero_curve_point` with `method` in the key. The disagreement is the useful part:

| Tenor | Bootstrap | NSS | NSS forward |
| --- | --- | --- | --- |
| 0.25y | 5.151% | 5.224% | 5.381% |
| 0.5y | 5.545% | 5.498% | 5.773% |
| 1y | 5.736% | 5.919% | 6.340% |
| 2y | 6.330% | 6.417% | 6.915% |
| 5y | 6.759% | 6.853% | 7.145% |
| 10y | 7.004% | 7.034% | 7.216% |
| 15y | 7.052% | **7.206%** | 7.462% |
| 20y | 7.052% | **7.360%** | 7.686% |
| 30y | 7.052% | **7.579%** | 7.951% |

They agree inside 20bp out to 10 years, where both are observing the same bonds.
Beyond 15 years the bootstrap goes **flat** — it holds its last zero rate rather
than extending a slope, because extending a slope invents curve shape nobody
observed — while NSS keeps rising. `is_extrapolated` flags exactly those points.

Use the bootstrap to reprice something you hold. Use NSS to price a maturity
nobody traded, or to ask what shape the curve is.

Forwards sit above spots at every tenor, which is what an upward-sloping curve
*means* mechanically: the market is pricing rates to rise.

## 5. The SLB fee curve as a forward curve

The piece that turns the specialness work from metrics into a model, using the
same no-arbitrage algebra. A security quotes a borrow fee at several tenors, so
the near and far fees imply the fee for the period between:

```
(1 + f_near·t_near/365) · (1 + f_fwd·(t_far−t_near)/365) = (1 + f_far·t_far/365)
```

Simple ACT/365, because that is how the fee is quoted and accrued.

**Why it matters.** A spot fee says a name is expensive *now*. The forward says
whether the market expects it to *stay* expensive, which is the actual trading
question. On 18-Sep:

| Symbol | 15d fee | 43d fee | Implied 15d→43d | Signal |
| --- | --- | --- | --- | --- |
| PIIND | 51.1% | 19.3% | **2.2%** | RESOLVING |
| LTM | 36.2% | 15.4% | 4.3% | RESOLVING |
| OBEROIRLTY | 34.0% | 16.6% | 7.2% | RESOLVING |
| IREDA | 28.9% | 22.6% | 18.9% | PERSISTENT |
| NYKAA | 29.3% | 1.3% | −13.6% | FRONT_LOADED |

PIIND at 51% p.a. to 15 days and 19% to 43 days implies about **2%** over the 28
days between. The market is pricing the squeeze to be finished within a
fortnight. A desk lending PIIND for 43 days at 19.3% earns almost all of it in
the first two weeks; a desk that borrows 43 days because "the fee looks lower"
has paid a large front-loaded cost for a cheap tail. No spot metric says that.

### FRONT_LOADED, and why it is not arbitrage

A negative implied forward arises whenever `f_far·t_far < f_near·t_near` — a
steeply falling fee curve — and that is about **a quarter of all tenor pairs** in
this data. The first version of this labelled them `ANOMALOUS`, which was wrong:
a quarter of the market is not a data error. It means the entire cost of the
borrow sits in the near window and the tail is expected to be nearly free.

More importantly, **it is not an arbitrage**. The no-arbitrage forward assumes
the near contract can be rolled *at* the forward. In SLB it cannot: rolling means
trading a fresh contract at a new market-determined fee, and NCL facilitates
recall, repay and rollover on a best-efforts basis only
([`01-domain-slb-lifecycle.md`](01-domain-slb-lifecycle.md) §5–6). So the forward
here is an **expectation the market is pricing**, not a rate anyone can lock.
Presenting it as free money would be the single easiest way to be wrong about
this whole module.

## 6. Key-rate durations

A single DV01 answers "what if the whole curve moves 1bp in parallel". Real
curves steepen, flatten and twist, and a book that is DV01-neutral overall can
still be badly exposed to a steepening.

Shock the zero curve at one key tenor, decay the shock linearly to zero at the
neighbouring keys — a "tent" — reprice, take the difference. Key tenors: 0.25,
0.5, 1, 2, 5, 10, 20, 30 years.

Two properties, and the distinction between them is not pedantry:

- Shocking **every tent simultaneously** is *exactly* a parallel shift, because
  the weights sum to 1 at every maturity. Exact to machine precision. This is
  what proves the tents partition the curve.
- Summing the **individually computed** buckets matches the parallel DV01 only to
  **first order**, because each is computed one shock at a time and repricing is
  non-linear in the rate. The gap is a few parts in 10⁵ — the curve equivalent of
  the difference between duration and duration-plus-convexity.

Asserting exactness on the second would be asserting something untrue, so the
tests assert each property at its own tolerance and say why.

Buckets are additive across positions, so a desk-level curve exposure is a plain
sum — which is the whole reason to compute them this way.

## 7. What is checked

| Property | Where |
| --- | --- |
| Bootstrap recovers a flat curve exactly | `test_bootstrap_recovers_a_flat_curve` |
| Bootstrap reprices every accepted input | `test_bootstrap_reprices_every_accepted_instrument` |
| Zeros → par returns the input coupon | `test_par_rate_returns_the_coupon_of_a_par_bond` |
| Forwards compound back to the spot | `test_forwards_compound_back_to_the_spot` |
| NSS recovers a curve it generated | `test_nss_recovers_parameters_it_generated` |
| `z(0) = b0 + b1`, with the 0/0 limit guarded | `test_nss_short_rate_is_beta0_plus_beta1` |
| Implied forward compounds back to the far fee | `test_implied_forward_compounds_back_to_the_far_fee` |
| Tent weights sum to 1 at every maturity | `test_tent_weights_partition_the_curve` |
| Simultaneous tent shock == parallel shift, exactly | `test_shocking_every_tent_at_once_is_exactly_a_parallel_shift` |
| Key rates distinguish a barbell from a bullet | `test_key_rates_distinguish_a_barbell_from_a_bullet` |
| Discount factors monotone, rates sane | `db/queries/validate_zero_curve.sql` |
| Fit tight enough and parameters interpretable | `db/queries/validate_nss_fit.sql` |

37 checks in `tests/test_curves.py`, all pure maths, no database.
