# 0002 — No ORM, no migration framework

**Status**: accepted

## Context
SQLAlchemy plus Alembic is the default Python answer. It is also several hundred
lines of models and config before the first query runs.

## Decision
`psycopg` directly. Migrations are numbered `.sql` files applied in filename order
by a ~40-line `scripts/migrate.py`, tracked in a `schema_migration` table with a
checksum.

## Consequences
- The schema is readable as SQL, which is how a data person wants to read it.
- No model/migration drift, because there is only one definition of the schema.
- Checksum mismatch on an already-applied migration fails the run, so an edited
  migration cannot silently diverge.
- Forward-only. No down migrations — `make reset` and replay is faster than
  maintaining reversals nobody tests.
- Cost: no compile-time safety on column names. Accepted; CI runs the real queries.
