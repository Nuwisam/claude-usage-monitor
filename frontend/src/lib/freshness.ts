/** Stan swiezosci -> wyglad. JEDYNE miejsce, w ktorym ta decyzja zapada.
 *
 *  Zasada 4 z AGENTS.md w pikselach: `unknown` nie ma zadnego wypelnienia toru ani liczby
 *  w kolumnie procentow, bo kazde wypelnienie da sie przeczytac jako wartosc. Stany musza
 *  roznic sie rysunkiem, nie tylko tooltipem. */
import type { SeriesStatus } from "../api/types";
import { clampPct, pct } from "./format";
import { countdown, hm, hms, parseUtc } from "./time";

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
  // Czas bierzemy z POTWIERDZENIA, nie z zapisu probki: dedup nie zapisuje probki przy
  // niezmienionej wartosci, wiec `capturedAt` bywa o godziny starsze niz ostatni pomiar.
  const confirmed = parseUtc(s.confirmedAt ?? s.capturedAt);
  const stable = parseUtc(s.valueSince);
  const suffix = extraNote ? ` · ${extraNote}` : "";
  const rawText = pct(s.rawUtilization);
  // Prog 2 min, inaczej „bez zmian od" wisialoby przy kazdym wierszu.
  const held =
    stable && confirmed && confirmed.getTime() - stable.getTime() >= 120_000
      ? ` · bez zmian od ${hm(stable)}`
      : "";

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
        note: `odczyt o ${hm(confirmed)}${held}${suffix}`,
        heroNote: `odczyt o ${hms(confirmed)}${held}`,
      };
    case "stale":
      // Brak potwierdzenia od 5 min — nie synonim awarii: hooki chodza tylko przy pracy,
      // wiec przerwa daje ten sam stan co martwy klient (awarie lapie `unknown`).
      // „moze byc juz wyzsza", bo w trwajacym oknie utilization tylko rosnie.
      return {
        ...base,
        number: pct(s.utilization),
        words: null,
        note: `ostatnie potwierdzenie o ${hm(confirmed)} · wartość może być już wyższa${suffix}`,
        heroNote: `ostatnie potwierdzenie o ${hm(confirmed)}`,
      };
    case "inferred_reset":
      return {
        ...base,
        // Tylda odroznia wnioskowanie od pomiaru.
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
          ? `ostatni odczyt ${rawText}% o ${hm(confirmed)} — teraz nie wiemy`
          : "brak danych dla tej serii — sprawdź klienta",
        heroNote: rawText
          ? `ostatni odczyt ${rawText}% o ${hm(confirmed)}`
          : "brak danych dla tej serii",
      };
  }
}

/** Podpis odliczania w hero, w dwoch czesciach: `at` idzie do spana, ktory waski uklad chowa,
 *  wiec `lead` musi bronic sie sam.
 *
 *  `resetsAt: null` ma DWA powody i „bez resetu" zlewalo je w napis czytany jak „to okno sie
 *  nie resetuje": (a) Anthropic nie podaje granicy dla okna z 0% zuzycia, (b) sonda zeruje
 *  przedawniona granice z cache (`reset-w-toku`, do ~5 min). */
export function resetNote(s: SeriesStatus, nowMs: number): { lead: string; at: string | null } {
  // `source` to enum kontraktu; nazwa bucketu byla by zlamaniem zasady 5.
  if (s.source === "spend" || s.source === "extra_usage") return { lead: "bez resetu", at: null };
  const r = parseUtc(s.resetsAt);
  if (r) {
    // Prog zaokraglenia TEN SAM co w countdown(), inaczej przy roznicy 300 ms wychodzi
    // sklejka „reset za po resecie".
    const secs = Math.round((r.getTime() - nowMs) / 1000);
    return secs > 0
      ? { lead: `reset za ${countdown(r, nowMs)}`, at: hm(r) }
      : { lead: "reset minął", at: hm(r) };
  }
  if (s.utilization === 0) return { lead: "okno nie wystartowało", at: null };
  return { lead: "czas resetu nieznany", at: null };
}

/** Kwoty z `extra` serii wydatkow. Jedyne pole kontraktu bez schematu — czytamy defensywnie. */
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
