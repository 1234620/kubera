# DAX measures

Every measure names the SQL metric it mirrors and the doc that defines it, so the
report and the database cannot drift apart silently. Where a figure is already
computed in SQL the measure is a plain `SUM` — recomputing analytics in DAX would
put the same formula in two places, and the frontend rule applies here too: the
report presents, it does not compute.

Units follow the repo convention: `_inr` rupees, `_pct` percent, `_bps` basis
points. Positive P&L is a gain to our book.

## Book

```dax
Gross Notional = SUM(slb_position_pnl_daily[notional_inr])

On Loan =
CALCULATE(
    SUM(slb_position_pnl_daily[notional_inr]),
    slb_position_pnl_daily[side] = "LEND"
)

Borrowed =
CALCULATE(
    SUM(slb_position_pnl_daily[notional_inr]),
    slb_position_pnl_daily[side] = "BORROW"
)

Positions = COUNTROWS(slb_position_pnl_daily)
```

## P&L — mirrors `desk_pnl_daily` (docs/07 §5)

```dax
Fee P&L = SUM(desk_pnl_daily[fee_pnl_inr])

-- BUSINESS desks only. Across every desk this is exactly zero, because Treasury
-- is credited whatever the desks are debited: correct as a ledger, useless as a
-- headline. Same reason book_kpis.sql filters it.
FTP Charge =
CALCULATE(
    SUM(desk_pnl_daily[ftp_charge_inr]),
    desk_pnl_daily[desk_id] <> "TREASURY"
)

Desk Spread     = SUM(desk_pnl_daily[desk_spread_inr])
Treasury Spread = SUM(desk_pnl_daily[treasury_spread_inr])

-- Across ALL desks this IS book NIM: the internal transfer cancels, leaving fee
-- income less Treasury's real cost of funds.
Book NIM = SUM(desk_pnl_daily[net_spread_inr])

-- Annualised on the balance sheet consumed.
Net Financing Spread bps =
DIVIDE([Book NIM] * 365, [Gross Notional]) * 10000

-- Show this on every P&L page. If the decomposition stops adding back to NIM the
-- FTP model is decorative, and the report should say so rather than hide it
-- (docs/04 §5).
FTP Reconciles =
IF(SELECTEDVALUE(desk_pnl_daily[reconciles], TRUE()), "reconciles", "DOES NOT RECONCILE")
```

## Specialness — mirrors `slb_specialness_daily` (docs/07 §4)

```dax
-- Notional-weighted, never a plain average: the fee distribution is severely
-- right-skewed, so a straight mean of scores is dominated by tiny positions.
Avg Specialness =
DIVIDE(
    SUMX(slb_specialness_daily,
         slb_specialness_daily[specialness_score] * slb_specialness_daily[fee_annualised_pct]),
    SUM(slb_specialness_daily[fee_annualised_pct])
)

Specials =
CALCULATE(COUNTROWS(slb_specialness_daily), slb_specialness_daily[specialness_score] >= 75)

Hard To Borrow =
CALCULATE(COUNTROWS(slb_specialness_daily), slb_specialness_daily[specialness_score] >= 90)

-- "No recent print" is information about liquidity, so surface it rather than
-- filtering stale rows away (docs/07 §4).
Stale Quotes =
CALCULATE(COUNTROWS(slb_specialness_daily), slb_specialness_daily[is_stale] = TRUE())

Stale Share = DIVIDE([Stale Quotes], COUNTROWS(slb_specialness_daily))

Spread to GC bps = AVERAGE(slb_specialness_daily[spread_to_gc_bps])

Weighted Avg Fee pct =
DIVIDE(
    SUMX(slb_position_pnl_daily,
         slb_position_pnl_daily[fee_annualised_pct] * slb_position_pnl_daily[notional_inr]),
    SUM(slb_position_pnl_daily[notional_inr])
)
```

## Utilisation — mirrors `slb_utilisation_daily` (docs/07 §2)

```dax
On Loan Qty = SUM(slb_utilisation_daily[on_loan_qty])

-- Weighted by what is actually on loan, so the book figure reflects exposure
-- rather than averaging a large and a tiny name equally.
Book Utilisation pct =
DIVIDE(
    SUMX(slb_utilisation_daily,
         slb_utilisation_daily[utilisation_pct] * slb_utilisation_daily[on_loan_qty]),
    SUM(slb_utilisation_daily[on_loan_qty])
)

-- ESTIMATED. Lendable supply is not published in India, so the denominator is a
-- delivery-volume proxy. Put this next to the utilisation card -- an estimate
-- must never look measured.
Utilisation Basis =
IF(SELECTEDVALUE(slb_utilisation_daily[is_estimated], TRUE()),
   "estimated — lendable supply is not published", "measured")

-- Needs no estimate at all, which is why it belongs beside utilisation rather
-- than behind it.
Days To Cover = AVERAGE(slb_utilisation_daily[days_to_cover])
```

## Bonds — mirrors `gsec_analytics_daily` (docs/03)

```dax
-- Traded-value weighted: a 300 crore print should move the book number more than
-- a 5 crore one.
Weighted YTM pct =
DIVIDE(
    SUMX(gsec_analytics_daily,
         gsec_analytics_daily[ytm_pct] * gsec_analytics_daily[traded_value_inr]),
    SUM(gsec_analytics_daily[traded_value_inr])
)

Modified Duration = AVERAGE(gsec_analytics_daily[modified_duration])
Convexity         = AVERAGE(gsec_analytics_daily[convexity])

-- Our accuracy against the exchange's own published figure, carried in the data
-- so nobody has to take the pricing on trust (docs/03 §11).
YTM vs NSE bps = AVERAGE(gsec_analytics_daily[ytm_diff_bps])
Max YTM Error bps = MAX(ABS(gsec_analytics_daily[ytm_diff_bps]))
```

## Repo & margin — mirrors `collateral_position`, `margin_call` (docs/07 §8)

```dax
Collateral Dirty Value = SUM(collateral_position[dirty_value])
Post Haircut Value     = SUM(collateral_position[post_haircut_value])
Effective Haircut pct  = DIVIDE([Collateral Dirty Value] - [Post Haircut Value],
                                [Collateral Dirty Value]) * 100

Open Margin Calls =
CALCULATE(COUNTROWS(margin_call), margin_call[status] = "OPEN")

Call Amount = SUM(margin_call[call_amount])

-- Signed at the position level, where the direction is known: a REPO delivers
-- collateral away, a REVERSE takes it in.
Book DV01 =
SUMX(
    collateral_position,
    VAR Direction = RELATED(repo_trade[direction])
    RETURN IF(Direction = "REVERSE", 1, -1)
        * collateral_position[nominal] / 100
        * RELATED(gsec_analytics_daily[dv01_per_100_face])
)
```

## Funding curve — mirrors `funding_curve_point` (docs/07 §9)

```dax
Base Rate pct = AVERAGE(funding_curve_point[base_rate_pct])
TLP bps       = AVERAGE(funding_curve_point[term_liquidity_premium_bps])
FTP Rate pct  = [Base Rate pct] + DIVIDE([TLP bps], 100)

-- The short end usually is extrapolated: SLB tenors run 4 to 356 days, exactly
-- where sovereign coverage in the WDM file is thinnest. Draw those nodes hollow,
-- as the dashboard does.
Extrapolated Nodes =
CALCULATE(COUNTROWS(funding_curve_point), funding_curve_point[is_estimated] = TRUE())
```
