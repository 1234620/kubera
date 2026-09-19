# Frontend rules

A desk blotter that looks like a desk blotter. Dark, dense, fast.

## Start from a reference, not from a checklist

Before writing any CSS, find two or three named, award-winning references and
take concrete cues from them — palette, gradient construction, type scale,
spacing rhythm, motion character. State which references were used.

This rule exists because the first version of this dashboard satisfied every
other rule on this page and still looked generic: "has gradients" is not the
same as "looks designed". The palette now derives from the Robinhood identity by
Porto Rocha, with Linear, Stripe and Vercel for the dark-first structure. See
[`../docs/09-frontend.md`](../docs/09-frontend.md).

**One accent.** If the tokens file grows an `--accent-2`, that is the smell.

## Non-negotiables
- **No login page.** Ever. This is localhost.
- **No build step.** Plain HTML, CSS and ES modules. Chart.js from a CDN. If a
  React app ever becomes justified it gets its own ADR first.
- **Gradients and motion are required**, not decoration:
  - a moving mesh/linear gradient on the page background and on KPI cards
  - gradient-filled sparklines and area charts
  - `@keyframes` for card entry (staggered fade + translate), number count-up on
    KPI values, a shimmer skeleton while a fetch is in flight, and a pulse on a
    row when a margin call fires
  - `prefers-reduced-motion: reduce` disables all of it — that is an
    accessibility floor, not an optional extra
- **CSS custom properties** for the palette in `:root`, redefined for light mode.
  No hardcoded hex outside `:root`.

## Layout
One page, four bands:
1. KPI strip — book value on loan, net financing spread, weighted average fee,
   utilisation, open margin calls
2. Specialness heatmap — securities × series, colour = specialness score
3. Term structure chart — lending fee by series tenor, one line per selected symbol
4. Blotter table — positions with fee, borrow cost, FTP charge, net spread, DV01

## Data
Fetch from the FastAPI endpoints in `docs/08-api-spec.md`. Format money in the
Indian numbering system with `Intl.NumberFormat('en-IN')`. Never format a rate
without its unit suffix.

## Performance
Numbers come pre-aggregated from SQL. The browser formats and draws; it does not
compute analytics. If the frontend is doing arithmetic beyond a percentage, the
query is wrong.
