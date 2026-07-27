"""Delta 1 h nie przechodzi przez granice resetu.

Regresja: zaraz po resecie sesji hero pisalo „−46 pp w ciagu godziny", bo punktem
odniesienia byla probka z POPRZEDNIEGO okna — i stalo tak przez cala godzine, akurat wtedy,
gdy zuzycie realnie roslo od zera.
"""
from datetime import datetime, timedelta

from tests.test_ingest_e2e import (  # noqa: F401 — fixture `db` przychodzi razem z nimi
    db, ingest_one, payload, utcnow, with_util,
)

from app.services.status import _delta_1h, build_status

NOW = datetime(2026, 7, 27, 12, 0, 0)
B_NEXT = NOW + timedelta(hours=4)      # granica biezacego okna
B_PREV = NOW - timedelta(hours=1)      # granica, ktora juz minela


def przebieg(*items):
    """(minut temu, utilization, granica) -> wiersze, rosnaco po czasie."""
    return [(NOW - timedelta(minutes=m), u, r) for m, u, r in items]


def test_bez_wartosci_biezacej_nie_ma_delty():
    assert _delta_1h(przebieg((30, 10.0, B_NEXT)), now=NOW, current=None,
                     resets_at=B_NEXT) == (None, None)


def test_po_minieciu_granicy_delty_nie_ma():
    """Wszystkie probki naleza do poprzedniego okna — ten sam warunek, po ktorym
    freshness() orzeka inferred_reset. Bez tego wychodzilo wlasnie „−46 pp"."""
    rows = przebieg((50, 46.0, B_PREV), (20, 46.0, B_PREV))
    assert _delta_1h(rows, now=NOW, current=0.0, resets_at=B_PREV) == (None, None)


def test_jedna_probka_w_oknie_to_nie_rozpietosc():
    rows = przebieg((60, 46.0, B_PREV), (2, 3.0, B_NEXT))
    assert _delta_1h(rows, now=NOW, current=3.0, resets_at=B_NEXT) == (None, None)


def test_baseline_przyciety_do_biezacego_okna():
    rows = przebieg((60, 46.0, B_PREV), (30, 46.0, B_PREV),
                    (5, 2.0, B_NEXT), (1, 4.0, B_NEXT))
    d, t0 = _delta_1h(rows, now=NOW, current=4.0, resets_at=B_NEXT)
    assert d == 2.0, "delta liczona od pierwszej probki PO resecie"
    assert d != -42.0, "baseline z poprzedniego okna — dokladnie ten blad"
    assert t0 == NOW - timedelta(minutes=5)


def test_cala_godzina_w_jednym_oknie():
    rows = przebieg((58, 12.0, B_NEXT), (30, 20.0, B_NEXT), (1, 31.0, B_NEXT))
    d, t0 = _delta_1h(rows, now=NOW, current=31.0, resets_at=B_NEXT)
    assert d == 19.0
    assert (NOW - t0).total_seconds() >= 45 * 60, "UI podpisze to jako „w ciagu godziny"


def test_reset_w_toku_gdy_granica_jest_wyzerowana():
    """Sonda zeruje przedawniona granice, wiec `resets_at` nie odslania resetu ani
    w probkach, ani w stanie serii — zostaje data probki i spadek."""
    rows = przebieg((50, 46.0, B_PREV), (4, 0.0, None), (1, 3.0, None))
    d, t0 = _delta_1h(rows, now=NOW, current=3.0, resets_at=None)
    assert (d, t0) == (3.0, NOW - timedelta(minutes=4))


def test_brak_probek_to_brak_delty():
    assert _delta_1h([], now=NOW, current=5.0, resets_at=B_NEXT) == (None, None)


def test_zaokraglenie_do_czterech_miejsc():
    rows = przebieg((30, 1.0, B_NEXT), (1, 4.123456, B_NEXT))
    d, _ = _delta_1h(rows, now=NOW, current=4.123456, resets_at=B_NEXT)
    assert d == 3.1235


# --------------------------------------------------------------------------- e2e
async def test_status_po_resecie_nie_pokazuje_ujemnej_delty(db, monkeypatch):
    """Ta sama sciezka co w produkcji: ingest -> series_state -> /api/status."""
    import app.services.ingest as ing
    import app.services.status as stat

    t0 = utcnow().replace(microsecond=0)     # probki i tak sa obcinane do sekund

    async def ingest_at(minutes, five_hour, resets_at):
        when = t0 + timedelta(minutes=minutes)
        monkeypatch.setattr(ing, "utcnow", lambda: when)
        u = with_util(five_hour=five_hour)
        u["five_hour"]["resets_at"] = resets_at.isoformat() + "Z"
        for lim in u["limits"]:
            if lim.get("kind") == "session":
                lim["percent"] = five_hour
                lim["resets_at"] = resets_at.isoformat() + "Z"
        await ingest_one(db, machine_name="desktop",
                         payload=payload(usage=u, captured_at=when))

    # granice podajemy jawnie: te z fixture'a dawno minely, a sonda odrzuca wygasle okno
    await ingest_at(0, 50.0, t0 + timedelta(minutes=20))
    await ingest_at(25, 2.0, t0 + timedelta(hours=5, minutes=20))
    await ingest_at(35, 5.0, t0 + timedelta(hours=5, minutes=20))
    await db.commit()

    monkeypatch.setattr(stat, "utcnow", lambda: t0 + timedelta(minutes=36))
    st = await build_status(db)
    sesje = [s for s in st.accounts[0].series if s.bucket_key == "five_hour"]
    assert sesje, "scenariusz nie wyprodukowal serii sesji — test bylby pusty"

    for s in sesje:
        assert s.delta_pct_1h == 3.0, "delta liczona od pierwszej probki po resecie"
        assert s.delta_pct_1h != -45.0, "baseline sprzed resetu — regresja"
        assert s.delta_from == t0 + timedelta(minutes=25)
