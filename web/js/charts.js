/* Chart.js configs and the inline-SVG sparklines. Gradients are built against
   the canvas context, which is why they are created per chart rather than
   declared in CSS. */

const MOTION = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function areaFill(ctx, area, colour) {
  const gradient = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  gradient.addColorStop(0, `color-mix(in oklab, ${colour} 46%, transparent)`);
  gradient.addColorStop(1, `color-mix(in oklab, ${colour} 0%, transparent)`);
  return gradient;
}

const BASE_SCALES = () => ({
  x: {
    grid: { color: token("--border"), drawTicks: false },
    ticks: { color: token("--text-faint"), font: { size: 10 } },
    title: { display: true, color: token("--text-faint"), font: { size: 10 } },
  },
  y: {
    grid: { color: token("--border"), drawTicks: false },
    ticks: { color: token("--text-faint"), font: { size: 10 } },
  },
});

/** Fee term structure: one line per symbol, x-axis in TENOR DAYS.
    Plotting against the series code instead would order the curve
    alphabetically and make its shape a lie (docs/09). */
export function termStructureChart(canvas, series) {
  // The accent first, then muted tones. Restraint: the first series is the one
  // being asked about, and the rest are context.
  // White first -- the series being asked about -- then muted tones for context.
  // In monochrome the ordering IS the hierarchy.
  const palette = [token("--accent"), token("--text-dim"), token("--warn"), token("--text-faint")];

  return new Chart(canvas, {
    type: "line",
    data: {
      datasets: series.map((s, i) => ({
        label: s.label,
        data: s.points.map((p) => ({ x: p.tenor_days, y: p.fee_annualised_pct })),
        borderColor: palette[i % palette.length],
        borderWidth: 2,
        tension: 0.3,
        pointRadius: 3,
        pointHoverRadius: 5,
        pointBackgroundColor: palette[i % palette.length],
        fill: series.length === 1 ? "origin" : false,
        backgroundColor: (c) =>
          c.chart.chartArea
            ? areaFill(c.chart.ctx, c.chart.chartArea, palette[i % palette.length])
            : "transparent",
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: MOTION ? { duration: 700 } : false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: { labels: { color: token("--text-dim"), boxWidth: 10, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: (items) => `${items[0].parsed.x} days`,
            label: (item) => ` ${item.dataset.label}: ${item.parsed.y.toFixed(2)}% p.a.`,
          },
        },
      },
      scales: {
        ...BASE_SCALES(),
        x: {
          ...BASE_SCALES().x,
          type: "linear",
          title: { display: true, text: "tenor (days)", color: token("--text-faint"), font: { size: 10 } },
        },
        y: {
          ...BASE_SCALES().y,
          title: { display: true, text: "fee % p.a.", color: token("--text-faint"), font: { size: 10 } },
        },
      },
    },
  });
}

/** The funding curve, with the extrapolated nodes drawn hollow so an estimate
    never looks measured. */
export function fundingCurveChart(canvas, points) {
  const base = token("--text-faint");
  const ftp = token("--accent");

  return new Chart(canvas, {
    type: "line",
    data: {
      labels: points.map((p) => p.tenor_days),
      datasets: [
        {
          label: "FTP rate (base + TLP)",
          data: points.map((p) => p.ftp_rate_pct),
          borderColor: ftp,
          borderWidth: 2,
          tension: 0.25,
          fill: "origin",
          backgroundColor: (c) =>
            c.chart.chartArea ? areaFill(c.chart.ctx, c.chart.chartArea, ftp) : "transparent",
          pointRadius: 4,
          pointBackgroundColor: points.map((p) => (p.is_estimated ? token("--bg") : ftp)),
          pointBorderColor: ftp,
          pointBorderWidth: 2,
        },
        {
          label: "base curve",
          data: points.map((p) => p.base_rate_pct),
          borderColor: base,
          borderWidth: 1.5,
          borderDash: [4, 3],
          tension: 0.25,
          fill: false,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: MOTION ? { duration: 700 } : false,
      plugins: {
        legend: { labels: { color: token("--text-dim"), boxWidth: 10, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: (items) => `${items[0].label} days`,
            label: (item) =>
              ` ${item.dataset.label}: ${item.parsed.y.toFixed(3)}%` +
              (item.datasetIndex === 0 && points[item.dataIndex].is_estimated
                ? "  (extrapolated)"
                : ""),
          },
        },
      },
      scales: {
        ...BASE_SCALES(),
        x: {
          ...BASE_SCALES().x,
          title: { display: true, text: "tenor (days)", color: token("--text-faint"), font: { size: 10 } },
        },
        y: {
          ...BASE_SCALES().y,
          title: { display: true, text: "% p.a.", color: token("--text-faint"), font: { size: 10 } },
        },
      },
    },
  });
}

/** Inline SVG sparkline with a gradient fill and a stroke draw-in. Inline rather
    than Chart.js because five of these on one strip should not cost five canvases. */
export function sparkline(values, colour, id) {
  const clean = values.filter((v) => v != null && Number.isFinite(v));
  if (clean.length < 2) return "";

  const w = 100;
  const h = 30;
  const min = Math.min(...clean);
  const max = Math.max(...clean);
  const span = max - min || 1;

  const point = (v, i) => [
    (i / (clean.length - 1)) * w,
    h - 2 - ((v - min) / span) * (h - 5),
  ];
  const line = clean.map(point).map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w},${h} L0,${h} Z`;

  return `
    <svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="sp-${id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${colour}" stop-opacity=".42"/>
          <stop offset="100%" stop-color="${colour}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="${area}" fill="url(#sp-${id})"/>
      <path d="${line}" fill="none" stroke="${colour}" stroke-width="1.6"
            stroke-linejoin="round" stroke-linecap="round"
            ${MOTION ? `pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"
            style="animation: draw .9s var(--ease) forwards"` : ""}/>
    </svg>`;
}
