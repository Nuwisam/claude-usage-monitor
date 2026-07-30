"""Nieznana granica okna a stan biezacy (F1 + F2).

Jeden defekt w dwoch punktach.

  * F2 UZBRAJAL: `st.last_resets_at = o.resets_at` bylo bezwarunkowe, wiec pomiar bez granicy
    kasowal granice znana.
  * F1 ODPALAL: guard monotonicznosci pytal o "to samo okno" TYM SAMYM boolem, ktorym dedup
    pyta "czy cokolwiek sie zmienilo". Dla dedupu "brak granicy po obu stronach" znaczy
    poprawnie "brak zmiany" (na tym stoja serie bez granicy). Dla guardu znaczy "nie wiem" —
    a niewiedza wzieta za dowod zamrazala stan: spadek 92% -> 0% szedl do bazy z flaga
    `stale_read`, `series_state` stal.

Produkcja (konto 1, 2026-07-30): piec probek 11:51:05-11:55:13 UTC ze `stale_read=1`, stan
zamrozony na 92% przy realnym zuzyciu 0-2%; odblokowane o 11:56:15, gdy Anthropic podal nowa
granice 16:49:59. Powtarzalne przy KAZDYM resecie okna sesji, bo dla swiezego okna z 0%
zuzycia Anthropic granicy nie podaje. Dla `spend:org` i `extra:usage` granica nie przychodzi
NIGDY, wiec tam zamrozenie bylo trwale.

Znaczniki czasu trzymamy w CLOCK_SKEW_TOLERANCE_SEC (300 s) od "teraz" — poza tym progiem
`resolve_captured_at` slusznie odrzuca zegar klienta i podstawia czas serwera, a wtedy test
mierzylby co innego. Mikrosekundy zerujemy, bo `parse_ts` tnie do pelnych sekund.
"""
import json
from datetime import timedelta

from sqlalchemy import func, select

from app.models import IngestEvent, LimitSample, SeriesState, UsageSeries
from app.services.ingest import ingest_one
from app.services.status import build_status
from tests.test_ingest_e2e import REAL, db, payload, utcnow   # noqa: F401


def five_hour(util, resets_at):
    """Realny payload z podmieniona wartoscia i granica bucketu `five_hour`.

    `resets_at=None` odwzorowuje DOKLADNIE to, co robi sonda regula `reset-w-toku`: procent
    zostaje (jest swiezy i prawdziwy), zerowane jest samo pole granicy, bo pochodzi
    z przedawnionego cache (`client/usage-probe.py`, `sanitize`)."""
    u = json.loads(json.dumps(REAL))
    u["five_hour"]["utilization"] = util
    u["five_hour"]["resets_at"] = None if resets_at is None else resets_at.isoformat()
    return u


def spend(percent):
    """Seria `spend:org` — na koncie Team to JEST wiazacy limit, i granicy nie ma NIGDY."""
    u = json.loads(json.dumps(REAL))
    u["spend"]["percent"] = percent
    return u


async def stan(db, series_key) -> SeriesState:
    return (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().one()


async def probki(db, series_key) -> int:
    return (await db.execute(
        select(func.count()).select_from(LimitSample)
        .join(UsageSeries, UsageSeries.id == LimitSample.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalar_one()


async def flagi_stale(db, series_key) -> list[bool]:
    return list((await db.execute(
        select(LimitSample.stale_read)
        .join(UsageSeries, UsageSeries.id == LimitSample.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().all())


async def zdarzenia(db) -> set[str]:
    return {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}


async def seria_w_status(db, series_key):
    st = await build_status(db)
    return {s.series_key: s for s in st.accounts[0].series}[series_key]


# --------------------------------------------------------------------- F1: brak dowodu
async def test_reset_bez_granicy_nie_zamraza_stanu(db):
    """Odtworzenie produkcji, trzy kroki i wszystkie trzy sa potrzebne.

    t0  znamy granice i 92% zuzycia — normalne okno w toku;
    t1  granica minela, sonda ja wyzerowala, procent jest wciaz swiezy (to UZBRAJALO F1);
    t2  okno sie przewinelo, zuzycie 0%, Anthropic nadal nie podaje granicy (to ODPALALO).

    Przed naprawa stan zostawal na 92% przez 9,5 minuty realnego czasu, czyli tak dlugo, jak
    Anthropic milczal o nowej granicy.
    """
    t0 = (utcnow() - timedelta(seconds=240)).replace(microsecond=0)
    granica = t0 + timedelta(seconds=30)          # minie miedzy t0 i t1
    t1, t2 = t0 + timedelta(seconds=60), t0 + timedelta(seconds=120)

    for ts, u in ((t0, five_hour(92.0, granica)),
                  (t1, five_hour(92.0, None)),
                  (t2, five_hour(0.0, None))):
        await ingest_one(db, machine_name="desktop", payload=payload(usage=u, captured_at=ts))
        await db.commit()

    st = await stan(db, "bucket:five_hour")
    assert float(st.last_utilization) == 0.0, \
        "brak granicy po obu stronach nie jest dowodem, ze to wciaz to samo okno"
    assert st.last_captured_at == t2
    assert "stale_read" not in await zdarzenia(db)

    # To samo widziane przez /api/status, czyli objaw zgloszony przez operatora:
    # "zuzycie limitu z sufitu" obok "czas resetu nieznany".
    assert (await seria_w_status(db, "bucket:five_hour")).utilization == 0.0


async def test_seria_bez_granicy_moze_spasc(db):
    """`spend:org` i `extra:usage` nie maja granicy NIGDY — dla nich zamrozenie bylo TRWALE.
    Pierwszy prawdziwy spadek (rozliczenie miesiaca, doladowanie kredytow) zatrzymywalby stan
    na zawsze, bo granica, ktora go odblokuje, nie przyjdzie nigdy."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=spend(60), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=spend(5), captured_at=t0 + timedelta(seconds=60)))
    await db.commit()

    assert float((await stan(db, "spend:org")).last_utilization) == 5.0, \
        "seria bez granicy nie moze zamarznac na zawsze"
    # Sam stan to nie wszystko: probka z `stale_read` wypada rowniez z historii i z baseline'u
    # delty (services/status.py, `_recent_samples` filtruje po tej fladze).
    assert not any(await flagi_stale(db, "spend:org"))


async def test_guard_nadal_zamraza_gdy_obie_granice_sa_znane(db):
    """Naprawa F1 nie ma prawa wylaczyc guardu. Najprostszy "fix" — nie odpalaj go nigdy —
    zdejmuje jedyna ochrone przed maszyna, ktora ma starszy cache tokenu, a odczyt swiezy
    z pozoru (wlasny `captured_at`, wiec `newest` go nie zatrzyma).

    Granica po stronie laptopa rozni sie o sekunde: to KOLYSANIE, a nie inne okno (zasada 9)."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    granica = t0 + timedelta(hours=3)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(60.0, granica), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="laptop",
                     payload=payload(usage=five_hour(40.0, granica + timedelta(seconds=1)),
                                     captured_at=t0 + timedelta(seconds=30)))
    await db.commit()

    assert float((await stan(db, "bucket:five_hour")).last_utilization) == 60.0, \
        "spadek przy DOWODNIE tym samym oknie to nieaktualny odczyt, stan sie nie cofa"
    assert "stale_read" in await zdarzenia(db)


async def test_brak_granicy_po_obu_stronach_nadal_deduplikuje(db):
    """Granica, ktorej naprawa nie moze przekroczyc: `same_reset_window(None, None) is True`
    jest POPRAWNE dla dedupu. Gdyby guard naprawiono przez zmiane odpowiedzi tej funkcji
    (zamiast dodania osobnego pytania o dowod), `spend:org` i `extra:usage` dostawalyby wiersz
    przy KAZDYM probie — a sonda odpytuje co ~60 s."""
    t0 = (utcnow() - timedelta(seconds=240)).replace(microsecond=0)
    for i in range(3):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(usage=spend(60),
                                         captured_at=t0 + timedelta(seconds=60 * i)))
        await db.commit()

    assert await probki(db, "spend:org") == 1, \
        "seria bez granicy o niezmienionej wartosci to jeden wiersz, nie trzy"


# ------------------------------------------------------------ F2: co ma trzymac stan
async def test_wciaz_wazna_granica_przezywa_pomiar_bez_granicy(db):
    """Pomiar bez granicy nie kasuje granicy, ktora JESZCZE NIE MINELA — ta nadal opisuje
    trwajace okno, a z niej zyje countdown, `secondsToReset`, przyciecie delty do okna
    i wnioskowanie `inferred_reset`.

    Wartosc MUSI sie zmienic. Przy niezmienionej dedup nie zapisuje probki, blok stanu w ogole
    sie nie wykonuje i granica zostawalaby w stanie sama z siebie — test przechodzilby prozno,
    niezaleznie od pilnowanej linii."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    granica = t0 + timedelta(hours=3)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(40.0, granica), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(45.0, None),
                                     captured_at=t0 + timedelta(seconds=60)))
    await db.commit()

    st = await stan(db, "bucket:five_hour")
    assert float(st.last_utilization) == 45.0, "blok stanu MUSIAL sie wykonac"
    assert st.last_resets_at == granica, "granica z przyszlosci opisuje TRWAJACE okno"

    # Objaw operatora od drugiej strony: `resetNote()` w UI mowi "czas resetu nieznany"
    # dokladnie wtedy, gdy `resetsAt` jest null przy niezerowym zuzyciu.
    seria = await seria_w_status(db, "bucket:five_hour")
    assert seria.resets_at == granica and seria.seconds_to_reset > 0


async def test_przedawniona_granica_nie_zostaje_w_stanie(db):
    """Druga strona tego samego kompromisu i granica, za ktora nie wolno przejsc.

    Przenosimy WYLACZNIE granice, ktora jeszcze nie minela. Granica, ktora minela, nie mowi
    o oknie biezacym nic — trzymanie jej dawaloby pewnie wygladajaca nieprawde ("reset minal
    o 20:00" przy 95% zuzycia w oknie, ktore skonczy sie 5 godzin pozniej), czyli dokladnie
    to, czego zabrania zasada 4. Wolimy widoczna niewiedze niz pewna zla liczbe."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    granica = t0 + timedelta(seconds=30)          # minie przed drugim pomiarem
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(92.0, granica), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(95.0, None),
                                     captured_at=t0 + timedelta(seconds=60)))
    await db.commit()

    st = await stan(db, "bucket:five_hour")
    assert float(st.last_utilization) == 95.0, "blok stanu MUSIAL sie wykonac"
    assert st.last_resets_at is None, "przedawniona granica nie opisuje biezacego okna"

    seria = await seria_w_status(db, "bucket:five_hour")
    assert seria.resets_at is None and seria.seconds_to_reset is None


async def test_pomiar_bez_granicy_nie_lamie_dedupu(db):
    """Dedup pyta "czy ZMIENI SIE STAN". Gdyby porownywal sie z surowym `o.resets_at`, to przy
    przenoszonej granicy kazdy kolejny pomiar bez granicy wygladalby jak zmiana (znana granica
    w stanie vs NULL w pomiarze) i pisalby wiersz co ~60 s — czyli naprawa F2 zepsulaby
    mechanizm, ktory ma z nia wspolzyc."""
    t0 = (utcnow() - timedelta(seconds=240)).replace(microsecond=0)
    granica = t0 + timedelta(hours=3)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(40.0, granica), captured_at=t0))
    await db.commit()
    po_pierwszym = await probki(db, "bucket:five_hour")
    assert po_pierwszym == 1

    for i in (1, 2):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(usage=five_hour(40.0, None),
                                         captured_at=t0 + timedelta(seconds=60 * i)))
        await db.commit()

    assert await probki(db, "bucket:five_hour") == po_pierwszym, \
        "niezmieniona wartosc przy przeniesionej granicy to nadal brak zmiany"
