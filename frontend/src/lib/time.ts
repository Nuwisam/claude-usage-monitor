/** Czas: kotwiczenie w `serverNow` i formaty z makiety.
 *
 *  Countdownow NIE liczymy z zegara przegladarki. Zegar klienta bywa rozjechany (backend
 *  ma na to osobny guard i zdarzenie `clock_skew`), a `resets_at` przychodzi z zegara
 *  Anthropic. Jedyna sensowna kotwica to `serverNow` z odpowiedzi — od niej liczymy, a
 *  lokalnie tylko TYKAMY.
 */

/** Backend v2 wysyla ISO-8601 z 'Z'. Gdyby kiedys wrocil bez strefy, dopinamy ja tutaj,
 *  bo `new Date("2026-07-26T19:07:37")` to w JS czas LOKALNY — cichy blad o cala strefe. */
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

/** Formaty UTC — celowo nie lokalne. Wszystkie granice okien podaje Anthropic w UTC,
 *  a mieszanie stref na jednym ekranie to najkrotsza droga do bledu w interpretacji. */
export function hm(d: Date | null): string {
  return d ? `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}` : "—";
}

export function hms(d: Date | null): string {
  return d ? `${hm(d)}:${p2(d.getUTCSeconds())}` : "—";
}

export function dm(d: Date | null): string {
  return d ? `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)}` : "—";
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
