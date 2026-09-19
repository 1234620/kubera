-- 006 — Give gsec the same provenance columns as the market tables.
--
-- gsec is reference data but it is still loaded from a file (wdmlist_*.csv), so
-- it should answer "which file did this row come from" like everything else. This
-- also means the loader needs no special case for it.
--
-- A separate migration rather than an edit to 002: scripts/migrate.py checksums
-- applied migrations and refuses a changed one, which is the whole point of 0002.

ALTER TABLE gsec
    ADD COLUMN source_file TEXT,
    ADD COLUMN loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now();
