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

/** Skroty dni indeksowane `getDay()` — 0 to NIEDZIELA. W Pythonie `weekday()` liczy od
 *  poniedzialku, wiec port do panelu NIE moze przepisac tej tablicy w tej kolejnosci. */
const DAYS = ["ndz.", "pon.", "wt.", "śr.", "czw.", "pt.", "sob."];

/** Roznica w DNIACH KALENDARZOWYCH, liczona po lokalnych polnocach.
 *
 *  Nigdy `Math.round(delta_ms / 86_400_000)`: doba przy zmianie czasu ma 23 albo 25 h,
 *  a para chwil po dwoch stronach polnocy rozni sie o dzien niezaleznie od tego, ile ms
 *  je dzieli. Czlowiek czyta „wczoraj o 23:50", nie „26 godzin temu". */
function dayDiff(d: Date, now: Date): number {
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((a - b) / 86_400_000);
}

/** Stempel chwili czytanej WZGLEDEM TERAZ — jedyne miejsce, w ktorym zapada decyzja
 *  „czy dopisac dzien". Kazdy podpis czasu stojacy obok wieku odczytu albo odliczania
 *  idzie przez to albo przez `atStamp`; dzieki temu wariant dobowy dochodzi wszedzie
 *  naraz, a nie w dziesieciu miejscach osobno.
 *
 *    dzis, `precise`, < 1 h  ->  "11:58:07"
 *    dzis                    ->  "11:58"
 *    +/- 1 dzien             ->  "wczoraj 23:50" / "jutro 20:00"
 *    +/- 2..6 dni            ->  "śr. 11:58"      (w tym oknie skrot jest jednoznaczny)
 *    dalej                   ->  "26.07 11:58"    (7 dni wstecz to znowu ten sam skrot)
 *    dalej, inny rok         ->  "26.07.2025 11:58"
 *
 *  `now` bierzemy z `nowMs`, nigdy z `Date.now()` — countdowny kotwicza sie na `serverNow`
 *  i rozjechany zegar przegladarki przewracalby sam podpis dnia. */
export function stamp(d: Date | null, nowMs: number, precise = false): string {
  if (!d) return "—";
  const now = new Date(nowMs);
  const diff = dayDiff(d, now);
  if (diff === 0) {
    return precise && nowMs - d.getTime() < 3_600_000 ? hms(d) : hm(d);
  }
  if (diff === -1) return `wczoraj ${hm(d)}`;
  if (diff === 1) return `jutro ${hm(d)}`;
  if (Math.abs(diff) <= 6) return `${DAYS[d.getDay()]} ${hm(d)}`;
  const year = d.getFullYear() === now.getFullYear() ? "" : `.${d.getFullYear()}`;
  return `${dm(d)}${year} ${hm(d)}`;
}

/** `stamp()` z przyimkiem. Przyimek MUSI byc tutaj, bo polszczyzna zmienia go razem
 *  z formatem — „o 11:58", ale „w śr. o 11:58" — a wolajacy nie ma prawa wiedziec, ktory
 *  wariant wyszedl.
 *
 *    dzis          ->  "o 11:58"  /  "o 11:58:07"
 *    +/- 1 dzien   ->  "wczoraj o 23:50" / "jutro o 20:00"
 *    +/- 2..6 dni  ->  "w śr. o 11:58"   ALE  "we wt. o 11:58"
 *    dalej         ->  "26.07 o 11:58"   („w 26.07" nie jest polszczyzna)
 */
export function atStamp(d: Date | null, nowMs: number, precise = false): string {
  if (!d) return "—";
  const now = new Date(nowMs);
  const diff = dayDiff(d, now);
  if (diff === 0) {
    return `o ${precise && nowMs - d.getTime() < 3_600_000 ? hms(d) : hm(d)}`;
  }
  if (diff === -1) return `wczoraj o ${hm(d)}`;
  if (diff === 1) return `jutro o ${hm(d)}`;
  if (Math.abs(diff) <= 6) {
    // „we wtorek", nie „w wtorek" — jedyny wyjatek i dlatego stoi tu, a nie w wolaniu.
    const prep = d.getDay() === 2 ? "we" : "w";
    return `${prep} ${DAYS[d.getDay()]} o ${hm(d)}`;
  }
  const year = d.getFullYear() === now.getFullYear() ? "" : `.${d.getFullYear()}`;
  return `${dm(d)}${year} o ${hm(d)}`;
}

/** Zakres dwoch chwil — dla dziur w historii. `stamp()` tu nie pasuje, bo zadnego z koncow
 *  nie czyta sie wzgledem „teraz".
 *
 *  Odstepy wokol pauzy TYLKO w wariancie z datami: „26.07 21:57–27.07 17:49" czyta sie jak
 *  jeden zlepek. Dwudziestogodzinna cisza klienta jest tu norma, wiec zakres regularnie
 *  przekracza polnoc i same godziny wygladaja jak podroz w czasie. */
export function stampRange(from: Date | null, to: Date | null): string {
  if (!from || !to) return "—";
  const sameDay = dayDiff(from, to) === 0;
  return sameDay
    ? `${hm(from)}–${hm(to)}`
    : `${dm(from)} ${hm(from)} – ${dm(to)} ${hm(to)}`;
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

/** "3 s temu" / "5 min temu" / "1 h 25 min temu" / "3 d 4 h temu".
 *
 *  Szczebel dobowy w ksztalcie `countdown()`, bo odkad swiezosc niesie sama etykieta,
 *  trzydniowa cisza musi czytac sie od razu — "76 h 00 min temu" wymaga dzielenia w glowie.
 *  Granica dokladnie na 24 h daje "1 d 0 h temu"; `countdown()` drukuje "1 d 0 h" dla tego
 *  samego wejscia, wiec to spojne, a nie przeoczone. */
export function ago(sinceMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - sinceMs) / 1000));
  if (s < 60) return `${s} s temu`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min temu`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${p2(m % 60)} min temu`;
  return `${Math.floor(h / 24)} d ${h % 24} h temu`;
}
