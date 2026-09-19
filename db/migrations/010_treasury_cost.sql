-- 010 — Treasury's actual cost of funds, so the NIM decomposition can be
-- checked against something rather than restated.
--
-- The first version of desk_pnl_daily computed `reconciles` as
--   |(fee + ftp) + treasury_spread - (fee + ftp + treasury_spread)| <= tol
-- which is true by construction. A test asserting that would assert nothing.
--
-- The identity that is NOT free is:
--
--   SUM over all desks of (desk_spread + treasury_spread) = book NIM
--
-- and it holds only because every FTP charge debited to a business desk is
-- credited to Treasury, so the internal transfer cancels and what is left is the
-- fee income less what Treasury really paid for the funding. That needs
-- Treasury's actual cost stored, which is this column.
--
-- A separate migration rather than an edit to 009: scripts/migrate.py checksums
-- applied migrations and refuses a changed one (ADR 0002).

ALTER TABLE desk_pnl_daily
    ADD COLUMN actual_cost_of_funds_inr NUMERIC(20, 4) NOT NULL DEFAULT 0;
