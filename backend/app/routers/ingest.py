"""POST /api/ingest — jedyny endpoint bez SSO.

Dwie warstwy auth: filtr brzegowy X-Ingest-Key w Apache (odcina skanery zanim dotkna
Pythona) i Bearer per maszyna tutaj (prawdziwa autoryzacja).

Cap rozmiaru jest sprawdzany PRZED parsowaniem JSON — endpoint jest wystawiony w internecie,
a klient moze wyslac backlog po dlugiej przerwie.

Zapisy sa SERIALIZOWANE i kazdy pomiar ma WLASNA transakcje — patrz `_WRITE_LOCK`.
"""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_ingest_token
from app.config import settings
from app.db import get_session
from app.schemas import IngestResult
from app.services.events import publish_accounts
from app.services.ingest import ingest_one, utcnow

router = APIRouter()

# Serializacja ZAPISOW ingestu w obrebie procesu.
#
# `ingest_one` zaczyna od SELECT-a na `machines` (services/ingest.py:61) — to otwiera read
# view transakcji — a potem mutuje ten sam, JEDEN wiersz (`last_seen_at`, `batches`) i
# wstawia do `ingest_batches` dziecko z FK na niego, wiec wstawka musi zalozyc blokade na
# wiersz rodzica. MariaDB od 11.6.2 ma innodb_snapshot_isolation=ON, a wtedy proba blokady
# wiersza zmienionego PO otwarciu read view nie czeka, tylko zwraca twardy ER_CHECKREAD
# (1020). Dwa nakladajace sie POST-y z tej samej maszyny to tutaj norma (rownolegle hooki
# sesji subagentowych): dawalo to 36x 1020 i 9x deadlock 1213, a kazdy z nich
# cofal CALE zadanie — batch, raw_payload, probki, zdarzenia i doklejony backlog.
#
# Lock obejmuje CALY span "read view otwarty -> commit". Skrocenie go do samego UPDATE-u
# nic nie daje, bo read view powstaje juz przy pierwszym SELECT. Sprawdzone, ze span
# naprawde zaczyna sie pod lockiem: `require_ingest_token` (app/auth.py:37) nie dotyka bazy,
# a `AsyncSession` nie otwiera transakcji przed pierwszym `execute()`.
#
# Gorace wiersze to nie tylko `machines`: ten sam ksztalt select-then-mutate maja
# `get_or_create_account` (services/ingest.py:73), `get_or_create_series` (:100),
# `store_raw` (:134) i upsert pary `MachineAccount` w `ingest_one`. `raw_payloads` jest
# gorace WLASNIE przy bezczynnosci, bo odpowiedz jest wtedy bajt-identyczna i wpada w ten
# sam wiersz — wspolny dla wszystkich maszyn i wszystkich kont. Dlatego lock jest jeden i
# obejmuje calego `ingest_one`, a nie sam fragment o maszynie.
#
# DLACZEGO LOCK W PROCESIE WYSTARCZA: entrypoint.sh startuje SWIADOMIE jeden proces
# uvicorna, bo broker SSE (services/events.py) zyje w pamieci procesu — patrz AGENTS.md,
# "Pulapki tego srodowiska". Przy --workers > 1 ten lock milczy i 1020 wraca. To ten sam
# warunek, ktory AGENTS.md juz zakazuje lamac, ale tam lamie sie halasliwie (SSE przestaje
# dochodzic), a tu CICHO, wiec jest wymieniony wprost.
#
# To NIE zastepuje retry na 1020/1213 (F4, swiadomie poza zakresem): lock usuwa zrodlo
# kolizji WEWNATRZ procesu, nie odpornosc na kolizje z zewnatrz.
_WRITE_LOCK = asyncio.Lock()


@asynccontextmanager
async def _ingest_tx(db: AsyncSession) -> AsyncIterator[None]:
    """Jeden pomiar = jedna transakcja, w calosci pod lockiem, z commitem I rollbackiem
    WEWNATRZ. Rollback musi byc pod lockiem: dopoki nie wrocil, sesja jest zatruta i
    nastepny czekajacy dostalby PendingRollbackError zamiast szansy na zapis."""
    async with _WRITE_LOCK:
        try:
            yield
            await db.commit()
            # `commit()` NIE wygasza obiektow, bo SessionLocal ma expire_on_commit=False
            # (app/db.py:14). To ustawienie jest dobrane pod JEDNA transakcje na zadanie —
            # wtedy po commicie nikt z tej sesji juz nic nie czytal. Tutaj transakcji jest
            # N, a lock jest ODDAWANY miedzy nimi, wiec w szczelinie commituje inne zadanie.
            # Bez tej linii kolejny `select()` dostalby z identity map TEN SAM obiekt z
            # wartosciami sprzed cudzego commitu (SQLAlchemy zwraca instancje z identity
            # map i odrzuca swieze kolumny wiersza) i po cichu nadpisalby czyjs inkrement
            # na `machines.batches`, `raw_payloads.seen_count`, `machine_accounts.samples`,
            # a guard monotonicznosci porownalby sie ze starym `series_state`.
            #
            # Ta linia to doslownie to, co robi domyslne expire_on_commit=True — czyli
            # przywrocenie domyslki SQLAlchemy dokladnie dla commitow, ktore wprowadza ten
            # podzial, bez zmiany jej dla reszty aplikacji. Nie jest wiec obrona przed
            # hipoteza: usuwa zaleznosc sciezki spojnosci danych od tego, czy refcounting
            # zdazyl wyrzucic obiekt z weak identity map — niezapisanego nigdzie warunku,
            # ktorego zaden test nie chroni i ktory psuje sie CICHO.
            #
            # Sciezka rollback nie potrzebuje odpowiednika: `Session.rollback()` wygasza
            # wszystko sam, zawsze.
            db.expire_all()
        except BaseException:
            # BaseException, nie Exception: CancelledError NIE dziedziczy po Exception od
            # 3.8, a leci tu realnie — Starlette anuluje zadanie, gdy klient sie rozlaczy
            # (a sonda ma timeout 5 s), uvicorn anuluje zadania w locie na SIGTERM.
            # Przepuszczenie go z otwarta transakcja trzymaloby blokade na wierszu
            # `machines` do konca zycia poolowanego polaczenia.
            await db.rollback()
            raise


@router.post("/ingest", response_model=IngestResult)
async def ingest(
    request: Request,
    machine: str = Depends(require_ingest_token),
    db: AsyncSession = Depends(get_session),
) -> IngestResult:
    raw = await request.body()
    if len(raw) > settings.max_ingest_body_bytes:
        logger.warning("Ingest odrzucony: body {} B > limit {} B",
                       len(raw), settings.max_ingest_body_bytes)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"reason": "body-too-large", "limit": settings.max_ingest_body_bytes},
        )
    try:
        import json
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"reason": "invalid-json"})
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"reason": "payload-not-object"})

    async with _ingest_tx(db):
        result = await ingest_one(db, machine_name=machine, payload=payload)
    touched: list[str] = [result.get("account_uuid")]

    # Backlog: partiami, z twardym capem. Klient obcina spool DOPIERO po potwierdzeniu,
    # ile wpisow przyjelismy — dzieki temu awaria w polowie nie gubi danych.
    #
    # Kazdy wpis ma WLASNA transakcje, bo `backlogAccepted` jest dla sondy DLUGOSCIA
    # PREFIKSU do obciecia spoola (`trim_spool` robi `lines[accepted:]`), a nie liczba
    # zapisanych pomiarow. Jedna wspolna transakcja znaczylaby, ze blad na wpisie k cofa
    # rowniez wpisy 0..k-1, ktore juz policzylismy — i sonda usunelaby je ze spoola.
    backlog = payload.get("backlog") or []
    accepted = 0
    if isinstance(backlog, list):
        for entry in backlog[: settings.max_backlog_entries]:
            if not isinstance(entry, dict):
                # Liczymy, mimo ze nie zapisujemy. `accepted` jest POZYCJA, nie licznikiem
                # sukcesow: pominiecie bez inkrementu przesuwa numeracje wszystkich
                # kolejnych wpisow i sonda obcina zly kawalek spoola — gubi dane albo
                # wysyla je drugi raz. Takiego wpisu nie da sie zapisac nigdy, wiec
                # przyznanie sie do niego jest poprawne.
                accepted += 1
                continue
            try:
                async with _ingest_tx(db):
                    r = await ingest_one(db, machine_name=machine, payload=entry,
                                         is_backlog=True)
            except Exception as exc:                       # noqa: BLE001
                # `break`, nie `continue`: `accepted` jest prefiksem, wiec doliczenie wpisu,
                # ktorego NIE zapisalismy, kazaloby sondzie usunac go ze spoola. Stajemy na
                # pierwszym niezapisanym — ogon wroci nastepnym zadaniem.
                #
                # ZNANY KOSZT, swiadomy: wpis, ktorego NIGDY nie da sie zapisac (trwale
                # zly ksztalt), wstrzymuje ogon na stale. Surowa tresc jest w bazie i w
                # logu, ale decyzja "co z takim wpisem" — kwarantanna po N probach albo
                # wyniki per wpis na drucie — rusza schemat albo kontrakt v3 i nalezy do
                # usera, nie do tej petli.
                #
                # Exception, a NIE BaseException: CancelledError musi leciec dalej i
                # przerwac zadanie, zamiast wygladac jak jeden zly wpis.
                logger.warning("Backlog: wpis {} odrzucony, ogon zostaje w spoolu: {}",
                               accepted, exc)
                break
            # Dopiero PO wyjsciu z `_ingest_tx`, czyli po udanym commicie — inaczej
            # liczylibysmy wpisy, ktore rollback wlasnie cofnal.
            touched.append(r.get("account_uuid"))
            accepted += 1

    # SSE stream. Three things here are deliberate:
    #
    # 1. AFTER the last commit. A receiver processes a frame within milliseconds and before
    #    the commit would see the pre-write state — then sit on it until the next poll.
    # 2. The gate is "batch assigned to an account", NOT `samples_written > 0`. On an
    #    unchanged value dedup writes no sample but does move `last_confirmed_at` — which
    #    is exactly what keeps the state `live`. Gating on the sample count would mute
    #    events in the most common case of all: when nothing is changing.
    # 3. An exception must NOT bring ingest down. The probe is the only code sitting in
    #    the path of your actual work; the chart may break, the session may not.
    try:
        await publish_accounts(db, [u for u in touched if u])
    except Exception as exc:                               # noqa: BLE001
        logger.warning("SSE publish failed: {}", exc)

    return IngestResult(
        ok=bool(result.get("ok")),
        samples_written=result.get("samples_written", 0),
        backlog_accepted=accepted,
        server_now=utcnow(),
        batch_id=result.get("batch_id"),
        series_registered=result.get("series_registered", []),
    )
