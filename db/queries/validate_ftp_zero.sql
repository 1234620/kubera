-- validate_ftp_zero.sql — the internal ledger must net to zero.
--
-- Every FTP charge debited to a business desk is credited to Treasury, so the
-- sum across ALL desks on a day must be zero. Returns any day where it is not.
-- An empty result is a pass.
--
-- This is the check that makes the NIM decomposition meaningful rather than a
-- restatement: the decomposition collapses to fee income less Treasury's real
-- cost of funds only because the internal transfer cancels here.

SELECT
    as_of_date,
    SUM(ftp_charge_inr) AS net_internal_transfer_inr,
    COUNT(*)            AS desk_rows
FROM desk_pnl_daily
GROUP BY as_of_date
HAVING ABS(SUM(ftp_charge_inr)) > 1.0;
