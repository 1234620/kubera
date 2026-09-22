/* Load once, render four bands, wire the filters.
   Everything here formats and draws. Anything beyond a percentage belongs in
   SQL (rules/FRONTEND.md). */

import { api, isLive } from "./api.js";
import { bps, countUp, day, money, num, pct, qty, sign, signedMoney } from "./format.js";
import { fundingCurveChart, sparkline, termStructureChart } from "./charts.js";
import { mountGlobe } from "./globe.js";

const $ = (id) => document.getElementById(id);

const state = {
  kpis: null,
  history: [],
  specialness: [],
  positions: [],
  curve: [],
  desks: [],
  calls: new Set(),
  charts: {},
};

/* --- KPI strip ------------------------------------------------------------ */

// `lead: true` marks the one card that carries the accent. Six equally bright
// cards rank nothing -- the reference dashboards give the accent to a single
// element per region and leave the rest on hairlines.
const KPI_CARDS = [
  {
    label: "On loan",
    tone: "var(--text-dim)",
    value: (k) => k.on_loan_inr,
    render: money,
    series: "on_loan_inr",
    foot: (k) => `${qty(k.positions)} positions · ${money(k.gross_notional_inr)} gross`,
  },
  {
    label: "Net financing spread",
    lead: true,
    tone: "var(--accent)",
    value: (k) => k.net_financing_spread_bps,
    render: (v) => bps(v, 1),
    series: "net_financing_spread_bps",
    foot: (k) => `${signedMoney(k.net_spread_inr)} / day book NIM`,
  },
  {
    label: "Weighted avg fee",
    tone: "var(--text-dim)",
    value: (k) => k.weighted_avg_fee_pct,
    render: (v) => pct(v, 2),
    series: "weighted_avg_fee_pct",
    foot: (k) => `FTP charged ${signedMoney(k.ftp_charge_inr)} / day`,
  },
  {
    label: "Utilisation",
    tone: "var(--text-dim)",
    value: (k) => k.book_utilisation_pct,
    render: (v) => pct(v, 1),
    series: "book_utilisation_pct",
    estimated: (k) => k.utilisation_is_estimated,
    foot: () => "lendable supply is not published — estimated",
  },
  {
    label: "Specials",
    tone: "var(--text-dim)",
    value: (k) => k.special_count,
    render: (v) => qty(Math.round(v)),
    series: "special_count",
    foot: (k) =>
      `${k.hard_to_borrow_count} hard-to-borrow · ${k.stale_count}/${k.scored_count} stale quote`,
  },
  {
    label: "Open margin calls",
    tone: "var(--text-dim)",
    value: (k) => k.open_margin_calls,
    render: (v) => qty(Math.round(v)),
    series: "open_margin_calls",
    foot: (k) => `${money(k.open_call_amount_inr)} to post · DV01 ${signedMoney(k.book_dv01_inr)}`,
  },
];

function renderKpis() {
  const k = state.kpis;
  $("kpis").innerHTML = KPI_CARDS.map((card, i) => {
    const series = state.history.map((row) => row[card.series]);
    const est = card.estimated?.(k) ? '<span class="tag-est">EST</span>' : "";
    return `
      <article class="kpi${card.lead ? " lead" : ""}" style="--i:${i}">
        <div class="label">${card.label}${est}</div>
        <div class="value" id="kpi-${i}">—</div>
        <div class="foot">${card.foot(k)}</div>
        ${sparkline(series, card.tone, i)}
      </article>`;
  }).join("");

  KPI_CARDS.forEach((card, i) => countUp($(`kpi-${i}`), card.value(k), card.render));
}

/* --- specialness heatmap -------------------------------------------------- */

/** Score 0-100 to a colour, interpolated across the OKLCH ramp declared in
    tokens.css. Stepping through the five stops keeps the ramp perceptually even. */
function heatColour(score) {
  const stops = ["--spec-0", "--spec-1", "--spec-2", "--spec-3", "--spec-4"];
  const position = Math.max(0, Math.min(score, 100)) / 100 * (stops.length - 1);
  const low = Math.floor(position);
  const high = Math.min(low + 1, stops.length - 1);
  const weight = position - low;
  return `color-mix(in oklab, var(${stops[high]}) ${(weight * 100).toFixed(0)}%, var(${stops[low]}))`;
}

function renderHeatmap() {
  const rows = state.specialness.filter((r) => !state.filters?.symbol || r.symbol.includes(state.filters.symbol));
  if (!rows.length) {
    $("heat").innerHTML = '<p class="dim">Nothing matches this filter.</p>';
    return;
  }

  // Columns are TENOR BUCKETS, not exact tenor days. The market is sparse -- a
  // typical security quotes at one or two exact tenors -- so a symbol x
  // exact-tenor grid is almost entirely empty and reads as a broken chart. The
  // buckets are the ones the score is already ranked within, which is what
  // slb_specialness_daily.tenor_bucket exists for.
  const BUCKETS = ["0-45d", "46-135d", "136-270d", "271d+"];
  const buckets = BUCKETS.filter((b) => rows.some((r) => r.tenor_bucket === b));

  const worst = new Map();
  for (const row of rows) {
    const current = worst.get(row.symbol) ?? 0;
    if (row.specialness_score > current) worst.set(row.symbol, row.specialness_score);
  }
  const symbols = [...worst.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 16)
    .map(([symbol]) => symbol);

  // A symbol can quote several series inside one bucket; keep the worst, because
  // the expensive borrow is the one the desk needs to see.
  const byKey = new Map();
  for (const row of rows) {
    const key = `${row.symbol}|${row.tenor_bucket}`;
    const existing = byKey.get(key);
    if (!existing || row.specialness_score > existing.specialness_score) byKey.set(key, row);
  }

  const cells = [
    '<div class="col-head"></div>',
    ...buckets.map((b) => `<div class="col-head">${b}</div>`),
  ];

  let index = 0;
  for (const symbol of symbols) {
    cells.push(`<div class="row-head" title="${symbol}">${symbol}</div>`);
    for (const bucket of buckets) {
      const row = byKey.get(`${symbol}|${bucket}`);
      index += 1;
      if (!row) {
        cells.push('<div class="cell empty" style="--i:' + index + '"></div>');
        continue;
      }
      const title = [
        `${row.symbol} ${row.series_code} · ${row.tenor_days}d`,
        `score ${num(row.specialness_score, 1)} (${row.classification})`,
        `fee ${pct(row.fee_annualised_pct)} p.a.`,
        row.gc_fee_annualised_pct != null ? `GC ${pct(row.gc_fee_annualised_pct)} → ${bps(row.spread_to_gc_bps)}` : null,
        `percentile ${num(row.xs_percentile * 100, 0)}`,
        row.own_z != null ? `own-history z ${num(row.own_z, 2)}σ` : "no usable history",
        row.is_stale ? `STALE — last print ${row.days_since_last_trade}d ago` : "priced today",
      ].filter(Boolean).join("\n");

      // Below the ramp's midpoint the cell is dark, so dark-on-dark text would
      // fail contrast. Flip to the light text token instead of relying on one
      // ink colour across the whole scale.
      const dark = row.specialness_score < 50 ? " low" : "";
      cells.push(
        `<div class="cell${dark}${row.is_stale ? " stale" : ""}" style="--i:${index};` +
          `background:${heatColour(row.specialness_score)}" title="${title}">` +
          `${num(row.specialness_score, 0)}</div>`
      );
    }
  }

  $("heat").style.setProperty("--cols", buckets.length);
  $("heat").innerHTML = cells.join("");
}

/* --- charts --------------------------------------------------------------- */

async function renderTermStructure() {
  const symbol = $("ts-symbol").value;
  if (!symbol) return;

  const points = await api.termStructure(symbol);
  state.charts.term?.destroy();

  if (!points.length) {
    $("ts-note").textContent = `${symbol} did not quote on this date`;
    return;
  }

  // ONE LINE PER CONTRACT SET. The two sets are separate curves priced on their
  // own percentile scale, so a single dataset spanning both doubles back on
  // itself and the shape becomes a lie. Same trap the SQL window frame and the
  // specialness monotonicity check both hit.
  const sets = new Map();
  for (const point of points) {
    if (!sets.has(point.contract_set)) sets.set(point.contract_set, []);
    sets.get(point.contract_set).push(point);
  }

  const series = [...sets.entries()].map(([contractSet, rows]) => ({
    label: `${symbol} · ${contractSet === "NON_FORECLOSING" ? "X series" : contractSet.toLowerCase()}`,
    points: rows.slice().sort((a, b) => a.tenor_days - b.tenor_days),
  }));

  // The slope is per curve, so describe the one with the most points.
  const longest = series.reduce((a, b) => (b.points.length > a.points.length ? b : a));
  const slope = longest.points[0].term_slope_bps;
  $("ts-note").textContent =
    longest.points.length < 2
      ? "only one tenor quoted — no curve"
      : slope > 0
        ? `upward ${bps(slope)} — market expects the borrow to stay tight`
        : slope < 0
          ? `downward ${bps(slope)} — demand looks transient, with a resolution date`
          : "flat";

  state.charts.term = termStructureChart($("ts-canvas"), series);
}

function renderCurve() {
  state.charts.curve?.destroy();
  state.charts.curve = fundingCurveChart($("curve-canvas"), state.curve);
  const estimated = state.curve.filter((p) => p.is_estimated).length;
  $("curve-note").textContent = estimated
    ? `${estimated} of ${state.curve.length} nodes extrapolated (hollow) — SLB tenors sit where sovereign coverage is thinnest`
    : "every node interpolated between observations";
}

/* --- desk decomposition --------------------------------------------------- */

function renderDesks() {
  $("desks").innerHTML = state.desks
    .map(
      (d, i) => `
      <tr style="--i:${i}">
        <td class="left">${d.desk_id}</td>
        <td>${qty(d.positions)}</td>
        <td>${money(d.gross_notional_inr)}</td>
        <td>${money(d.net_notional_inr)}</td>
        <td class="${sign(d.fee_pnl_inr)}">${signedMoney(d.fee_pnl_inr)}</td>
        <td class="${sign(d.ftp_charge_inr)}">${signedMoney(d.ftp_charge_inr)}</td>
        <td class="${sign(d.desk_spread_inr)}">${signedMoney(d.desk_spread_inr)}</td>
        <td class="${sign(d.treasury_spread_inr)}">${signedMoney(d.treasury_spread_inr)}</td>
        <td class="${sign(d.net_spread_inr)}">${signedMoney(d.net_spread_inr)}</td>
      </tr>`
    )
    .join("");
}

/* --- blotter -------------------------------------------------------------- */

const PILL = { GC: "gc", WARM: "warm", SPECIAL: "spec", HARD_TO_BORROW: "htb" };

function renderBlotter() {
  const desk = $("bl-desk").value;
  const search = $("bl-search").value.trim().toUpperCase();

  const rows = state.positions
    .filter((r) => !desk || r.desk_id === desk)
    .filter((r) => !search || r.symbol.includes(search))
    .slice(0, 300);

  $("bl-count").textContent = `${rows.length} of ${state.positions.length}`;

  $("blotter").innerHTML = rows
    .map(
      (r, i) => `
      <tr style="--i:${i}" class="${state.calls.has(r.symbol) ? "called" : ""}">
        <td class="left">${r.symbol}</td>
        <td class="left dim">${r.series_code}</td>
        <td class="left ${r.side === "LEND" ? "pos" : "neg"}">${r.side}</td>
        <td>${qty(r.quantity)}</td>
        <td>${money(r.notional_inr)}</td>
        <td>${r.tenor_days}d</td>
        <td>${pct(r.fee_annualised_pct)}</td>
        <td>${r.specialness_score == null ? "—" : `<span class="pill ${PILL[r.classification] ?? "gc"}">${num(r.specialness_score, 0)}</span>`}</td>
        <td>${r.ftp_rate_pct == null ? "—" : pct(r.ftp_rate_pct)}</td>
        <td class="${sign(r.fee_pnl_inr)}">${signedMoney(r.fee_pnl_inr)}</td>
        <td class="${sign(r.ftp_charge_inr)}">${r.ftp_charge_inr == null ? "—" : signedMoney(r.ftp_charge_inr)}</td>
      </tr>`
    )
    .join("");
}

/* --- boot ----------------------------------------------------------------- */

async function boot() {
  try {
    const [kpis, history, specialness, positions, curve, desks, calls] = await Promise.all([
      api.kpis(),
      api.history(),
      api.specialness(),
      api.positions(),
      api.ftpCurve(),
      api.decomposition(),
      api.marginCalls({ status: "OPEN" }),
    ]);

    Object.assign(state, { kpis, history, specialness, positions, curve, desks });

    // Positions are keyed by symbol; a margin call is keyed by repo. Flagging
    // the desk rather than the exact position is deliberately loose -- it says
    // "this name has collateral under stress", which is the useful signal.
    const latest = calls.length ? calls[0].as_of_date : null;
    state.calls = new Set(
      calls.filter((c) => c.as_of_date === latest).map((c) => c.desk_id)
    );

    $("stamp-date").textContent = day(kpis.as_of_date);
    const dot = $("stamp-dot");

    // On GitHub Pages there is no FastAPI behind the page, so /docs and the
    // coverage endpoint would 404 and the numbers are a frozen snapshot. Say
    // both, and send the links to the files in the repository instead.
    if (!(await isLive())) {
      $("stamp-text").textContent = "static snapshot ·";
      const docs = "https://github.com/1234620/kubera/blob/main/docs/08-api-spec.md";
      document.querySelectorAll('a[href^="/docs"], a[href^="/api/"]').forEach((link) => {
        // An /api link has a snapshot file; /docs is FastAPI's own generated
        // page, which only exists where FastAPI is running.
        const path = link.getAttribute("href").replace("/api/", "");
        link.href = link.getAttribute("href") === "/docs" ? docs : `data/snapshot/${path}.json`;
        link.rel = "noreferrer";
      });
    }
    if (!kpis.ftp_reconciles) {
      dot.classList.add("bad");
      $("stamp-text").textContent = "FTP does not reconcile";
    }

    renderKpis();
    renderHeatmap();
    renderDesks();
    renderBlotter();
    renderCurve();

    const symbols = [...new Set(specialness.map((r) => r.symbol))].sort();
    $("ts-symbol").innerHTML = symbols.map((s) => `<option>${s}</option>`).join("");
    $("ts-symbol").value = specialness[0]?.symbol ?? symbols[0];
    await renderTermStructure();

    $("bl-desk").innerHTML =
      '<option value="">all desks</option>' +
      [...new Set(positions.map((p) => p.desk_id))].sort().map((d) => `<option>${d}</option>`).join("");
  } catch (error) {
    document.querySelector(".main").insertAdjacentHTML(
      "afterbegin",
      `<div class="error"><strong>Could not load.</strong> ${error.message}
       <br>Run <code>make up &amp;&amp; make migrate &amp;&amp; make ingest &amp;&amp;
       make seed &amp;&amp; make analytics</code>, then reload.</div>`
    );
  }
}

// The globe is decorative, so it is mounted independently of the data load: a
// failed fetch must not leave the landing page blank.
mountGlobe($("globe"));

// Filters are client-side over data already fetched, so changing one does not
// hit the API. Only as_of and the term-structure symbol re-fetch.
$("bl-desk").addEventListener("change", renderBlotter);
$("bl-search").addEventListener("input", renderBlotter);
$("ts-symbol").addEventListener("change", renderTermStructure);

boot();
