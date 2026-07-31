/** Tresc wyjasnienia przy wierszu wydatkow — to, czego PASEK pokazac nie umie.
 *
 *  Powod istnienia tego pliku: `spend` i `extra_usage` to dwa widoki tej samej puli, wiec
 *  backend gasi wiersz `extra:usage` (`primary: false`, patrz services/status.py). Jego dane
 *  jednak zostaja w odpowiedzi i to sa jedyne miejsca, w ktorych UI widzi, KTO kredyty
 *  wylaczyl i czy sciana juz padla. Bez tego „?" ta wiedza zniknelaby razem z wierszem.
 *
 *  NIE w `lib/freshness.ts`: tamten plik jest jedynym miejscem decyzji „stan -> wyglad",
 *  a to nie jest swiezosc.
 *
 *  `extra` to jedyne pole kontraktu BEZ SCHEMATU — czytamy je defensywnie, tak jak
 *  `spendNote`: brak pola i pole zlego typu znacza to samo, czyli „nie wiem", i wtedy
 *  wiersz po prostu nie powstaje. Nigdy nie zgadujemy.
 */
import type { SeriesStatus } from "../api/types";

export interface CreditsFact {
  term: string;
  value: string;
  /** Doslowny kod od Anthropic — do wyswietlenia czcionka o stalej szerokosci, bez
   *  tlumaczenia (zasada 5: zbior jest otwarty, wiec rozgalezamy sie na ISTNIENIE pola,
   *  a nie na jego brzmienie). */
  code?: string;
}

/** Jedno zdanie, ktore odpowiada na pytanie „czyj to limit". Etykieta serii mowi
 *  „(Twoja pula)", ale skrot nie tlumaczy, czym rozni sie od sufitu organizacji — a to
 *  wlasnie ta roznica sprawia, ze wyczerpanie sufitu GASI ten licznik zamiast go dopelnic. */
export const POOL_DEFINITION =
  "Pula przydzielona Tobie, nie sufit całej organizacji. Sufitu Anthropic w ogóle nie " +
  "raportuje — gdy się wyczerpie, ten licznik nie rośnie, tylko gaśnie.";

/** Tylko prawdziwy bool cokolwiek znaczy (jak `_flag` w services/cascade.py). */
function flag(extra: Record<string, unknown> | null, key: string): boolean | null {
  const v = extra?.[key];
  return typeof v === "boolean" ? v : null;
}

/** Pelna precyzja, w odroznieniu od `pct()` z lib/format.ts, ktore obcina do jednego
 *  miejsca. Tutaj precyzja JEST trescia: pasek stoi na zaokraglonym `spend.percent`
 *  (93), a zmierzone bylo 92,656 — i to jest cala roznica miedzy tymi dwoma seriami. */
function exact(v: number): string {
  return String(Number(v.toFixed(3))).replace(".", ",");
}

/** Fakty o kredytach, ktorych wiersz `spend` nie niesie sam.
 *
 *  `eu` bywa NIEOBECNE i to jest poprawny stan, nie awaria: na koncie, ktore kredytow nigdy
 *  nie mialo, `extra_usage.utilization` jest null na zawsze, wiec seria nie wchodzi do
 *  `series[]` (filtr `ever_non_null` w services/status.py). Zostaje wtedy sama definicja
 *  i ewentualny powod wycofania — z NIEOBECNOSCI serii nie wnioskujemy nic, bo brak danych
 *  nie jest danymi (zasada 4 w duchu). */
export function creditsFacts(spend: SeriesStatus, eu: SeriesStatus | undefined): CreditsFact[] {
  const out: CreditsFact[] = [];

  // NAJPIERW powod wycofania, i to NIE z `extra`: przy wycofanym mierniku backend podmienia
  // `extra` na to z ostatniego prawdziwego pomiaru (`services/status.py`), wiec lezy tam
  // jeszcze `disabled_reason: null` z czasow, gdy brama byla otwarta. Prawda o wycofaniu
  // siedzi w `unavailableReason` — polu pierwszej kategorii, a nie w bezschematowym `extra`.
  //
  // Czytamy je z WIERSZA WYDATKOW, nie z `eu`: dziala wiec takze na koncie, na ktorym `eu`
  // w ogole nie ma, i jest to jedyny fakt, ktory tam dojezdza.
  const withdrawn = spend.unavailableReason ?? eu?.unavailableReason ?? null;
  if (withdrawn !== null) {
    out.push({ term: "Wyłączone przez", value: "organizację", code: withdrawn });
  }

  if (!eu) return out;
  const x = eu.extra;

  const measured = eu.utilization ?? eu.rawUtilization;
  if (measured !== null) {
    out.push({
      term: withdrawn ? "Ostatnio zmierzone" : "Zmierzone dokładnie",
      value: `${exact(measured)}%`,
    });
  }

  const enabled = flag(x, "is_enabled");
  if (enabled !== null) {
    out.push({ term: "Kredyty", value: enabled ? "włączone" : "wyłączone" });
  }

  // Tylko `true`. Brak sciany nie jest informacja, a wiersz „nieosiągnięty" przy kazdym
  // zdrowym koncie robilby z tego panelu szum.
  if (flag(x, "spend_limit_reached") === true) {
    out.push({ term: "Limit wydatków", value: "osiągnięty" });
  }

  // Dwie rozne rzeczy, ktore w samym `enabled: false` wygladaja identycznie: przelacznik
  // zdjety przez CIEBIE i brama zamknieta przez organizacje. Pierwsze cofniesz sam.
  if (flag(x, "user_disabled") === true) {
    out.push({ term: "Wyłączone przez", value: "Ciebie" });
  }
  // Tylko gdy `unavailableReason` nie powiedzialo tego wyzej — inaczej ten sam fakt stalby
  // w panelu dwa razy, raz z kodem z pasma, raz z kodem sprzed wycofania.
  const reason = x?.disabled_reason;
  if (withdrawn === null && typeof reason === "string") {
    out.push({ term: "Wyłączone przez", value: "organizację", code: reason });
  }

  if (flag(x, "credits_ever_enabled") === false) {
    out.push({ term: "Kredyty na tym koncie", value: "nigdy nie były włączone" });
  }

  // Podlimity dobowy i tygodniowy wewnatrz miesiecznego. W kazdym zaobserwowanym payloadzie
  // sa null — wiersz istnieje po to, zeby dzien, w ktorym Anthropic je zapelni, nie wymagal
  // zmiany kodu (zasada 5). Tresci nie interpretujemy, bo nie znamy jej ksztaltu.
  const subLimits = [["daily", "Podlimit dobowy"], ["weekly", "Podlimit tygodniowy"]] as const;
  for (const [key, term] of subLimits) {
    const v = x?.[key];
    if (v !== null && v !== undefined) out.push({ term, value: JSON.stringify(v) });
  }

  return out;
}
