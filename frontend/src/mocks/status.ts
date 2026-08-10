/** Dane z makiety v2 na kontrakcie v3. Tryb `VITE_MOCKS=1`.
 *
 *  Stany `unknown`, `inferred_reset`, 100% i konto bez serii wymagaja w produkcji awarii,
 *  a wlasnie one musza wygladac dokladnie tak, jak mowi katalog stanow (makieta 2d).
 *
 *  Wiek odczytu liczony w DNIACH tez jest tu jedynym sposobem na oglad: w produkcji trzeba
 *  na niego czekac trzy dni, a od niego zaleza wszystkie stemple z dniem tygodnia.
 *
 *  Warianty przez `?mock=`:
 *    base   (domyslny) — dwa konta 1:1 z makiety, do porownania piksel w piksel
 *    three            — trzy pelne konta; test ukladu, nie stanow
 *    states           — dodatkowo inferred_reset, konto bez serii, rozjechany duplikat,
 *                       wiek w dniach, reset za kilka dni i seria nigdy nie zmierzona
 *    reset            — trzy podpisy okna zaraz po resecie sesji
 *    credits          — kredyty wycofane przez organizacje obok wyczerpanej wlasnej puli;
 *                       oba stany wygladaja w payloadzie identycznie poza `disabled_reason`
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
  /** Powod wycofania miernika. Ustawiony => `u` i `raw` MUSZA byc null. */
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
      series(1, { key: SESSION_KEY, label: "Session", source: "limit", sort: 15, kind: "session",
        group: "session", u: 31, resetMin: 52, capturedMin: -0.3, fresh: "live",
        active: false, severity: "normal", delta: 2 }),
      series(2, { key: "bucket:five_hour", label: "Session (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 31, resetMin: 52, capturedMin: -0.3, fresh: "live",
        delta: 2, primary: false, dupOf: SESSION_KEY }),
      // Probka sprzed 47 min (dedup), potwierdzenie sprzed 18 s — sztandarowy stan v3.
      series(3, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -47,
        confirmedMin: -0.3, sinceMin: -47,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
      series(4, { key: "bucket:seven_day", label: "Week (all models)", source: "bucket",
        sort: 20, bucket: "seven_day", u: 30, resetMin: 8453, capturedMin: -47,
        confirmedMin: -0.3, sinceMin: -47, fresh: "live",
        delta: 1, primary: false, dupOf: WEEKLY_KEY }),
      // Skrajny przypadek: kredyty wylaczone, wiec 0 stoi od czterech godzin.
      //
      // CELOWO BEZ `extra:usage`: na koncie, ktore kredytow nigdy nie mialo, ta seria ma
      // `utilization` null na zawsze i nie wchodzi do `series[]` (filtr `ever_non_null`
      // w services/status.py). To jest przypadek DEGRADACJI „?" — panel pokazuje wtedy sama
      // definicje puli, bez bloku flag, i musi byc widoczny w makiecie.
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
      series(1, { key: SESSION_KEY, label: "Session", source: "limit", sort: 15, kind: "session",
        group: "session", u: 12, resetMin: 112, capturedMin: -0.9, fresh: "live",
        active: false, severity: "normal", delta: 4 }),
      series(2, { key: "bucket:five_hour", label: "Session (5 h)", source: "bucket", sort: 10,
        bucket: "five_hour", u: 12, resetMin: 112, capturedMin: -0.9, fresh: "live",
        delta: 4, primary: false, dupOf: SESSION_KEY }),
      // Wyczerpany tydzien z natury stoi — tu „bez zmian od" niesie realna tresc.
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
      // Drugi widok TEJ SAMEJ puli. Wiersza nie widac (`primary: false`), ale jego liczba
      // i flagi sa trescia „?" przy wierszu wyzej — i tylko tu widac, ze zmierzone bylo
      // 41,42%, a nie zaokraglone 41.
      series(6, { key: "extra:usage", label: "Extra credits", source: "extra_usage",
        sort: 40, u: 41.42, resetMin: null, capturedMin: -0.9, fresh: "live", delta: 6,
        primary: false, dupOf: "spend:org",
        extra: { is_enabled: true, monthly_limit: 9000, used_credits: 3820, currency: "USD",
                 decimal_places: 2, disabled_reason: null, user_disabled: false,
                 spend_limit_reached: false, credits_ever_enabled: true,
                 daily: null, weekly: null } }),
      // AWARIA: klient raportuje, ale dla tej serii nie ma probek. Ostatni pomiar 42%.
      series(7, { key: "limit:weekly_scoped|weekly|fable|-", label: "Week — Fable",
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

// ─── warianty brzegowe z katalogu stanow (2d) ────────────────────────────────
function withEdgeCases(accounts: AccountStatus[]): AccountStatus[] {
  const max = accounts[0]!;
  // inferred_reset: okno wstalo w nocy i nikt nie pracowal
  max.series.push(
    series(7, { key: "bucket:seven_day_cowork", label: "Week — Cowork", source: "bucket",
      sort: 100, bucket: "seven_day_cowork", u: 0, raw: 18, resetMin: 5600,
      capturedMin: -660, fresh: "inferred_reset" }),
  );
  // rozjechany duplikat: para nie powstaje, oba wpisy zostaja glowne
  max.series.push(
    series(8, { key: "bucket:tangelo", label: "Tangelo", source: "bucket", sort: 100,
      bucket: "tangelo", u: 27, resetMin: 52, capturedMin: -0.3, fresh: "live" }),
  );
  // `stale` z wartoscia — musi wygladac DOKLADNIE jak `live`, roznic sie ma tylko wiek
  max.series.push(
    series(9, { key: "bucket:seven_day_opus", label: "Week — Opus", source: "bucket",
      sort: 100, bucket: "seven_day_opus", u: 17, resetMin: 8453, capturedMin: -85,
      fresh: "stale" }),
  );
  // Wiek w DNIACH: pelny tor z ostatnim pomiarem i „potwierdzone w ... o HH:MM · 3 d 4 h temu".
  // Do tego `valueSince` sprzed dni przy tym samym potwierdzeniu — wartosc stala, bo nikt nie
  // pracowal, wiec „bez zmian od" tez musi niesc dzien.
  max.series.push(
    series(10, { key: "bucket:seven_day_haiku", label: "Week — Haiku", source: "bucket",
      sort: 100, bucket: "seven_day_haiku", u: null, raw: 42, resetMin: 4300,
      capturedMin: -4560, sinceMin: -8880, fresh: "unknown" }),
  );
  // Reset za kilka dni: godzina bez dnia klamala, teraz podpis to „w pt. o 20:00".
  max.series.push(
    series(11, { key: "bucket:amber_ladder", label: "Amber ladder", source: "bucket",
      sort: 100, bucket: "amber_ladder", u: 61, resetMin: 5860, capturedMin: -0.4,
      fresh: "live" }),
  );
  // Pomiaru NIE BYLO NIGDY — jedyny pozostaly kreskowany tor i jedyne „nie wiem".
  max.series.push(
    series(12, { key: "bucket:nimbus_quill", label: "Nimbus quill", source: "bucket",
      sort: 100, bucket: "nimbus_quill", u: null, raw: null, resetMin: null,
      capturedMin: -30, fresh: "unknown" }),
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

// ─── kredyty: wycofane przez organizacje vs wyczerpana wlasna pula ───────────
// Dwa stany, ktorych w dzialajacym systemie nie da sie wywolac na zadanie — a rozni je JEDNO pole.
// Bez tego podgladu jedyna droga do ich zobaczenia jest czekanie na awarie.
function creditsStates(): AccountStatus[] {
  const konto = (uuid: string, name: string, spend: Spec, eu: Spec,
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
      // Zgaszony blizniak wiersza wyzej. Na ekranie go nie ma, ale bez niego oba te warianty
      // pokazywalyby „?" bez bloku flag — czyli nie testowalyby tego, po co istnieja.
      series(4, eu),
    ],
  });

  return [
    // WYCOFANY MIERNIK: Anthropic podaje `percent: 0` i `used: 0,00`, wiec bez tej zmiany
    // tor stalby pusty z podpisem „potwierdzone" — czyli „masz cale 300 EUR" przy blokadzie.
    // Liczba i kwoty pochodza z OSTATNIEGO POMIARU sprzed blokady (stad wiek w dniach),
    // `utilization` jest null, bo biezacego odczytu nie ma. Payload wycofania niesie
    // `percent: 0`, `used: 0,00` i `limit: null` — gdyby to on wypelnial wiersz, ekran
    // obiecywalby caly wolny limit w chwili twardej blokady.
    konto("0000wyco", "sufit organizacji", {
      key: "spend:org", label: "Spend limit (your pool)", source: "spend", sort: 30,
      u: null, raw: 100, resetMin: null, capturedMin: -3120, fresh: "live",
      severity: "critical", reason: "org_level_disabled_until",
      extra: { enabled: true, disabled_reason: null,
               used: { amount_minor: 30004, currency: "EUR", exponent: 2 },
               limit: { amount_minor: 30000, currency: "EUR", exponent: 2 } },
    }, {
      // `extra` niesie stan SPRZED wycofania — dokladnie tak jak w produkcji, gdzie backend
      // podmienia je na to z ostatniego pomiaru. Dlatego `disabled_reason` jest tu null,
      // a o zamknietej bramie mowi `reason` (czyli `unavailableReason` w kontrakcie).
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
    // WYCZERPANA WLASNA PULA: licznik DZIALA i mowi prawde — 100% przy 300,04 / 300,00 EUR.
    // Tu liczba musi zostac; jej ukrycie bylo by strata jedynej poprawnej wartosci.
    konto("1111pula", "wlasna pula", {
      key: "spend:org", label: "Spend limit (your pool)", source: "spend", sort: 30,
      u: 100, resetMin: null, capturedMin: -0.4, fresh: "live", severity: "critical",
      delta: 1,
      extra: { enabled: true, disabled_reason: null,
               used: { amount_minor: 30004, currency: "EUR", exponent: 2 },
               limit: { amount_minor: 30000, currency: "EUR", exponent: 2 } },
    }, {
      // Licznik DZIALA, wiec „?" pokazuje pelna precyzje przy pasku stojacym na 100.
      // `spend_limit_reached` zostaje `false` — tak jest w realnym payloadzie wyczerpanej
      // WLASNEJ puli (backend/tests/team.py), bo ta flaga mowi o suficie organizacji.
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
      series(2, { key: WEEKLY_KEY, label: "Week (all models)", source: "limit", sort: 25,
        kind: "weekly_all", group: "weekly", u: 30, resetMin: 8453, capturedMin: -0.3,
        fresh: "live", active: true, severity: "normal", delta: 1 }),
    ],
  });
  const sesja = { key: SESSION_KEY, label: "Session", source: "limit" as SeriesSource, sort: 15,
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
  if (variant === "credits") accounts = creditsStates();

  return {
    contractVersion: 3,
    serverNow: new Date(now()).toISOString(),
    accounts,
    // Backend nie generuje dzis zadnego ostrzezenia — bylo tu wyprowadzane z serii `unknown`
    // i zniklo razem z samym pojeciem w UI. Puste `warnings[]` to poprawny stan.
    warnings: [],
  };
}
