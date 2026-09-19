/* One wrapper per endpoint. Same-origin, so no CORS and no base URL. */

async function get(path, params = {}) {
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
