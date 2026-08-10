/** Time: anchoring on `serverNow` and the formats from the mockup.
 *
 *  Countdowns are not computed from the browser clock — it drifts, and `resets_at`
 *  comes from Anthropic's clock. The anchor is `serverNow`; locally we only tick. */

/** With no zone we append 'Z': `new Date("2026-07-26T19:07:37")` is LOCAL time in JS. */
export function parseUtc(iso: string | null): Date | null {
  if (!iso) return null;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Browser clock offset relative to the server, in ms. */
export function serverOffsetMs(serverNow: string, receivedAt = Date.now()): number {
  const s = parseUtc(serverNow);
  return s ? s.getTime() - receivedAt : 0;
}

/** "Now" seen through the server's eyes. */
export function serverClock(offsetMs: number): number {
  return Date.now() + offsetMs;
}

const p2 = (n: number) => String(n).padStart(2, "0");

/** Formats in the BROWSER'S ZONE — the data travels in UTC, but "read at 06:35" next to a
 *  clock showing 8:35 is unreadable. Wherever raw hours are visible, the zone is labelled
 *  (`tzLabel`); `Date` does the conversion, so DST falls out on its own. */
export function hm(d: Date | null): string {
  return d ? `${p2(d.getHours())}:${p2(d.getMinutes())}` : "—";
}

export function hms(d: Date | null): string {
  return d ? `${hm(d)}:${p2(d.getSeconds())}` : "—";
}

export function dm(d: Date | null): string {
  return d ? `${p2(d.getDate())}.${p2(d.getMonth() + 1)}` : "—";
}

/** Day abbreviations indexed by `getDay()` — 0 is SUNDAY. In Python `weekday()` counts from
 *  Monday, so the port to the panel MUST NOT copy this table in this order. */
const DAYS = ["Sun.", "Mon.", "Tue.", "Wed.", "Thu.", "Fri.", "Sat."];

/** Difference in CALENDAR DAYS, measured across local midnights.
 *
 *  Never `Math.round(delta_ms / 86_400_000)`: a day at a clock change has 23 or 25 h, and a
 *  pair of instants on two sides of midnight differ by a day no matter how many ms separate
 *  them. A person reads "yesterday at 23:50", not "26 hours ago". */
function dayDiff(d: Date, now: Date): number {
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((a - b) / 86_400_000);
}

/** Stamp of an instant read RELATIVE TO NOW — the only place where the decision "do we add
 *  the day" is made. Every time label standing next to a reading's age or next to a
 *  countdown goes through this or through `atStamp`; that way the day variant reaches
 *  everywhere at once, and not in ten places separately.
 *
 *    today, `precise`, < 1 h  ->  "11:58:07"
 *    today                    ->  "11:58"
 *    +/- 1 day                ->  "yesterday 23:50" / "tomorrow 20:00"
 *    +/- 2..6 days            ->  "Wed. 11:58"     (in that window the abbreviation is unambiguous)
 *    further                  ->  "26.07 11:58"    (7 days back is the same abbreviation again)
 *    further, other year      ->  "26.07.2025 11:58"
 *
 *  `now` comes from `nowMs`, never from `Date.now()` — countdowns anchor on `serverNow`
 *  and a drifting browser clock would flip the day label itself. */
export function stamp(d: Date | null, nowMs: number, precise = false): string {
  if (!d) return "—";
  const now = new Date(nowMs);
  const diff = dayDiff(d, now);
  if (diff === 0) {
    return precise && nowMs - d.getTime() < 3_600_000 ? hms(d) : hm(d);
  }
  if (diff === -1) return `yesterday ${hm(d)}`;
  if (diff === 1) return `tomorrow ${hm(d)}`;
  if (Math.abs(diff) <= 6) return `${DAYS[d.getDay()]} ${hm(d)}`;
  const year = d.getFullYear() === now.getFullYear() ? "" : `.${d.getFullYear()}`;
  return `${dm(d)}${year} ${hm(d)}`;
}

/** `stamp()` with a preposition. The preposition MUST be here, because the format is what
 *  picks it — "at 11:58", but "on Wed. at 11:58" — and the caller has no right to know
 *  which variant came out.
 *
 *    today         ->  "at 11:58"  /  "at 11:58:07"
 *    +/- 1 day     ->  "yesterday at 23:50" / "tomorrow at 20:00"
 *    +/- 2..6 days ->  "on Wed. at 11:58"
 *    further       ->  "26.07 at 11:58"  (a numeric date takes no preposition)
 */
export function atStamp(d: Date | null, nowMs: number, precise = false): string {
  if (!d) return "—";
  const now = new Date(nowMs);
  const diff = dayDiff(d, now);
  if (diff === 0) {
    return `at ${precise && nowMs - d.getTime() < 3_600_000 ? hms(d) : hm(d)}`;
  }
  if (diff === -1) return `yesterday at ${hm(d)}`;
  if (diff === 1) return `tomorrow at ${hm(d)}`;
  if (Math.abs(diff) <= 6) return `on ${DAYS[d.getDay()]} at ${hm(d)}`;
  const year = d.getFullYear() === now.getFullYear() ? "" : `.${d.getFullYear()}`;
  return `${dm(d)}${year} at ${hm(d)}`;
}

/** A range of two instants — for gaps in the history. `stamp()` does not fit here, because
 *  neither end is read relative to "now".
 *
 *  Spaces around the dash ONLY in the variant with dates: "26.07 21:57–27.07 17:49" reads as
 *  one blob. Twenty hours of client silence is the norm here, so the range regularly crosses
 *  midnight and bare hours look like time travel. */
export function stampRange(from: Date | null, to: Date | null): string {
  if (!from || !to) return "—";
  const sameDay = dayDiff(from, to) === 0;
  return sameDay
    ? `${hm(from)}–${hm(to)}`
    : `${dm(from)} ${hm(from)} – ${dm(to)} ${hm(to)}`;
}

/** "UTC+2" / "UTC+5:30" / "UTC" for a GIVEN instant — over a 30 d range the ends can fall on
 *  two sides of a clock change and one label for both would lie by an hour. */
export function tzLabel(d: Date = new Date()): string {
  const min = -d.getTimezoneOffset();
  if (min === 0) return "UTC";
  const abs = Math.abs(min);
  const rest = abs % 60;
  return `UTC${min < 0 ? "−" : "+"}${Math.floor(abs / 60)}${rest ? `:${p2(rest)}` : ""}`;
}

/** "2 d 4 h" / "3 h 05 min" / "12 min 34 s" / "past reset" — exactly as in the mockup. */
export function countdown(target: Date | null, nowMs: number): string {
  if (!target) return "no reset";
  const s = Math.round((target.getTime() - nowMs) / 1000);
  if (s <= 0) return "past reset";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${p2(m)} min`;
  return `${m} min ${p2(s % 60)} s`;
}

/** "3 s ago" / "5 min ago" / "1 h 25 min ago" / "3 d 4 h ago".
 *
 *  A day rung shaped like `countdown()`, because ever since freshness is carried by the label
 *  itself, three days of silence must read at once — "76 h 00 min ago" needs division in the
 *  head. The boundary exactly at 24 h gives "1 d 0 h ago"; `countdown()` prints "1 d 0 h" for
 *  the same input, so that is consistent, not overlooked. */
export function ago(sinceMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - sinceMs) / 1000));
  if (s < 60) return `${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${p2(m % 60)} min ago`;
  return `${Math.floor(h / 24)} d ${h % 24} h ago`;
}
