"""FastAPI over the analytics layer. Read-only, no auth, one process.

No auth because this runs on localhost and authentication would be theatre
(rules/FRONTEND.md). No service layer either: a repository pattern over a dozen
SELECTs is an abstraction with one implementation.

The dashboard is served as static files from the same app at `/`, so there is one
port, no CORS configuration and no reverse proxy. Interactive API docs are at
`/docs` -- that is why this project does not hand-write API documentation.
"""

from __future__ import annotations

import contextlib
import pathlib

import psycopg
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool

from slbdesk import db
from slbdesk.api.routes import router

WEB = pathlib.Path(__file__).parents[3] / "web"

pool: ConnectionPool | None = None


def configure(conn: psycopg.Connection) -> None:
    """Return NUMERIC as float rather than Decimal.

    Pydantic v2 serialises Decimal to a JSON *string*, so without this every
    money field arrives as "3946218034.89" and the frontend has to parse it --
    while docs/08 promises JSON numbers. Registering the loader once here is the
    one-place fix; converting in each handler would be boilerplate in twenty.

    Float loses exactness, which is the right trade for a read-only display API:
    the exact values stay NUMERIC in Postgres, where the arithmetic happens.
    """
    conn.adapters.register_loader("numeric", FloatLoader)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    pool = ConnectionPool(
        db.dsn(),
        min_size=1,
        max_size=8,
        kwargs={"row_factory": dict_row},
        configure=configure,
        # Explicit, because psycopg_pool's default is changing and an implicit
        # open emits a DeprecationWarning.
        open=True,
    )
    pool.wait()
    yield
    pool.close()


app = FastAPI(
    title="Securities Lending & Repo Financing Desk Analytics",
    description=(
        "Read-only analytics over NSE Securities Lending & Borrowing data and NSE "
        "Wholesale Debt Market G-Secs. Market data is real; the book is synthetic. "
        "Fields ending `_inr` are rupees, `_pct` percent, `_bps` basis points. "
        "Responses carrying an estimate expose `is_estimated`; responses carrying a "
        "possibly stale quote expose `is_stale`."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api")

# Mounted last so it does not shadow /api. html=True serves index.html at /.
if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
