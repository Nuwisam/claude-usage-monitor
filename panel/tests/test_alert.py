"""Zablokowana sesja na ekranie: karta, zwiniecie do trojkata, koszt zmiany sceny.

Zegar jest wstrzykiwany wszedzie, bo inaczej test wypalenia okna trwalby 300 s.
"""
import pytest

from panel import app as app_mod, config as C, fmt, render, status, surface, theme


class Zegar:
    """Monotonic pod kontrola testu."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def skok(self, sekundy):
        self.t += sekundy


def cfg(**kw):
    # Blysk domyslnie wylaczony: testy karty maja sprawdzac karte, nie to, co ja
    # poprzedza. Testy blysku wlaczaja go jawnie.
    d = {"stream_token": "t", "account_1": {"uuid": "konto-a"},
         "account_2": {"uuid": "konto-b"}, "alert_flash_sec": 0}
    d.update(kw)
    return C.Config(d)


def po_migotaniu(a, z):
    """Renderuje, az baner przestanie migac. Okno uzbraja sie przy PIERWSZYM renderze
    po pojawieniu sie klucza, wiec sam skok zegara nie wystarczy."""
    ekran = a.screen()
    z.skok(a.cfg.alert_flash_sec + 1)
    return a.screen()


NOW = "2026-08-05T21:07:00Z"


def app(zegar, **kw):
    a = app_mod.App(cfg(**kw), monotonic=zegar)
    a.clock.anchor(NOW)
    return a


def ramka(*entries):
    return {"contractVersion": 3, "serverNow": NOW, "alerts": list(entries)}


def wpis(**kw):
    base = {"key": "s__main__k", "reason": "permission", "project": "proj",
            "machine": "laptop", "tool": "Bash", "since": NOW}
    base.update(kw)
    return base


# --- wejscie i wyjscie karty ------------------------------------------------

def test_karta_wchodzi_po_debounce():
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    assert a.screen().alert is None, "debounce ma stlumic blysk przy zgodzie od razu"
    z.skok(a.cfg.blocked_debounce_sec)
    assert a.screen().alert is not None


def test_karta_niesie_projekt_narzedzie_i_maszyne():
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    card = a.screen().alert
    assert card.project == "proj"
    assert card.label == "Bash · maszyna: laptop"
    assert card.title == "CZEKA NA ZGODĘ"


def test_pusty_zbior_gasi_karte_dopiero_po_lingerze():
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert a.screen().alert is not None
    a.on_event("alert", ramka())
    # Linger zaczyna sie w pierwszym renderze PO oproznieniu zbioru, nie w chwili
    # przyjscia ramki: liczy sie moment, w ktorym scena mialaby sie przelaczyc.
    assert a.screen().alert is not None, "linger ogranicza liczbe przejsc sceny"
    z.skok(a.cfg.blocked_linger_sec + 1)
    assert a.screen().alert is None


def test_karta_w_lingerze_jest_zamrozona():
    """Bez zamrozenia 'czeka N min' tykaloby na prompcie, na ktory juz odpowiedziales,
    a kazdy przeskok tego napisu to pelna klatka na AX206."""
    z = Zegar()
    a = app(z)
    # Tak dobrane, zeby przy pierwszym renderze bylo 119 s, a piec sekund pozniej 124 s.
    a.on_event("alert", ramka(wpis(since="2026-08-05T21:05:06Z")))
    z.skok(5)
    przed = a.screen().alert.waited
    assert przed == "czeka 1 min"
    a.on_event("alert", ramka())
    a.screen()                      # uzbrojenie lingera
    z.skok(5)                       # przekracza granice minuty — bez zamrozenia "2 min"
    assert a.screen().alert.waited == przed


def test_wypalenie_okna_zwija_karte_do_trojkata():
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(wpis(since="2026-08-05T21:06:50Z")))   # 10 s temu
    z.skok(5)
    assert a.screen().alert is not None
    # Okno liczy sie od `since` z SERWERA, wiec przesuwamy zegar serwera, nie tylko
    # monotonic: inaczej test sprawdzalby cos innego niz produkcja.
    a.clock.anchor("2026-08-05T21:08:00Z")
    a.first_data_at = z.t
    a.screen()                      # okno wypalone: karta wchodzi w linger
    z.skok(a.cfg.blocked_linger_sec + 1)
    ekran = a.screen()
    assert ekran.alert is None, "po alert_takeover_sec karta ma oddac ekran"
    assert ekran.bands[0].alert, "ale wpis zyje dalej i musi byc widoczny jako trojkat"


def test_nowa_blokada_dostaje_nowe_okno():
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(wpis(key="stary", since="2026-08-05T21:00:00Z")))
    z.skok(5)
    assert a.screen().alert is None, "stara blokada ma juz wypalone okno"
    a.on_event("alert", ramka(wpis(key="stary", since="2026-08-05T21:00:00Z"),
                              wpis(key="nowy", since=NOW)))
    z.skok(5)
    assert a.screen().alert is not None


# --- blysk ------------------------------------------------------------------

def fazy(a, z, sekundy):
    """Wartosci `blink` w kolejnych sekundach."""
    out = []
    for _ in range(sekundy):
        out.append(a.screen().alert.blink)
        z.skok(1)
    return out


def test_baner_miga_a_potem_przestaje():
    z = Zegar(1000.0)
    a = app(z, alert_flash_sec=6)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    obraz = fazy(a, z, 10)
    assert True in obraz[:6] and False in obraz[:6], "w oknie ma migac"
    assert not any(obraz[7:]), "po oknie baner stoi"


def test_migotanie_nie_wraca_przy_tykaniu_karty():
    z = Zegar()
    a = app(z, alert_flash_sec=6)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    po_migotaniu(a, z)
    for _ in range(5):
        z.skok(60)
        assert not a.screen().alert.blink


def test_druga_blokada_znow_zapala_migotanie():
    z = Zegar()
    a = app(z, alert_flash_sec=6)
    a.on_event("alert", ramka(wpis(key="a")))
    z.skok(5)
    po_migotaniu(a, z)
    a.on_event("alert", ramka(wpis(key="a"), wpis(key="b")))
    z.skok(5)
    assert any(fazy(a, z, 4))


def test_migotanie_da_sie_wylaczyc():
    z = Zegar()
    a = app(z, alert_flash_sec=0)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert not any(fazy(a, z, 4))


def test_infinity_miga_przez_cale_zycie_karty():
    z = Zegar(1000.0)
    a = app(z, alert_flash_sec="infinity")
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    obraz = fazy(a, z, 60)
    assert any(obraz) and not all(obraz), "ma migac, a nie stac na czerwono"
    assert any(obraz[-6:]), "po minucie nadal miga"


@pytest.mark.parametrize("raw,oczekiwane", [
    (20, 20.0), ("infinity", float("inf")), ("INF", float("inf")),
    (0, 0.0), (-5, 0.0), ("bzdura", 0.0), (None, 0.0), (float("nan"), 0.0),
])
def test_seconds(raw, oczekiwane):
    """Wartosci pochodza z recznie edytowanego panel.json i ida do POROWNANIA —
    goly string wywroci tick TypeError-em. Smieci maja znaczyc wartosc domyslna."""
    assert app_mod.seconds(raw) == oczekiwane


def test_takeover_infinity_nie_zwija_karty():
    z = Zegar()
    a = app(z, alert_takeover_sec="infinity")
    a.on_event("alert", ramka(wpis(since="2020-01-01T00:00:00Z")))
    z.skok(5)
    assert a.screen().alert is not None, "karta ma stac, dopoki nie odpowiesz"


def test_takeover_zero_daje_od_razu_trojkat():
    z = Zegar()
    a = app(z, alert_takeover_sec=0)
    a.first_data_at = z.t
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    ekran = a.screen()
    assert ekran.alert is None and ekran.bands[0].alert


def test_smieci_w_progach_nie_wywracaja_ticku():
    """Regresja: `age >= "infinity"` to TypeError, czyli martwy tick pod pythonw,
    bez konsoli, ktora by go pokazala."""
    z = Zegar()
    a = app(z, alert_takeover_sec="bzdura", alert_flash_sec="bzdura")
    a.first_data_at = z.t
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert a.screen() is not None


def test_migniecie_banera_miesci_sie_w_ticku():
    """Sedno poprawki: pelnoekranowy blysk to pelna klatka (1,87 s na Turingu),
    czyli powolne zamalowanie zamiast blysku. Sam baner musi byc na tyle tani,
    zeby zdazyc w sekunde."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    blokady = status.parse_frame(ramka(wpis()))
    R = render.Renderer()
    a = R.frame(render.ScreenState(alert=render.alert_state(blokady, now_ms))).rgb565("be")
    b = R.frame(render.ScreenState(
        alert=render.alert_state(blokady, now_ms, blink=True))).rgb565("be")
    rects = surface.coalesce(surface.dirty_tiles(a, b, 480, 320), surface.TILE)
    nbytes = sum((x1 - x0) * (y1 - y0) * 2 for x0, y0, x1, y1 in rects)
    assert nbytes / len(a) < 0.25, "migniecie brudzi %.1f%% klatki" % (nbytes / len(a) * 100)
    assert nbytes * 6.1e-6 < 0.5, "%.0f ms na drucie — nie zdazy w ticku" % (nbytes * 6.1e-3)


# --- pierwszenstwo ----------------------------------------------------------

def test_alert_bije_holding():
    """Alert jest wiadomoscia, na ktora czekasz — nie ma powodu, zeby czekal na
    uplyw progu splash."""
    z = Zegar()
    a = app(z)
    assert a.holding()
    a.on_event("alert", ramka(wpis()))
    assert not a.holding()


def test_alert_bije_niezgodny_kontrakt():
    z = Zegar()
    a = app(z)
    a.contract_mismatch = 4
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    ekran = a.screen()
    assert ekran.alert is not None
    assert "kontrakt" in ekran.alert.footer, "informacja o kontrakcie schodzi do stopki"


def test_po_karcie_nie_wracamy_do_holding():
    """Regresja: sam latch `ever_painted` nie wystarczal, bo `screen()` mial WLASNA,
    niezalezna bramke czasu — po zgaszeniu karty malowal 'brak danych z serwera'."""
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert a.tick() is not None
    a.on_event("alert", ramka())
    a.screen()                      # uzbrojenie lingera
    z.skok(a.cfg.blocked_linger_sec + 1)
    assert not a.holding()
    ekran = a.screen()
    assert ekran.message, "bez danych o zuzyciu mowimy to wprost, a nie malujemy pustych pasow"


def test_alert_nie_udaje_swiezych_danych():
    """Ramka `alert` nie moze ani otworzyc bramki `first_data_at`, ani ustawic
    `link_state` na `live`: nie jest dowodem swiezosci danych o zuzyciu."""
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    assert a.first_data_at is None
    assert a.link_state == "down"


def test_wylacznik_konfiguracji():
    z = Zegar()
    a = app(z, session_alerts=False)
    a.on_event("alert", ramka(wpis()))
    z.skok(60)
    assert a.alerts == []
    assert a.screen().alert is None


# --- trojkat przy koncie ----------------------------------------------------

def test_trojkat_laduje_na_pasie_wlasciwego_konta():
    z = Zegar()
    a = app(z)
    a.first_data_at = z.t
    a.on_event("alert", ramka(wpis(accountUuid="konto-b")))
    bands = a.screen().bands
    assert not bands[0].alert and bands[1].alert


def test_alert_bez_dopasowania_laduje_na_pasie_gornym():
    z = Zegar()
    a = app(z)
    a.first_data_at = z.t
    a.on_event("alert", ramka(wpis(accountUuid="konto-ktorego-nie-znamy")))
    bands = a.screen().bands
    assert bands[0].alert and not bands[1].alert


def test_trojkat_nie_wypycha_tytulu_poza_pas():
    """Trojkat odejmuje sobie miejsce z budzetu TYTULU, a nie dokleja sie za pasem.

    Sprawdzane na najgorszym przypadku: dluga nazwa, zegar, znacznik lacza i plakietka
    planu naraz — czyli wszystko, co konkuruje o te sama szerokosc.
    """
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    b = lay.bands[0]
    dluga = ("bardzo.dluga.nazwa.konta.ktorej.nikt.nie.przewidzial"
             "@poddomena.przyklad.example.pl")
    f = draw.font(L.F_NAME)
    for alert in (False, True):
        img, d = draw.new_canvas((480, 320))
        state = render.BandState(title=dluga, plan="MAX 5×", show_clock=True,
                                 alert=alert)
        render.Renderer()._header(d, b, state,
                                  render.ScreenState(clock="21:07", link="live"))
        px = img.load()
        # Kolumny na prawo od pasa musza zostac tlem — nic nie wyjechalo poza margines.
        for x in range(b.x1 + 1, 480):
            for y in range(b.header[1], b.header[3]):
                assert px[x, y] == theme.BG, "cos wyjechalo poza pas w kolumnie %d" % x
    # A sam tytul ma byc KROTSZY, gdy trojkat zajmuje miejsce.
    bez = draw.ellipsize(dluga, f, b.x1 - b.x0)
    z_trojkatem = draw.ellipsize(dluga, f, b.x1 - b.x0 - L.WARN_W - L.WARN_GAP)
    assert len(z_trojkatem) < len(bez)


# --- koszt na drucie --------------------------------------------------------

def scena_pasy(now_ms):
    from tests import fixtures
    bands = [render.band_state(acc, now_ms=now_ms, show_clock=(i == 0))
             for i, acc in enumerate(fixtures.SCENES["base"]())]
    return render.ScreenState(clock="21:07", link="live", bands=bands)


def _frakcja(a, b):
    R = render.Renderer()
    pa, pb = R.frame(a).rgb565("be"), R.frame(b).rgb565("be")
    tiles = surface.dirty_tiles(pa, pb, 480, 320)
    rects = surface.coalesce(tiles, surface.TILE)
    nbytes = sum((x1 - x0) * (y1 - y0) * 2 for x0, y0, x1, y1 in rects)
    return nbytes / len(pa), len(rects)


def test_przejscie_do_karty_miesci_sie_pod_progiem_pelnej_klatki():
    """Przypina liczbe, na ktorej stoi `FULL_AT = 0.85`.

    Przy dawnym progu 0.60 to przejscie ladowalo 2 punkty NAD nim i zamienialo
    45 wycinkow w pelna klatke — czyli 1,16 s w 1,87 s, bez zadnego zysku.
    """
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pasy = scena_pasy(now_ms)
    karta = scena_pasy(now_ms)
    karta.alert = render.alert_state(
        status.parse_frame(ramka(wpis(since="2026-08-05T21:00:00Z"))), now_ms)
    frakcja, rects = _frakcja(pasy, karta)
    assert 0.55 < frakcja < surface.FULL_AT, \
        "przejscie do karty zmienia %.1f%% klatki — prog FULL_AT wymaga rewizji" % (
            frakcja * 100)
    assert rects <= surface.MAX_RECTS


def test_sam_trojkat_jest_tani():
    """Trojkat ma prawo zapalac sie i gasnac czesto — musi kosztowac tyle co nic."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pasy = scena_pasy(now_ms)
    z_trojkatem = scena_pasy(now_ms)
    for band in z_trojkatem.bands:
        if band is not None:
            band.alert = True
    frakcja, rects = _frakcja(pasy, z_trojkatem)
    assert frakcja < 0.02, "trojkat zmienia %.2f%% klatki" % (frakcja * 100)
    assert rects <= 8


# --- format czasu -----------------------------------------------------------

@pytest.mark.parametrize("sekundy,oczekiwane", [
    (0, "chwilę"), (59, "chwilę"), (60, "1 min"), (245, "4 min"),
    (3600, "1 h 00 min"), (3900, "1 h 05 min"), (86400, "1 d 0 h"),
    (183600, "2 d 3 h"),
])
def test_waited(sekundy, oczekiwane):
    assert fmt.waited(0.0, sekundy * 1000.0) == oczekiwane


def test_waited_nie_schodzi_ponizej_zera():
    """Zegary maszyn sie rozjezdzaja, wiec `since` z przyszlosci jest realne."""
    assert fmt.waited(10_000.0, 0.0) == "chwilę"
    assert fmt.waited(None, 0.0) == "—"
