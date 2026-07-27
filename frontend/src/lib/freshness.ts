/** Stan swiezosci -> wyglad. JEDYNE miejsce, w ktorym ta decyzja zapada.
 *
 *  Zasada 4 z AGENTS.md w postaci pikseli: `unknown` NIGDY nie jest zerem. Kazde
 *  wypelnienie toru da sie przeczytac jako wartosc, wiec przy `unknown` nie ma zadnego
 *  wypelnienia i w kolumnie procentow nie ma liczby — jest slowo "nie wiem".
 *
 *  Najgorszy tryb awarii tego narzedzia to falszywe, pewnie wygladajace zero: na jego
 *  podstawie odpalasz duze zadanie i trafiasz w sciane. Dlatego rozne stany musza wygladac
 *  ROZNIE, a nie tylko miec inny tooltip.
 */
import type { SeriesStatus } from "../api/types";
import { clampPct, pct } from "./format";
import { hm, hms, parseUtc } from "./time";

export interface SeriesView {
  /** Tor z tlem i wypelnieniem — tylko dla realnych pomiarow (live, stale). */
  measured: boolean;
  /** Przygaszone wypelnienie: odczyt sie starzeje, ale wartosc jest nadal prawdziwa. */
  dimmed: boolean;
  /** Kontur przerywany zamiast wypelnienia: ksztalt bez masy. */
  outline: boolean;
  /** Skos w konturze — dodatkowy sygnal, ze to nie brak zuzycia, a brak wiedzy. */
  hatch: boolean;
  /** Kikut przy zerze dla wywnioskowanego resetu. */
  stub: boolean;
  /** Kreska ostatniego POMIARU przy stanie unknown. */
  ghost: boolean;
  ghostPct: number;
  barPct: number;
  full: boolean;
  /** Liczba do kolumny procentow. null => zamiast liczby idzie `words`. */
  number: string | null;
  words: string | null;
  /** Podpis pod wierszem serii. */
  note: string;
  /** Podpis w naglowku hero (dokladniejszy czas). */
  heroNote: string;
}

export function describeSeries(s: SeriesStatus, extraNote?: string | null): SeriesView {
  const f = s.freshness;
  const captured = parseUtc(s.capturedAt);
  const suffix = extraNote ? ` · ${extraNote}` : "";
  const rawText = pct(s.rawUtilization);

  const base = {
    measured: f === "live" || f === "stale",
    dimmed: f === "stale",
    outline: f === "inferred_reset" || f === "unknown",
    hatch: f === "unknown",
    stub: f === "inferred_reset",
    ghost: f === "unknown" && s.rawUtilization !== null,
    ghostPct: clampPct(s.rawUtilization),
    barPct: f === "unknown" ? 0 : clampPct(s.utilization),
    full: s.utilization !== null && s.utilization >= 100,
  };

  switch (f) {
    case "live":
      return {
        ...base,
        number: pct(s.utilization),
        words: null,
        note: `odczyt o ${hm(captured)}${suffix}`,
        heroNote: `odczyt o ${hms(captured)}`,
      };
    case "stale":
      return {
        ...base,
        number: pct(s.utilization),
        words: null,
        note: `odczyt o ${hm(captured)} · okno trwa, wartość wciąż aktualna`,
        heroNote: `odczyt o ${hm(captured)}`,
      };
    case "inferred_reset":
      return {
        ...base,
        // Tylda jest tu istotna: to wynik wnioskowania, nie pomiar.
        number: "~0",
        words: null,
        note: "wyliczone ~0%, nie zmierzone",
        heroNote: "okno się zresetowało, klient milczał",
      };
    case "unknown":
      return {
        ...base,
        number: null,
        words: "nie wiem",
        note: rawText
          ? `ostatni odczyt ${rawText}% o ${hm(captured)} — teraz nie wiemy`
          : "brak danych dla tej serii — sprawdź klienta",
        heroNote: rawText
          ? `ostatni odczyt ${rawText}% o ${hm(captured)}`
          : "brak danych dla tej serii",
      };
  }
}

/** Doklejka do podpisu serii wydatkow: kwoty z jej wlasnego `extra`.
 *  Backend zostawia je w jednostkach mniejszych i nietypowane — czytamy defensywnie,
 *  bo to jedyne pole w kontrakcie bez schematu. */
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
    (x[0] / 10 ** x[2]).toFixed(x[2]).replace(".", ",");

  if (used && limit) return `kredyty ${fmt(used)} / ${fmt(limit)} ${limit[1] ?? ""}`.trim();
  if (s.extra.enabled === false) {
    return used ? `kredyty wyłączone · ${fmt(used)} ${used[1] ?? ""}`.trim() : "kredyty wyłączone";
  }
  return used ? `${fmt(used)} ${used[1] ?? ""}`.trim() : null;
}
