"""DTO. Na zewnatrz camelCase, wewnatrz snake_case.

CZAS NA DRUCIE JEST ZAWSZE UTC Z OFFSETEM (`...Z`) — patrz `UtcDt`. Kolumny w bazie sa
naiwne i tak zostaje; strefe dopinamy wylacznie przy serializacji.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _utc_iso(v: datetime) -> str:
    """Naiwny UTC -> ISO-8601 z 'Z'.

    Bez tego przegladarka MILCZACO interpretuje "2026-07-26T19:07:37" jako czas lokalny
    i countdown przesuwa sie o strefe — liczba wyglada dobrze i jest zla. Caly projekt
    broni sie przed falszywa pewnoscia, a znacznik bez strefy jest jej czysta postacia.
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Uzywaj TEGO typu w kazdym polu wyjsciowym z data. Zwykly `datetime` serializuje sie
# bez offsetu i to jest pulapka, nie preferencja stylistyczna.
UtcDt = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str)]


def _naive_utc(v: datetime) -> datetime:
    """ISO-8601 z drutu -> naiwny UTC. Wejsciowe lustro `UtcDt`.

    Kiedy wyjscie dostalo strefe (kontrakt v2), przegladarka zaczela ODSYLAC czas z 'Z' —
    `Date.toISOString()`. Pydantic robi z tego datetime ZE STREFA, a caly backend (kolumny,
    `utcnow()`, probki) jest naiwny. Bez tej konwersji /history wybucha na odejmowaniu
    "can't subtract offset-naive and offset-aware datetimes".

    Gorsze jest to, czego nie widac: sterownik MySQL formatuje datetime `strftime`-em
    i tzinfo IGNORUJE, wiec parametr z offsetem innym niz UTC (np. '+02:00') trafilby do
    WHERE jako czas scienny i cicho przesunal caly zakres o dwie godziny. Z 'Z' wychodzi
    to samo przez przypadek — dlatego granice zamykamy tutaj, a nie liczymy na klienta.
    """
    return v if v.tzinfo is None else v.astimezone(timezone.utc).replace(tzinfo=None)


# Uzywaj TEGO typu w kazdym parametrze WEJSCIOWYM z data (query, body).
NaiveUtcDt = Annotated[datetime, AfterValidator(_naive_utc)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True,
                              from_attributes=True)


# --------------------------------------------------------------------------- ingest
class IngestResult(CamelModel):
    ok: bool
    samples_written: int
    backlog_accepted: int = 0
    server_now: UtcDt
    batch_id: int | None = None
    series_registered: list[str] = []


# --------------------------------------------------------------------------- odczyt
class SeriesStatus(CamelModel):
    series_id: int
    series_key: str
    label: str
    source: str                        # bucket | limit | extra_usage | spend
    sort_order: int
    # Semantyka serii. Bez tych trzech pol UI musialoby zgadywac, ktora seria jest oknem
    # 5-godzinnym — po sort_order albo po prefiksie klucza, czyli dokladnie tak, jak
    # zabrania zasada 5 z AGENTS.md.
    kind: str | None                   # session | weekly_all | weekly_scoped | ...
    group: str | None                  # session | weekly | ...
    bucket_key: str | None             # five_hour, seven_day, tangelo, ...
    utilization: float | None          # None gdy nie ma czego pokazac — to jest poprawne
    raw_utilization: float | None      # ostatnia ZMIERZONA wartosc, bez wnioskowania
    # Powod, dla ktorego tej serii NIE DA SIE zmierzyc — doslownie od Anthropic
    # (zaobserwowane: `org_level_disabled_until`, `org_spend_cap_reached`; zbior JEST
    # otwarty, wiec konsument sprawdza `!== null`, a nie tresc). Gdy jest ustawiony, OBA
    # pola wartosci sa null — inaczej UI, ktore liczy `utilization ?? rawUtilization`,
    # narysowaloby zmierzone 0% w chwili twardej blokady.
    unavailable_reason: str | None = None
    resets_at: UtcDt | None
    seconds_to_reset: int | None
    # Trzy rozne pytania o czas — mylenie ich sprawia, ze stabilny odczyt wyglada jak awaria:
    #   captured_at  - kiedy zapisano ostatnia PROBKE (dedup pomija niezmienione wartosci)
    #   confirmed_at - kiedy ostatnio POTWIERDZONO te wartosc  <- z tego liczy sie swiezosc
    #   value_since  - odkad wartosc jest niezmienna           <- z tego "niezmienne od ..."
    captured_at: UtcDt | None
    confirmed_at: UtcDt | None
    value_since: UtcDt | None
    freshness: str                     # live | stale | inferred_reset | unknown
    is_active: bool | None
    severity: str | None
    delta_pct_1h: float | None
    # Od ktorej PROBKI liczy sie delta_pct_1h — baseline jest przyciety do biezacego okna,
    # wiec rozpietosc bywa krotsza niz godzina. Null razem z delta_pct_1h.
    delta_from: UtcDt | None
    # API raportuje te same limity dwukrotnie: raz jako bucket najwyzszego poziomu,
    # raz jako wpis w limits[]. Wykrywamy to z danych (identyczne utilization + resets_at),
    # bez hardkodowanego mapowania — bo wlasnie hardkodowanie nas juz raz ugryzlo.
    # UI ma domyslnie pokazywac tylko primary=true.
    primary: bool = True
    duplicate_of: str | None = None
    extra: dict[str, Any] | None


class CascadeRung(CamelModel):
    """Jeden szczebel kaskady limitow: 5 h -> tydzien -> kredyty -> twardy blok.

    Kwoty w JEDNOSTKACH MNIEJSZYCH z wykladnikiem, nigdy jako float — i bez formatowania.
    Backend nie zwraca "38,20 / 90,00 USD"; sklada to UI, bo to jest prezentacja.
    """
    key: str                           # session | weekly | credits | hard_block
    state: str                         # on | off | unknown  ("unknown" != "off")
    # Dlaczego szczebel jest wylaczony — ten sam lancuch od Anthropic co w SeriesStatus.
    # `state` NIE dostaje czwartej wartosci: wycofane kredyty to nadal `off`, wiec
    # konsument, ktory tego pola nie zna, napisze "wylaczone" — zdanie niepelne, ale
    # prawdziwe.
    reason: str | None = None
    is_current: bool = False           # szczebel, ktory ogranicza CIEBIE w tej chwili
    utilization: float | None = None   # session, weekly
    series_key: str | None = None      # powrot do serii, ktora dala te wartosc
    used_minor: int | None = None
    limit_minor: int | None = None
    currency: str | None = None
    exponent: int | None = None


class AccountStatus(CamelModel):
    uuid: str
    label: str | None
    email: str | None
    display_name: str | None
    color: str | None
    org_type: str | None
    seat_tier: str | None
    rate_limit_tier: str | None
    subscription_type: str | None
    is_enabled: bool
    last_sample_at: UtcDt | None
    last_batch_at: UtcDt | None
    last_client_host: str | None
    cascade: list[CascadeRung] = []
    series: list[SeriesStatus]


class StatusResponse(CamelModel):
    contract_version: int
    server_now: UtcDt
    accounts: list[AccountStatus]
    warnings: list[str] = []


# --------------------------------------------------------------------------- stream
# SSE frame envelopes. `AccountFrame` embeds `AccountStatus` VERBATIM — never a "lite"
# variant. The model is already frozen by /api/status, so reusing it adds not a single
# frozen field; a separate, simplified shape would be a second contract to maintain, and
# THAT is what would break rule 8 — not this envelope.
class HelloFrame(CamelModel):
    contract_version: int
    server_now: UtcDt
    # `subscribed` are the UUIDs found in the database, `unknown` the ones that are not.
    # An unknown UUID is NOT an error (the account may only appear later), but it must be
    # visible: otherwise a typo in a panel's config shows up as silence indistinguishable
    # from idleness — the failure mode this whole project is built to avoid.
    subscribed: list[str]
    unknown: list[str]
    ping_sec: float
    max_lifetime_sec: float


class AccountFrame(CamelModel):
    contract_version: int
    server_now: UtcDt
    account: AccountStatus
    # Warnings for THIS account. The full list in /api/status is computed across accounts
    # and stays there — the poll remains its owner.
    warnings: list[str] = []


class PingFrame(CamelModel):
    server_now: UtcDt


# Sesja Claude Code, ktora stanela i czeka na czlowieka. Stan CHWILOWY: zyje wylacznie
# w pamieci procesu, nie ma go w bazie i nie ma po nim historii.
#
# Wszystkie pola opisowe sa opcjonalne CELOWO. To jedyny ksztalt w tym kontrakcie, ktorego
# zrodlem jest payload hooka Claude Code, a ten zmienia sie miedzy wersjami klienta —
# `PermissionRequest` nie ma `tool_use_id` mimo ze `PreToolUse` ma. Twardy model odrzucalby
# tu cala ramke za brak pola, ktore i tak jest tylko podpisem na ekranie.
class SessionAlert(CamelModel):
    key: str
    machine: str | None = None
    reason: str                  # permission | question | plan
    project: str | None = None
    tool: str | None = None
    detail: str | None = None
    since: UtcDt | None = None
    account_uuid: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    permission_mode: str | None = None


class AlertFrame(CamelModel):
    contract_version: int
    server_now: UtcDt
    # PELNY biezacy zbior ze WSZYSTKICH maszyn, nie przyrost. Trzy niezalezne powody:
    # snapshot w stream.py kasuje kolejke, przepelnienie kolejki wymienia zawartosc na
    # `lag`, a polityka projektu (docs/API.md) mowi wprost, ze kazda ramka jest pelnym
    # stanem. Przy pelnym stanie kazda pojedyncza ramka leczy dowolna zgube.
    alerts: list[SessionAlert] = []


class SessionAlertResult(CamelModel):
    ok: bool
    machine: str
    accepted: int
    subscribers: int


class HistoryPoint(CamelModel):
    t: UtcDt
    min: float | None
    max: float | None
    avg: float | None
    last: float | None
    n: int


class HistoryGap(CamelModel):
    from_: UtcDt
    to: UtcDt
    # client_silent — klient milczal (brak batchy).
    # no_samples    — klient raportowal, ale dla TEJ serii nie przyszla ani jedna probka.
    # To dwie rozne rzeczy i na wykresie musza wygladac inaczej: druga jest AWARIA,
    # dokladnie tak samo jak stan `unknown` w /status.
    kind: str


class HistoryResponse(CamelModel):
    bucket: str
    points: list[HistoryPoint]
    resets: list[UtcDt]
    gaps: list[HistoryGap]
