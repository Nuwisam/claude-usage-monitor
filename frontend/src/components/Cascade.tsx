import type { CascadeRung } from "../api/types";
import { money, pct } from "../lib/format";

/** Cascade: 5 h -> week -> credits -> hard block.
 *
 *  The backend computes the STATE of every rung and points at the one limiting you now
 *  (services/cascade.py). Only the wording is decided here — and that is the right split,
 *  because amounts arrive in minor units and the presentation layer formats them.
 */
const LABELS: Record<CascadeRung["key"], string> = {
  session: "Session (5 h)",
  weekly: "Week",
  credits: "Credits",
  hard_block: "Hard block",
};

/** "300.04 / 300.00 EUR" — currency once, next to the second number. `null` when unknown. */
function amounts(r: CascadeRung): string | null {
  const used = money(r.usedMinor, r.currency, r.exponent);
  const limit = money(r.limitMinor, r.currency, r.exponent);
  if (used && limit) return `${used.replace(/ \S+$/, "")} / ${limit}`;
  return used ?? null;
}

function valueOf(r: CascadeRung): string {
  if (r.state === "unknown") return "unknown";

  switch (r.key) {
    case "session":
    case "weekly": {
      const p = pct(r.utilization);
      if (p === null) return "unknown";
      // 100% is not "almost there" — it is the end of this rung.
      return r.utilization !== null && r.utilization >= 100 ? `${p}% — exhausted` : `${p}%`;
    }
    case "credits": {
      const amountsForRung = amounts(r);
      // Amounts beat the state: when we know them, the rung shows NUMBERS, without a word
      // of commentary and without a strikethrough. The withdrawal reason belongs to the
      // hard block — that is the rung which changed because of it.
      if (amountsForRung) return amountsForRung;
      if (r.state === "off") return "off";
      return pct(r.utilization) ?? "on";
    }
    case "hard_block": {
      const limit = money(r.limitMinor, r.currency, r.exponent);
      if (limit) return `at ${limit}`;
      // The withdrawal fact lives here, not at the credits: it is the hard block that moved
      // up because of it. The organization ceiling is not in the contract — there is neither
      // an amount nor a percent for it — so a sentence is the only content we can give.
      return r.reason ? "credits disabled by the organization" : "after the weekly limit";
    }
  }
}

export function Cascade({ rungs }: { rungs: CascadeRung[] }) {
  return (
    <div className="cascade">
      {rungs.map((r) => (
        // `data-plain` takes the strikethrough off an `off` rung that nevertheless shows
        // NUMBERS. A strikethrough says "this no longer works" and next to the word "off" it
        // is fine; dragged through amounts it would read as "these amounts are untrue".
        <div key={r.key} className="rung" data-state={r.state}
             data-plain={r.state === "off" && amounts(r) ? "1" : undefined}>
          <span className="rung-label">{LABELS[r.key]}</span>
          <span className="rung-value">{valueOf(r)}</span>
          {r.isCurrent && <span className="rung-current" title="you are here now" />}
        </div>
      ))}
    </div>
  );
}
