/* One wrapper per endpoint. Same-origin, so no CORS and no base URL.

   The public copy of this page is on GitHub Pages, which serves files and not
   processes: no FastAPI, no Postgres. So every response the dashboard reads is
   also committed as static JSON under `web/data/snapshot`
   (`scripts/snapshot_api.py`). Probe `/api/health` once at boot; if nothing
   answers, read the snapshot instead of the API.

   One probe rather than a fallback per request, because otherwise the Pages copy
   fires a dozen 404s before drawing anything. */

const SNAPSHOT = "data/snapshot";

let probe = null;

/** Whether a live API is answering. Resolved once, then reused. */
export function isLive() {
  probe ??= fetch("/api/health")
    .then((response) => response.ok)
    .catch(() => false);
  return probe;
}

async function fromSnapshot(path, params) {
  const file = `${SNAPSHOT}/${path.replace(/^\//, "").replaceAll("/", "-")}.json`;
  const response = await fetch(file);
  if (!response.ok) throw new Error(`no snapshot for ${path} (${file})`);
  const data = await response.json();

  // Term structure is one file keyed by symbol, because the dropdown can pick
  // any of 372. Every other file is the response as it came off the API, with
  // the frontend's default filters already applied.
  return path === "/slb/term-structure" ? (data[params.symbol] ?? []) : data;
}

async function get(path, params = {}) {
  if (!(await isLive())) return fromSnapshot(path, params);

  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v != null && v !== "")
  );
  const url = `/api${path}${query.toString() ? `?${query}` : ""}`;

  const response = await fetch(url);
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`${response.status} ${url}${detail ? ` — ${detail}` : ""}`);
  }
  return response.json();
}

export const api = {
  health: () => get("/health"),
  kpis: (as_of) => get("/book/kpis", { as_of }),
  history: () => get("/book/history"),
  positions: (params) => get("/book/positions", { limit: 500, ...params }),
  deskPnl: (params) => get("/book/pnl", params),
  specialness: (params) => get("/slb/specialness", { limit: 1000, ...params }),
  termStructure: (symbol, as_of) => get("/slb/term-structure", { symbol, as_of }),
  gcRate: () => get("/slb/gc-rate"),
  utilisation: (params) => get("/slb/utilisation", { limit: 500, ...params }),
  ftpCurve: (as_of) => get("/ftp/curve", { as_of }),
  decomposition: (as_of) => get("/ftp/decomposition", { as_of }),
  marginCalls: (params) => get("/repo/margin-calls", params),
};
