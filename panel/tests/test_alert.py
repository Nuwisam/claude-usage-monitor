"""A blocked session on the screen: the card, the fold into a marker, the cost of a scene change.

The clock is injected everywhere, because otherwise the window burn-out test would take 300 s.
"""
import pytest

from panel import app as app_mod, config as C, fmt, render, status, surface, theme


class Zegar:
    """Monotonic under the test's control."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def skok(self, sekundy):
        self.t += sekundy


def cfg(**kw):
    # The flash is off by default: the card tests are to check the card, not what comes
    # before it. The flash tests switch it on explicitly.
    d = {"stream_token": "t", "account_1": {"uuid": "konto-a"},
         "account_2": {"uuid": "konto-b"}, "alert_flash_sec": 0}
    d.update(kw)
    return C.Config(d)


def po_migotaniu(a, z):
    """Renders until the banner stops blinking. The window arms itself on the FIRST render
    after the key appears, so moving the clock alone is not enough."""
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


# --- card entry and exit ----------------------------------------------------

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
    assert card.title == "NEEDS PERMISSION"


def test_pusty_zbior_gasi_karte_dopiero_po_lingerze():
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert a.screen().alert is not None
    a.on_event("alert", ramka())
    # The linger starts on the first render AFTER the set empties, not at the moment the
    # frame arrives: what counts is the moment the scene would have switched.
    assert a.screen().alert is not None, "linger ogranicza liczbe przejsc sceny"
    z.skok(a.cfg.blocked_linger_sec + 1)
    assert a.screen().alert is None


def test_karta_w_lingerze_jest_zamrozona():
    """Without the freeze, 'waiting N min' would tick on a prompt that has already been
    answered, and every jump of that caption is a full frame on the AX206."""
    z = Zegar()
    a = app(z)
    # Chosen so that the first render sees 119 s and five seconds later 124 s.
    a.on_event("alert", ramka(wpis(since="2026-08-05T21:05:06Z")))
    z.skok(5)
    przed = a.screen().alert.rows[0].waited
    assert przed == "1 min"
    a.on_event("alert", ramka())
    a.screen()                      # arming the linger
    z.skok(5)                       # crosses the minute boundary — without the freeze "2 min"
    assert a.screen().alert.rows[0].waited == przed


def test_wypalenie_okna_zwija_karte_do_znacznika():
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(wpis(since="2026-08-05T21:06:50Z")))   # 10 s ago
    z.skok(5)
    assert a.screen().alert is not None
    # The window counts from the SERVER's `since`, so we move the server clock, not just
    # the monotonic one: otherwise the test would check something other than the real run.
    a.clock.anchor("2026-08-05T21:08:00Z")
    a.first_data_at = z.t
    a.screen()                      # window burnt out: the card enters the linger
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
    """Regression on three dead layouts out of four.

    The window belongs to the SET: as long as anything is fresh, the card lists everything
    waiting. With per-entry filtering two blocks would have to start inside the same
    five-minute window — with sequential work that never happens, so `AlertPair`,
    `AlertList` and `AlertMany` had no way of reaching the screen.
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
    assert karta.rows[0].project == "nowa", "najmlodsza w pierwszym wierszu"


def test_wypalony_zbior_gasnie_w_calosci():
    """When the LAST fresh block burns out, the card hands the screen back along with the rest.

    Honestly: this is a guard AGAINST AN OVERREACHING FIX, not proof of one. It passes the
    same way on the old code and that is as it should be — it watches that a window belonging
    to the set has not started holding the card forever. The test that settles the change
    itself is `test_swieza_blokada_wciaga_wypalona_na_ta_sama_karte`.

    Both blocks have to come in fresh: a set that is old from the start builds no card at all,
    so there would be nothing to put out.
    """
    z = Zegar()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", ramka(
        wpis(key="a", since="2026-08-05T21:06:50Z", accountUuid="konto-a"),
        wpis(key="b", since="2026-08-05T21:06:40Z", accountUuid="konto-b")))
    z.skok(5)
    a.first_data_at = z.t
    assert len(a.screen().alert.rows) == 2
    # The SERVER clock, because the window counts from `since`, not from the monotonic one.
    a.clock.anchor("2026-08-05T21:09:00Z")
    a.screen()                      # window burnt out for both: the card enters the linger
    z.skok(a.cfg.blocked_linger_sec + 1)
    ekran = a.screen()
    assert ekran.alert is None, "wypalony zbior gasnie w calosci"
    assert ekran.bands[0].alert and ekran.bands[1].alert, "oba wpisy zyja jako znaczniki"


def test_zero_wylacza_karte_takze_bez_since():
    """`alert_takeover_sec: 0` means "a marker right away, no card" — including for an entry
    with no stamp, which used to skip the age comparison and get a card after all."""
    z = Zegar()
    a = app(z, alert_takeover_sec=0)
    a.on_event("alert", ramka(wpis(since=None)))
    z.skok(5)
    a.first_data_at = z.t
    ekran = a.screen()
    assert ekran.alert is None
    assert ekran.bands[0].alert, "wpis zyje dalej jako znacznik"


# --- flash ------------------------------------------------------------------

def fazy(a, z, sekundy):
    """The `flood` values in consecutive seconds."""
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
    """The values come from a hand-edited panel.json and go into a COMPARISON —
    a bare string tips the tick over with a TypeError. Junk is to mean the default."""
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
    """Regression: `age >= "infinity"` is a TypeError, that is a dead tick under pythonw,
    with no console to show it."""
    z = Zegar()
    a = app(z, alert_takeover_sec="bzdura", alert_flash_sec="bzdura")
    a.first_data_at = z.t
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert a.screen() is not None


def test_migniecie_banera_miesci_sie_w_ticku():
    """The heart of the fix: a full-screen flash is a full frame (1.87 s on the Turing),
    that is a slow repaint instead of a flash. The banner alone has to be cheap enough
    to make it inside a second."""
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


# --- precedence -------------------------------------------------------------

def test_alert_bije_holding():
    """An alert is the message being waited for — there is no reason for it to wait out
    the splash threshold."""
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
    assert "contract" in ekran.alert.footer, "the contract notice drops into the footer"


def test_po_karcie_nie_wracamy_do_holding():
    """Regression: the `ever_painted` latch alone was not enough, because `screen()` had its
    OWN, independent time gate — once the card went out it painted 'no data from server'."""
    z = Zegar()
    a = app(z)
    a.on_event("alert", ramka(wpis()))
    z.skok(5)
    assert a.tick() is not None
    a.on_event("alert", ramka())
    a.screen()                      # arming the linger
    z.skok(a.cfg.blocked_linger_sec + 1)
    assert not a.holding()
    ekran = a.screen()
    assert ekran.message, "bez danych o zuzyciu mowimy to wprost, a nie malujemy pustych pasow"


def test_alert_nie_udaje_swiezych_danych():
    """An `alert` frame may neither open the `first_data_at` gate nor set `link_state`
    to `live`: it is no proof that the usage data is fresh."""
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


# --- the marker next to the account -----------------------------------------

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
    """The reason takes its room out of the TITLE's budget instead of being glued on past
    the band.

    Checked on the worst case: a long name, the clock, the link marker and the plan badge
    all at once — that is, everything competing for the same width.
    """
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    b = lay.bands[0]
    dluga = ("bardzo.dluga.nazwa.konta.ktorej.nikt.nie.przewidzial"
             "@poddomena.przyklad.example.pl")
    f = draw.font(L.F_NAME)
    dlugosci = []
    for alert in (None, "question"):
        img, d = draw.new_canvas((480, 320))
        state = render.BandState(title=dluga, plan="MAX 5×", show_clock=True,
                                 alert=alert)
        render.Renderer()._header(d, b, state,
                                  render.ScreenState(clock="21:07", link="live"))
        px = img.load()
        # The columns right of the band must stay background — nothing ran past the margin.
        for x in range(b.x1 + 1, 480):
            for y in range(b.header[1], b.header[3]):
                assert px[x, y] == theme.BG, "cos wyjechalo poza pas w kolumnie %d" % x
        # The end of the TITLE, not the end of the header: the title is set left, and the
        # reason, the badge and the clock right, so there is a gap between them. We look for
        # the first gap wider than the spacing between letters.
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
    """The 4 px bar stands in the margin field (PAD_X 14), so the band's layout does not
    shift by a pixel — and it has the band's full height whatever the number of rows inside."""
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    for b in lay.bands:
        img, d = draw.new_canvas((480, 320))
        render.Renderer()._band(d, b, render.BandState(title="konto",
                                                       alert="allow"),
                                render.ScreenState())
        px = img.load()
        assert all(px[x, y] == theme.ACCENT
                   for x in range(L.MARK_W) for y in range(b.top, b.bottom))
        assert px[L.MARK_W, b.top] != theme.ACCENT, "pasek jest szerszy niz 4 px"
        assert L.MARK_W < L.PAD_X, "pasek musialby zabrac miejsce trescia pasa"


# --- cost on the wire -------------------------------------------------------

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
    """Pins the number `FULL_AT = 0.85` stands on.

    At the old threshold of 0.60 this transition landed 2 points ABOVE it and turned
    45 crops into a full frame — that is 1.16 s into 1.87 s, for no gain at all.
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
    """The marker has the right to light up and go out often — it has to cost next to nothing.

    The threshold is looser than for the triangle (2%), because the bar runs through the
    band's WHOLE height and the account name changes color along with it: the left column of
    tiles and two headers get dirty. 6% is ~0.11 s on the Turing, still a fraction of a tick.
    """
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pasy = scena_pasy(now_ms)
    ze_znacznikiem = scena_pasy(now_ms)
    for band in ze_znacznikiem.bands:
        if band is not None:
            band.alert = "allow"
    frakcja, rects = _frakcja(pasy, ze_znacznikiem)
    assert frakcja < 0.08, "znacznik zmienia %.2f%% klatki" % (frakcja * 100)
    assert rects <= 12


# --- time format ------------------------------------------------------------

@pytest.mark.parametrize("sekundy,oczekiwane", [
    (0, "a moment"), (59, "a moment"), (60, "1 min"), (245, "4 min"),
    (3600, "1 h 00 min"), (3900, "1 h 05 min"), (86400, "1 d 0 h"),
    (183600, "2 d 3 h"),
])
def test_waited(sekundy, oczekiwane):
    assert fmt.waited(0.0, sekundy * 1000.0) == oczekiwane


def test_waited_nie_schodzi_ponizej_zera():
    """Machine clocks drift apart, so a `since` from the future is real."""
    assert fmt.waited(10_000.0, 0.0) == "a moment"
    assert fmt.waited(None, 0.0) == "—"


# --- card layouts -----------------------------------------------------------

def blokady(n):
    """n blocks with different reasons and stamps, in the parser's order."""
    return status.parse_frame(ramka(*[
        wpis(key="k%d" % i, reason=("plan", "question", "permission")[i % 3],
             project="projekt-%d" % i, detail="szczegol %d" % i,
             since="2026-08-05T21:0%d:00Z" % i)
        for i in range(n)]))


@pytest.mark.parametrize("ile,metoda", [
    (1, "_alert_solo"), (2, "_alert_pair"), (3, "_alert_list"),
    (4, "_alert_many"), (5, "_alert_many"),
])
def test_uklad_wybiera_liczba_blokad(ile, metoda, monkeypatch):
    """The threshold is at three: up to two the project name stays the hero, from three on
    it drops into a list, because three names in 34 px do not exist."""
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
    """The first row is the NEWEST block, while the hour in the banner is the start of the
    OLDEST wait on the screen. Those are two different things and they have to diverge."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    stan = render.alert_state(status.parse_frame(ramka(
        wpis(key="plan", reason="plan", since="2026-08-05T21:06:00Z"),
        wpis(key="zgoda", reason="permission", since="2026-08-05T21:01:00Z"),
    )), now_ms)
    assert stan.rows[0].short == "plan", "najmlodsza idzie pierwsza"
    assert stan.at == fmt.hm(fmt.parse_utc("2026-08-05T21:01:00Z"))


def test_swieza_blokada_jest_widoczna_mimo_trzech_starszych():
    """Regression: with the window belonging to the SET, the reason's rank pushed out of the
    rows the very block that had taken the screen over — only its name stayed in the footer."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    stan = render.alert_state(status.parse_frame(ramka(
        wpis(key="s1", reason="plan", project="stary-1", since="2026-08-05T19:07:00Z"),
        wpis(key="s2", reason="plan", project="stary-2", since="2026-08-05T19:10:00Z"),
        wpis(key="s3", reason="question", project="stary-3", since="2026-08-05T19:13:00Z"),
        wpis(key="f", reason="permission", project="SWIEZY", since="2026-08-05T21:06:50Z"),
    )), now_ms)
    assert stan.rows[0].project == "SWIEZY", "blokada, ktora przejela ekran, ma byc widoczna"
    assert stan.rest == ["stary-1"], "do stopki schodzi najstarsza, nie najnowsza"


def test_kafel_szczegolu_nie_wchodzi_na_listwe_trybu():
    from panel import layout as L
    lay = L.Layout(480, 320)
    for linie in (1, 2):
        assert lay.alert_solo.fits(linie), "kafel na %d linie wchodzi na listwe" % linie


@pytest.mark.parametrize("ze_stopka", [True, False])
def test_wiersze_listy_wypelniaja_ekran_bez_szpar(ze_stopka):
    """The remainder of the division goes where the browser puts it. A gap of background
    at the footer would read as a screen cut short."""
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
    """The last row in which there is ink inside the given rectangle. For a caption with
    no descenders below the baseline that is exactly the baseline minus one."""
    ostatni = None
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = px[x, y]
            if max(abs(p[i] - tlo[i]) for i in range(3)) > prog:
                ostatni = y
                break
    return ostatni


def test_tusz_pasma_i_listwy_stoi_tam_gdzie_makieta():
    """The baselines are MEASURED on the rendered mockup, so the test guards a measurement,
    not a formula.

    Measured on the mockup (`1a-alert`, rendered 3x, the bottom of the clock digits and the
    bottom of the caption in the `MODE` strip): banner 24.33 px, strip 306.33 px. Pillow with
    anchor="ls" puts the bottom of the ink at `base - 1`, so with the right constants the last
    written row is 23 and 305. It was 26 and 308 before — 1.67 px too low, in each of the four
    layouts at once.
    """
    from panel import layout as L

    now_ms = fmt.ms(fmt.parse_utc(NOW))
    # With `permissionMode`, otherwise the `MODE` strip is not drawn at all and we would be
    # measuring an empty band.
    stan = render.alert_state(
        status.parse_frame(ramka(wpis(permissionMode="default"))), now_ms)
    px = render.Renderer().frame(render.ScreenState(alert=stan)).image.load()

    assert L.BANNER_BASE == 24, "pomiar makiety: dol cyfr zegara na 24,33 px"
    assert L.AlertSolo.MODE_BASE == 306, "pomiar makiety: dol napisu `Tryb` na 306,33 px"

    # The clock in the banner: the digits do not go below the baseline, so the bottom of the
    # ink gives it.
    assert _dol_tuszu(px, 380, 470, 0, L.BANNER_H, theme.ACCENT_800) == L.BANNER_BASE - 1
    # The `MODE` strip: neither "MODE" nor "default" has a descender.
    assert _dol_tuszu(px, 18, 300, 320 - L.AlertSolo.MODE_H, 320,
                      theme.SUNKEN) == L.AlertSolo.MODE_BASE - 1


@pytest.mark.parametrize("ile", [1, 2, 3, 5])
@pytest.mark.parametrize("zalane,kolor", [(False, "NEUTRAL_900"), (True, "ACCENT")])
def test_rail_stoi_w_obu_klatkach_kazdego_ukladu(ile, zalane, kolor):
    """The rail is not a property of the full frame: it always stands below the banner, and
    flooding only repaints it. A bar appearing out of nothing would be a stronger movement
    than a change of color, and outside the `alert_flash_sec` window the card would be left
    with no left edge.

    It runs through the WHOLE height below the banner, through the `MODE` strip in layout 1a
    and through the footers in 1c/1d too — which is why the banner is drawn last, over the
    content.
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
    """`FULL_AT = 0.85` has to hold for EVERY layout, not only the one with a single block:
    above the threshold the set of crops turns into a full frame, that is 1.87 s on the
    Turing instead of ~1.2 s."""
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
    """The heart of the movement layer: swapping the empty frame for the full one is the
    banner plus the rail, not the whole screen. A full-screen flash would be a full frame,
    that is 1.87 s of slow repainting instead of a flash.

    One pass per layout, because the cost of the flood must not depend on how many blocks
    happen to be waiting — the banner and the rail are shared, so the number has to come
    out the same."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    pusta = render.ScreenState(alert=render.alert_state(blokady(ile), now_ms))
    pelna = render.ScreenState(
        alert=render.alert_state(blokady(ile), now_ms, flood=True))
    frakcja, _ = _frakcja(pusta, pelna)
    assert frakcja < 0.25, "klatka pelna brudzi %.1f%% klatki" % (frakcja * 100)
    assert frakcja * len(render.Renderer().frame(pusta).rgb565("be")) * 6.1e-6 < 0.5, \
        "nie zdazy w ticku"
