"""Database connection. One place that knows the DSN."""

from __future__ import annotations

import os

import psycopg


def dsn() -> str:
    return (
        f"host={os.environ.get('POSTGRES_HOST', 'localhost')}"
        f" port={os.environ.get('POSTGRES_PORT', '5432')}"
        f" dbname={os.environ.get('POSTGRES_DB', 'slbdesk')}"
        f" user={os.environ.get('POSTGRES_USER', 'slbdesk')}"
        f" password={os.environ.get('POSTGRES_PASSWORD', 'slbdesk')}"
    )


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn(), autocommit=autocommit)
