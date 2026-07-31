/** Stan swiezosci -> wyglad. JEDYNE miejsce, w ktorym ta decyzja zapada.
 *
 *  SWIEZOSC NIESIE ETYKIETA WIEKU ODCZYTU, NIE RYSUNEK TORU. `live`, `stale` i `unknown`
 *  wygladaja identycznie: pelny tor z ostatnia PRAWDZIWA wartoscia. O tym, ile ta liczba
 *  jest warta, mowi stojace obok „potwierdzone w śr. o 11:58 · 3 d 4 h temu".
 *
 *  Zasada 4 z AGENTS.md („unknown nigdy nie jest zerem") wychodzi z tego MOCNIEJSZA, nie
 *  slabsza: przy braku swiezego pomiaru pokazujemy ostatni ZMIERZONY, a nie zero. Kreskowany
 *  tor i slowa `nie wiem` zostaja wylacznie tam, gdzie pomiaru nie bylo NIGDY — tam pusty tor
 *  czytaloby sie jako zero, a zero byloby klamstwem.
 *
 *  Ten sam model chodzi na panelu AX206 od poczatku (`panel/panel/view.py`) — to jest port
 *  jego decyzji do WWW, nie trzeci wariant. */
import type { SeriesStatus } from "../api/types";
import { clampPct, pct } from "./format";
import { ago, atStamp, countdown, parseUtc, stamp } from "./time";

export interface SeriesView {
  /** Tor z tlem i wypelnieniem — dla kazdego realnego pomiaru, niezaleznie od wieku. */
  measured: boolean;
  /** Kontur przerywany zamiast wypelnienia: ksztalt bez masy. */
  outline: boolean;
  /** Skos w konturze — pomiaru nie bylo NIGDY, nie ma czego narysowac. */
  hatch: boolean;
  /** Kikut przy zerze dla wywnioskowanego resetu. */
  stub: boolean;
  barPct: number;
  full: boolean;
  /** Liczba do kolumny procentow. null => zamiast liczby idzie `words`. */
  number: string | null;
  words: string | null;
  /** Podpis pod wierszem serii. */
  note: string;
  /** Podpis w naglowku hero (sekundy przy swiezym odczycie). */
  heroNote: string;
}

export function describeSeries(
  s: SeriesStatus,
  nowMs: number,
  extraNote?: string | null,
): SeriesView {
  // Czas bierzemy z POTWIERDZENIA, nie z zapisu probki: dedup nie zapisuje probki przy
  // niezmienionej wartosci, wiec `capturedAt` bywa o godziny starsze niz ostatni pomiar.
  const confirmed = parseUtc(s.confirmedAt ?? s.capturedAt);
  const stable = parseUtc(s.valueSince);
  const suffix = extraNote ? ` · ${extraNote}` : "";
  // Prog 2 min, inaczej „bez zmian od" wisialoby przy kazdym wierszu. Po „od" przyimek sie
  // nie zmienia, wiec idzie goly `stamp()`.
  const held =
    stable && confirmed && confirmed.getTime() - stable.getTime() >= 120_000
      ? ` · bez zmian od ${stamp(stable, nowMs)}`
      : "";

  // Wartosc: pomiar biezacy, a gdy backend go nie podal (stan `unknown`) — OSTATNI ZMIERZONY.
  // `??`, nie `||`: przy `inferred_reset` utilization to 0.0 i ma nim zostac.
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
      // Tylda odroznia wnioskowanie od pomiaru. Swiadomie BEZ stempla ostatniego pomiaru:
      // tamten nalezy do POPRZEDNIEGO okna, a `~0` mowi o biezacym.
      number: "~0",
      words: null,
      note: `wyliczone ~0%, nie zmierzone${suffix}`,
      heroNote: "okno się zresetowało, klient milczał",
    };
  }

  const age = confirmed ? ` · ${ago(confirmed.getTime(), nowMs)}` : "";
  // Przy wycofanym mierniku wiersz wyglada TAK SAMO jak zwykly: liczba, kwoty i „potwierdzone
  // …" ze stemplem ostatniego pomiaru. Nie ma tu osobnego napisu o wycofaniu, bo backend
  // podmienia znaczniki na czas TAMTEGO pomiaru — podpis mowi wiec prawde sam z siebie,
  // a wiek odczytu robi reszte. O tym, ze bramy nie otworzysz, mowi twardy blok w kaskadzie.
  return {
    measured: true,
    outline: false,
    hatch: false,
    stub: false,
    barPct: clampPct(value),
    full: value >= 100,
    number: pct(value),
    words: null,
    note: `potwierdzone ${atStamp(confirmed, nowMs)}${age}${held}${suffix}`,
    heroNote: `potwierdzone ${atStamp(confirmed, nowMs, true)}${age}${held}`,
  };
}

/** Brak liczby dla tej serii. Pusty tor czytaloby sie jako zero, wiec tor jest kreskowany
 *  ze skosem, a zamiast liczby stoja slowa.
 *
 *  Dwa rozne powody, ten sam RYSUNEK i dwa rozne slowa:
 *    - pomiaru nie bylo NIGDY — „nie wiem", jedyne miejsce, w ktorym UI tak mowi, i jedyne,
 *      w ktorym naprawde nie wie;
 *    - miernik zostal WYCOFANY (`unavailableReason`) — wtedy wiemy dokladnie, dlaczego
 *      liczby nie ma, i „nie wiem" byloby przyznaniem sie do niewiedzy, ktorej nie ma.
 *      Anthropic w tym stanie podaje `percent: 0`; gdybysmy je narysowali, obiecalibysmy
 *      caly wolny limit w chwili twardej blokady.
 *
 *  Tresci powodu nie tlumaczymy (zasada 5): zbior jest otwarty, wiec rozgałezia nas samo
 *  jego istnienie, a nie jego brzmienie. */
function missingView(suffix: string, reason?: string | null): SeriesView {
  return {
    measured: false,
    outline: true,
    hatch: true,
    stub: false,
    barPct: 0,
    full: false,
    number: null,
    words: reason ? "bez licznika" : "nie wiem",
    note: reason
      ? `licznik wycofany przez organizację${suffix}`
      : `brak pomiaru dla tej serii${suffix}`,
    heroNote: reason ? "licznik wycofany przez organizację" : "brak pomiaru dla tej serii",
  };
}

/** Podpis odliczania w hero, w dwoch czesciach: `at` idzie do spana, ktory waski uklad chowa,
 *  wiec `lead` musi bronic sie sam.
 *
 *  `at` NIESIE JUZ PRZYIMEK — bo przy resecie za piec dni sama godzina klamie, a dzien zmienia
 *  polszczyzne („o 20:00", ale „w pt. o 20:00"). Wolajacy nie ma prawa wiedziec, ktory wariant
 *  wyszedl, wiec nie dokleja wlasnego „o".
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
      ? { lead: `reset za ${countdown(r, nowMs)}`, at: atStamp(r, nowMs) }
      : { lead: "reset minął", at: atStamp(r, nowMs) };
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
