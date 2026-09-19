# 0003 — No frontend framework, no build step

**Status**: accepted

## Context
The dashboard needs gradients, animation and charts. The reflex answer is React
plus Vite plus a chart library plus a component library.

## Decision
Static HTML, CSS custom properties, ES modules, and Chart.js from a CDN. FastAPI
serves `web/` with `StaticFiles`. No bundler, no `node_modules`, no `package.json`.

## Consequences
- `git clone` and open a browser. Nothing to install, nothing to build, nothing to
  go stale.
- CSS does the visual work directly: gradients, `@keyframes`, `prefers-reduced-motion`.
  All of it is native platform capability, none of it needs a library.
- One page with four panels does not have a state-management problem, so it does not
  need a state-management solution.
- Cost: no component reuse across pages. There is one page. Revisit with an ADR if
  that stops being true.
