-- 001 — Nothing to do.
--
-- scripts/migrate.py creates schema_migration itself before reading this
-- directory, so the first real migration is 002. This file exists so the
-- migration runner has something to apply in stage 0 and CI exercises the
-- checksum path end to end.
SELECT 1;
