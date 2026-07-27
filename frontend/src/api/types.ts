/** Kontrakt /api — odbicie 1:1 backend/app/schemas.py, opisane w docs/UI-HANDOUT.md. */

/** Musi isc w tym samym commicie co `CONTRACT_VERSION` w services/status.py — Nav
 *  porownuje obie i przy rozjezdzie zapala ostrzezenie w naglowku. */
export const CONTRACT_VERSION = 3;

/** live — pomiar swiezszy niz FRESH_WINDOW_SEC · stale — brak potwierdzenia od 5 min ·
 *  inferred_reset — wnioskowanie, nie pomiar · unknown — brak probek serii, awaria. */
export type Freshness = "live" | "stale" | "inferred_reset" | "unknown";

export type SeriesSource = "bucket" | "limit" | "extra_usage" | "spend";

export interface SeriesStatus {
  seriesId: number;
  seriesKey: string;
  label: string;
  source: SeriesSource;
  sortOrder: number;
  /** Semantyka serii — po to jest, zeby UI nie zgadywalo po nazwie klucza. */
  kind: string | null;
  group: string | null;
  bucketKey: string | null;
  /** null WYLACZNIE przy freshness === "unknown". Nigdy nie renderuj tego jako 0. */
  utilization: number | null;
  /** Ostatnia ZMIERZONA wartosc, bez wnioskowania. Zrodlo kreski-ducha przy unknown. */
  rawUtilization: number | null;
  resetsAt: string | null;
  secondsToReset: number | null;
  /** Ostatnia zapisana PROBKA. Dedup pomija niezmienione, wiec bywa starsze niz pomiar. */
  capturedAt: string | null;
  /** Kiedy ostatnio POTWIERDZONO te wartosc. Z tego liczy sie swiezosc. */
  confirmedAt: string | null;
  /** Odkad wartosc jest niezmienna. */
  valueSince: string | null;
  freshness: Freshness;
  isActive: boolean | null;
  severity: string | null;
  deltaPct1h: number | null;
  /** Od ktorej PROBKI liczy sie deltaPct1h — baseline jest przyciety do biezacego okna,
   *  wiec rozpietosc bywa krotsza niz godzina. Null razem z deltaPct1h. */
  deltaFrom: string | null;
  primary: boolean;
  duplicateOf: string | null;
  extra: Record<string, unknown> | null;
}

export type RungKey = "session" | "weekly" | "credits" | "hard_block";
export type RungState = "on" | "off" | "unknown";

export interface CascadeRung {
  key: RungKey;
  /** "off" (wylaczone) i "unknown" (nie wiemy) to DWIE ROZNE rzeczy. */
  state: RungState;
  isCurrent: boolean;
  utilization: number | null;
  seriesKey: string | null;
  /** Kwoty w jednostkach mniejszych z wykladnikiem — formatuje je UI, nie backend. */
  usedMinor: number | null;
  limitMinor: number | null;
  currency: string | null;
  exponent: number | null;
}

export interface AccountStatus {
  uuid: string;
  label: string | null;
  email: string | null;
  displayName: string | null;
  color: string | null;
  orgType: string | null;
  seatTier: string | null;
  rateLimitTier: string | null;
  subscriptionType: string | null;
  isEnabled: boolean;
  lastSampleAt: string | null;
  lastBatchAt: string | null;
  lastClientHost: string | null;
  cascade: CascadeRung[];
  series: SeriesStatus[];
}

export interface StatusResponse {
  contractVersion: number;
  /** Kotwica wszystkich countdownow. Zegar przegladarki NIE jest zrodlem prawdy. */
  serverNow: string;
  accounts: AccountStatus[];
  warnings: string[];
}

export interface HistoryPoint {
  t: string;
  min: number | null;
  max: number | null;
  avg: number | null;
  last: number | null;
  n: number;
}

/** client_silent — nie pracowales; no_samples — klient raportowal, ale bez probek TEJ
 *  serii, czyli awaria. Dwa cieniowania; zlanie ich robi z awarii zwykla przerwe. */
export type GapKind = "client_silent" | "no_samples";

export interface HistoryGap {
  from: string;
  to: string;
  kind: GapKind;
}

export interface HistoryResponse {
  bucket: string;
  points: HistoryPoint[];
  resets: string[];
  gaps: HistoryGap[];
}

/** Koperta bledu z FastAPI. Backend zwraca DWIE: `{detail:{reason}}` z HTTPException
 *  i gole `{reason}` z handlera 500. Klient musi obsluzyc obie. */
export interface ApiErrorBody {
  detail?: { reason?: string; redirect_url?: string | null } | string;
  reason?: string;
}
