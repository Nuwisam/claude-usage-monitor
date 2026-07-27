/** Dane z makiety v2 na kontrakcie v3. Tryb `VITE_MOCKS=1`.
 *
 *  Stany `unknown`, `inferred_reset`, 100% i konto bez serii wymagaja w produkcji awarii,
 *  a wlasnie one musza wygladac dokladnie tak, jak mowi katalog stanow (makieta 2d).
 *
 *  Warianty przez `?mock=`:
 *    base   (domyslny) — dwa konta 1:1 z makiety, do porownania piksel w piksel
 *    three            — trzy pelne konta; test ukladu, nie stanow
 *    states           — dodatkowo inferred_reset, konto bez serii i rozjechany duplikat
 *    reset            — trzy podpisy okna zaraz po resecie sesji
 *
 *  `reset` jest osobnym wariantem, bo te stany widac WYLACZNIE w hero, a hero bierze tylko
 *  serie `kind: "session"` — dopisanie ich do `states` nie pokazaloby niczego.
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
  /** Minuty wstecz dla potwierdzenia. Rozne od `capturedMin` = stabilny odczyt. */
  confirmedMin?: number;
  /** Odkad wartosc jest niezmienna. Domyslnie = capturedMin. */
  sinceMin?: number;
  fresh: Freshness;
  active?: boolean | null;
  severity?: string | null;
  delta?: number | null;
  /** Minuty wstecz dla probki-baseline delty. Brak => `deltaFrom: null` i brzmienie godzinowe. */
  deltaFromMin?: number;
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
      // Probka sprzed 47 min (dedup), potwierdzenie sprzed 18 s — sztandarowy stan v3.
      series(3, { key: WEEKLY_KEY, label: "Tydzień (wszystkie modele)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -47,
        confirmedMin: -0.3, sinceMin: -47,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
      series(4, { key: "bucket:seven_day", label: "Tydzień (wszystkie modele)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 30, resetMin: 8453, capturedMin: -47,
        confirmedMin: -0.3, sinceMin: -47, fresh: "live",
        delta: 1, primary: false, dupOf: WEEKLY_KEY }),
      // Skrajny przypadek: kredyty wylaczone, wiec 0 stoi od czterech godzin.
      series(5, { key: "spend:org", label: "Limit wydatków organizacji", source: "spend", sort: 30,
        u: 0, resetMin: null, capturedMin: -240, confirmedMin: -0.3, sinceMin: -240,
        fresh: "live", severity: "normal", delta: 0,
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
      // Wyczerpany tydzien z natury stoi — tu „bez zmian od" niesie realna tresc.
      series(3, { key: WEEKLY_KEY, label: "Tydzień (wszystkie modele)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 100, resetMin: 3713, capturedMin: -26,
        confirmedMin: -0.9, sinceMin: -26,
        fresh: "live", active: true, severity: null, delta: 3 }),
      series(4, { key: "bucket:seven_day", label: "Tydzień (wszystkie modele)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 100, resetMin: 3713, capturedMin: -26,
        confirmedMin: -0.9, sinceMin: -26, fresh: "live",
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

// ─── konto Pro: trzecie konto do testu ukladu ────────────────────────────────
// Dlugi email (text-overflow w naglowku), kaskada urwana na kredytach.
function accountPro(): AccountStatus {
  return {
    uuid: "dddd1111-2222-3333-4444-555566667777",
    label: null,
    email: "bardzo.dluga.nazwa.konta@example.org",
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
      series(1, { key: SESSION_KEY, label: "Sesja", source: "limit", sort: 15, kind: "session",
        group: "session", u: 68, resetMin: 19, capturedMin: -1.6, fresh: "live",
        active: true, severity: "warning", delta: 11 }),
      series(2, { key: "bucket:five_hour", label: "Sesja (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 68, resetMin: 19, capturedMin: -1.6, fresh: "live",
        delta: 11, primary: false, dupOf: SESSION_KEY }),
      series(3, { key: WEEKLY_KEY, label: "Tydzień (wszystkie modele)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 54, resetMin: 14_800, capturedMin: -12,
        confirmedMin: -1.6, sinceMin: -12, fresh: "live", active: false, severity: "normal",
        delta: 2 }),
      series(4, { key: "limit:weekly_scoped|weekly|opus|-", label: "Tydzień — Opus",
        source: "limit", sort: 200, kind: "weekly_scoped", group: "weekly", u: 76,
        resetMin: 14_800, capturedMin: -1.6, fresh: "live", active: false,
        severity: "warning", delta: 5 }),
    ],
  };
}

// ─── warianty brzegowe z katalogu stanow (2d) ────────────────────────────────
function withEdgeCases(accounts: AccountStatus[]): AccountStatus[] {
  const max = accounts[0]!;
  // inferred_reset: okno wstalo w nocy i nikt nie pracowal
  max.series.push(
    series(7, { key: "bucket:seven_day_cowork", label: "Tydzień — Cowork", source: "bucket",
      sort: 100, bucket: "seven_day_cowork", u: 0, raw: 18, resetMin: 5600,
      capturedMin: -660, fresh: "inferred_reset" }),
  );
  // rozjechany duplikat: para nie powstaje, oba wpisy zostaja glowne
  max.series.push(
    series(8, { key: "bucket:tangelo", label: "Tangelo", source: "bucket", sort: 100,
      bucket: "tangelo", u: 27, resetMin: 52, capturedMin: -0.3, fresh: "live" }),
  );
  // `stale` z wartoscia — w `base` stoi na 0, wiec przygaszenia nie widac
  max.series.push(
    series(9, { key: "bucket:seven_day_opus", label: "Tydzień — Opus", source: "bucket",
      sort: 100, bucket: "seven_day_opus", u: 17, resetMin: 8453, capturedMin: -85,
      fresh: "stale" }),
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

// ─── stany okna zaraz po resecie sesji ───────────────────────────────────────
function afterReset(): AccountStatus[] {
  const acc = (uuid: string, name: string, sesja: Spec): AccountStatus => ({
    uuid, label: null, email: `${uuid.slice(0, 4)}@example.org`, displayName: name, color: null,
    orgType: "claude_max", seatTier: null, rateLimitTier: "default_claude_max_5x",
    subscriptionType: "max", isEnabled: true,
    lastSampleAt: at(-0.3), lastBatchAt: at(-0.2), lastClientHost: "desktop",
    cascade: [
      rung({ key: "session", state: "on", utilization: sesja.u, seriesKey: SESSION_KEY,
        isCurrent: true }),
      rung({ key: "weekly", state: "on", utilization: 30, seriesKey: WEEKLY_KEY }),
      rung({ key: "credits", state: "off", seriesKey: "spend:org" }),
      rung({ key: "hard_block", state: "on" }),
    ],
    series: [
      series(1, sesja),
      series(2, { key: WEEKLY_KEY, label: "Tydzień (wszystkie modele)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -0.3,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
    ],
  });
  const sesja = { key: SESSION_KEY, label: "Sesja", source: "limit" as SeriesSource, sort: 15,
    kind: "session", group: "session", severity: "normal" };

  return [
    // Anthropic nie podal granicy, bo w nowym oknie nic jeszcze nie poszlo.
    acc("0000zero", "po resecie", { ...sesja, u: 0, resetMin: null, capturedMin: -0.3,
      fresh: "live", delta: null }),
    // `reset-w-toku`: sonda wyzerowala przedawniona granice, zuzycie juz rosnie.
    acc("1111toku", "reset w toku", { ...sesja, u: 3, resetMin: null, capturedMin: -0.3,
      fresh: "live", delta: 3, deltaFromMin: -6 }),
    // Granica minela, a klient milczal — countdown nie ma czego odliczac.
    acc("2222poza", "po granicy", { ...sesja, u: 0, raw: 18, resetMin: -3, capturedMin: -310,
      fresh: "inferred_reset", delta: null }),
  ];
}

export function mockStatus(): StatusResponse {
  const variant = new URLSearchParams(window.location.search).get("mock") ?? "base";
  let accounts = [accountMax(), accountTeam()];
  if (variant === "three") accounts = [...accounts, accountPro()];
  if (variant === "states") accounts = withEdgeCases(accounts);
  if (variant === "reset") accounts = afterReset();

  const warnings = accounts
    .filter((a) => a.series.some((s) => s.freshness === "unknown"))
    .map((a) => `Część serii na koncie ${a.email} jest w stanie „unknown” — sprawdź klienta`);

  return {
    contractVersion: 3,
    serverNow: new Date(now()).toISOString(),
    accounts,
    warnings,
  };
}
