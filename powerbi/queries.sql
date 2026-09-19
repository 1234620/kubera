-- Import queries for the Power BI model. One per table, columns named rather
-- than SELECT * (rules/SQL.md), and restricted to the materialised analytics
-- outputs -- importing the raw market tables would pull a million rows to
-- recompute what `make analytics` already did.
--
-- Paste each into Get Data -> PostgreSQL -> Advanced options -> SQL statement.
-- Model and measures: powerbi/README.md, powerbi/measures.md.

-- desk_pnl_daily -----------------------------------------------------------
SELECT as_of_date, desk_id, positions, gross_notional_inr, net_notional_inr,
       fee_pnl_inr, ftp_charge_inr, net_spread_inr, nim_inr, desk_spread_inr,
       treasury_spread_inr, actual_cost_of_funds_inr, reconciles
FROM desk_pnl_daily;

-- ftp_charge_daily ---------------------------------------------------------
SELECT as_of_date, desk_id, symbol, series_code, net_quantity, net_notional_inr,
       tenor_days, base_rate_pct, term_liquidity_bps, contingent_liq_bps,
       ftp_rate_pct, ftp_charge_inr, curve_is_estimated
FROM ftp_charge_daily;

-- slb_position_pnl_daily ---------------------------------------------------
SELECT as_of_date, trade_id, desk_id, symbol, series_code, side, quantity,
       underlying_close, notional_inr, tenor_days, fee_per_share,
       fee_annualised_pct, fee_pnl_inr
FROM slb_position_pnl_daily;

-- slb_specialness_daily ----------------------------------------------------
SELECT trade_date, symbol, series_code, contract_set, tenor_bucket, tenor_days,
       fee_per_share, fee_annualised_pct, gc_fee_annualised_pct,
       spread_to_gc_bps, xs_percentile, baseline_mean_pct, baseline_sd_pct,
       baseline_obs, own_z, own_z_cdf, specialness_score, classification,
       quote_date, days_since_last_trade, is_stale
FROM slb_specialness_daily;

-- slb_utilisation_daily ----------------------------------------------------
SELECT trade_date, symbol, on_loan_qty, avg_volume_20d, avg_delivery_20d,
       lendable_qty_est, utilisation_pct, days_to_cover, on_loan_change_1d,
       utilisation_5d_avg, is_estimated
FROM slb_utilisation_daily;

-- gsec_analytics_daily -----------------------------------------------------
SELECT trade_date, isin, security_code, coupon_pct, maturity_date,
       settlement_date, residual_years, clean_price, accrued_interest,
       dirty_price, ytm_pct, nse_ytm_pct, ytm_diff_bps, macaulay_duration,
       modified_duration, convexity, dv01_per_100_face, traded_value_inr
FROM gsec_analytics_daily;

-- funding_curve_point ------------------------------------------------------
SELECT as_of_date, tenor_days, base_rate_pct, term_liquidity_premium_bps,
       source, is_estimated
FROM funding_curve_point;

-- gc_rate_daily ------------------------------------------------------------
SELECT as_of_date, contract_set, gc_fee_per_share_median,
       gc_fee_annualised_pct, cohort_size, is_estimated
FROM gc_rate_daily;

-- collateral_position ------------------------------------------------------
SELECT as_of_date, repo_id, isin, nominal, mtm_clean_price, accrued_interest,
       dirty_value, haircut, post_haircut_value, haircut_source
FROM collateral_position;

-- margin_call --------------------------------------------------------------
SELECT call_id, as_of_date, repo_id, desk_id, exposure, collateral_value,
       shortfall, call_amount, due_at, status
FROM margin_call;

-- repo_trade ---------------------------------------------------------------
SELECT repo_id, desk_id, direction, isin, nominal, trade_date, start_date,
       end_date, repo_rate_pct, haircut, purchase_price, repurchase_price, status
FROM repo_trade;

-- reference ----------------------------------------------------------------
SELECT symbol, security_name, isin, first_seen, last_seen FROM security;

SELECT desk_id, desk_name, desk_type FROM desk;

SELECT series_code, reverse_leg_date, contract_set, first_seen, last_seen
FROM slb_series;

SELECT isin, security_code, instrument_type, issue_desc, coupon_pct, issue_date,
       maturity_date, last_ip_date, next_ip_date, coupon_freq, face_value
FROM gsec;
