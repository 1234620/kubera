"""Apply db/migrations/*.sql in filename order, once each.

Forward-only by design (ADR 0002). A migration whose checksum no longer matches
the recorded one fails the run rather than diverging silently.
"""

import hashlib
import os
import pathlib
import sys

import psycopg

MIGRATIONS = pathlib.Path(__file__).parent.parent / "db" / "migrations"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version    TEXT PRIMARY KEY,
    checksum   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def dsn() -> str:
    return (
        f"host={os.environ.get('POSTGRES_HOST', 'localhost')}"
        f" port={os.environ.get('POSTGRES_PORT', '5432')}"
        f" dbname={os.environ.get('POSTGRES_DB', 'slbdesk')}"
        f" user={os.environ.get('POSTGRES_USER', 'slbdesk')}"
        f" password={os.environ.get('POSTGRES_PASSWORD', 'slbdesk')}"
    )


def main() -> int:
    files = sorted(MIGRATIONS.glob("*.sql"))
    with psycopg.connect(dsn(), autocommit=False) as conn:
        conn.execute(TRACKING_TABLE)
        conn.commit()
        applied = dict(conn.execute("SELECT version, checksum FROM schema_migration").fetchall())

        for path in files:
            sql = path.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

            if path.name in applied:
                if applied[path.name] != checksum:
                    print(f"FAIL {path.name}: already applied but its contents changed.")
                    return 1
                continue

            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migration (version, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
            conn.commit()
            print(f"applied {path.name}")

    print(f"{len(files)} migration(s) present, all applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
