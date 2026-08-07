"""Zablokowana sesja na ekranie: karta, zwiniecie do znacznika, koszt zmiany sceny.

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
    row = card.rows[0]
    assert (row.project, row.tool, row.machine) == ("proj", "Bash", "laptop")
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
    przed = a.screen().alert.rows[0].waited
    assert przed == "1 min"
    a.on_event("alert", ramka())
    a.screen()                      # uzbrojenie lingera
    z.skok(5)                       # przekracza granice minuty — bez zamrozenia "2 min"
    assert a.screen().alert.rows[0].waited == przed


def test_wypalenie_okna_zwija_karte_do_znacznika():
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
    assert ekran.bands[0].alert, "ale wpis zyje dalej i musi byc widoczny jako znacznik"


def test_nowa_blokada_dostaje_nowe_okno():
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(wpis(key="stary", since="2026-08-05T21:00:00Z")))
    z.skok(5)
    assert a.screen().alert is None, "stara blokada ma juz wypalone okno"
    a.on_event("alert", ramka(wpis(key="stary", since="2026-08-05T21:00:00Z"),
                              wpis(key="nowy", since=NOW)))
    z.skok(5)
    karta = a.screen().alert
    assert karta is not None
    assert len(karta.rows) == 2, "swieza blokada wciaga wypalona na te sama karte"


def test_swieza_blokada_wciaga_wypalona_na_ta_sama_karte():
    """Regresja na trzy martwe uklady z czterech.

    Okno nalezy do ZBIORU: dopoki cokolwiek jest swieze, karta wypisuje wszystkie
    czekajace. Przy filtrowaniu per wpis dwie blokady musialyby zaczac sie w tym samym
    pieciominutowym oknie — przy pracy sekwencyjnej to sie nie zdarza, wiec `AlertPair`,
    `AlertList` i `AlertMany` nie mialy jak wejsc na ekran.
    """
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(wpis(key="wypalona", project="stara",
                                   since="2026-08-05T21:00:00Z"),
                              wpis(key="swieza", project="nowa", since=NOW)))
    z.skok(5)
    karta = a.screen().alert
    assert len(karta.rows) == 2 and karta.count == 2
    assert "2" in karta.title, "pasmo liczy obie, nie tylko swieza"


def test_wypalony_zbior_gasnie_w_calosci():
    """Gdy wypali sie OSTATNIA swieza blokada, karta oddaje ekran razem z reszta.

    Uczciwie: to jest guard PRZED NADMIAROWA POPRAWKA, nie dowod na nia. Zdaje sie
    tak samo na starym kodzie i tak ma byc — pilnuje, ze okno nalezace do zbioru nie
    zaczelo trzymac karty w nieskonczonosc. Test rozstrzygajacy o samej zmianie to
    `test_swieza_blokada_wciaga_wypalona_na_ta_sama_karte`.

    Obie blokady musza wejsc swieze: zbior stary od poczatku nie zbuduje karty w ogole,
    wiec nie byloby czego gasic.
    """
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(
        wpis(key="a", since="2026-08-05T21:06:50Z", accountUuid="konto-a"),
        wpis(key="b", since="2026-08-05T21:06:40Z", accountUuid="konto-b")))
    z.skok(5)
    a.first_data_at = z.t
    assert len(a.screen().alert.rows) == 2
    # Zegar SERWERA, bo okno liczy sie od `since`, nie od monotonic.
    a.clock.anchor("2026-08-05T21:09:00Z")
    a.screen()                      # okno wypalone dla obu: karta wchodzi w linger
    z.skok(a.cfg.blocked_linger_sec + 1)
    ekran = a.screen()
    assert ekran.alert is None, "wypalony zbior gasnie w calosci"
    assert ekran.bands[0].alert and ekran.bands[1].alert, "oba wpisy zyja jako znaczniki"


def test_zero_wylacza_karte_takze_bez_since():
    """`alert_takeover_sec: 0` to "od razu znacznik, bez karty" — takze dla wpisu bez
    stempla, ktory wczesniej omijal porownanie wieku i karte jednak dostawal."""
    z = Zegar()
    a = app(z, alert_takeover_sec=0)
    a.on_event("alert", ramka(wpis(since=None)))
    z.skok(5)
    a.first_data_at = z.t
    ekran = a.screen()
    assert ekran.alert is None
    assert ekran.bands[0].alert, "wpis zyje dalej jako znacznik"


# --- blysk ------------------------------------------------------------------

def fazy(a, z, sekundy):
    """Wartosci `flood` w kolejnych sekundach."""
    out = []
    for _ in range(sekundy):
        out.append(a.screen().alert.flood)
        z.skok(1)
    return out


def test_klatka_pelna_wchodzi_i_gasnie():
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
        assert not a.screen().alert.flood


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
    assert any(obraz) and not all(obraz), "ma migac, a nie stac zalane"
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


def test_takeover_zero_daje_od_razu_znacznik():
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
        alert=render.alert_state(blokady, now_ms, flood=True))).rgb565("be")
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


# --- znacznik przy koncie ---------------------------------------------------

def test_znacznik_laduje_na_pasie_wlasciwego_konta():
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


def test_powod_nie_wypycha_tytulu_poza_pas():
    """Powod odejmuje sobie miejsce z budzetu TYTULU, a nie dokleja sie za pasem.

    Sprawdzane na najgorszym przypadku: dluga nazwa, zegar, znacznik lacza i plakietka
    planu naraz — czyli wszystko, co konkuruje o te sama szerokosc.
    """
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    b = lay.bands[0]
    dluga = ("bardzo.dluga.nazwa.konta.ktorej.nikt.nie.przewidzial"
             "@poddomena.przyklad.example.pl")
    f = draw.font(L.F_NAME)
    dlugosci = []
    for alert in (None, "pytanie"):
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
        # Koniec TYTULU, nie koniec naglowka: tytul jest do lewej, a powod, plakietka
        # i zegar do prawej, wiec miedzy nimi jest przerwa. Szukamy pierwszej przerwy
        # szerszej niz odstep miedzyliterowy.
        tusz = [any(px[x, y] != theme.BG for y in range(b.header[1], b.header[3]))
                for x in range(b.x0, b.x1)]
        koniec, przerwa = 0, 0
        for i, ma in enumerate(tusz):
            if ma:
                koniec, przerwa = i, 0
            else:
                przerwa += 1
                if przerwa >= 6:
                    break
        dlugosci.append(koniec)
    assert dlugosci[1] < dlugosci[0], "powod ma skracac tytul, nie nachodzic na niego"


def test_znacznik_pasa_ma_pelna_wysokosc_i_siedzi_w_marginesie():
    """Pasek 4 px stoi w polu marginesu (PAD_X 14), wiec uklad pasa nie drga ani
    o piksel — i ma pelna wysokosc pasa niezaleznie od liczby wierszy w srodku."""
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    for b in lay.bands:
        img, d = draw.new_canvas((480, 320))
        render.Renderer()._band(d, b, render.BandState(title="konto",
                                                       alert="zgoda"),
                                render.ScreenState())
        px = img.load()
        assert all(px[x, y] == theme.ACCENT
                   for x in range(L.MARK_W) for y in range(b.top, b.bottom))
        assert px[L.MARK_W, b.top] != theme.ACCENT, "pasek jest szerszy niz 4 px"
        assert L.MARK_W < L.PAD_X, "pasek musialby zabrac miejsce trescia pasa"


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


def test_sam_znacznik_jest_tani():
    """Znacznik ma prawo zapalac sie i gasnac czesto — musi kosztowac tyle co nic.

    Prog jest luzniejszy niz przy trojkacie (2%), bo pasek idzie przez CALA wysokosc
    pasa, a nazwa konta zmienia przy tym kolor: brudzi sie lewa kolumna kafli i dwa
    naglowki. 6% to ~0,11 s na Turingu, czyli nadal ulamek ticku.
    """
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pasy = scena_pasy(now_ms)
    ze_znacznikiem = scena_pasy(now_ms)
    for band in ze_znacznikiem.bands:
        if band is not None:
            band.alert = "zgoda"
    frakcja, rects = _frakcja(pasy, ze_znacznikiem)
    assert frakcja < 0.08, "znacznik zmienia %.2f%% klatki" % (frakcja * 100)
    assert rects <= 12


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


# --- uklady karty -----------------------------------------------------------

def blokady(n):
    """n blokad o roznych powodach i stemplach, w kolejnosci parsera."""
    return status.parse_frame(ramka(*[
        wpis(key="k%d" % i, reason=("plan", "question", "permission")[i % 3],
             project="projekt-%d" % i, detail="szczegol %d" % i,
             since="2026-08-05T21:0%d:00Z" % i)
        for i in range(n)]))


@pytest.mark.parametrize("ile,metoda", [
    (1, "_alert_solo"), (2, "_alert_pair"), (3, "_alert_list"), (5, "_alert_many"),
])
def test_uklad_wybiera_liczba_blokad(ile, metoda, monkeypatch):
    """Prog jest przy trzech: do dwoch nazwa projektu zostaje bohaterem, od trzech
    schodzi do listy, bo trzy nazwy w 34 px nie istnieja."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    stan = render.alert_state(blokady(ile), now_ms)
    wolane = []
    R = render.Renderer()
    for nazwa in ("_alert_solo", "_alert_pair", "_alert_list", "_alert_many"):
        monkeypatch.setattr(R, nazwa,
                            lambda d, a, n=nazwa: wolane.append(n))
    R.frame(render.ScreenState(alert=stan))
    assert wolane == [metoda]


def test_lista_pokazuje_trzy_a_liczy_wszystkie():
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    stan = render.alert_state(blokady(5), now_ms)
    assert len(stan.rows) == render.ALERT_ROWS_MAX
    assert stan.count == 5
    assert len(stan.rest) == 2, "reszta idzie do stopki z nazwy, nie znika"
    assert "5" in stan.title


def test_pasmo_podaje_najstarsze_czekanie_a_nie_naglowek():
    """Kolejnosc sortuje najpierw po POWODZIE, wiec pierwszy wiersz nie musi byc
    najstarszy — a godzina w pasmie ma byc poczatkiem najstarszego czekania."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    stan = render.alert_state(status.parse_frame(ramka(
        wpis(key="plan", reason="plan", since="2026-08-05T21:06:00Z"),
        wpis(key="zgoda", reason="permission", since="2026-08-05T21:01:00Z"),
    )), now_ms)
    assert stan.rows[0].short == "plan", "plan idzie pierwszy"
    assert stan.at == fmt.hm(fmt.parse_utc("2026-08-05T21:01:00Z"))


def test_kafel_szczegolu_nie_wchodzi_na_listwe_trybu():
    from panel import layout as L
    lay = L.Layout(480, 320)
    for linie in (1, 2):
        assert lay.alert_solo.fits(linie), "kafel na %d linie wchodzi na listwe" % linie


@pytest.mark.parametrize("ze_stopka", [True, False])
def test_wiersze_listy_wypelniaja_ekran_bez_szpar(ze_stopka):
    """Reszta z dzielenia idzie tam, gdzie daje ja przegladarka. Szpara tla przy
    stopce czytalaby sie jako urwany ekran."""
    from panel import layout as L
    lay = L.Layout(480, 320)
    for uklad in (lay.alert_list, lay.alert_many):
        rects = uklad.rows(footer=ze_stopka)
        assert rects[0][0] == L.BANNER_H
        for (_, bottom), (top, _) in zip(rects, rects[1:]):
            assert top == bottom + L.DIVIDER_H, "wiersze nie stykaja sie dzielnikiem"
        koniec = uklad.footer[1] if ze_stopka else uklad.height
        assert rects[-1][1] == koniec, "ostatni wiersz nie dochodzi do stopki"


def _dol_tuszu(px, x0, x1, y0, y1, tlo, prog=25):
    """Ostatni wiersz, w ktorym w podanym prostokacie jest tusz. Dla napisu bez
    zejsc ponizej linii bazowej to jest wlasnie linia bazowa minus jeden."""
    ostatni = None
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = px[x, y]
            if max(abs(p[i] - tlo[i]) for i in range(3)) > prog:
                ostatni = y
                break
    return ostatni


def test_tusz_pasma_i_listwy_stoi_tam_gdzie_makieta():
    """Linie bazowe sa ZMIERZONE na wyrenderowanej makiecie, wiec test pilnuje pomiaru,
    a nie wzoru.

    Zmierzone na makiecie (`1a-alert`, render 3x, dol cyfr zegara i dol napisu w listwie
    `Tryb`): pasmo 24,33 px, listwa 306,33 px. Pillow z anchor="ls" klazie dol tuszu na
    `base - 1`, wiec przy poprawnych stalych ostatni zapisany wiersz to 23 i 305.
    Wczesniej bylo 26 i 308 — o 1,67 px za nisko, w kazdym z czterech ukladow naraz.
    """
    from panel import layout as L

    now_ms = fmt.ms(fmt.parse_utc(NOW))
    # Z `permissionMode`, inaczej listwa `Tryb` w ogole sie nie rysuje i mierzylibysmy
    # pusty pas.
    stan = render.alert_state(
        status.parse_frame(ramka(wpis(permissionMode="default"))), now_ms)
    px = render.Renderer().frame(render.ScreenState(alert=stan)).image.load()

    assert L.BANNER_BASE == 24, "pomiar makiety: dol cyfr zegara na 24,33 px"
    assert L.AlertSolo.MODE_BASE == 306, "pomiar makiety: dol napisu `Tryb` na 306,33 px"

    # Zegar w pasmie: cyfry nie schodza ponizej linii bazowej, wiec dol tuszu ja podaje.
    assert _dol_tuszu(px, 380, 470, 0, L.BANNER_H, theme.ACCENT_800) == L.BANNER_BASE - 1
    # Listwa `Tryb`: ani "TRYB", ani "default" nie ma zejscia.
    assert _dol_tuszu(px, 18, 300, 320 - L.AlertSolo.MODE_H, 320,
                      theme.SUNKEN) == L.AlertSolo.MODE_BASE - 1


@pytest.mark.parametrize("ile", [1, 2, 3, 5])
@pytest.mark.parametrize("zalane,kolor", [(False, "NEUTRAL_900"), (True, "ACCENT")])
def test_rail_stoi_w_obu_klatkach_kazdego_ukladu(ile, zalane, kolor):
    """Rail nie jest wlasnoscia klatki pelnej: stoi pod pasmem zawsze, a zalanie tylko
    go przemalowuje. Pasek pojawiajacy sie z niczego bylby mocniejszym ruchem niz zmiana
    koloru, a poza oknem `alert_flash_sec` karta zostawalaby bez lewej krawedzi.

    Idzie przez CALA wysokosc pod pasmem, takze przez listwe `Tryb` w ukladzie 1a
    i przez stopki w 1c/1d — dlatego pasmo rysuje sie na koncu, nad trescia.
    """
    from panel import layout as L

    oczekiwany = getattr(theme, kolor)
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    stan = render.alert_state(blokady(ile), now_ms, flood=zalane)
    px = render.Renderer().frame(render.ScreenState(alert=stan)).image.load()

    for y in range(L.BANNER_H, 320):
        for x in range(L.RAIL_W):
            assert px[x, y] == oczekiwany, \
                "rail dziurawy w (%d, %d) przy %d blokadach" % (x, y, ile)
        assert px[L.RAIL_W, y] != oczekiwany, \
            "rail szerszy niz %d px w wierszu %d" % (L.RAIL_W, y)
    assert px[0, L.BANNER_H - 1] != oczekiwany or zalane, \
        "rail wchodzi w pasmo"


@pytest.mark.parametrize("ile", [1, 2, 3, 5])
def test_przejscie_do_karty_miesci_sie_pod_progiem_dla_kazdego_ukladu(ile):
    """`FULL_AT = 0.85` musi wytrzymac KAZDY uklad, nie tylko ten z jedna blokada:
    powyzej progu zestaw wycinkow zamienia sie w pelna klatke, czyli 1,87 s na Turingu
    zamiast ~1,2 s."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pasy = scena_pasy(now_ms)
    karta = scena_pasy(now_ms)
    karta.alert = render.alert_state(blokady(ile), now_ms)
    frakcja, rects = _frakcja(pasy, karta)
    assert frakcja < surface.FULL_AT, \
        "uklad na %d blokad brudzi %.1f%% klatki — prog FULL_AT wymaga rewizji" % (
            ile, frakcja * 100)
    assert rects <= surface.MAX_RECTS


@pytest.mark.parametrize("ile", [1, 2, 3, 5])
def test_klatka_pelna_miesci_sie_w_ticku(ile):
    """Sedno warstwy ruchu: podmiana klatki pustej na pelna to pasmo plus rail, nie
    caly ekran. Pelnoekranowy blysk bylby pelna klatka, czyli 1,87 s powolnego
    zamalowania zamiast blysku.

    Po jednym przebiegu na uklad, bo koszt zalania nie moze zalezec od tego, ile
    blokad akurat czeka — pasmo i rail sa wspolne, wiec liczba ma wyjsc ta sama."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pusta = render.ScreenState(alert=render.alert_state(blokady(ile), now_ms))
    pelna = render.ScreenState(
        alert=render.alert_state(blokady(ile), now_ms, flood=True))
    frakcja, _ = _frakcja(pusta, pelna)
    assert frakcja < 0.25, "klatka pelna brudzi %.1f%% klatki" % (frakcja * 100)
    assert frakcja * len(render.Renderer().frame(pusta).rgb565("be")) * 6.1e-6 < 0.5, \
        "nie zdazy w ticku"
