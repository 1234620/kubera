-- validate_ftp_reconciliation.sql — the decomposition must add back to NIM.
--
--   SUM over desks of (desk_spread + treasury_spread) = book NIM
--
-- where desk_spread = fee + FTP charged, treasury_spread = FTP credited + what
-- Treasury actually paid, and book NIM = fee income + Treasury's real cost.
--
-- Returns any day where the two sides differ by more than a rupee. An empty
-- result is a pass. If this fails, the FTP model is decorative (docs/04 §5).

SELECT
    as_of_date,
    SUM(desk_spread_inr + treasury_spread_inr) AS decomposition_inr,
    SUM(fee_pnl_inr + actual_cost_of_funds_inr) AS book_nim_inr,
    SUM(desk_spread_inr + treasury_spread_inr)
        - SUM(fee_pnl_inr + actual_cost_of_funds_inr) AS difference_inr,
    BOOL_AND(reconciles) AS row_flag_agrees
FROM desk_pnl_daily
GROUP BY as_of_date
HAVING ABS(
    SUM(desk_spread_inr + treasury_spread_inr)
    - SUM(fee_pnl_inr + actual_cost_of_funds_inr)
) > 1.0
    OR NOT BOOL_AND(reconciles);
