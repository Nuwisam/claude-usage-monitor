/** The /api contract — a 1:1 reflection of backend/app/schemas.py, described in docs/API.md. */

/** Must ship in the SAME COMMIT as the contract-version constant in services/status.py —
 *  Nav compares the two and on a mismatch lights a warning in the header. */
export const CONTRACT_VERSION = 3;

/** live — a measurement fresher than FRESH_WINDOW_SEC · stale — no confirmation for 5 min ·
 *  inferred_reset — inference, not measurement · unknown — no samples for the series, a failure.
 *
 *  This is a contract of DATA, not four drawings: the UI merges `live`/`stale`/`unknown` into
 *  one appearance, because all three carry the last true value and only the reading age tells
 *  them apart (`lib/freshness.ts`). Only `inferred_reset` gets a drawing of its own. */
export type Freshness = "live" | "stale" | "inferred_reset" | "unknown";

export type SeriesSource = "bucket" | "limit" | "extra_usage" | "spend";

export interface SeriesStatus {
  seriesId: number;
  seriesKey: string;
  label: string;
  source: SeriesSource;
  sortOrder: number;
  /** The series' semantics — it is here so the UI does not guess from the key's name. */
  kind: string | null;
  group: string | null;
  bucketKey: string | null;
  /** null when there is nothing to show — on `unknown`, or when the meter is WITHDRAWN
   *  (see `unavailableReason`). Never render this as 0. */
  utilization: number | null;
  /** The last MEASURED value, with no inference.
   *
   *  The UI's rule is `utilization ?? rawUtilization`, NEVER `utilization ?? 0`: on `unknown`
   *  we show the last measurement with its age, because the percentage is known and true. The
   *  words "unknown" stay only when both fields are null — there was never a measurement. */
  rawUtilization: number | null;
  /** The reason this series CANNOT be measured — verbatim from Anthropic
   *  (observed: `org_level_disabled_until`, `org_spend_cap_reached`). The set IS
   *  open, so branch on `!== null`, never on the contents (rule 5).
   *
   *  When it is set, `utilization` is null — this is not a 0% measurement, it is a missing
   *  meter. `rawUtilization` survives: it holds the last MEASURED percent, so the reading
   *  stays on screen with its age. It tells apart two states that look identical in the
   *  payload: an account that never had credits, and one whose organization has just cut
   *  them off. */
  unavailableReason: string | null;
  resetsAt: string | null;
  secondsToReset: number | null;
  /** The last SAMPLE written. Dedup skips unchanged ones, so it can be older than the measurement. */
  capturedAt: string | null;
  /** When this value was last CONFIRMED. Freshness is counted from this. */
  confirmedAt: string | null;
  /** Since when the value has been unchanged. */
  valueSince: string | null;
  freshness: Freshness;
  isActive: boolean | null;
  severity: string | null;
  deltaPct1h: number | null;
  /** Which SAMPLE deltaPct1h is counted from — the baseline is clipped to the current window,
   *  so the span is sometimes shorter than an hour. Null together with deltaPct1h. */
  deltaFrom: string | null;
  primary: boolean;
  duplicateOf: string | null;
  extra: Record<string, unknown> | null;
}

export type RungKey = "session" | "weekly" | "credits" | "hard_block";
export type RungState = "on" | "off" | "unknown";

export interface CascadeRung {
  key: RungKey;
  /** "off" (turned off) and "unknown" (we do not know) are TWO DIFFERENT things. */
  state: RungState;
  /** Why the rung is off — the same string as `SeriesStatus.unavailableReason`.
   *  `state` does NOT get a fourth value: withdrawn credits are still "off". */
  reason: string | null;
  isCurrent: boolean;
  utilization: number | null;
  seriesKey: string | null;
  /** Amounts in minor units with an exponent — the UI formats them, not the backend. */
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
  /** The anchor of every countdown. The browser clock is NOT the source of truth. */
  serverNow: string;
  accounts: AccountStatus[];
  warnings: string[];
}

/* --------------------------------------------------------------- SSE (/api/stream) */

/** Frames carry `AccountStatus` VERBATIM — the same model /api/status returns, so there is
 *  no second shape to keep in sync. Subscription is by `account.uuid` only; the email is
 *  not part of this contract, because one address can point at several accounts. */
export interface HelloFrame {
  contractVersion: number;
  serverNow: string;
  /** UUIDs found in the database. */
  subscribed: string[];
  /** UUIDs that do not exist (yet). Not an error — the account may appear later — but
   *  reported, so a typo never looks like idleness. */
  unknown: string[];
  pingSec: number;
  maxLifetimeSec: number;
}

export interface AccountFrame {
  contractVersion: number;
  serverNow: string;
  account: AccountStatus;
  warnings: string[];
}

export interface PingFrame {
  serverNow: string;
}

export interface HistoryPoint {
  t: string;
  min: number | null;
  max: number | null;
  avg: number | null;
  last: number | null;
  n: number;
}

/** client_silent — the client sent nothing; no_samples — the client reported, but with no samples
 *  for THIS series, i.e. a failure. Two shadings; merging them turns a failure into a plain break. */
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

/** The FastAPI error envelope. The backend returns TWO: `{detail:{reason}}` from HTTPException
 *  and a bare `{reason}` from the 500 handler. The client has to handle both. */
export interface ApiErrorBody {
  detail?: { reason?: string; redirect_url?: string | null } | string;
  reason?: string;
}
