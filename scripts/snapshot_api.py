"""Dump every response the dashboard reads into static JSON.

GitHub Pages serves files, not processes: no Python, no Postgres. So the public
copy of the dashboard reads a snapshot instead of the API, and `web/js/api.js`
falls back to these files when `/api/health` does not answer.

The snapshot is committed, because CI has neither a database nor NSE's files.
Run it against a loaded local stack after `make analytics`:

    make up && python scripts/snapshot_api.py

File names are the endpoint path with slashes turned into dashes, which is what
api.js reconstructs. Term structure is one file keyed by symbol rather than 372
files, because the dropdown can pick any of them.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = pathlib.Path(__file__).parents[1] / "web" / "data" / "snapshot"

# Path -> query, mirroring the defaults in web/js/api.js. Anything the frontend
# does not call is left out; a snapshot of an unused endpoint is dead weight.
ENDPOINTS = {
    "/health": {},
    "/data-coverage": {},
    "/book/kpis": {},
    "/book/history": {},
    "/book/positions": {"limit": 500},
    "/book/pnl": {},
    "/slb/specialness": {"limit": 1000},
    "/slb/gc-rate": {},
    "/slb/utilisation": {"limit": 500},
    "/ftp/curve": {},
    "/ftp/decomposition": {},
    "/repo/margin-calls": {"status": "OPEN"},
}


def slug(path: str) -> str:
    return path.strip("/").replace("/", "-") + ".json"


def fetch(path: str, params: dict) -> object:
    query = urllib.parse.urlencode(params)
    url = f"{BASE}/api{path}" + (f"?{query}" if query else "")
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 -- localhost
        return json.load(response)


def write(name: str, payload: object) -> int:
    path = OUT / name
    # Compact, sorted keys: a stable byte-for-byte file, so re-running the
    # snapshot on unchanged data produces no diff.
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    path.write_text(text)
    return len(text)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    total = 0
    payloads = {}
    for path, params in ENDPOINTS.items():
        try:
            payloads[path] = fetch(path, params)
        except (urllib.error.URLError, OSError) as error:
            print(f"{path}: {error}\nIs the stack up? `make up`", file=sys.stderr)
            return 1
        size = write(slug(path), payloads[path])
        total += size
        print(f"{slug(path):28} {size:>9,} B")

    symbols = sorted({row["symbol"] for row in payloads["/slb/specialness"]})
    curves = {symbol: fetch("/slb/term-structure", {"symbol": symbol}) for symbol in symbols}
    size = write(slug("/slb/term-structure"), curves)
    total += size
    print(f"{'slb-term-structure.json':28} {size:>9,} B  ({len(symbols)} symbols)")

    print(f"{'total':28} {total:>9,} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
