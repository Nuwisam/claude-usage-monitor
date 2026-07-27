/** Czas: kotwiczenie w `serverNow` i formaty z makiety.
 *
 *  Countdownow nie liczymy z zegara przegladarki — bywa rozjechany, a `resets_at`
 *  przychodzi z zegara Anthropic. Kotwica to `serverNow`, lokalnie tylko tykamy. */

/** Bez strefy dopinamy 'Z': `new Date("2026-07-26T19:07:37")` to w JS czas LOKALNY. */
export function parseUtc(iso: string | null): Date | null {
  if (!iso) return null;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Przesuniecie zegara przegladarki wzgledem serwera, w ms. */
export function serverOffsetMs(serverNow: string, receivedAt = Date.now()): number {
  const s = parseUtc(serverNow);
  return s ? s.getTime() - receivedAt : 0;
}

/** "Teraz" widziane oczami serwera. */
export function serverClock(offsetMs: number): number {
  return Date.now() + offsetMs;
}

const p2 = (n: number) => String(n).padStart(2, "0");

/** Formaty w STREFIE PRZEGLADARKI — dane jada w UTC, ale „odczyt o 06:35" przy zegarze
 *  8:35 jest nieczytelny. Tam, gdzie widac surowe godziny, strefa jest podpisana
 *  (`tzLabel`); konwersje robi `Date`, wiec DST wychodzi samo. */
export function hm(d: Date | null): string {
  return d ? `${p2(d.getHours())}:${p2(d.getMinutes())}` : "—";
}

export function hms(d: Date | null): string {
  return d ? `${hm(d)}:${p2(d.getSeconds())}` : "—";
}

export function dm(d: Date | null): string {
  return d ? `${p2(d.getDate())}.${p2(d.getMonth() + 1)}` : "—";
}

/** "UTC+2" / "UTC+5:30" / "UTC" dla DANEJ chwili — przy zakresie 30 d konce moga wypasc
 *  po dwoch stronach zmiany czasu i jedna etykieta na oba klamalaby o godzine. */
export function tzLabel(d: Date = new Date()): string {
  const min = -d.getTimezoneOffset();
  if (min === 0) return "UTC";
  const abs = Math.abs(min);
  const rest = abs % 60;
  return `UTC${min < 0 ? "−" : "+"}${Math.floor(abs / 60)}${rest ? `:${p2(rest)}` : ""}`;
}

/** "2 d 4 h" / "3 h 05 min" / "12 min 34 s" / "po resecie" — dokladnie jak w makiecie. */
export function countdown(target: Date | null, nowMs: number): string {
  if (!target) return "bez resetu";
  const s = Math.round((target.getTime() - nowMs) / 1000);
  if (s <= 0) return "po resecie";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${p2(m)} min`;
  return `${m} min ${p2(s % 60)} s`;
}

/** "3 s temu" / "5 min temu" — wiek ostatniego odswiezenia w nagłowku. */
export function ago(sinceMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - sinceMs) / 1000));
  if (s < 60) return `${s} s temu`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min temu`;
  return `${Math.floor(m / 60)} h ${p2(m % 60)} min temu`;
}
