# Coding rules

## The ladder
Before writing anything, stop at the first rung that holds:
1. Does this need to exist? Speculative need → skip it.
2. Does this repo already have it? Reuse it.
3. Does the stdlib do it? `datetime`, `decimal`, `csv`, `itertools`, `functools`, `statistics`.
4. Does Postgres do it? A window function beats a Python loop over a result set.
5. Does an installed dependency do it? Never add one for what ten lines cover.
6. Can it be one line? One line.
7. Only then: the minimum code that works.

## Concretely, for this repo

- **No ORM.** `psycopg` + SQL files. The SQL is the portfolio piece; hiding it
  behind SQLAlchemy defeats the point.
- **No migration framework.** `db/migrations/NNN_name.sql` applied in filename
  order by `scripts/migrate.py`, tracked in a `schema_migration` table. That is
  forty lines and it is enough.
- **No frontend build step.** Static HTML/CSS/JS. Chart.js from a CDN.
- **No class where a function does.** Bond math is pure functions taking floats
  and dates. No `Bond` class with mutable state.
- **`Decimal` for money, `float` for rates and risk.** Mixing them silently is the
  bug you find in production. Convert at the boundary, once.
- **No config for a value that never changes.** Hardcode the NSE archive base URL.
- **Dependencies** are pinned in `pyproject.toml`. The whole list should fit on a
  screen: `httpx`, `psycopg[binary]`, `fastapi`, `uvicorn`, `pydantic`, `pytest`.
  `pandas` only if a parser genuinely needs it — most of these files are `csv`-shaped.

## Naming
`snake_case` everywhere, including SQL. Market-data table names mirror the NSE
file they came from so provenance is obvious: `slb_quote_daily` ← `SLBM_BC_*.DAT`.

## Checks, not test suites
Non-trivial logic leaves exactly one runnable check behind — a `test_*.py` with
plain `assert`s, or a `demo()` under `__main__`. No fixtures framework, no mocks
of Postgres, no per-function unit tests. Parsers are checked against the real
files in `tests/fixtures/`. Bond math is checked against hand-computed values and
against NSE's own published weighted YTM.

## Comments
Comment the *why* and the *convention*, never the *what*. Every financial formula
carries a one-line comment naming its day count, compounding and sign convention.
Mark a deliberate corner-cut with `# ponytail:` and name the ceiling.
