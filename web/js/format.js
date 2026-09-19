/* Formatting only. Indian numbering throughout -- lakh and crore, not thousands
   and millions, because that is how the market these numbers come from reads. */

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const CRORE = 1e7;
export const LAKH = 1e5;

/** Rupees, scaled to crore or lakh so a 990 crore book does not print 10 digits. */
export function money(value) {
  if (value == null) return "—";
  const magnitude = Math.abs(value);
  if (magnitude >= CRORE) return `₹${inr2.format(value / CRORE)} cr`;
  if (magnitude >= LAKH) return `₹${inr2.format(value / LAKH)} L`;
  return `₹${inr.format(value)}`;
}

/** Signed rupees, for a P&L column where the direction is the point. */
export function signedMoney(value) {
  if (value == null) return "—";
  return (value > 0 ? "+" : "") + money(value);
}

export function pct(value, digits = 2) {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

export function bps(value, digits = 0) {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)} bp`;
}

export function qty(value) {
  return value == null ? "—" : inr.format(value);
}

export function num(value, digits = 2) {
  return value == null ? "—" : value.toFixed(digits);
}

export function day(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${d}-${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m - 1]}-${y}`;
}

export function sign(value) {
  if (value == null || value === 0) return "dim";
  return value > 0 ? "pos" : "neg";
}

/** Count a KPI up from zero. Twenty lines, and the cheapest thing that makes a
    dashboard feel alive. Skipped entirely under prefers-reduced-motion. */
export function countUp(el, target, render, ms = 700) {
  if (target == null || !Number.isFinite(target)) {
    el.textContent = render(target);
    return;
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    el.textContent = render(target);
    return;
  }

  const started = performance.now();
  const step = (now) => {
    const t = Math.min((now - started) / ms, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = render(target * eased);
    if (t < 1) requestAnimationFrame(step);
    else el.textContent = render(target);
  };
  requestAnimationFrame(step);
}
