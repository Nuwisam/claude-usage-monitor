/** Formatowanie liczb i kwot. Polskie separatory, wszedzie tabelarycznie. */
import { hm } from "./time";

/** 31 -> "31", 30.5 -> "30,5". null zostaje nullem — o tym, co pokazac zamiast liczby,
 *  decyduje komponent, bo w stanie `unknown` odpowiedzia jest slowo, nie zero. */
export function pct(v: number | null): string | null {
  if (v === null || Number.isNaN(v)) return null;
  return (Number.isInteger(v) ? String(v) : v.toFixed(1)).replace(".", ",");
}

/** Kwota z jednostek mniejszych: (3820, "USD", 2) -> "38,20 USD".
 *  Backend nigdy nie splaszcza kwot do floata i my ich tez nie splaszczamy w drodze. */
export function money(
  minor: number | null,
  currency: string | null,
  exponent: number | null,
): string | null {
  if (minor === null) return null;
  const exp = exponent ?? 2;
  const value = minor / 10 ** exp;
  const text = value.toFixed(exp).replace(".", ",");
  return currency ? `${text} ${currency}` : text;
}

/** Powyzej tego progu wolno napisac „w ciągu godziny" — sonda melduje sie co <= 60 s. */
const HOURISH_MS = 45 * 60_000;

/** "+2 pp w ciągu godziny" / "−1,5 pp od 14:03" / "±0 pp ..."
 *
 *  Rozpietosc jest w napisie, bo baseline jest przyciety do biezacego okna: „w ciągu godziny"
 *  nad liczba z pieciu minut zaniza tempo palenia limitu. `from === null` => brzmienie
 *  godzinowe (starszy backend). */
export function delta(v: number | null, from: Date | null, nowMs: number): string {
  if (v === null) return "brak danych z ostatniej godziny";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  const n = `${sign}${pct(Math.abs(v)) ?? "0"} pp`;
  if (from === null || nowMs - from.getTime() >= HOURISH_MS) return `${n} w ciągu godziny`;
  return `${n} od ${hm(from)}`;
}

export function severityLabel(s: string | null): string {
  return s ? `severity: ${s}` : "severity: nie podana";
}

/** Pasek nie moze wyjechac za tor ani wjechac na minus. */
export function clampPct(v: number | null): number {
  if (v === null || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(100, v));
}
