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

/** „300,04 / 300,00 EUR" — waluta raz, przy drugiej liczbie. `null`, gdy kwot nie znamy. */
function amounts(r: CascadeRung): string | null {
  const used = money(r.usedMinor, r.currency, r.exponent);
  const limit = money(r.limitMinor, r.currency, r.exponent);
  if (used && limit) return `${used.replace(/ \S+$/, "")} / ${limit}`;
  return used ?? null;
}

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
      const kwoty = amounts(r);
      // Kwoty wygrywaja ze stanem: gdy je znamy, szczebel pokazuje LICZBY, bez slowa
      // komentarza i bez skreslenia. Powod wycofania nalezy do twardego bloku — to on
      // jest szczeblem, ktory sie przez to zmienil.
      if (kwoty) return kwoty;
      if (r.state === "off") return "wyłączone";
      return pct(r.utilization) ?? "włączone";
    }
    case "hard_block": {
      const limit = money(r.limitMinor, r.currency, r.exponent);
      if (limit) return `przy ${limit}`;
      // Tu, a nie przy kredytach, mieszka informacja o wycofaniu: to wlasnie twardy blok
      // przesunal sie przez nie w gore. Sufitu organizacji nie ma w kontrakcie — nie ma dla
      // niego ani kwoty, ani procentu — wiec zdanie jest jedyna trescia, jaka mozemy podac.
      return r.reason ? "kredyty wyłączone przez organizację" : "po tygodniowym";
    }
  }
}

export function Cascade({ rungs }: { rungs: CascadeRung[] }) {
  return (
    <div className="cascade">
      {rungs.map((r) => (
        // `data-plain` zdejmuje skreslenie ze szczebla `off`, ktory mimo wszystko pokazuje
        // LICZBY. Skreslenie mowi „to juz nie dziala" i przy slowie „wyłączone" jest w
        // porzadku; przeciagniete przez kwoty czytaloby sie jak „te kwoty sa nieprawdziwe".
        <div key={r.key} className="rung" data-state={r.state}
             data-plain={r.state === "off" && amounts(r) ? "1" : undefined}>
          <span className="rung-label">{LABELS[r.key]}</span>
          <span className="rung-value">{valueOf(r)}</span>
          {r.isCurrent && <span className="rung-current" title="tu jesteś teraz" />}
        </div>
      ))}
    </div>
  );
}
