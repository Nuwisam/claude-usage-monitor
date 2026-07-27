import type { CascadeRung } from "../api/types";
import { money, pct } from "../lib/format";

/** Kaskada: 5 h -> tydzien -> kredyty -> twardy blok.
 *
 *  Backend liczy STAN kazdego szczebla i wskazuje ten, ktory ogranicza Cie teraz
 *  (services/cascade.py). Tutaj zapada tylko tresc — i to jest wlasciwy podzial,
 *  bo kwoty przychodza w jednostkach mniejszych i formatuje je warstwa prezentacji.
 */
const LABELS: Record<CascadeRung["key"], string> = {
  session: "Sesja 5 h",
  weekly: "Tydzień",
  credits: "Kredyty",
  hard_block: "Twardy blok",
};

function valueOf(r: CascadeRung): string {
  if (r.state === "unknown") return "nie wiem";

  switch (r.key) {
    case "session":
    case "weekly": {
      const p = pct(r.utilization);
      if (p === null) return "nie wiem";
      // 100% to nie "prawie koniec" — to koniec tego szczebla.
      return r.utilization !== null && r.utilization >= 100 ? `${p}% — koniec` : `${p}%`;
    }
    case "credits": {
      if (r.state === "off") return "wyłączone";
      const used = money(r.usedMinor, r.currency, r.exponent);
      const limit = money(r.limitMinor, r.currency, r.exponent);
      if (used && limit) return `${used.replace(/ \S+$/, "")} / ${limit}`;
      return used ?? pct(r.utilization) ?? "włączone";
    }
    case "hard_block": {
      const limit = money(r.limitMinor, r.currency, r.exponent);
      return limit ? `przy ${limit}` : "po tygodniowym";
    }
  }
}

export function Cascade({ rungs }: { rungs: CascadeRung[] }) {
  return (
    <div className="cascade">
      {rungs.map((r) => (
        <div key={r.key} className="rung" data-state={r.state}>
          <span className="rung-label">{LABELS[r.key]}</span>
          <span className="rung-value">{valueOf(r)}</span>
          {r.isCurrent && <span className="rung-current" title="tu jesteś teraz" />}
        </div>
      ))}
    </div>
  );
}
