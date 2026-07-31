"""Zapis pomiaru: walidacja, rejestracja serii, dedup, guard monotonicznosci, stan.

Trzy mechanizmy, ktore nie sa oczywiste i bez ktorych system daje zle dane:

1. DEDUP. Sonda odpytuje co ~120 s, a utilization zmienia sie rzadko. Bez dedupu tabela
   puchnie od identycznych wierszy. Piszemy nowy wiersz tylko gdy (utilization, resets_at)
   sie zmienily ALBO minal heartbeat. Bezstratne, bo miedzy punktami zmiany wartosc jest
   stala z definicji, a ingest_batches i tak dowodzi, ze klient zyl.

2. GUARD MONOTONICZNOSCI. Gdy to samo konto dziala na dwoch maszynach, kazda ma WLASNY
   cache tokenu i wlasny moment odpytania. Maszyna ze starszym odczytem moze przyslac
   nizsza wartosc niz juz znamy. Naiwny zapis cofnalby stan biezacy i wygladalo to jak
   reset okna. Regula: przy DOWODNIE tym samym oknie (obie granice znane i zgodne w
   tolerancji) spadek > MONOTONIC_EPS zapisujemy z flaga stale_read, ale NIE ruszamy
   series_state. Sam brak granicy po obu stronach dowodem NIE jest i wstrzymywac zapisu
   nie moze — patrz parsing.known_same_reset_window.

3. STAN AKTUALIZUJE TYLKO NAJNOWSZA PROBKA. Chroni takze przed backlogiem, ktory po
   wielogodzinnej przerwie wlewa stare probki — te nie moga nadpisac stanu biezacego.
   Stan trzyma tez granice okna, a pomiar bez granicy jej NIE kasuje, dopoki ta granica
   jeszcze nie minela — patrz parsing.carry_reset_window.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Account, IngestBatch, IngestEvent, LimitSample, Machine, MachineAccount,
    RawPayload, SeriesState, UsageSeries,
)
from app.parsing import (
    Observation, carry_reset_window, known_same_reset_window, meter_withdrawn, parse_ts,
    parse_usage, same_reset_window,
)

_ACCOUNT_FIELDS = {
    "email": "email", "display_name": "display_name", "org_uuid": "org_uuid",
    "org_name": "org_name", "org_type": "org_type", "seat_tier": "seat_tier",
    "org_rate_limit_tier": "org_rate_limit_tier",
    "user_rate_limit_tier": "user_rate_limit_tier",
    "extra_usage_enabled": "extra_usage_enabled",
}
# Pola planu — ich zmiana jest warta zdarzenia, bo zmienia znaczenie procentow.
_PLAN_FIELDS = ("org_type", "seat_tier", "org_rate_limit_tier",
                "user_rate_limit_tier", "subscription_type")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _event(db: AsyncSession, *, level: str, event_type: str, message: str | None = None,
                 account_id: int | None = None, batch_id: int | None = None,
                 detail: dict | None = None) -> None:
    db.add(IngestEvent(level=level, event_type=event_type, message=message,
                       account_id=account_id, batch_id=batch_id, detail=detail))


async def get_or_create_machine(db: AsyncSession, name: str, client: dict) -> Machine:
    m = (await db.execute(select(Machine).where(Machine.name == name))).scalar_one_or_none()
    if m is None:
        m = Machine(name=name)
        db.add(m)
        await db.flush()
    m.host = client.get("host") or m.host
    m.script_version = client.get("script_version") or m.script_version
    m.last_seen_at = utcnow()
    m.batches = (m.batches or 0) + 1
    return m


async def get_or_create_account(db: AsyncSession, acct: dict,
                                token_meta: dict) -> tuple[Account, bool]:
    uuid = acct.get("uuid")
    a = (await db.execute(select(Account).where(Account.account_uuid == uuid))).scalar_one_or_none()
    created = False
    if a is None:
        a = Account(account_uuid=uuid, label=acct.get("email") or uuid[:8])
        db.add(a)
        await db.flush()
        created = True

    before = {f: getattr(a, f) for f in _PLAN_FIELDS}
    for src, dst in _ACCOUNT_FIELDS.items():
        v = acct.get(src)
        if v is not None:
            setattr(a, dst, v)
    if token_meta.get("subscription_type"):
        a.subscription_type = token_meta["subscription_type"]
    after = {f: getattr(a, f) for f in _PLAN_FIELDS}

    if not created and before != after:
        changed = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
        await _event(db, level="info", event_type="plan_changed", account_id=a.id,
                     message="Zmiana pol planu konta", detail=changed)
    return a, created


async def get_or_create_series(db: AsyncSession, o: Observation,
                               cache: dict[str, UsageSeries]) -> tuple[UsageSeries, bool]:
    if o.series_key in cache:
        return cache[o.series_key], False
    s = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == o.series_key)
    )).scalar_one_or_none()
    created = False
    if s is None:
        s = UsageSeries(
            series_key=o.series_key, source=o.source, bucket_key=o.bucket_key,
            kind=o.kind, group_key=o.group_key,
            model_display_name=o.model_display_name,
            surface_display_name=o.surface_display_name,
            display_label=o.display_label, sort_order=o.sort_order,
        )
        db.add(s)
        await db.flush()
        created = True
    s.last_seen_at = utcnow()
    # Etykieta i kolejnosc sa OPISEM, nie danymi — odswiezamy je przy kazdym pomiarze.
    # Bez tego poprawka slownika etykiet (albo nowa nazwa modelu w scope) nigdy nie
    # doszlaby do serii zarejestrowanych wczesniej i UI pokazywalby stara tresc do konca
    # zycia bazy.
    if s.display_label != o.display_label:
        s.display_label = o.display_label
    if s.sort_order != o.sort_order:
        s.sort_order = o.sort_order
    if o.utilization is not None:
        s.ever_non_null = True
    cache[o.series_key] = s
    return s, created


async def store_raw(db: AsyncSession, usage: Any) -> tuple[RawPayload, str]:
    """Adresowane trescia — przy bezczynnosci odpowiedz jest bajt-identyczna, wiec
    zamiast tysiecy kopii mamy jeden wiersz i licznik."""
    body = json.dumps(usage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    rp = (await db.execute(select(RawPayload).where(RawPayload.sha256 == digest))).scalar_one_or_none()
    now = utcnow()
    if rp is None:
        rp = RawPayload(sha256=digest, body=body, last_seen_at=now, seen_count=1)
        db.add(rp)
        await db.flush()
    else:
        rp.last_seen_at = now
        rp.seen_count = (rp.seen_count or 0) + 1
    return rp, digest


def resolve_captured_at(raw: Any, now: datetime, is_backlog: bool) -> tuple[datetime, str | None]:
    """Zegar klienta jest niezaufany. Przy zbyt duzym rozjezdzie bierzemy czas serwera,
    zeby jedna maszyna z rozwalonym zegarem nie zasmiecila wykresow.

    Wpisy z backlogu maja z definicji stary captured_at — dla nich tolerancja jest zniesiona,
    a granica jest BACKLOG_MAX_AGE_SEC."""
    ts = parse_ts(raw)
    if ts is None:
        return now, "brak-captured_at"
    delta = abs((now - ts).total_seconds())
    if is_backlog:
        if delta > settings.backlog_max_age_sec:
            return now, "backlog-za-stary"
        return ts, None
    if delta > settings.clock_skew_tolerance_sec:
        return now, "skew-%ds" % int(delta)
    return ts, None


async def _write_observation(
    db: AsyncSession, *, account: Account, series: UsageSeries, o: Observation,
    captured_at: datetime, batch: IngestBatch, session_id: str | None, source: str,
    is_backlog: bool = False, client_reason: str | None = None,
) -> tuple[bool, bool]:
    """Zwraca (zapisano, stale_read)."""
    if is_backlog:
        # IDEMPOTENCJA POWTORKI ZE SPOOLA. Sonda obcina spool dopiero po odpowiedzi, wiec
        # gdy odpowiedz przepadnie (timeout, zerwane polaczenie), te same wpisy przyjda
        # ponownie. Bez tego guardu `changed` nizej porownuje sie z BIEZACYM stanem, ktory
        # od tamtego pomiaru poszedl dalej — powtorka wychodzi wiec jako "zmiana" i
        # dopisuje DRUGI wiersz na tym samym captured_at. Baza tego nie zatrzyma: na
        # (account_id, series_id, captured_at) nie ma UNIQUE, tylko zwykly indeks
        # (models.py:230).
        #
        # Klucz: (konto, seria, captured_at, maszyna, sha256 payloadu).
        #   - maszyna, bo to samo konto z DWOCH maszyn to poprawne dwa pomiary;
        #   - sha256, bo `parse_ts` obcina czas do pelnych sekund, a rownolegle hooki
        #     potrafia odpalic dwie sondy w tej samej sekundzie. Bez niego zderzenie po
        #     samym czasie skasowaloby drugi, ROZNY pomiar. Z nim zwija sie wylacznie
        #     przypadek bajt-identyczny, czyli ten sam pomiar — a zwijanie tego samego
        #     pomiaru jest dokladnie tym, co robi dedup nizej.
        #     `batch.payload_sha256` jest ustawione w :335, przed petla obserwacji, wiec
        #     tutaj nigdy nie jest NULL.
        #
        # Per OBSERWACJA, nie raz na wpis: dedup jest per seria, wiec pierwotny zapis mogl
        # pominac serie 1 i zapisac serie 3. Skrot "sprawdz pierwsza obserwacje i wyjdz"
        # przepuscilby wtedy duplikat serii 3. Koszt to jeden seek po ix_samples_series_time
        # na obserwacje (~7 na wpis, do ~1400 przy maksymalnym backlogu) — w calosci z
        # indeksu, zwraca 0 albo 1 wiersz.
        already = (await db.execute(
            select(LimitSample.id)
            .join(IngestBatch, IngestBatch.id == LimitSample.batch_id)
            .where(LimitSample.account_id == account.id,
                   LimitSample.series_id == series.id,
                   LimitSample.captured_at == captured_at,
                   IngestBatch.machine_id == batch.machine_id,
                   IngestBatch.payload_sha256 == batch.payload_sha256)
            .limit(1)
        )).scalar_one_or_none()
        if already is not None:
            # (False, False), nie wyjatek: to jest POPRAWNY wynik — mamy ten pomiar.
            # Wpis musi zostac policzony w `accepted`, zeby sonda go obciela.
            return False, False

    st = (await db.execute(
        select(SeriesState).where(SeriesState.account_id == account.id,
                                  SeriesState.series_id == series.id)
    )).scalar_one_or_none()

    # Wycofanie miernika i jego powrot sa ZDARZENIEM, nie tylko zmiana liczby: dla `spend`
    # i `extra:usage` to jedyny moment, w ktorym w logu widac, ze organizacja zamknela
    # brame. Tylko na PRZEJSCIU i tylko gdy jest z czym porownywac — pierwszy w zyciu pomiar
    # niczego nie zmienia, wiec nie ma o czym meldowac.
    if st is not None:
        was = meter_withdrawn(st.last_extra)
        if o.unavailable_reason and not was:
            await _event(db, level="warn", event_type="meter_withdrawn",
                         account_id=account.id, batch_id=batch.id,
                         message="Miernik %s wycofany przez organizacje" % series.series_key,
                         detail={"series": series.series_key,
                                 "reason": o.unavailable_reason,
                                 # Powod z cache KLIENTA (`cachedExtraUsageDisabledReason`)
                                 # — wylacznie do wgladu. Werdykt stoi na danych w pasmie,
                                 # bo tylko one sa spojne z reszta tej samej odpowiedzi.
                                 "client_reason": client_reason,
                                 "last_utilization": (float(st.last_utilization)
                                                      if st.last_utilization is not None
                                                      else None)})
        elif was and not o.unavailable_reason:
            await _event(db, level="info", event_type="meter_restored",
                         account_id=account.id, batch_id=batch.id,
                         message="Miernik %s znow dziala" % series.series_key,
                         detail={"series": series.series_key, "was_reason": was,
                                 "client_reason": client_reason,
                                 "utilization": o.utilization})

    stale_read = False
    should_write = True
    changed = True
    newest = st is None or st.last_captured_at is None or captured_at > st.last_captured_at

    prev_r = st.last_resets_at if st is not None else None
    # Granica, ktora WEJDZIE do stanu. Pomiar bez granicy nie kasuje granicy, ktora jeszcze
    # nie minela — patrz parsing.carry_reset_window. Przeniesienie liczy sie wylacznie dla
    # probki, ktora stan naprawde zapisze (`newest`); probka z backlogu stanu nie rusza, wiec
    # dla niej porownanie musi isc do tego, co przyszlo.
    next_r = carry_reset_window(prev_r, o.resets_at, captured_at) if newest else o.resets_at

    if st is not None and st.last_captured_at is not None:
        prev_u = float(st.last_utilization) if st.last_utilization is not None else None
        # Z TOLERANCJA, nie na rownosc: granica okna podawana przez Anthropic kolysze sie
        # o ~2 s, wiec porownanie doslowne bylo zawsze falszywe i po cichu wylaczalo
        # zarowno dedup, jak i guard monotonicznosci ponizej.
        #
        # Dedup pyta "czy ZMIENI SIE STAN", wiec porownuje `prev_r` z `next_r`, a nie
        # z surowym `o.resets_at`. Inaczej przy przenoszonej granicy KAZDY kolejny pomiar bez
        # granicy wygladalby jak zmiana (znana granica w stanie vs NULL w pomiarze) i dedup
        # pisalby wiersz co pomiar — dokladnie to, czego ma nie robic.
        changed = (prev_u != o.utilization
                   or not same_reset_window(prev_r, next_r,
                                            settings.reset_window_eps_sec))

        # (2) guard monotonicznosci — nieaktualny odczyt z innej maszyny.
        #
        # `known_same_reset_window`, a NIE `same_reset_window`: guard wstrzymuje zapis stanu,
        # wiec wolno mu sie odpalic tylko na DOWODZIE, ze okno jest to samo. Dwa NULL-e sa
        # brakiem dowodu, a wziete za dowod zamrazaly stan po kazdym resecie sesji i TRWALE
        # dla serii, ktore granicy nie maja nigdy (`spend:org`, `extra:usage`).
        #
        # Do guardu idzie `o.resets_at`, czyli to, co pomiar POWIEDZIAL — nie `next_r`.
        # Przeniesiona granica jest naszym WNIOSKIEM i uzyta tutaj odtworzylaby to samo
        # zamrozenie: kazdy pomiar bez granicy dostawalby z powrotem "to samo okno".
        if (known_same_reset_window(prev_r, o.resets_at, settings.reset_window_eps_sec)
                and prev_u is not None and o.utilization is not None
                and o.utilization < prev_u - settings.monotonic_eps):
            stale_read = True

        if not changed:
            # (1) dedup: piszemy tylko heartbeat
            age = (captured_at - st.last_captured_at).total_seconds()
            should_write = age >= settings.sample_heartbeat_sec

    if not should_write:
        # Probki nie piszemy, ale POMIAR SIE ODBYL. Bez tego pominiecie zapisu jest
        # nieodroznialne od ciszy klienta i UI raportuje falszywa starosc danych.
        if newest and not stale_read and st is not None:
            st.last_confirmed_at = captured_at
        return False, False

    # `o.resets_at`, nie `next_r`: PROBKA jest zapisem pomiaru i trzyma to, co pomiar podal.
    # Przenoszenie granicy nalezy do stanu (`series_state` jest odtwarzalnym cache'em, nie
    # faktem), a `window_start_index` czyta wlasnie probki i na braku granicy opiera sygnal
    # `passed` — wpisanie tam naszego wniosku zaslepiloby wykrywanie resetu w historii.
    sample = LimitSample(
        account_id=account.id, series_id=series.id, captured_at=captured_at,
        batch_id=batch.id, source=source, utilization=o.utilization,
        resets_at=o.resets_at, is_active=o.is_active, severity=o.severity,
        stale_read=stale_read, session_id=session_id,
        extra=o.extra or None,
    )
    db.add(sample)
    await db.flush()

    # (3) stan aktualizuje wylacznie najnowsza, niepodejrzana probka
    if newest and not stale_read:
        if st is None:
            st = SeriesState(account_id=account.id, series_id=series.id)
            db.add(st)
        st.prev_utilization = st.last_utilization
        st.prev_captured_at = st.last_captured_at
        st.last_sample_id = sample.id
        st.last_captured_at = captured_at
        st.last_confirmed_at = captured_at
        # value_since przesuwamy WYLACZNIE przy zmianie wartosci — inaczej zapis
        # heartbeatu (ta sama wartosc, nowy wiersz) udawalby zmiane i "niezmienne od"
        # resetowaloby sie co heartbeat, czyli pokazywaloby bzdure.
        if changed or st.value_since is None:
            st.value_since = captured_at
        st.last_utilization = o.utilization
        st.last_resets_at = next_r
        st.last_is_active = o.is_active
        st.last_severity = o.severity
        st.last_extra = o.extra or None
    return True, stale_read


async def ingest_one(db: AsyncSession, *, machine_name: str, payload: dict,
                     is_backlog: bool = False) -> dict:
    """Przetwarza jeden pomiar. Nigdy nie rzuca na danych — problem zapisujemy jako
    zdarzenie i zwracamy, ile udalo sie zapisac."""
    now = utcnow()
    client = payload.get("client") or {}
    hook = payload.get("hook") or {}
    meas = payload.get("measurement") or {}
    # token_meta jest OPCJONALNY od wersji 3 sondy: na macOS credentiale siedza w Keychain,
    # a pomiar juz od nich nie zalezy — brakuje wtedy tylko tagow planu, nie danych.
    token_meta = payload.get("token_meta") or {}
    acct = payload.get("account") or {}
    usage = payload.get("usage")

    machine = await get_or_create_machine(db, machine_name, client)

    source = meas.get("source") or "probe"
    if source not in ("probe", "cli_merged", "cli_usage_cache"):
        source = "cli_usage_cache"      # nieznana etykieta: nie wywracamy zapisu przez enum

    batch = IngestBatch(
        received_at=now, machine_id=machine.id,
        client_host=client.get("host"), config_dir_hash=client.get("config_dir_hash"),
        script_version=client.get("script_version"),
        hook_event=hook.get("event"), session_id=hook.get("session_id"),
        measurement_source=source, cache_age_s=meas.get("cache_age_s"),
        fresh_age_s=meas.get("fresh_age_s"), probe_ms=client.get("exec_ms"),
    )
    db.add(batch)
    await db.flush()

    if not acct.get("uuid"):
        batch.ok = False
        batch.error_kind = "no_account"
        await _event(db, level="warn", event_type="no_oauth_account", batch_id=batch.id,
                     message="Payload bez account.uuid — pomiar odrzucony")
        return {"samples_written": 0, "batch_id": batch.id, "ok": False,
                "account_uuid": None}

    account, created = await get_or_create_account(db, acct, token_meta)
    batch.account_id = account.id
    account.last_sample_at = now
    account.last_client_host = client.get("host") or account.last_client_host

    if created:
        await _event(db, level="info", event_type="account_created", account_id=account.id,
                     batch_id=batch.id, message="Wykryto nowe konto",
                     detail={"email": acct.get("email"), "org_type": acct.get("org_type")})

    # para (maszyna, konto) — detekcja zamiast zakazu
    ma = (await db.execute(
        select(MachineAccount).where(MachineAccount.machine_id == machine.id,
                                     MachineAccount.account_id == account.id)
    )).scalar_one_or_none()
    if ma is None:
        db.add(MachineAccount(machine_id=machine.id, account_id=account.id,
                              last_seen_at=now, samples=0))
        await _event(db, level="info", event_type="new_account_for_token",
                     account_id=account.id, batch_id=batch.id,
                     message="Maszyna %s po raz pierwszy raportuje to konto" % machine_name)
    else:
        ma.last_seen_at = now
        ma.samples = (ma.samples or 0) + 1

    # przelaczenie konta na tej maszynie — czyli /login
    prev = (await db.execute(
        select(IngestBatch).where(IngestBatch.machine_id == machine.id,
                                  IngestBatch.id != batch.id,
                                  IngestBatch.account_id.is_not(None))
        .order_by(IngestBatch.id.desc()).limit(1)
    )).scalar_one_or_none()
    if prev is not None and prev.account_id != account.id:
        await _event(db, level="info", event_type="account_switched", account_id=account.id,
                     batch_id=batch.id, message="Przelaczenie konta na maszynie %s" % machine_name,
                     detail={"from_account_id": prev.account_id, "to_account_id": account.id})

    if not isinstance(usage, dict):
        batch.ok = False
        batch.error_kind = "no_usage"
        await _event(db, level="warn", event_type="parse_error", account_id=account.id,
                     batch_id=batch.id, message="Brak obiektu usage w payloadzie")
        # account_uuid despite ok=False: the batch WAS assigned to an account, so
        # `last_batch_at` moved — and that is precisely what lets `freshness()` tell
        # "the client was silent" apart from "the client was alive and there is still no
        # sample" (unknown). This frame can flip a series into the failure state and must
        # reach subscribers.
        return {"samples_written": 0, "batch_id": batch.id, "ok": False,
                "account_uuid": account.account_uuid}

    rp, digest = await store_raw(db, usage)
    batch.raw_payload_id = rp.id
    batch.payload_sha256 = digest

    captured_at, skew = resolve_captured_at(payload.get("captured_at"), now, is_backlog)
    if skew:
        await _event(db, level="warn", event_type="clock_skew", account_id=account.id,
                     batch_id=batch.id, message="Czas klienta odrzucony: %s" % skew,
                     detail={"raw": payload.get("captured_at")})

    parsed = parse_usage(usage)
    if parsed.problems:
        await _event(db, level="warn", event_type="schema_drift", account_id=account.id,
                     batch_id=batch.id, message="Nieoczekiwany ksztalt odpowiedzi",
                     detail={"problems": parsed.problems})

    cache: dict[str, UsageSeries] = {}
    written = stale = 0
    new_series: list[str] = []
    for o in parsed.observations:
        series, s_created = await get_or_create_series(db, o, cache)
        if s_created:
            new_series.append(o.series_key)
        w, sr = await _write_observation(
            db, account=account, series=series, o=o, captured_at=captured_at,
            batch=batch, session_id=hook.get("session_id"), source=source,
            is_backlog=is_backlog,
            # Zwykly `get`: sonda ponizej wersji 4 tego pola nie przysyla i to jest
            # poprawny stan, nie brak danych.
            client_reason=meas.get("extra_usage_disabled_reason"),
        )
        written += int(w)
        stale += int(sr)

    if new_series:
        await _event(db, level="info", event_type="series_registered", account_id=account.id,
                     batch_id=batch.id, message="Zarejestrowano nowe serie",
                     detail={"series": new_series})
    if stale:
        await _event(db, level="info", event_type="stale_read", account_id=account.id,
                     batch_id=batch.id,
                     message="%d nieaktualnych odczytow — stan biezacy nietkniety" % stale)

    batch.samples_written = written
    return {"samples_written": written, "batch_id": batch.id, "ok": True,
            "account_uuid": account.account_uuid,
            "series_registered": new_series, "stale_reads": stale}
