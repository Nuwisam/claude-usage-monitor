"""Normalizacja odpowiedzi /api/oauth/usage do plaskiej listy obserwacji.

CZYSTE FUNKCJE — zero I/O, zero zaleznosci od bazy. Cala logika, ktora moze sie mylic,
jest tutaj i jest testowalna na realnych payloadach z docs/POC-FINDINGS.md.

Zasady wyniesione z kroku 0:
  * Odpowiedz ma 17 kluczy najwyzszego poziomu, z czego 5 bylo dla nas nowych. Nie wolno
    zakladac zamknietej listy — parser akceptuje dowolny nowy klucz i zglasza go jako drift.
  * `bucket.utilization` to float, `limits[].percent` to int, `spend.percent` to int.
    Wszystko sprowadzamy do 0..100.
  * `resets_at` w surowej odpowiedzi to ISO-8601 z mikrosekundami i offsetem. Statusline
    podawal epoch — parser przyjmuje oba, bo to kosztuje trzy linie.
  * Na koncie Team wiazacym limitem jest `spend` (miesieczny limit organizacji), a nie okna
    czasowe. Dlatego `spend` jest seria pierwszej kategorii, nie polem pobocznym.
  * Kwoty w `spend` sa w jednostkach mniejszych z wykladnikiem — nie splaszczamy ich do float.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

# Klucze najwyzszego poziomu obslugiwane osobno, nie jako zwykly bucket.
_SPECIAL_KEYS = {"limits", "extra_usage", "spend", "member_dashboard_available"}

_SORT = {
    "bucket:five_hour": 10,
    "bucket:seven_day": 20,
    "spend:org": 30,
    "extra:usage": 40,
}

# Etykiety sa TRESCIA WIDOCZNA W UI — w odroznieniu od komentarzy w kodzie pisze sie je
# z polskimi znakami. Nieznany klucz dostaje etykiete z samego klucza (patrz `humanize`),
# wiec nowy bucket u Anthropic wyswietli sie sam, bez wpisu w tej tabeli.
_LABELS = {
    "five_hour": "Sesja (5 h)",
    "seven_day": "Tydzień (wszystkie modele)",
    "seven_day_opus": "Tydzień — Opus",
    "seven_day_sonnet": "Tydzień — Sonnet",
    "seven_day_omelette": "Tydzień — Design",
    "seven_day_cowork": "Tydzień — Cowork",
    "seven_day_oauth_apps": "Tydzień — aplikacje OAuth",
    "seven_day_overage_included": "Tydzień — z nadwyżką",
}

_KIND_LABELS = {
    "session": "Sesja",
    "weekly_all": "Tydzień (wszystkie modele)",
    "weekly_scoped": "Tydzień",
}


@dataclass
class Observation:
    series_key: str
    source: str                       # bucket | limit | extra_usage | spend
    display_label: str
    utilization: float | None         # ZAWSZE 0..100
    # Powod, dla ktorego tej serii NIE DA SIE zmierzyc — doslownie od Anthropic. Gdy jest
    # ustawiony, `utilization` jest None Z DEFINICJI: to nie jest pomiar 0%, tylko brak
    # miernika. Patrz `meter_withdrawn`.
    unavailable_reason: str | None = None
    resets_at: datetime | None = None
    bucket_key: str | None = None
    kind: str | None = None
    group_key: str | None = None
    model_display_name: str | None = None
    surface_display_name: str | None = None
    is_active: bool | None = None
    severity: str | None = None
    sort_order: int = 1000
    extra: dict[str, Any] = field(default_factory=dict)
    # Czy wartosc tej serii pochodzi ze zrzutu `claude -p "/usage"` (a nie z cache Claude
    # Code). Rozstrzyga o DACIE pomiaru: oba zrodla leza w jednym payloadzie i maja rozny
    # wiek. Ustawiane z `measurement.fresh_covered`, bo tylko sonda wie, co czym nadpisala.
    covered_by_fresh: bool = False


@dataclass
class ParseResult:
    observations: list[Observation]
    seen_keys: set[str]               # wszystkie klucze najwyzszego poziomu w odpowiedzi
    null_keys: set[str]               # klucze obecne, ale None (seria istnieje, brak wartosci)
    problems: list[str]               # nie-fatalne: cokolwiek, czego nie umielismy odczytac


# --------------------------------------------------------------------------- pomocnicze
def parse_pct(v: Any) -> float | None:
    """Toleruje int, float i string z sufiksem '%'. Referencyjna implementacja musiala
    obsluzyc wszystkie trzy, wiec zakladamy to samo."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().rstrip("%").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def parse_ts(v: Any) -> datetime | None:
    """ISO-8601 (surowa odpowiedz) albo epoch w sekundach (statusline). Zwraca naiwny UTC
    OBCIETY DO PELNYCH SEKUND, bo kolumny w bazie sa naiwne i wszystko trzymamy w UTC.

    Obciecie mikrosekund nie jest kosmetyka i nie gubi informacji. Anthropic stempluje
    `resets_at` mikrosekundami SWOJEJ ODPOWIEDZI, nie granicy okna — w jednej odpowiedzi
    `five_hour` konczy sie na `00:59:59.056340`, a `seven_day` na `15:59:59.056361`
    (21 us roznicy, bo pola sa liczone po kolei). Efekt zmierzony: 63 probki
    w 6 h dawaly 63 rozne `resets_at`, a po obcieciu do sekundy zostaja 2 wartosci.

    Przez to porownanie `last_resets_at == o.resets_at` bylo praktycznie zawsze falszywe,
    co po cichu wylaczalo TRZY mechanizmy naraz: dedup (kazda probka zapisywana jako
    zmiana), guard monotonicznosci (odpalal sie tylko przy niezmienionym resets_at)
    i wykrywanie granic resetu w historii (61 "resetow" na dobe zamiast pieciu).
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        # >1e12 => milisekundy; endpoint tego nie robi, ale tanio sie zabezpieczyc
        secs = float(v) / 1000.0 if float(v) > 1e12 else float(v)
        dt = datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None)
        return dt.replace(microsecond=0)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        # fromisoformat w 3.12 radzi sobie z mikrosekundami i offsetem, ale nie z >6 cyframi
        m = re.match(r"^(.*\.\d{1,6})\d*(.*)$", s)
        if m:
            s = m.group(1) + m.group(2)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.replace(microsecond=0)
    return None


def same_reset_window(a: datetime | None, b: datetime | None, eps_sec: float) -> bool:
    """Czy dwa `resets_at` opisuja TO SAMO okno.

    Nie da sie tego sprawdzic rownoscia, bo granica okna podawana przez Anthropic KOLYSZE
    SIE. Zmierzone: 49 probek w ciagu 3 godzin, jedno okno sesji, wartosci od
    `00:59:59.014384` do `01:00:00.982268` — rozstrzal niespelna 2 sekundy, przechodzacy
    przy okazji przez granice minuty (wiec obcinanie do sekundy ani do minuty tego nie
    zalatwia; obcinanie zostaje, ale tylko zeby nie trzymac w bazie szumu).

    Tolerancja rozstrzyga to jednoznacznie: prawdziwy reset przesuwa granice o CALE OKNO —
    5 godzin dla sesji, 7 dni dla tygodnia. Kilkuminutowy prog jest o dwa rzedy wielkosci
    wiekszy od kolysania i o dwa rzedy mniejszy od najkrotszego okna.

    Brak wartosci po obu stronach traktujemy jako to samo okno (np. `spend` nie ma resetu);
    brak po jednej stronie to zmiana.

    ODPOWIADA NA PYTANIE DEDUPU ("czy cokolwiek sie zmienilo"), nie guardu monotonicznosci.
    Guard pyta o DOWOD i ma na to `known_same_reset_window` — jeden bool na oba pytania
    zamrazal stan biezacy.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= eps_sec


def known_same_reset_window(a: datetime | None, b: datetime | None, eps_sec: float) -> bool:
    """Czy WIADOMO, ze to samo okno — w odroznieniu od `same_reset_window`, ktore przy braku
    obu granic odpowiada "tak".

    Te dwa pytania wygladaja na jedno i dlatego przez dlugi czas obsluzyl je jeden bool.
    Roznica jest jednak zasadnicza:

      * DEDUP pyta "czy cokolwiek sie zmienilo". Dwa razy brak granicy to brak zmiany, wiec
        `same_reset_window(None, None) is True` jest tam POPRAWNE — i na tym stoi dedup serii
        `spend:org` oraz `extra:usage`, ktore granicy nie maja NIGDY.
      * GUARD MONOTONICZNOSCI pyta "czy mam DOWOD, ze okno sie nie przewinelo". Dwa razy brak
        granicy to brak dowodu, a nie dowod przeciwny.

    Niewiedza wzieta za dowod zamrazala stan biezacy. Zaraz po resecie Anthropic podaje
    `resets_at: null` dla swiezego okna z 0% zuzycia, wiec spadek 92% -> 0% przy dwoch
    NULL-ach wygladal jak nieaktualny odczyt z drugiej maszyny: probka szla do bazy z flaga
    `stale_read`, a `series_state` stal. Produkcja 2026-07-30: piec kolejnych probek
    11:51:05-11:55:13 UTC, stan zamrozony na 92% przy realnym zuzyciu 0-2%, odblokowany
    dopiero nowa granica po 9,5 minuty. Dla `spend:org` i `extra:usage` bylo to TRWALE —
    granica nie przyjdzie tam nigdy, wiec nic by tego nie odblokowalo.

    Asymetria kosztow jest jednoznaczna, ale nie tak tania, jak wyglada. Przyjecie
    nieaktualnego odczytu psuje stan do NASTEPNEGO pomiaru tej serii — zwykle minuta — z tym
    ze gornej granicy nie wyznacza czestotliwosc sondy, tylko jej milczenie: gdy klient
    zamilknie zaraz po zapisie, zla wartosc stoi, dopoki `freshness()` jej nie zdegraduje,
    czyli az do `CLIENT_SILENT_SEC` (domyslnie 6 h, `app/config.py:38`), i przez caly ten
    czas jest podawana jako `live`/`stale`, czyli jako wartosc, a nie jako niewiedza.
    Zamrozony stan klamie dokladnie tak samo dlugo, a dodatkowo nie ma z czego wyjsc: dla
    `spend:org` i `extra:usage` granica nie przyjdzie NIGDY. Probka trafia do bazy w obu
    wariantach, wiec historii nie traci zaden. Guard dostaje wiec tylko dowod, nigdy domysl.
    """
    return a is not None and b is not None and same_reset_window(a, b, eps_sec)


def carry_reset_window(prev: datetime | None, incoming: datetime | None,
                       at: datetime) -> datetime | None:
    """Ktora granice okna ma trzymac STAN, gdy pomiar granicy nie przyniosl.

    Pomiar bez granicy nie znaczy "granicy nie ma", tylko "ten odczyt jej nie zna". Powody sa
    dwa i oba sa normalne: Anthropic nie podaje granicy dla okna z 0% zuzycia, a sonda zeruje
    granice przedawniona we WLASNYM cache (regula `reset-w-toku`, `client/usage-probe.py`).

    Oba skrajne rozwiazania sa zle:
      * nadpisac NULL-em — tracimy granice, ktora NADAL opisuje trwajace okno. Z niej zyje
        countdown w UI, `seconds_to_reset`, przyciecie baseline'u delty do okna i wnioskowanie
        `inferred_reset` przy dluzszej ciszy klienta (`app/freshness.py`).
      * trzymac stara zawsze — stan twierdzi wtedy, ze biezace okno konczy sie w chwili, ktora
        JUZ MINELA. To pewnie wygladajaca nieprawda. Zasada 4 z AGENTS.md mowi doslownie o
        `utilization`, nie o polach czasowych, wiec nie jest to jej cytat, a analogia: ten sam
        ruch, czyli podmiana niewiedzy na konkretna liczbe, ktorej odbiorca nie ma jak
        podwazyc.

    Rozstrzyga zegar, nie domysl. Granica z przyszlosci opisuje okno, ktore sie jeszcze nie
    skonczylo — pomiar sprzed niej nalezy wiec do tego samego okna i granica pozostaje
    prawdziwa. Granica, ktora minela, o oknie biezacym nie mowi NIC; wtedy stan przyznaje sie
    do niewiedzy (NULL), zamiast podawac przedawniona liczbe.

    Porownanie idzie do `at`, czyli do czasu POMIARU, a nie do "teraz": probke z backlogu
    ocenia sie wzgledem chwili, w ktorej ja zmierzono. Bez tolerancji, dokladnie jak
    `freshness()` i `_delta_1h` — tolerancja z zasady 9 dotyczy porownania DWOCH granic ze
    soba, a nie granicy z czasem.
    """
    if incoming is not None:
        return incoming
    if prev is not None and prev > at:
        return prev
    return None


def meter_withdrawn(block: Any) -> str | None:
    """Powod, dla ktorego ten miernik NIE DZIALA — albo None, gdy dziala.

    Gdy organizacja wyczerpie swoj globalny sufit wydatkow, Anthropic nie przestaje
    odpowiadac — ZERUJE MIERNIK: `percent` spada z 91 na 0, `used` z 273,15 EUR na 0,00,
    `limit` i `cap` znikaja (null), `severity` wraca z `critical` na `normal`. Zapisane
    wprost, to zero jest pewnym, zmierzonym "masz caly limit" w chwili twardej blokady.

    Payload tego stanu jest strukturalnie IDENTYCZNY z payloadem konta Max, ktore kredytow
    nigdy nie wlaczylo: `enabled:false`, `cap:null`, `percent:0`, `used:0` w obu. Rozni je
    WYLACZNIE `disabled_reason` — to jedyny dyskryminator, jaki dostajemy.

    TRESCI powodu nie interpretujemy (zasada 5): kazdy niepusty string znaczy "wycofany",
    a sam string jedzie dalej doslownie, az do UI. Zbior JEST otwarty — na jednym koncie
    zaobserwowano dwa rozne lancuchy w ciagu doby (`org_level_disabled_until`,
    `org_spend_cap_reached`).

    Wyczerpanie WLASNEJ puli to NIE jest wycofanie: wtedy `enabled` zostaje `true`,
    `disabled_reason` jest `null`, a `percent` dochodzi do 100 (zmierzone: used 300,04 przy
    limicie 300,00 EUR) — licznik dziala i mowi prawde. Dlatego werdykt stoi na fladze
    `enabled`, nigdy na wysokosci procentu: uznanie 100% za wycofanie odebraloby jedyna
    poprawna liczbe dokladnie w momencie, w ktorym jest najbardziej potrzebna.

    Czyta te same nazwy pol w bloku `spend` (`enabled`) i `extra_usage` (`is_enabled`), wiec
    dziala tak samo na surowej odpowiedzi, jak i na `extra` zapisanym w bazie — a to znaczy,
    ze stan sprzed tej zmiany odczyta sie poprawnie bez migracji.
    """
    if not isinstance(block, dict):
        return None
    reason = block.get("disabled_reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    enabled = block.get("enabled")
    if not isinstance(enabled, bool):
        enabled = block.get("is_enabled")
    if enabled is True:
        # Powod jest, ale brama stoi otworem — licznik dziala i jego liczba obowiazuje.
        return None
    return reason


# (captured_at, utilization, resets_at) — jeden wiersz przebiegu serii.
Sample = tuple[datetime, float | None, datetime | None]


def window_start_index(rows: Sequence[Sample], eps_sec: float, monotonic_eps: float) -> int:
    """Indeks pierwszej probki z BIEZACEGO okna. `rows` rosnaco, bez probek `stale_read`.

    Trzy sygnaly, bo `resets_at` bywa None z dwoch roznych powodow i zaden pojedynczy nie
    widzi wszystkich resetow:
      shift  - granica przeskoczyla o cale okno (z tolerancja, zasada 9),
      passed - probka jest mlodsza od znanej granicy; jedyny sygnal na `reset-w-toku` sondy,
               gdzie sanitize() zeruje `resets_at`, a zuzycie moze rosnac,
      drop   - utilization spadl; w obrebie okna niemozliwe (guard monotonicznosci). Ratunek
               dla serii bez znanej granicy — przy 0% Anthropic nie podaje `resets_at`.

    Falszywy sygnal SKRACA okno, nigdy nie wciaga probki z poprzedniego.
    """
    if not rows:
        return 0
    start = 0
    cur = rows[0][2]          # granica okna, po ktorym idziemy
    prev_u = rows[0][1]
    for i in range(1, len(rows)):
        t, u, r = rows[i]
        drop = prev_u is not None and u is not None and u < prev_u - monotonic_eps
        shift = (cur is not None and r is not None
                 and not same_reset_window(cur, r, eps_sec))
        passed = cur is not None and (t - cur).total_seconds() > eps_sec
        if drop or shift or passed:
            start, cur = i, r
        elif cur is None and r is not None:
            cur = r           # granica wrocila po przerwie — to NIE jest drugi reset
        if u is not None:
            prev_u = u        # null nie kasuje punktu odniesienia dla spadku
    return start


def _slug(s: Any) -> str:
    if s is None:
        return "-"
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "-"


def humanize(key: str) -> str:
    return _LABELS.get(key) or key.replace("_", " ").capitalize()


def limit_series_key(kind, group, model, surface) -> str:
    key = "limit:%s|%s|%s|%s" % (_slug(kind), _slug(group), _slug(model), _slug(surface))
    if len(key) > 255:                       # nigdy w praktyce; parser nie ma prawa rzucic
        import hashlib
        key = key[:240] + ":h" + hashlib.sha256(key.encode()).hexdigest()[:8]
    return key


def probe_key(o: Observation) -> str | None:
    """Identyfikator serii W JEZYKU SONDY — do porownania z `measurement.fresh_covered`.

    To NIE jest `series_series_key`: sonda nie zna ani `group`, ani `surface`, ani slugowania,
    a klucz sklada z tego, czym sama dysponuje w `usage` (patrz `_limit_key`
    w client/usage-probe.py). Rozbieznosc jest CICHA — zbior nigdy sie nie dopasuje,
    `covered_by_fresh` nigdy sie nie zapali i datowanie po cichu cofnie sie do stanu sprzed
    tej zmiany. Dlatego: BEZ `_slug` (sonda wysyla surowy `display_name`) i BEZ `surface`
    (`merge` dopasowuje po `kind`+`model`, wiec dwa limity roznjace sie tylko powierzchnia
    naprawde sa pokryte oba).

    `spend` i `extra_usage` zwracaja None: zrzut `/usage` ich nie zawiera NIGDY, wiec pytanie
    o pokrycie nie ma dla nich sensu."""
    if o.source == "bucket":
        return "bucket:%s" % o.bucket_key
    if o.source == "limit":
        return "limit:%s:%s" % (o.kind or "?", o.model_display_name or "-")
    return None


# --------------------------------------------------------------------------- glowne
def parse_usage(payload: Any, fresh_covered: frozenset[str] = frozenset()) -> ParseResult:
    """Zamienia odpowiedz /api/oauth/usage na liste obserwacji.

    Nigdy nie rzuca. Czego nie umie odczytac, zglasza w `problems`, a reszte przetwarza —
    lepiej zapisac 15 z 17 serii niz odrzucic caly pomiar.

    `fresh_covered` to zbior identyfikatorow z `measurement.fresh_covered` sondy. Pusty
    (domyslny) znaczy "nic nie pochodzi ze zrzutu" — i to jest poprawna, lagodna degradacja
    dla payloadu bez tego pola, nie galaz kompatybilnosci.
    """
    obs: list[Observation] = []
    problems: list[str] = []
    seen: set[str] = set()
    nulls: set[str] = set()

    if not isinstance(payload, dict):
        return ParseResult([], seen, nulls, ["payload nie jest obiektem"])

    for key, val in payload.items():
        seen.add(key)
        if val is None:
            nulls.add(key)

    # --- 1. buckety najwyzszego poziomu -----------------------------------
    for key, val in payload.items():
        if key in _SPECIAL_KEYS:
            continue
        if val is None:
            continue
        if not isinstance(val, dict):
            # np. member_dashboard_available=bool trafia tu tylko gdy zmieni sie schemat
            problems.append("klucz %s ma nieoczekiwany typ %s" % (key, type(val).__name__))
            continue
        if "utilization" not in val:
            problems.append("bucket %s bez pola utilization" % key)
            continue
        skey = "bucket:%s" % key
        extra = {k: v for k, v in val.items() if k not in ("utilization", "resets_at")}
        obs.append(Observation(
            series_key=skey, source="bucket", bucket_key=key,
            display_label=humanize(key),
            utilization=parse_pct(val.get("utilization")),
            resets_at=parse_ts(val.get("resets_at")),
            sort_order=_SORT.get(skey, 100),
            extra=extra,
        ))

    # --- 2. limits[] ------------------------------------------------------
    limits = payload.get("limits")
    if limits is not None and not isinstance(limits, list):
        problems.append("limits nie jest lista")
        limits = None
    for i, lim in enumerate(limits or []):
        if not isinstance(lim, dict):
            problems.append("limits[%d] nie jest obiektem" % i)
            continue
        scope = lim.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name")
                 if isinstance(scope, dict) else None)
        surface = ((scope.get("surface") or {}).get("display_name")
                   if isinstance(scope, dict) else None)
        kind, group = lim.get("kind"), lim.get("group")
        label = _KIND_LABELS.get(kind or "", (kind or "limit").replace("_", " "))
        # Pauza, nie dywiz — etykieta jest tresci widoczna w UI ("Tydzień — Fable").
        if model:
            label = "%s — %s" % (label, model)
        if surface:
            label = "%s / %s" % (label, surface)
        obs.append(Observation(
            series_key=limit_series_key(kind, group, model, surface),
            source="limit", kind=kind, group_key=group,
            model_display_name=model, surface_display_name=surface,
            display_label=label,
            utilization=parse_pct(lim.get("percent")),
            resets_at=parse_ts(lim.get("resets_at")),
            is_active=bool(lim.get("is_active")) if lim.get("is_active") is not None else None,
            severity=lim.get("severity"),
            sort_order=15 if kind == "session" else (25 if kind == "weekly_all" else 200),
            extra={k: v for k, v in lim.items()
                   if k not in ("percent", "resets_at", "is_active", "severity",
                                "kind", "group", "scope")},
        ))

    # --- 3. extra_usage ---------------------------------------------------
    eu = payload.get("extra_usage")
    if isinstance(eu, dict):
        eu_reason = meter_withdrawn(eu)
        obs.append(Observation(
            series_key="extra:usage", source="extra_usage",
            display_label="Kredyty dodatkowe",
            # Wycofany miernik nie ma pomiaru. Kwoty i flagi zostaja nietkniete w `extra`,
            # surowa odpowiedz lezy w `raw_payloads` — nie ginie nic poza fantomowym zerem.
            utilization=None if eu_reason else parse_pct(eu.get("utilization")),
            unavailable_reason=eu_reason,
            sort_order=_SORT["extra:usage"],
            extra={k: v for k, v in eu.items() if k != "utilization"},
        ))
    elif eu is not None:
        problems.append("extra_usage nie jest obiektem")

    # --- 4. spend — na Team to JEST wiazacy limit --------------------------
    sp = payload.get("spend")
    if isinstance(sp, dict):
        sp_reason = meter_withdrawn(sp)
        obs.append(Observation(
            series_key="spend:org", source="spend",
            # NIE "limit organizacji": to jest pula PRZYDZIELONA TOBIE (300,00 EUR na
            # miesiac). Sufit calej organizacji jest czyms innym i w kontrakcie nie istnieje
            # — gdy sie wyczerpie, ta seria nie rosnie, tylko gasnie (patrz meter_withdrawn).
            # Zmieniamy WYLACZNIE etykiete; `series_key` jest tozsamoscia i zostaje.
            display_label="Limit wydatków (Twoja pula)",
            utilization=None if sp_reason else parse_pct(sp.get("percent")),
            unavailable_reason=sp_reason,
            severity=sp.get("severity"),
            sort_order=_SORT["spend:org"],
            # Kwoty zostaja w jednostkach mniejszych z wykladnikiem — bez splaszczania.
            extra={k: v for k, v in sp.items() if k not in ("percent", "severity")},
        ))
    elif sp is not None:
        problems.append("spend nie jest obiektem")

    if fresh_covered:
        for o in obs:
            k = probe_key(o)
            if k is not None and k in fresh_covered:
                o.covered_by_fresh = True

    return ParseResult(obs, seen, nulls, problems)
