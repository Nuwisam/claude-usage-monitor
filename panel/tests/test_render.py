"""The layout, the colors and the render itself. No hardware.

The tests guard the things that break quietly on 480x320: clipped tails,
rectangles running off the screen, colors lost to the 5/6/5 quantization.
"""
import pytest

from PIL import Image

from panel import draw, layout as L, render, theme, view
from tests import fixtures


# --- geometry ---------------------------------------------------------------

def test_bands_divide_screen_without_overlap():
    lay = L.Layout(480, 320)
    a, b = lay.bands
    assert a.top == 0 and b.bottom == 320
    assert a.bottom <= lay.divider[1] and lay.divider[3] <= b.top
    assert a.height + b.height + L.DIVIDER_H == 320


def test_credits_do_not_overlap_week():
    """The credits row is glued to the bottom of the band. Were the band too short,
    it would run onto the week countdown and nobody would notice that on a PNG."""
    for band in L.Layout(480, 320).bands:
        assert band.fits, "credits overlap week"


def test_everything_fits_within_screen():
    lay = L.Layout(480, 320)
    for band in lay.bands:
        for name in ("header", "ses_label", "ses_bar", "ses_line",
                     "wk_label", "wk_bar", "wk_line", "credits"):
            x0, y0, x1, y1 = getattr(band, name)
            assert 0 <= x0 < x1 <= lay.width, "%s runs off horizontally" % name
            assert band.top <= y0 < y1 <= band.bottom, "%s runs off vertically" % name


def test_number_column_and_bar_column_do_not_overlap():
    band = L.Layout(480, 320).band_a
    assert band.num_right < band.block_x0


# --- typography -------------------------------------------------------------

def test_three_digit_value_fits_in_column():
    """That is exactly why the mockup drops from 42 to 34 px at 100%. Without it
    the number would push the column apart and the bars would stop starting level."""
    f_big = draw.font(L.F_SES_NUM)
    f_tight = draw.font(L.F_SES_NUM_TIGHT)
    f_pct = draw.font(L.F_SES_PCT)
    width = lambda f: draw.text_width("100", f) + L.PCT_GAP + draw.text_width("%", f_pct)
    assert width(f_big) > L.NUM_W, "if it fit, the step-down would be unnecessary"
    assert width(f_tight) <= L.NUM_W


# i18n-keep: the Polish letters below are the measurement, not text. 'Ń' reaches higher
# than any unaccented capital and the pangram carries every tail the panel can clip, so
# translating either makes this test pass while measuring nothing. The docstring stays
# Polish with them: it is the sentence that explains what is being measured.
def test_descenders_are_not_clipped():
    """Mierzymy z faktycznego obrysu, nie z nominalnego rozmiaru: 'Ń' siega
    wyzej niz wielkie litery bez znaku diakrytycznego."""
    for size in (10, 11, 12, 13, 15):
        f = draw.font(size)
        assert draw.line_height(f) >= f.getbbox("TYDZIEŃ")[3] - f.getbbox("TYDZIEŃ")[1]
        assert draw.text_width("Zażółć gęślą jaźń", f) > 0


def test_ellipsize_does_not_exceed_width():
    f = draw.font(15)
    long_name = "very.long.account.name.that.does.not.fit@example.example.pl"
    for limit in (40, 80, 160, 300):
        assert draw.text_width(draw.ellipsize(long_name, f, limit), f) <= limit
    assert draw.ellipsize("short", f, 300) == "short", "short strings stay unchanged"


# --- colors -----------------------------------------------------------------

@pytest.mark.parametrize("fg,name", [
    (theme.TEXT, "text"), (theme.TEXT_50, "plan"), (theme.TEXT_28, "outline"),
    (theme.TEXT_10, "diagonal"), (theme.ACCENT, "accent"), (theme.ACCENT_200, "label"),
    (theme.NEUTRAL_900, "track"), (theme.DIVIDER, "separator"),
    (theme.SURFACE, "detail tile"), (theme.SUNKEN, "strip"),
])
def test_colors_survive_quantization(fg, name):
    """The panel has 5/6/5 bits. The most endangered one is the dashed outline
    (28% white on a nearly black background) — were it to blend into the background,
    the `unknown` state would become invisible."""
    assert theme.to_rgb565_pair(fg) != theme.to_rgb565_pair(theme.BG), \
        "%s gets lost against the background after quantization" % name


@pytest.mark.parametrize("fg,bg,name", [
    (theme.ACCENT_800, theme.BG, "alert banner on background"),
    (theme.ACCENT_100, theme.ACCENT_800, "alert title on banner"),
    (theme.ACCENT_200, theme.ACCENT_800, "time on banner"),
    (theme.BG, theme.ACCENT, "caption in flooded banner"),
    (theme.TEXT_78_SURFACE, theme.SURFACE, "detail in tile"),
    (theme.TEXT_45_SURFACE, theme.SURFACE, "label in tile"),
    (theme.TEXT_70_SUNKEN, theme.SUNKEN, "value in strip"),
    (theme.TEXT_72_SUNKEN, theme.SUNKEN, "detail in list footer"),
    (theme.TEXT_62_SUNKEN, theme.SUNKEN, "names of the rest in footer"),
    (theme.TEXT_45_SUNKEN, theme.SUNKEN, "label in strip and footer"),
    (theme.ACCENT_200, theme.SUNKEN, "rest counter in footer"),
    (theme.SURFACE, theme.BG, "tile on background"),
    (theme.SUNKEN, theme.BG, "strip on background"),
])
def test_color_pairs_survive_quantization(fg, bg, name):
    """A separate test from the one above, because that one compares SOLELY with `theme.BG`.

    The card's marginal pair is `ACCENT_800` on `BG` (a difference of 42, 13, 4 before
    quantization), not the text on the banner. The pairs with `SURFACE` and `SUNKEN` are here
    because the detail tile and the mode strip differ from the background by a few units —
    were they to blend after quantization, the card would lose its whole division into fields.
    """
    assert theme.to_rgb565_pair(fg) != theme.to_rgb565_pair(bg), \
        "%s disappears after quantization" % name


# --- render -----------------------------------------------------------------

@pytest.mark.parametrize("scene", sorted(fixtures.SCENES))
def test_each_scene_renders(scene):
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


def test_same_scene_gives_same_payload():
    """The whole saving rests on this: we send a frame only when it differs from the
    previous one. Were the render non-deterministic, the panel would transmit non-stop."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    def build_payload():
        bands = [render.band_state(a, now_ms=now_ms, show_clock=(i == 0))
                 for i, a in enumerate(fixtures.base())]
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link="live", bands=bands)).rgb565("be")
    assert build_payload() == build_payload()


def test_status_card_instead_of_bands():
    frame = render.Renderer().frame(render.ScreenState(
        message=["No configuration", "panel.json"]))
    assert frame.image.size == (480, 320)


def test_empty_slot_does_not_blow_up():
    frame = render.Renderer().frame(
        render.ScreenState(clock="21:07", link="down", bands=[None, None]))
    assert len(frame.rgb565("be")) == 480 * 320 * 2


def test_three_link_states_give_three_different_images():
    """The difference is in the DRAWING, not in the color: when the stream dies, the
    reading age grows on both accounts at once and that looks exactly like 'the work stopped'."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    def build_frame(link):
        bands = [render.band_state(a, now_ms=now_ms, show_clock=(i == 0))
                 for i, a in enumerate(fixtures.base())]
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link=link, bands=bands)).rgb565("be")
    assert len({build_frame("live"), build_frame("reconnecting"), build_frame("down")}) == 3


def test_age_takes_the_older_of_two_series():
    """The age label is the ONLY carrier of freshness here and appears only next to
    the session row. The backend confirms each series separately, so were it to take
    the session stamp, the panel would write "1 min ago" beside a confidently drawn
    week bar from three days back."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    acc = fixtures.account(
        "u", "who@example.pl",
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


def test_withdrawn_credits_still_get_drawn():
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

    def build_frame(**kw):
        template = fixtures.base()[1]
        cascade = [{"key": r.key, "state": r.state} for r in template.cascade
                   if r.key != "credits"]
        cascade.append(fixtures.rung("credits", **kw))
        acc = fixtures.account(template.uuid, template.email, org_type="claude_team",
                               cascade=cascade, series=[])
        band = render.band_state(acc, now_ms=now_ms)
        return render.Renderer().frame(
            render.ScreenState(clock="21:07", link="live", bands=[band, None])).rgb565("be")

    amounts = dict(usedMinor=30004, limitMinor=30000, currency="EUR", exponent=2)
    withdrawn = build_frame(state="off", reason="org_level_disabled_until", **amounts)
    empty = build_frame(state="off")
    working = build_frame(state="on", **amounts)

    assert withdrawn != empty, "the amounts row disappeared — the panel lost the number"
    assert withdrawn == working, "the panel does not draw the withdrawal reason; amounts are amounts"


# --- text wrapping ----------------------------------------------------------

@pytest.mark.parametrize("text,lines", [
    ("short", 1),
    # This `detail` fits in ONE line of the tile (420 px) and the tile is then to
    # shorten itself rather than leave an empty field — see AlertSolo.detail_box.
    ("Dump scope: session only, session and week, or all the limit windows", 1),
    ("A 6-step plan: layout.Alert, render._alert, AlertState, geometry and "
     "quantization tests, plus one more sentence for good measure", 2),
    # i18n-keep: a pangram repeated 20 times is the densest overflow this wrapper can be
    # given; the accents also prove the width measurement is not ASCII-only.
    ("Zażółć gęślą jaźń " * 20, 2),
    ("", 0),
])
def test_wrap_lines_does_not_exceed_limits(text, lines):
    """`detail` is sometimes a sentence, and the tile has two lines and a width of
    420 px. Neither of those may depend on the content."""
    f = draw.font(12)
    out = draw.wrap_lines(text, f, 420, 2)
    assert len(out) == lines
    for line in out:
        assert draw.text_width(line, f) <= 420


def test_wrap_lines_truncates_last_line_with_ellipsis():
    f = draw.font(12)
    # i18n-keep: same pangram, same reason as the parametrized case above.
    out = draw.wrap_lines("Zażółć gęślą jaźń " * 20, f, 420, 2)
    assert out[-1].endswith("…"), "the overflow must be visible, not silently cut off"


def test_wrap_lines_survives_word_longer_than_line():
    """Regression: without this one long word either vanished or looped the function."""
    f = draw.font(12)
    out = draw.wrap_lines("a" * 400, f, 100, 2)
    assert len(out) == 1 and draw.text_width(out[0], f) <= 100
