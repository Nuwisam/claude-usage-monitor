/** Data from mockup v2 on contract v3. Mode `VITE_MOCKS=1`.
 *
 *  Reaching `unknown`, `inferred_reset`, 100% and an account with no series takes a live-system
 *  failure, and those are exactly the states that must look as the state catalog says (mockup 2d).
 *
 *  A reading age counted in DAYS is likewise viewable only here: live it takes three days of
 *  waiting, and every stamp carrying a weekday depends on it.
 *
 *  Variants via `?mock=`:
 *    base   (default) — two accounts 1:1 from the mockup, for a pixel-by-pixel comparison
 *    three           — three full accounts; a test of layout, not of states
 *    states          — additionally inferred_reset, an account with no series, a broken duplicate,
 *                      an age in days, a reset a few days out and a series never measured
 *    reset           — three window captions right after a session reset
 *    credits         — credits withdrawn by the organization next to an exhausted own pool;
 *                      the two states look identical in the payload apart from `disabled_reason`
 *
 *  `reset` is a separate variant because these states are visible ONLY in the hero, and the hero
 *  takes only `kind: "session"` series — adding them to `states` would show nothing.
 */
import type {
  AccountStatus,
  CascadeRung,
  Freshness,
  SeriesSource,
  SeriesStatus,
  StatusResponse,
} from "../api/types";

const MIN = 60_000;
const now = () => Date.now();
const at = (offsetMin: number) => new Date(now() + offsetMin * MIN).toISOString();

interface Spec {
  key: string;
  label: string;
  source: SeriesSource;
  sort: number;
  kind?: string | null;
  group?: string | null;
  bucket?: string | null;
  u: number | null;
  raw?: number | null;
  resetMin?: number | null;
  capturedMin: number;
  /** Minutes back for the confirmation. Different from `capturedMin` = a stable reading. */
  confirmedMin?: number;
  /** Since when the value has been unchanged. Defaults to capturedMin. */
  sinceMin?: number;
  fresh: Freshness;
  active?: boolean | null;
  severity?: string | null;
  delta?: number | null;
  /** Minutes back for the delta's baseline sample. Absent => `deltaFrom: null`, hourly wording. */
  deltaFromMin?: number;
  primary?: boolean;
  dupOf?: string | null;
  extra?: Record<string, unknown> | null;
  /** Reason the meter was withdrawn. Set => `u` is null; `raw` keeps the last measured
   *  percent when there is one. */
  reason?: string | null;
}

function series(id: number, s: Spec): SeriesStatus {
  const raw = s.raw === undefined ? s.u : s.raw;
  return {
    seriesId: id,
    seriesKey: s.key,
    label: s.label,
    source: s.source,
    sortOrder: s.sort,
    kind: s.kind ?? null,
    group: s.group ?? null,
    bucketKey: s.bucket ?? null,
    utilization: s.u,
    rawUtilization: raw,
    unavailableReason: s.reason ?? null,
    resetsAt: s.resetMin === null || s.resetMin === undefined ? null : at(s.resetMin),
    secondsToReset:
      s.resetMin === null || s.resetMin === undefined ? null : Math.round(s.resetMin * 60),
    capturedAt: at(s.capturedMin),
    confirmedAt: at(s.confirmedMin ?? s.capturedMin),
    valueSince: at(s.sinceMin ?? s.capturedMin),
    freshness: s.fresh,
    isActive: s.active ?? null,
    severity: s.severity ?? null,
    deltaPct1h: s.delta ?? null,
    deltaFrom: s.deltaFromMin === undefined ? null : at(s.deltaFromMin),
    primary: s.primary ?? true,
    duplicateOf: s.dupOf ?? null,
    extra: s.extra ?? null,
  };
}

const SESSION_KEY = "limit:session|session|-|-";
const WEEKLY_KEY = "limit:weekly_all|weekly|-|-";

function rung(r: Partial<CascadeRung> & { key: CascadeRung["key"]; state: CascadeRung["state"] }): CascadeRung {
  return {
    isCurrent: false,
    reason: null,
    utilization: null,
    seriesKey: null,
    usedMinor: null,
    limitMinor: null,
    currency: null,
    exponent: null,
    ...r,
  };
}

// ─── Max account: session 31%, week 30% and limiting, credits disabled ───────
function accountMax(): AccountStatus {
  return {
    uuid: "00000000-0000-4000-8000-000000000003",
    label: null,
    email: "you@example.org",
    displayName: "Tomasz",
    color: null,
    orgType: "claude_max",
    seatTier: null,
    rateLimitTier: "default_claude_max_5x",
    subscriptionType: "max",
    isEnabled: true,
    lastSampleAt: at(-0.3),
    lastBatchAt: at(-0.05),
    lastClientHost: "desktop",
    cascade: [
      rung({ key: "session", state: "on", utilization: 31, seriesKey: SESSION_KEY }),
      rung({ key: "weekly", state: "on", utilization: 30, seriesKey: WEEKLY_KEY, isCurrent: true }),
      rung({ key: "credits", state: "off", seriesKey: "spend:org", usedMinor: 0, currency: "USD", exponent: 2 }),
      rung({ key: "hard_block", state: "on" }),
    ],
    series: [
      series(1, { key: SESSION_KEY, label: "Session", source: "limit", sort: 15, kind: "session",
        group: "session", u: 31, resetMin: 52, capturedMin: -0.3, fresh: "live",
        active: false, severity: "normal", delta: 2 }),
      series(2, { key: "bucket:five_hour", label: "Session (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 31, resetMin: 52, capturedMin: -0.3, fresh: "live",
        delta: 2, primary: false, dupOf: SESSION_KEY }),
      // Sample from 47 min ago (dedup), confirmation from 18 s ago — the flagship v3 state.
      series(3, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -47,
        confirmedMin: -0.3, sinceMin: -47,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
      series(4, { key: "bucket:seven_day", label: "Week (all models)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 30, resetMin: 8453, capturedMin: -47,
        confirmedMin: -0.3, sinceMin: -47, fresh: "live",
        delta: 1, primary: false, dupOf: WEEKLY_KEY }),
      // Edge case: credits disabled, so the 0 has been unchanged for four hours.
      //
      // DELIBERATELY WITHOUT `extra:usage`: on an account that never had credits this series
      // has `utilization` null forever and does not enter `series[]` (the `ever_non_null`
      // filter in services/status.py). This is the "?" DEGRADATION case — the panel then shows
      // the pool definition alone, with no flag block, and it has to be visible in the mockup.
      series(5, { key: "spend:org", label: "Spend limit (your pool)", source: "spend",
        sort: 30,
        u: 0, resetMin: null, capturedMin: -240, confirmedMin: -0.3, sinceMin: -240,
        fresh: "live", severity: "normal", delta: 0,
        extra: { enabled: false, used: { amount_minor: 0, currency: "USD", exponent: 2 } } }),
      series(6, { key: "limit:weekly_scoped|weekly|fable|-", label: "Week — Fable",
        source: "limit", sort: 200, kind: "weekly_scoped", group: "weekly", u: 0, resetMin: null,
        capturedMin: -85, fresh: "stale", active: false, severity: "normal" }),
    ],
  };
}

// ─── Team account: week exhausted, work runs on credits, Fable in unknown ────
function accountTeam(): AccountStatus {
  return {
    uuid: "aaaabbbb-0000-1111-2222-333344445555",
    label: null,
    email: "second@example.org",
    displayName: "Second",
    color: null,
    orgType: "claude_team",
    seatTier: "standard",
    rateLimitTier: "default_claude_team_standard",
    subscriptionType: "team",
    isEnabled: true,
    lastSampleAt: at(-0.9),
    lastBatchAt: at(-0.8),
    lastClientHost: "laptop",
    cascade: [
      rung({ key: "session", state: "on", utilization: 12, seriesKey: SESSION_KEY }),
      rung({ key: "weekly", state: "on", utilization: 100, seriesKey: WEEKLY_KEY }),
      rung({ key: "credits", state: "on", utilization: 41, seriesKey: "spend:org",
        usedMinor: 3820, limitMinor: 9000, currency: "USD", exponent: 2, isCurrent: true }),
      rung({ key: "hard_block", state: "on", limitMinor: 9000, currency: "USD", exponent: 2 }),
    ],
    series: [
      series(1, { key: SESSION_KEY, label: "Session", source: "limit", sort: 15, kind: "session",
        group: "session", u: 12, resetMin: 112, capturedMin: -0.9, fresh: "live",
        active: false, severity: "normal", delta: 4 }),
      series(2, { key: "bucket:five_hour", label: "Session (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 12, resetMin: 112, capturedMin: -0.9, fresh: "live",
        delta: 4, primary: false, dupOf: SESSION_KEY }),
      // An exhausted week stands still by nature — here "unchanged since" carries real content.
      series(3, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 100, resetMin: 3713, capturedMin: -26,
        confirmedMin: -0.9, sinceMin: -26,
        fresh: "live", active: true, severity: null, delta: 3 }),
      series(4, { key: "bucket:seven_day", label: "Week (all models)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 100, resetMin: 3713, capturedMin: -26,
        confirmedMin: -0.9, sinceMin: -26, fresh: "live",
        delta: 3, primary: false, dupOf: WEEKLY_KEY }),
      series(5, { key: "spend:org", label: "Spend limit (your pool)", source: "spend",
        sort: 30,
        u: 41, resetMin: null, capturedMin: -0.9, fresh: "live", severity: "normal", delta: 6,
        extra: { enabled: true, used: { amount_minor: 3820, currency: "USD", exponent: 2 },
                 limit: { amount_minor: 9000, currency: "USD", exponent: 2 } } }),
      // A second view of THE SAME pool. The row is not visible (`primary: false`), but its
      // number and flags are the content of the "?" next to the row above — and only here is
      // it visible that what was measured was 41.42%, and not a rounded 41.
      series(6, { key: "extra:usage", label: "Extra credits", source: "extra_usage",
        sort: 40, u: 41.42, resetMin: null, capturedMin: -0.9, fresh: "live", delta: 6,
        primary: false, dupOf: "spend:org",
        extra: { is_enabled: true, monthly_limit: 9000, used_credits: 3820, currency: "USD",
                 decimal_places: 2, disabled_reason: null, user_disabled: false,
                 spend_limit_reached: false, credits_ever_enabled: true,
                 daily: null, weekly: null } }),
      // FAILURE: the client reports, but this series has no samples. Last measurement was 42%.
      series(7, { key: "limit:weekly_scoped|weekly|fable|-", label: "Week — Fable",
        source: "limit", sort: 200, kind: "weekly_scoped", group: "weekly", u: null, raw: 42,
        resetMin: -185, capturedMin: -365, fresh: "unknown", active: false, severity: "normal" }),
    ],
  };
}

// ─── Pro account: a third account for the layout test ────────────────────────
// A long email (text-overflow in the header), the cascade cut off at credits.
function accountPro(): AccountStatus {
  return {
    uuid: "dddd1111-2222-3333-4444-555566667777",
    label: null,
    email: "extremely.long.account.name@example.org",
    displayName: "Pro",
    color: null,
    orgType: "claude_pro",
    seatTier: null,
    rateLimitTier: "default_claude_pro",
    subscriptionType: "pro",
    isEnabled: true,
    lastSampleAt: at(-1.6),
    lastBatchAt: at(-1.4),
    lastClientHost: "Laptop",
    cascade: [
      rung({ key: "session", state: "on", utilization: 68, seriesKey: SESSION_KEY, isCurrent: true }),
      rung({ key: "weekly", state: "on", utilization: 54, seriesKey: WEEKLY_KEY }),
      rung({ key: "credits", state: "unknown" }),
      rung({ key: "hard_block", state: "on" }),
    ],
    series: [
      series(1, { key: SESSION_KEY, label: "Session", source: "limit", sort: 15, kind: "session",
        group: "session", u: 68, resetMin: 19, capturedMin: -1.6, fresh: "live",
        active: true, severity: "warning", delta: 11 }),
      series(2, { key: "bucket:five_hour", label: "Session (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 68, resetMin: 19, capturedMin: -1.6, fresh: "live",
        delta: 11, primary: false, dupOf: SESSION_KEY }),
      series(3, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 54, resetMin: 14_800, capturedMin: -12,
        confirmedMin: -1.6, sinceMin: -12, fresh: "live", active: false, severity: "normal",
        delta: 2 }),
      series(4, { key: "limit:weekly_scoped|weekly|opus|-", label: "Week — Opus",
        source: "limit", sort: 200, kind: "weekly_scoped", group: "weekly", u: 76,
        resetMin: 14_800, capturedMin: -1.6, fresh: "live", active: false,
        severity: "warning", delta: 5 }),
    ],
  };
}

// ─── edge-case variants from the state catalog (2d) ──────────────────────────
function withEdgeCases(accounts: AccountStatus[]): AccountStatus[] {
  const max = accounts[0]!;
  // inferred_reset: the window rolled over overnight and nobody worked
  max.series.push(
    series(7, { key: "bucket:seven_day_cowork", label: "Week — Cowork", source: "bucket",
      sort: 100, bucket: "seven_day_cowork", u: 0, raw: 18, resetMin: 5600,
      capturedMin: -660, fresh: "inferred_reset" }),
  );
  // a broken duplicate: the pair does not form, both entries stay primary
  max.series.push(
    series(8, { key: "bucket:tangelo", label: "Tangelo", source: "bucket", sort: 100,
      bucket: "tangelo", u: 27, resetMin: 52, capturedMin: -0.3, fresh: "live" }),
  );
  // `stale` with a value — must look EXACTLY like `live`, only the age is meant to differ
  max.series.push(
    series(9, { key: "bucket:seven_day_opus", label: "Week — Opus", source: "bucket",
      sort: 100, bucket: "seven_day_opus", u: 17, resetMin: 8453, capturedMin: -85,
      fresh: "stale" }),
  );
  // Age in DAYS: full track with the last measurement and "confirmed on ... at HH:MM · 3 d 4 h ago".
  // On top of that a `valueSince` days old with the same confirmation — the value did not change
  // because nobody worked, so "unchanged since" has to carry a day too.
  max.series.push(
    series(10, { key: "bucket:seven_day_haiku", label: "Seven day haiku", source: "bucket",
      sort: 100, bucket: "seven_day_haiku", u: null, raw: 42, resetMin: 4300,
      capturedMin: -4560, sinceMin: -8880, fresh: "unknown" }),
  );
  // A reset a few days out: the hour without a day lied, now the caption is "on Fri. at 20:00".
  max.series.push(
    series(11, { key: "bucket:amber_ladder", label: "Amber ladder", source: "bucket",
      sort: 100, bucket: "amber_ladder", u: 61, resetMin: 5860, capturedMin: -0.4,
      fresh: "live" }),
  );
  // There has NEVER been a measurement — the only dashed track left and the only "unknown".
  max.series.push(
    series(12, { key: "bucket:nimbus_quill", label: "Nimbus quill", source: "bucket",
      sort: 100, bucket: "nimbus_quill", u: null, raw: null, resetMin: null,
      capturedMin: -30, fresh: "unknown" }),
  );
  // an account that reports, but no series has had a value yet
  return [
    ...accounts,
    {
      uuid: "cccc0000-1111-2222-3333-444455556666",
      label: "fresh account",
      email: "new@example.org",
      displayName: null,
      color: null,
      orgType: "claude_team",
      seatTier: "standard",
      rateLimitTier: "default_claude_team_standard",
      subscriptionType: "team",
      isEnabled: true,
      lastSampleAt: null,
      lastBatchAt: at(-2),
      lastClientHost: "laptop",
      cascade: [
        rung({ key: "session", state: "unknown" }),
        rung({ key: "weekly", state: "unknown" }),
        rung({ key: "credits", state: "unknown" }),
        rung({ key: "hard_block", state: "unknown" }),
      ],
      series: [],
    },
  ];
}

// ─── credits: withdrawn by the organization vs an exhausted own pool ─────────
// Two states a working system cannot be made to produce on demand — ONE field tells them apart.
// Without this preview the only way to see them is to wait for a failure.
function creditsStates(): AccountStatus[] {
  const account = (uuid: string, name: string, spend: Spec, eu: Spec,
                   cascade: CascadeRung[]): AccountStatus => ({
    uuid, label: null, email: `${uuid.slice(0, 4)}@example.org`, displayName: name,
    color: null, orgType: "claude_team", seatTier: "standard",
    rateLimitTier: "default_claude_team_standard", subscriptionType: "team",
    isEnabled: true, lastSampleAt: at(-0.4), lastBatchAt: at(-0.3),
    lastClientHost: "laptop",
    cascade,
    series: [
      series(1, { key: SESSION_KEY, label: "Session", source: "limit", sort: 15,
        kind: "session", group: "session", u: 54, resetMin: 70, capturedMin: -0.4,
        fresh: "live", active: false, severity: "normal", delta: 7 }),
      series(2, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit",
        sort: 25, kind: "weekly_all", group: "weekly", u: 67, resetMin: 7200,
        capturedMin: -0.4, fresh: "live", active: true, severity: "normal", delta: 2 }),
      series(3, spend),
      // The dimmed twin of the row above. It is not on screen, but without it both of these
      // variants would show a "?" with no flag block — they would not test what they exist for.
      series(4, eu),
    ],
  });

  return [
    // WITHDRAWN METER: Anthropic reports `percent: 0` and `used: 0.00`, so without this change
    // the track would sit empty under a "confirmed" caption — that is, "you have the whole
    // 300 EUR" next to a block. The number and the amounts come from the LAST MEASUREMENT
    // before the block (hence the age in days), `utilization` is null because there is no
    // current reading. The withdrawal payload carries `percent: 0`, `used: 0.00` and
    // `limit: null` — had that filled the row, the screen would promise the whole free limit
    // at the moment of a hard block.
    account("0000gone", "organization ceiling", {
      key: "spend:org", label: "Spend limit (your pool)", source: "spend", sort: 30,
      u: null, raw: 100, resetMin: null, capturedMin: -3120, fresh: "live",
      severity: "critical", reason: "org_level_disabled_until",
      extra: { enabled: true, disabled_reason: null,
               used: { amount_minor: 30004, currency: "EUR", exponent: 2 },
               limit: { amount_minor: 30000, currency: "EUR", exponent: 2 } },
    }, {
      // `extra` carries the state from BEFORE the withdrawal — exactly as in a live system,
      // where the backend swaps it for the one from the last measurement. That is why
      // `disabled_reason` is null here, and the shut gate is told by `reason` (that is,
      // `unavailableReason` in the contract).
      key: "extra:usage", label: "Extra credits", source: "extra_usage", sort: 40,
      u: null, raw: 100.014, resetMin: null, capturedMin: -3120, fresh: "live",
      reason: "org_level_disabled_until", primary: false, dupOf: "spend:org",
      extra: { is_enabled: true, monthly_limit: 30000, used_credits: 30004, currency: "EUR",
               decimal_places: 2, disabled_reason: null, user_disabled: false,
               spend_limit_reached: false, credits_ever_enabled: true,
               daily: null, weekly: null },
    }, [
      rung({ key: "session", state: "on", utilization: 54, seriesKey: SESSION_KEY }),
      rung({ key: "weekly", state: "on", utilization: 67, seriesKey: WEEKLY_KEY,
        isCurrent: true }),
      rung({ key: "credits", state: "off", seriesKey: "spend:org",
        reason: "org_level_disabled_until", usedMinor: 30004, limitMinor: 30000,
        currency: "EUR", exponent: 2 }),
      rung({ key: "hard_block", state: "on", reason: "org_level_disabled_until" }),
    ]),
    // EXHAUSTED OWN POOL: the meter WORKS and tells the truth — 100% at 300.04 / 300.00 EUR.
    // Here the number has to stay; hiding it would lose the only correct value there is.
    account("1111pool", "own pool", {
      key: "spend:org", label: "Spend limit (your pool)", source: "spend", sort: 30,
      u: 100, resetMin: null, capturedMin: -0.4, fresh: "live", severity: "critical",
      delta: 1,
      extra: { enabled: true, disabled_reason: null,
               used: { amount_minor: 30004, currency: "EUR", exponent: 2 },
               limit: { amount_minor: 30000, currency: "EUR", exponent: 2 } },
    }, {
      // The meter WORKS, so the "?" shows full precision next to a bar at 100.
      // `spend_limit_reached` stays `false` — that is how the real payload of an exhausted
      // OWN pool looks (backend/tests/team.py), because that flag speaks of the org ceiling.
      key: "extra:usage", label: "Extra credits", source: "extra_usage", sort: 40,
      u: 100.014, resetMin: null, capturedMin: -0.4, fresh: "live", delta: 1,
      primary: false, dupOf: "spend:org",
      extra: { is_enabled: true, monthly_limit: 30000, used_credits: 30004, currency: "EUR",
               decimal_places: 2, disabled_reason: null, user_disabled: false,
               spend_limit_reached: false, credits_ever_enabled: true,
               daily: null, weekly: null },
    }, [
      rung({ key: "session", state: "on", utilization: 54, seriesKey: SESSION_KEY }),
      rung({ key: "weekly", state: "on", utilization: 100, seriesKey: WEEKLY_KEY }),
      rung({ key: "credits", state: "on", utilization: 100, seriesKey: "spend:org",
        usedMinor: 30004, limitMinor: 30000, currency: "EUR", exponent: 2 }),
      rung({ key: "hard_block", state: "on", limitMinor: 30000, currency: "EUR",
        exponent: 2, isCurrent: true }),
    ]),
  ];
}

// ─── window states right after a session reset ───────────────────────────────
function afterReset(): AccountStatus[] {
  const acc = (uuid: string, name: string, session: Spec): AccountStatus => ({
    uuid, label: null, email: `${uuid.slice(0, 4)}@example.org`, displayName: name, color: null,
    orgType: "claude_max", seatTier: null, rateLimitTier: "default_claude_max_5x",
    subscriptionType: "max", isEnabled: true,
    lastSampleAt: at(-0.3), lastBatchAt: at(-0.2), lastClientHost: "desktop",
    cascade: [
      rung({ key: "session", state: "on", utilization: session.u, seriesKey: SESSION_KEY,
        isCurrent: true }),
      rung({ key: "weekly", state: "on", utilization: 30, seriesKey: WEEKLY_KEY }),
      rung({ key: "credits", state: "off", seriesKey: "spend:org" }),
      rung({ key: "hard_block", state: "on" }),
    ],
    series: [
      series(1, session),
      series(2, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -0.3,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
    ],
  });
  const session = { key: SESSION_KEY, label: "Session", source: "limit" as SeriesSource, sort: 15,
    kind: "session", group: "session", severity: "normal" };

  return [
    // Anthropic gave no boundary, because nothing has gone through the new window yet.
    acc("0000zero", "after reset", { ...session, u: 0, resetMin: null, capturedMin: -0.3,
      fresh: "live", delta: null }),
    // `reset-in-progress`: the probe zeroed a stale boundary, usage is already growing.
    acc("1111prog", "reset in progress", { ...session, u: 3, resetMin: null, capturedMin: -0.3,
      fresh: "live", delta: 3, deltaFromMin: -6 }),
    // The boundary has passed and the client was silent — the countdown has nothing to count.
    acc("2222past", "past the boundary", { ...session, u: 0, raw: 18, resetMin: -3, capturedMin: -310,
      fresh: "inferred_reset", delta: null }),
  ];
}

export function mockStatus(): StatusResponse {
  const variant = new URLSearchParams(window.location.search).get("mock") ?? "base";
  let accounts = [accountMax(), accountTeam()];
  if (variant === "three") accounts = [...accounts, accountPro()];
  if (variant === "states") accounts = withEdgeCases(accounts);
  if (variant === "reset") accounts = afterReset();
  if (variant === "credits") accounts = creditsStates();

  return {
    contractVersion: 3,
    serverNow: new Date(now()).toISOString(),
    accounts,
    // The backend generates no warning today — it used to be derived here from `unknown` series
    // and disappeared along with that very concept in the UI. An empty `warnings[]` is correct.
    warnings: [],
  };
}
