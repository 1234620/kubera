# 09 — Dashboard

One page. No framework, no bundler, no login. Served by FastAPI from `web/`.

Rules, including the accessibility floor: [`../rules/FRONTEND.md`](../rules/FRONTEND.md).

## Files

```
web/
├── index.html          structure only
├── css/
│   ├── tokens.css      :root custom properties — the entire palette
│   └── app.css         layout, gradients, @keyframes
└── js/
    ├── api.js          fetch wrappers, one per endpoint
    ├── format.js       Intl.NumberFormat('en-IN'), bps/pct/₹ formatters
    ├── charts.js       Chart.js configs with gradient fills
    └── app.js          load, render, wire the filters
```

Chart.js from `cdn.jsdelivr.net`. That is the only external dependency.

## Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  SLB & REPO FINANCING DESK            as of 18-Sep-2026  ● live  │
├──────────────────────────────────────────────────────────────────┤
│ ┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌──────────┐    │
│ │ ON LOAN  ││ NET SPRD ││ WTD FEE  ││  UTIL    ││  CALLS   │    │
│ │ ₹412.6cr ││  +38 bps ││  4.12%   ││  61.4%   ││    3     │    │
│ │ ▁▂▄▆█▆▄  ││ ▁▃▂▅▇█▆  ││ ▄▄▅▅▆▆▇  ││ ▂▄▃▅▄▆▅  ││  ▁▁▂▁▃   │    │
│ └──────────┘└──────────┘└──────────┘└──────────┘└──────────┘    │
├───────────────────────────────┬──────────────────────────────────┤
│  SPECIALNESS  symbol × series │  FEE TERM STRUCTURE              │
│  ▓▓░░▒▒██░░▒▒░░  heatmap      │  gradient-filled line, by tenor  │
├───────────────────────────────┴──────────────────────────────────┤
│  BLOTTER                                                         │
│  symbol series side qty notional fee% util% spec net_spread DV01 │
└──────────────────────────────────────────────────────────────────┘
```

## The visual work

**Background** — a layered `radial-gradient` mesh on `body`, two blobs drifting
on a 40 s `@keyframes` translate loop at low opacity. Motion you notice only if you
look, which is the right amount for a screen someone stares at all day.

**KPI cards** — `linear-gradient(145deg, …)` surface, a 1px gradient border via
`background-clip: padding-box, border-box` on two backgrounds, and a hover lift
(`translateY(-2px)` plus a deeper shadow) on a 180 ms `cubic-bezier(.4,0,.2,1)`.
Each card's accent hue is keyed to its metric so the strip is readable at a glance.

**Number count-up** — KPI values animate from 0 to their value over 700 ms with an
ease-out, via `requestAnimationFrame`. Twenty lines, and it is the single cheapest
thing that makes a dashboard feel alive.

**Sparklines** — inline SVG `path` with a `linearGradient` fill fading to
transparent, and a `stroke-dasharray` / `stroke-dashoffset` draw-in animation on
first paint.

**Specialness heatmap** — CSS grid, cell background interpolated across the
score scale in OKLCH so the ramp is perceptually even (a naive HSL ramp bunches up
in the greens and misleads the eye). Staleness is rendered as reduced opacity plus
a diagonal hatch, never as a missing cell — "no recent print" is information.

Columns are **tenor buckets**, not exact tenor days. That was a correction, not
the original plan: a typical security quotes at only one or two exact tenors, so a
symbol × exact-tenor grid came out about 10% filled and read as a broken chart.
The buckets are the ones the score is already ranked within, and the grid is ~47%
filled — where an empty cell now genuinely means "this name does not quote at that
tenor", which is itself information about the market's shape.

**Term structure** — Chart.js line with a canvas `createLinearGradient` fill,
`tension: 0.3`, and the axis in **tenor days** rather than series code so the
curve's shape is true to the tenors and not to an alphabetical ordering of codes.

**One line per contract set.** The regular and non-foreclosing sets are separate
curves; a single dataset spanning both doubles back on itself and the shape
becomes a lie. This project hit that same trap three times — in the SQL window
frame for `LAST_VALUE`, in the specialness monotonicity test, and here — which is
why the contract set is now part of every grouping key by reflex.

**Blotter** — staggered row entry (`animation-delay: calc(var(--i) * 18ms)`),
positive/negative spreads coloured from the token palette, and a 2 s pulse on any
row whose position has an open margin call. Sticky header, monospace numerics with
`font-variant-numeric: tabular-nums` so columns of digits line up.

**Loading** — a shimmer skeleton (`@keyframes` sweeping a gradient across a
translucent block) in the shape of the eventual content. Never a spinner, never a
layout jump when data lands.

**Reduced motion** — `@media (prefers-reduced-motion: reduce)` sets
`animation: none` and `transition: none` globally and skips the count-up, going
straight to the final value. Required, not optional.

## Palette

Dark by default; this is a trading screen. `web/css/tokens.css` defines every
colour on `:root` and redefines them under
`@media (prefers-color-scheme: light)`. No hex literal appears outside that file.

Semantic tokens: `--bg`, `--surface`, `--surface-raised`, `--border`, `--text`,
`--text-dim`, `--accent`, `--accent-2`, `--pos` (gain), `--neg` (loss),
`--warn`, plus `--spec-0` … `--spec-4` for the specialness ramp.

Gain/loss are **not** bare red and green — they are adjusted for deuteranopia and
paired with sign and position so colour is never the only channel carrying meaning.

## What checks it

`tests/test_frontend.py` is a linter for this document: it asserts there is no
build step, no login anywhere in `web/`, no colour literal outside `tokens.css`,
that the specialness ramp is OKLCH, that `prefers-reduced-motion` is honoured in
the CSS *and* in the count-up *and* in the chart configs, that the required
`@keyframes` exist, that only the allowed CDNs are referenced, that money is
formatted `en-IN`, and that the annualisation formula does not appear
client-side — because the frontend formats and draws, it does not compute.

It earned its place immediately by catching a hardcoded `#06080f` in `app.css`,
which is exactly the rule it exists to enforce.

## Behaviour

- One `Promise.all` on load; every panel renders from that single batch.
- Filters (desk, classification, symbol search) are client-side over data already
  fetched. Changing a filter does not hit the API.
- `as_of` changes do re-fetch.
- No polling. This is end-of-day data; a live ticker would be a lie about what the
  numbers are.
- The frontend formats and draws. It does not compute analytics. Anything beyond a
  percentage belongs in SQL.
