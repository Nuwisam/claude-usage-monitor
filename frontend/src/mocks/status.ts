/** Dane z makiety v2, przelozone na kontrakt v2. Tryb `VITE_MOCKS=1`.
 *
 *  To nie jest zabawka. Stany `unknown`, `inferred_reset`, 100% i konto bez serii sa
 *  w produkcji rzadkie albo wymagaja zepsucia klienta — a to wlasnie one musza wygladac
 *  dokladnie tak, jak mowi katalog stanow (makieta 2d). Bez tego pliku jedyna droga do
 *  ich zobaczenia jest czekanie na awarie.
 *
 *  Warianty przez `?mock=`:
 *    base   (domyslny) — dwa konta 1:1 z makiety, do porownania piksel w piksel
 *    states           — dodatkowo inferred_reset, konto bez serii i rozjechany duplikat
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
  fresh: Freshness;
  active?: boolean | null;
  severity?: string | null;
  delta?: number | null;
  primary?: boolean;
  dupOf?: string | null;
  extra?: Record<string, unknown> | null;
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
    resetsAt: s.resetMin === null || s.resetMin === undefined ? null : at(s.resetMin),
    secondsToReset:
      s.resetMin === null || s.resetMin === undefined ? null : Math.round(s.resetMin * 60),
    capturedAt: at(s.capturedMin),
    freshness: s.fresh,
    isActive: s.active ?? null,
    severity: s.severity ?? null,
    deltaPct1h: s.delta ?? null,
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
    utilization: null,
    seriesKey: null,
    usedMinor: null,
    limitMinor: null,
    currency: null,
    exponent: null,
    ...r,
  };
}

// ─── konto Max: sesja 31%, tydzien 30% i to on wiaze, kredyty wyłączone ──────
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
      series(1, { key: SESSION_KEY, label: "Sesja", source: "limit", sort: 15, kind: "session",
        group: "session", u: 31, resetMin: 52, capturedMin: -0.3, fresh: "live",
        active: false, severity: "normal", delta: 2 }),
      series(2, { key: "bucket:five_hour", label: "Sesja (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 31, resetMin: 52, capturedMin: -0.3, fresh: "live",
        delta: 2, primary: false, dupOf: SESSION_KEY }),
      series(3, { key: WEEKLY_KEY, label: "Tydzień (wszystkie modele)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -0.3,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
      series(4, { key: "bucket:seven_day", label: "Tydzień (wszystkie modele)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 30, resetMin: 8453, capturedMin: -0.3, fresh: "live",
        delta: 1, primary: false, dupOf: WEEKLY_KEY }),
      series(5, { key: "spend:org", label: "Limit wydatków organizacji", source: "spend", sort: 30,
        u: 0, resetMin: null, capturedMin: -0.3, fresh: "live", severity: "normal", delta: 0,
        extra: { enabled: false, used: { amount_minor: 0, currency: "USD", exponent: 2 } } }),
      series(6, { key: "limit:weekly_scoped|weekly|fable|-", label: "Tydzień — Fable",
        source: "limit", sort: 200, kind: "weekly_scoped", group: "weekly", u: 0, resetMin: null,
        capturedMin: -85, fresh: "stale", active: false, severity: "normal" }),
    ],
  };
}

// ─── konto Team: tydzien wyczerpany, praca leci z kredytow, Fable w unknown ──
function accountTeam(): AccountStatus {
  return {
    uuid: "aaaabbbb-0000-1111-2222-333344445555",
    label: null,
    email: "second@example.org",
    displayName: "Drugie",
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
      series(1, { key: SESSION_KEY, label: "Sesja", source: "limit", sort: 15, kind: "session",
        group: "session", u: 12, resetMin: 112, capturedMin: -0.9, fresh: "live",
        active: false, severity: "normal", delta: 4 }),
      series(2, { key: "bucket:five_hour", label: "Sesja (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 12, resetMin: 112, capturedMin: -0.9, fresh: "live",
        delta: 4, primary: false, dupOf: SESSION_KEY }),
      series(3, { key: WEEKLY_KEY, label: "Tydzień (wszystkie modele)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 100, resetMin: 3713, capturedMin: -0.9,
        fresh: "live", active: true, severity: null, delta: 3 }),
      series(4, { key: "bucket:seven_day", label: "Tydzień (wszystkie modele)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 100, resetMin: 3713, capturedMin: -0.9, fresh: "live",
        delta: 3, primary: false, dupOf: WEEKLY_KEY }),
      series(5, { key: "spend:org", label: "Limit wydatków organizacji", source: "spend", sort: 30,
        u: 41, resetMin: null, capturedMin: -0.9, fresh: "live", severity: "normal", delta: 6,
        extra: { enabled: true, used: { amount_minor: 3820, currency: "USD", exponent: 2 },
                 limit: { amount_minor: 9000, currency: "USD", exponent: 2 } } }),
      // AWARIA: klient raportuje, ale dla tej serii nie ma probek. Ostatni pomiar 42%.
      series(6, { key: "limit:weekly_scoped|weekly|fable|-", label: "Tydzień — Fable",
        source: "limit", sort: 200, kind: "weekly_scoped", group: "weekly", u: null, raw: 42,
        resetMin: -185, capturedMin: -365, fresh: "unknown", active: false, severity: "normal" }),
    ],
  };
}

// ─── warianty brzegowe z katalogu stanow (2d) ────────────────────────────────
function withEdgeCases(accounts: AccountStatus[]): AccountStatus[] {
  const max = accounts[0]!;
  // inferred_reset: okno wstalo w nocy i nikt nie pracowal. Kontur bez masy, liczba "~0".
  max.series.push(
    series(7, { key: "bucket:seven_day_cowork", label: "Tydzień — Cowork", source: "bucket",
      sort: 100, bucket: "seven_day_cowork", u: 0, raw: 18, resetMin: 5600,
      capturedMin: -660, fresh: "inferred_reset" }),
  );
  // rozjechany duplikat: bucket i limits[] pokazuja co innego, wiec para NIE powstaje
  // i oba wpisy zostaja glowne. Wolimy pokazac rozjazd niz go ukryc.
  max.series.push(
    series(8, { key: "bucket:tangelo", label: "Tangelo", source: "bucket", sort: 100,
      bucket: "tangelo", u: 27, resetMin: 52, capturedMin: -0.3, fresh: "live" }),
  );
  // konto, ktore raportuje, ale zadna seria nie miala jeszcze wartosci
  return [
    ...accounts,
    {
      uuid: "cccc0000-1111-2222-3333-444455556666",
      label: "swieze konto",
      email: "nowe@example.org",
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

export function mockStatus(): StatusResponse {
  const variant = new URLSearchParams(window.location.search).get("mock") ?? "base";
  let accounts = [accountMax(), accountTeam()];
  if (variant === "states") accounts = withEdgeCases(accounts);

  const warnings = accounts
    .filter((a) => a.series.some((s) => s.freshness === "unknown"))
    .map((a) => `Część serii na koncie ${a.email} jest w stanie „unknown” — sprawdź klienta`);

  return {
    contractVersion: 2,
    serverNow: new Date(now()).toISOString(),
    accounts,
    warnings,
  };
}
