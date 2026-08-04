"""Uklad, kolory i sam render. Bez sprzetu.

Testy pilnuja rzeczy, ktore na 480x320 psuja sie po cichu: obciete ogonki,
prostokaty wychodzace poza ekran, kolory ginace po kwantyzacji 5/6/5.
"""
import pytest

from PIL import Image

from panel import draw, layout as L, render, theme, view
from tests import fixtures


# --- geometria --------------------------------------------------------------

def test_pasy_dziela_ekran_bez_nachodzenia():
    lay = L.Layout(480, 320)
    a, b = lay.bands
    assert a.top == 0 and b.bottom == 320
    assert a.bottom <= lay.divider[1] and lay.divider[3] <= b.top
    assert a.height + b.height + L.DIVIDER_H == 320


def test_kredyty_nie_wchodza_na_tydzien():
    """Wiersz kredytow jest przyklejony do dolu pasa. Gdyby pas byl za niski,
    nachodzilby na odliczanie tygodnia i nikt by tego nie zauwazyl na PNG."""
    for band in L.Layout(480, 320).bands:
        assert band.fits, "kredyty nachodza na tydzien"


def test_wszystko_miesci_sie_w_ekranie():
    lay = L.Layout(480, 320)
    for band in lay.bands:
        for name in ("header", "ses_label", "ses_bar", "ses_line",
                     "wk_label", "wk_bar", "wk_line", "credits"):
            x0, y0, x1, y1 = getattr(band, name)
            assert 0 <= x0 < x1 <= lay.width, "%s wychodzi w poziomie" % name
            assert band.top <= y0 < y1 <= band.bottom, "%s wychodzi w pionie" % name


def test_kolumna_liczb_i_blok_nie_nachodza():
    band = L.Layout(480, 320).band_a
    assert band.num_right < band.block_x0


# --- typografia -------------------------------------------------------------

def test_trzycyfrowa_wartosc_miesci_sie_w_kolumnie():
    """Makieta schodzi ze 42 na 34 px przy 100% wlasnie dlatego. Gdyby nie,
    liczba rozepchnelaby kolumne i paski przestalyby startowac rowno."""
    f_big = draw.font(L.F_SES_NUM)
    f_tight = draw.font(L.F_SES_NUM_TIGHT)
    f_pct = draw.font(L.F_SES_PCT)
    szerokosc = lambda f: draw.text_width("100", f) + L.PCT_GAP + draw.text_width("%", f_pct)
    assert szerokosc(f_big) > L.NUM_W, "gdyby sie miescilo, zejscie byloby zbedne"
    assert szerokosc(f_tight) <= L.NUM_W


def test_ogonki_nie_sa_obcinane():
    """Mierzymy z faktycznego obrysu, nie z nominalnego rozmiaru: 'Ń' siega
    wyzej niz wielkie litery bez znaku diakrytycznego."""
    for size in (10, 11, 12, 13, 15):
        f = draw.font(size)
        assert draw.line_height(f) >= f.getbbox("TYDZIEŃ")[3] - f.getbbox("TYDZIEŃ")[1]
        assert draw.text_width("Zażółć gęślą jaźń", f) > 0


def test_ellipsize_nie_przekracza_szerokosci():
    f = draw.font(15)
    dlugi = "bardzo.dluga.nazwa.konta.ktora.sie.nie.miesci@przyklad.example.pl"
    for limit in (40, 80, 160, 300):
        assert draw.text_width(draw.ellipsize(dlugi, f, limit), f) <= limit
    assert draw.ellipsize("krotki", f, 300) == "krotki", "krotkie zostaja bez zmian"


# --- kolory -----------------------------------------------------------------

@pytest.mark.parametrize("fg,nazwa", [
    (theme.TEXT, "tekst"), (theme.TEXT_50, "plan"), (theme.TEXT_28, "kontur"),
    (theme.TEXT_10, "skos"), (theme.ACCENT, "akcent"), (theme.ACCENT_200, "etykieta"),
    (theme.NEUTRAL_900, "tor"), (theme.DIVIDER, "separator"),
])
def test_kolory_przezywaja_kwantyzacje(fg, nazwa):
    """Panel ma 5/6/5 bitow. Najbardziej zagrozony jest kreskowany kontur
    (28% bieli na prawie czarnym tle) — gdyby zlal sie z tlem, stan `unknown`
    stalby sie niewidoczny."""
    assert theme.to_rgb565_pair(fg) != theme.to_rgb565_pair(theme.BG), \
        "%s ginie na tle po kwantyzacji" % nazwa


# --- render -----------------------------------------------------------------

@pytest.mark.parametrize("scene", sorted(fixtures.SCENES))
def test_kazda_scena_renderuje_sie(scene):
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    bands = []
    for i, acc in enumerate(fixtures.SCENES[scene]()):
        bands.append(None if acc is None
                     else render.band_state(acc, now_ms=now_ms, show_clock=(i == 0)))
    frame = render.Renderer().frame(
        render.ScreenState(clock="21:07", link="live", bands=bands))
    assert frame.image.size == (480, 320)
    assert len(frame.rgb565("be")) == 480 * 320 * 2


def test_ta_sama_scena_daje_ten_sam_ladunek():
    """Na tym stoi cala oszczednosc: klatke wysylamy tylko, gdy rozni sie od
    poprzedniej. Gdyby render byl niedeterministyczny, panel nadawalby non stop."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    def zbuduj():
        bands = [render.band_state(a, now_ms=now_ms, show_clock=(i == 0))
                 for i, a in enumerate(fixtures.base())]
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link="live", bands=bands)).rgb565("be")
    assert zbuduj() == zbuduj()


def test_karta_stanu_zamiast_pasow():
    frame = render.Renderer().frame(render.ScreenState(
        message=["Brak konfiguracji", "panel.json"]))
    assert frame.image.size == (480, 320)


def test_pusty_slot_nie_wybucha():
    frame = render.Renderer().frame(
        render.ScreenState(clock="21:07", link="down", bands=[None, None]))
    assert len(frame.rgb565("be")) == 480 * 320 * 2


def test_trzy_stany_lacza_daja_trzy_rozne_obrazy():
    """Roznica RYSUNKIEM, nie kolorem: gdy strumien padnie, wiek odczytu rosnie
    obu kontom naraz i wyglada to identycznie jak 'przestales pracowac'."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    def klatka(link):
        bands = [render.band_state(a, now_ms=now_ms, show_clock=(i == 0))
                 for i, a in enumerate(fixtures.base())]
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link=link, bands=bands)).rgb565("be")
    assert len({klatka("live"), klatka("reconnecting"), klatka("down")}) == 3


def test_wiek_bierze_starsza_z_dwoch_serii():
    """Etykieta wieku jest tu JEDYNYM nosnikiem swiezosci i stoi tylko przy
    wierszu sesji. Backend potwierdza kazda serie osobno, wiec gdyby brala
    stempel sesji, panel pisalby "1 min temu" obok pewnie wygladajacego paska
    tygodnia sprzed trzech dni."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    acc = fixtures.account(
        "u", "kto@example.pl",
        series=[fixtures.series("limit:session|session|-|-", "Sesja",
                                kind="session", bucketKey="five_hour",
                                utilization=31,
                                confirmedAt="2026-07-26T19:06:40Z",
                                capturedAt="2026-07-26T19:06:40Z"),
                fixtures.series("limit:weekly_all|weekly|-|-", "Tydzien",
                                kind="weekly_all", bucketKey="seven_day",
                                utilization=30,
                                confirmedAt="2026-07-23T19:07:40Z",
                                capturedAt="2026-07-23T19:07:40Z")])
    assert render.band_state(acc, now_ms=now_ms).ago == "3 d 0 h temu"


# Pakowanie pikseli ma swoj plik: tests/test_pixels.py.


def test_wycofane_kredyty_zostaja_narysowane():
    """Regresja na realny przypadek: organizacja odcina kredyty, szczebel schodzi na `off`
    i wiersz kwot ZNIKAL z pasa. Panel gubil wtedy jedyna liczbe, ktora o wydatkach mial —
    a kwoty przychodza dalej, bo backend podaje je z ostatniego pomiaru.

    Porownujemy ladunki: wersja z kwotami MUSI dac inny obraz niz `off` bez kwot, a taki
    sam jak `on` z tymi samymi kwotami — bo rysunek jest ten sam, rozni sie tylko stan.
    """
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))

    def klatka(**kw):
        wzor = fixtures.base()[1]
        cascade = [{"key": r.key, "state": r.state} for r in wzor.cascade
                   if r.key != "credits"]
        cascade.append(fixtures.rung("credits", **kw))
        acc = fixtures.account(wzor.uuid, wzor.email, org_type="claude_team",
                               cascade=cascade, series=[])
        band = render.band_state(acc, now_ms=now_ms)
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link="live", bands=[band, None])).rgb565("be")

    kwoty = dict(usedMinor=30004, limitMinor=30000, currency="EUR", exponent=2)
    wycofane = klatka(state="off", reason="org_level_disabled_until", **kwoty)
    puste = klatka(state="off")
    dzialajace = klatka(state="on", **kwoty)

    assert wycofane != puste, "wiersz kwot zniknal — panel stracil liczbe"
    assert wycofane == dzialajace, "powodu wycofania panel nie rysuje; kwoty to kwoty"
