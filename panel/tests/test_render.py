"""The layout, the colors and the render itself. No hardware.

The tests guard the things that break quietly on 480x320: clipped tails,
rectangles running off the screen, colors lost to the 5/6/5 quantization.
"""
import pytest

from PIL import Image

from panel import draw, layout as L, render, theme, view
from tests import fixtures


# --- geometry ---------------------------------------------------------------

def test_pasy_dziela_ekran_bez_nachodzenia():
    lay = L.Layout(480, 320)
    a, b = lay.bands
    assert a.top == 0 and b.bottom == 320
    assert a.bottom <= lay.divider[1] and lay.divider[3] <= b.top
    assert a.height + b.height + L.DIVIDER_H == 320


def test_kredyty_nie_wchodza_na_tydzien():
    """The credits row is glued to the bottom of the band. Were the band too short,
    it would run onto the week countdown and nobody would notice that on a PNG."""
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


# --- typography -------------------------------------------------------------

def test_trzycyfrowa_wartosc_miesci_sie_w_kolumnie():
    """That is exactly why the mockup drops from 42 to 34 px at 100%. Without it
    the number would push the column apart and the bars would stop starting level."""
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


# --- colors -----------------------------------------------------------------

@pytest.mark.parametrize("fg,nazwa", [
    (theme.TEXT, "tekst"), (theme.TEXT_50, "plan"), (theme.TEXT_28, "kontur"),
    (theme.TEXT_10, "skos"), (theme.ACCENT, "akcent"), (theme.ACCENT_200, "etykieta"),
    (theme.NEUTRAL_900, "tor"), (theme.DIVIDER, "separator"),
    (theme.SURFACE, "kafel szczegolu"), (theme.SUNKEN, "listwa"),
])
def test_kolory_przezywaja_kwantyzacje(fg, nazwa):
    """The panel has 5/6/5 bits. The most endangered one is the dashed outline
    (28% white on a nearly black background) — were it to blend into the background,
    the `unknown` state would become invisible."""
    assert theme.to_rgb565_pair(fg) != theme.to_rgb565_pair(theme.BG), \
        "%s ginie na tle po kwantyzacji" % nazwa


@pytest.mark.parametrize("fg,bg,nazwa", [
    (theme.ACCENT_800, theme.BG, "baner alertu na tle"),
    (theme.ACCENT_100, theme.ACCENT_800, "tytul alertu na banerze"),
    (theme.ACCENT_200, theme.ACCENT_800, "godzina na banerze"),
    (theme.BG, theme.ACCENT, "napis w zalanym pasmie"),
    (theme.TEXT_78_SURFACE, theme.SURFACE, "szczegol w kaflu"),
    (theme.TEXT_45_SURFACE, theme.SURFACE, "etykieta w kaflu"),
    (theme.TEXT_70_SUNKEN, theme.SUNKEN, "wartosc w listwie"),
    (theme.TEXT_72_SUNKEN, theme.SUNKEN, "szczegol w stopce listy"),
    (theme.TEXT_62_SUNKEN, theme.SUNKEN, "nazwy reszty w stopce"),
    (theme.TEXT_45_SUNKEN, theme.SUNKEN, "etykieta w listwie i stopce"),
    (theme.ACCENT_200, theme.SUNKEN, "licznik reszty w stopce"),
    (theme.SURFACE, theme.BG, "kafel na tle"),
    (theme.SUNKEN, theme.BG, "listwa na tle"),
])
def test_pary_kolorow_przezywaja_kwantyzacje(fg, bg, nazwa):
    """A separate test from the one above, because that one compares SOLELY with `theme.BG`.

    The card's marginal pair is `ACCENT_800` on `BG` (a difference of 42, 13, 4 before
    quantization), not the text on the banner. The pairs with `SURFACE` and `SUNKEN` are here
    because the detail tile and the mode strip differ from the background by a few units —
    were they to blend after quantization, the card would lose its whole division into fields.
    """
    assert theme.to_rgb565_pair(fg) != theme.to_rgb565_pair(bg), \
        "%s znika po kwantyzacji" % nazwa


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
    """The whole saving rests on this: we send a frame only when it differs from the
    previous one. Were the render non-deterministic, the panel would transmit non-stop."""
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
    """The difference is in the DRAWING, not in the color: when the stream dies, the
    reading age grows on both accounts at once and that looks exactly like 'the work stopped'."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    def klatka(link):
        bands = [render.band_state(a, now_ms=now_ms, show_clock=(i == 0))
                 for i, a in enumerate(fixtures.base())]
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link=link, bands=bands)).rgb565("be")
    assert len({klatka("live"), klatka("reconnecting"), klatka("down")}) == 3


def test_wiek_bierze_starsza_z_dwoch_serii():
    """The age label is the ONLY carrier of freshness here and stands next to the
    session row alone. The backend confirms each series separately, so were it to take
    the session stamp, the panel would write "1 min ago" beside a confidently drawn
    week bar from three days back."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    acc = fixtures.account(
        "u", "kto@example.pl",
        series=[fixtures.series("limit:session|session|-|-", "Session",
                                kind="session", bucketKey="five_hour",
                                utilization=31,
                                confirmedAt="2026-07-26T19:06:40Z",
                                capturedAt="2026-07-26T19:06:40Z"),
                fixtures.series("limit:weekly_all|weekly|-|-", "Week",
                                kind="weekly_all", bucketKey="seven_day",
                                utilization=30,
                                confirmedAt="2026-07-23T19:07:40Z",
                                capturedAt="2026-07-23T19:07:40Z")])
    assert render.band_state(acc, now_ms=now_ms).ago == "3 d 0 h ago"


# Pixel packing has a file of its own: tests/test_pixels.py.


def test_wycofane_kredyty_zostaja_narysowane():
    """Regression on a real case: the organization cuts credits off, the rung drops to `off`
    and the amounts row USED TO VANISH from the band. The panel then lost the only number it had
    about spending — and the amounts keep coming, because the backend gives them from the last
    measurement.

    We compare payloads: the version with amounts MUST give a different image than `off` without
    amounts, and the same one as `on` with those same amounts — because the drawing is the same,
    only the state differs.
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


# --- text wrapping ----------------------------------------------------------

@pytest.mark.parametrize("tekst,linie", [
    ("krotki", 1),
    # This `detail` fits in ONE line of the tile (420 px) and the tile is then to
    # shorten itself rather than leave an empty field — see AlertSolo.detail_box.
    ("Dump scope: session only, session and week, or all the limit windows", 1),
    ("A 6-step plan: layout.Alert, render._alert, AlertState, geometry and "
     "quantization tests, plus one more sentence for good measure", 2),
    ("Zażółć gęślą jaźń " * 20, 2),
    ("", 0),
])
def test_wrap_lines_nie_przekracza_limitow(tekst, linie):
    """`detail` is sometimes a sentence, and the tile has two lines and a width of
    420 px. Neither of those may depend on the content."""
    f = draw.font(12)
    out = draw.wrap_lines(tekst, f, 420, 2)
    assert len(out) == linie
    for line in out:
        assert draw.text_width(line, f) <= 420


def test_wrap_lines_obcina_ostatnia_linie_wielokropkiem():
    f = draw.font(12)
    out = draw.wrap_lines("Zażółć gęślą jaźń " * 20, f, 420, 2)
    assert out[-1].endswith("…"), "nadmiar ma byc widoczny, nie uciety po cichu"


def test_wrap_lines_znosi_slowo_dluzsze_niz_linia():
    """Regression: without this one long word either vanished or looped the function."""
    f = draw.font(12)
    out = draw.wrap_lines("a" * 400, f, 100, 2)
    assert len(out) == 1 and draw.text_width(out[0], f) <= 100
