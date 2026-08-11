/** Freshness state -> appearance. THE ONLY place where this decision is made.
 *
 *  FRESHNESS IS CARRIED BY THE READING-AGE LABEL, NOT BY THE TRACK DRAWING. `live`, `stale`
 *  and `unknown` look identical: a full track with the last TRUE value. How much that number
 *  is worth is told by the "confirmed on Wed. at 11:58 · 3 d 4 h ago" sitting next to it.
 *
 *  Rule 4 from AGENTS.md ("unknown is never zero") comes out of this STRONGER, not weaker:
 *  with no fresh measurement we show the last MEASURED value, not zero. The dashed track and
 *  the words `unknown` stay only where there has NEVER been a measurement — there an empty
 *  track would read as zero, and zero would be a lie.
 *
 *  The same model has run on the AX206 panel from the start (`panel/panel/view.py`) — this is
 *  a port of its decision to the web, not a third variant. */
import type { SeriesStatus } from "../api/types";
import { clampPct, pct } from "./format";
import { ago, atStamp, countdown, parseUtc, stamp } from "./time";

export interface SeriesView {
  /** Track with a background and a fill — for every real measurement, regardless of age. */
  measured: boolean;
  /** Dashed outline instead of a fill: shape without mass. */
  outline: boolean;
  /** Hatching in the outline — there has NEVER been a measurement, nothing to draw. */
  hatch: boolean;
  /** Stub at zero for an inferred reset. */
  stub: boolean;
  barPct: number;
  full: boolean;
  /** Number for the percent column. null => `words` goes in place of the number. */
  number: string | null;
  words: string | null;
  /** Caption under the series row. */
  note: string;
  /** Caption in the hero header (seconds on a fresh reading). */
  heroNote: string;
}

export function describeSeries(
  s: SeriesStatus,
  nowMs: number,
  extraNote?: string | null,
): SeriesView {
  // Time comes from the CONFIRMATION, not from the sample write: dedup writes no sample when
  // the value is unchanged, so `capturedAt` is sometimes hours older than the last measurement.
  const confirmed = parseUtc(s.confirmedAt ?? s.capturedAt);
  const stable = parseUtc(s.valueSince);
  const suffix = extraNote ? ` · ${extraNote}` : "";
  // A 2 min threshold, otherwise "unchanged since" would show on every row. After "since" the
  // preposition does not change, so a bare `stamp()` goes in.
  const held =
    stable && confirmed && confirmed.getTime() - stable.getTime() >= 120_000
      ? ` · unchanged since ${stamp(stable, nowMs)}`
      : "";

  // The value: the current measurement, and when the backend did not supply one (`unknown`
  // state) — the LAST MEASURED. `??`, not `||`: on `inferred_reset` utilization is 0.0 and stays.
  const value = s.utilization ?? s.rawUtilization;

  if (value === null) return missingView(suffix, s.unavailableReason);

  if (s.freshness === "inferred_reset") {
    return {
      measured: false,
      outline: true,
      hatch: false,
      stub: true,
      barPct: 0,
      full: false,
      // The tilde tells inference apart from measurement. Deliberately WITHOUT the stamp of the
      // last measurement: that one belongs to the PREVIOUS window, and `~0` speaks of this one.
      number: "~0",
      words: null,
      note: `inferred ~0%, not measured${suffix}`,
      heroNote: "window reset, client was silent",
    };
  }

  const age = confirmed ? ` · ${ago(confirmed.getTime(), nowMs)}` : "";
  // With a withdrawn meter the row looks EXACTLY like a normal one: number, amounts and the
  // "confirmed …" stamp of the last measurement. No separate withdrawal caption here: the
  // backend swaps the markers to the time of THAT measurement, so the caption tells the truth by
  // itself, the reading age does the rest, and the hard block in the cascade says the gate is shut.
  return {
    measured: true,
    outline: false,
    hatch: false,
    stub: false,
    barPct: clampPct(value),
    full: value >= 100,
    number: pct(value),
    words: null,
    note: `confirmed ${atStamp(confirmed, nowMs)}${age}${held}${suffix}`,
    heroNote: `confirmed ${atStamp(confirmed, nowMs, true)}${age}${held}`,
  };
}

/** No number for this series. An empty track would read as zero, so the track is dashed
 *  with hatching, and words stand in place of the number.
 *
 *  Two different reasons, the same DRAWING and two different words:
 *    - there has NEVER been a measurement — "unknown", the only place where the UI says so,
 *      and the only one where it really does not know;
 *    - the meter has been WITHDRAWN (`unavailableReason`) — then we know exactly why the
 *      number is missing, and "unknown" would admit to an ignorance that is not there.
 *      Anthropic reports `percent: 0` in this state; had we drawn it, we would be promising
 *      the whole free limit at the moment of a hard block.
 *
 *  We do not spell the reason out (rule 5): the set is open, so what branches us is its
 *  existence, not its wording. */
function missingView(suffix: string, reason?: string | null): SeriesView {
  return {
    measured: false,
    outline: true,
    hatch: true,
    stub: false,
    barPct: 0,
    full: false,
    number: null,
    words: reason ? "no meter" : "unknown",
    note: reason
      ? `meter withdrawn by the organization${suffix}`
      : `no measurement for this series${suffix}`,
    heroNote: reason ? "meter withdrawn by the organization" : "no measurement for this series",
  };
}

/** Countdown caption in the hero, in two parts: `at` goes into a span that a narrow layout
 *  hides, so `lead` has to stand on its own.
 *
 *  `at` ALREADY CARRIES THE PREPOSITION — because with a reset five days out the bare time
 *  lies, and the day changes the wording ("at 20:00", but "on Fri. at 20:00"). The caller has
 *  no right to know which variant came out, so it does not glue on an "at" of its own.
 *
 *  `resetsAt: null` has TWO reasons, and "no reset" merged them into a caption read as "this
 *  window does not reset": (a) Anthropic gives no boundary for a window at 0% usage, (b) the
 *  probe zeroes a stale boundary from the cache (`reset-in-progress`, up to ~5 min). */
export function resetNote(s: SeriesStatus, nowMs: number): { lead: string; at: string | null } {
  // `source` is a contract enum; a bucket name here would break rule 5.
  if (s.source === "spend" || s.source === "extra_usage") return { lead: "no reset", at: null };
  const r = parseUtc(s.resetsAt);
  if (r) {
    // Rounding threshold THE SAME as in countdown(), otherwise a 300 ms difference produces
    // the splice "reset in past reset".
    const secs = Math.round((r.getTime() - nowMs) / 1000);
    return secs > 0
      ? { lead: `reset in ${countdown(r, nowMs)}`, at: atStamp(r, nowMs) }
      : { lead: "reset has passed", at: atStamp(r, nowMs) };
  }
  if (s.utilization === 0) return { lead: "window has not started", at: null };
  return { lead: "reset time unknown", at: null };
}

/** Amounts from the spend series `extra`. The only schema-less contract field — read defensively. */
export function spendNote(s: SeriesStatus): string | null {
  if (s.source !== "spend" || !s.extra) return null;
  const m = (v: unknown): [number, string | null, number] | null => {
    if (typeof v !== "object" || v === null) return null;
    const o = v as Record<string, unknown>;
    if (typeof o.amount_minor !== "number") return null;
    return [
      o.amount_minor,
      typeof o.currency === "string" ? o.currency : null,
      typeof o.exponent === "number" ? o.exponent : 2,
    ];
  };
  const used = m(s.extra.used);
  const limit = m(s.extra.limit) ?? m(s.extra.cap);
  const fmt = (x: [number, string | null, number]) =>
    (x[0] / 10 ** x[2]).toFixed(x[2]);

  if (used && limit) return `credits ${fmt(used)} / ${fmt(limit)} ${limit[1] ?? ""}`.trim();
  if (s.extra.enabled === false) {
    return used ? `credits off · ${fmt(used)} ${used[1] ?? ""}`.trim() : "credits off";
  }
  return used ? `${fmt(used)} ${used[1] ?? ""}`.trim() : null;
}
