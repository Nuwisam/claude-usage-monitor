/** The content of the explainer next to the spend row — what the BAR cannot show.
 *
 *  Why this file exists: `spend` and `extra_usage` are two views of the same pool, so the
 *  backend suppresses the `extra:usage` row (`primary: false`, see services/status.py). Its
 *  data stays in the response all the same, and these are the only places where the UI sees
 *  WHO turned the credits off and whether the wall has fallen. Without this "?" that knowledge
 *  would disappear together with the row.
 *
 *  NOT in `lib/freshness.ts`: that file is the only place where the "state -> appearance"
 *  decision is made, and this is not freshness.
 *
 *  `extra` is the only schema-less contract field — read defensively, just like `spendNote`:
 *  a missing field and a field of the wrong type mean the same thing, "not known", and then
 *  the row simply does not come into being. Nothing is ever guessed.
 */
import type { SeriesStatus } from "../api/types";

export interface CreditsFact {
  term: string;
  value: string;
  /** The literal code from Anthropic — to be shown in a fixed-width font, untranslated
   *  (rule 5: the set is open, so what branches us is the field's existence, not its
   *  wording). */
  code?: string;
}

/** One sentence answering the question "whose limit is this". The series label says
 *  "(your pool)", but that shorthand does not explain how it differs from the organization
 *  ceiling — and it is exactly that difference which makes an exhausted ceiling GO DARK on
 *  this meter instead of topping it up. */
export const POOL_DEFINITION =
  "The pool allotted to you, not the ceiling of the whole organization. Anthropic does not " +
  "report the ceiling at all — when it runs out, this meter does not rise, it goes dark.";

/** Only a real bool means anything (like `_flag` in services/cascade.py). */
function flag(extra: Record<string, unknown> | null, key: string): boolean | null {
  const v = extra?.[key];
  return typeof v === "boolean" ? v : null;
}

/** Full precision, unlike `pct()` from lib/format.ts, which cuts to a single decimal place.
 *  Here the precision IS the content: the bar is drawn from a rounded `spend.percent` (93),
 *  while the measurement was 92.656 — and that is the whole difference between the two series. */
function exact(v: number): string {
  return String(Number(v.toFixed(3)));
}

/** Credit facts that the `spend` row does not carry on its own.
 *
 *  `eu` is sometimes ABSENT, and that is a correct state, not a failure: on an account that
 *  never had credits `extra_usage.utilization` is null forever, so the series does not enter
 *  `series[]` (the `ever_non_null` filter in services/status.py). What is left then is the
 *  definition alone and a possible withdrawal reason — from the ABSENCE of the series nothing
 *  is inferred, because missing data is not data (rule 4 in spirit). */
export function creditsFacts(spend: SeriesStatus, eu: SeriesStatus | undefined): CreditsFact[] {
  const out: CreditsFact[] = [];

  // THE WITHDRAWAL REASON FIRST, and NOT from `extra`: with a withdrawn meter the backend
  // swaps `extra` for the one from the last true measurement (`services/status.py`), so what
  // still lies there is the `disabled_reason: null` from the time when the gate was open. The
  // truth about the withdrawal sits in `unavailableReason` — a first-class field, not the
  // schema-less `extra`.
  //
  // Read from the SPEND ROW, not from `eu`: it therefore works on an account that has no `eu`
  // at all, and it is the only fact that gets through there.
  const withdrawn = spend.unavailableReason ?? eu?.unavailableReason ?? null;
  if (withdrawn !== null) {
    out.push({ term: "Disabled by", value: "the organization", code: withdrawn });
  }

  if (!eu) return out;
  const x = eu.extra;

  const measured = eu.utilization ?? eu.rawUtilization;
  if (measured !== null) {
    out.push({
      term: withdrawn ? "Last measured" : "Measured exactly",
      value: `${exact(measured)}%`,
    });
  }

  const enabled = flag(x, "is_enabled");
  if (enabled !== null) {
    out.push({ term: "Credits", value: enabled ? "on" : "off" });
  }

  // `true` only. The absence of a wall is not information, and a "not reached" row on every
  // healthy account would turn this panel into noise.
  if (flag(x, "spend_limit_reached") === true) {
    out.push({ term: "Spend limit", value: "reached" });
  }

  // Two different things that look identical in `enabled: false` alone: a switch flipped off
  // by YOU and a gate shut by the organization. The first one you can undo yourself.
  if (flag(x, "user_disabled") === true) {
    out.push({ term: "Disabled by", value: "you" });
  }
  // Only when `unavailableReason` did not already say so above — otherwise the same fact would
  // appear in the panel twice, once with the in-band code, once with the pre-withdrawal one.
  const reason = x?.disabled_reason;
  if (withdrawn === null && typeof reason === "string") {
    out.push({ term: "Disabled by", value: "the organization", code: reason });
  }

  if (flag(x, "credits_ever_enabled") === false) {
    out.push({ term: "Credits on this account", value: "have never been turned on" });
  }

  // Daily and weekly sub-limits inside the monthly one. In every observed payload they are
  // null — the row exists so that the day Anthropic fills them in needs no code change
  // (rule 5). The content is not interpreted, because its shape is unknown.
  const subLimits = [["daily", "Daily sub-limit"], ["weekly", "Weekly sub-limit"]] as const;
  for (const [key, term] of subLimits) {
    const v = x?.[key];
    if (v !== null && v !== undefined) out.push({ term, value: JSON.stringify(v) });
  }

  return out;
}
