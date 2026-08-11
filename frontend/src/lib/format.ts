/** Number and amount formatting. Dot decimal separator, tabular figures everywhere. */
import { stamp } from "./time";

/** 31 -> "31", 30.5 -> "30.5". null stays null — what to show instead of a number is the
 *  component's decision, because in the `unknown` state the answer is a word, not a zero. */
export function pct(v: number | null): string | null {
  if (v === null || Number.isNaN(v)) return null;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** Amount from minor units: (3820, "USD", 2) -> "38.20 USD".
 *  The backend never flattens amounts to a float and neither do we on the way. */
export function money(
  minor: number | null,
  currency: string | null,
  exponent: number | null,
): string | null {
  if (minor === null) return null;
  const exp = exponent ?? 2;
  const value = minor / 10 ** exp;
  const text = value.toFixed(exp);
  return currency ? `${text} ${currency}` : text;
}

/** Above this threshold we may write "in the last hour" — the probe reports every <= 60 s. */
const HOURISH_MS = 45 * 60_000;

/** "+2 pp in the last hour" / "−1.5 pp since 14:03" / "±0 pp ..."
 *
 *  The span is in the string, because the baseline is clipped to the current window: "in the
 *  last hour" over a number covering five minutes understates the rate at which the limit
 *  burns. `from === null` => the hourly wording (older backend). */
export function delta(v: number | null, from: Date | null, nowMs: number): string {
  if (v === null) return "no data from the last hour";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  const n = `${sign}${pct(Math.abs(v)) ?? "0"} pp`;
  if (from === null || nowMs - from.getTime() >= HOURISH_MS) return `${n} in the last hour`;
  // `stamp`, not `hm`: the baseline will not go past 45 min, but across midnight "since 23:50"
  // would read like tonight.
  return `${n} since ${stamp(from, nowMs)}`;
}

export function severityLabel(s: string | null): string {
  return s ? `severity: ${s}` : "severity: not given";
}

/** The bar must not run off the track or dip below zero. */
export function clampPct(v: number | null): number {
  if (v === null || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(100, v));
}
