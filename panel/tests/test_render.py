"""The layout, the colors and the render itself. No hardware.

The tests guard the things that break quietly on 480x320: clipped tails,
rectangles running off the screen, colors lost to the 5/6/5 quantization.
"""
import pytest

from PIL import Image

from panel import draw, fmt, layout as L, layout_wide as LW, render, theme, view
from tests import fixtures

#: The clock face the panel actually draws — DERIVED from the formatter, not hand-typed,
#: so the scenes cannot keep exercising a face the code stopped producing. The run of
#: glyphs matters here: it is what competes with the account name for the header.
CLOCK = fmt.panel_clock(fmt.parse_utc("2026-08-30T19:07:33Z"))

#: Every shape a band can take, as (scoped weekly window, credits drawn, pair glued). A
#: shape is only reached by the data and the configuration that select it, so one of them
#: fitting says nothing about the rest.
SHAPES = [(s, c, g) for s in (False, True)
          for c in (False, True) for g in (False, True)]


# --- geometry ---------------------------------------------------------------

def test_bands_divide_screen_without_overlap():
    lay = L.Layout(480, 320)
    a, b = lay.bands
    assert a.top == 0 and b.bottom == 320
    assert a.bottom <= lay.divider[1] and lay.divider[3] <= b.top
    assert a.height + b.height + L.DIVIDER_H == 320


def test_credits_do_not_overlap_week():
    """The credits row is glued to the bottom of the band and the rungs share out what is
    left above it. Were the band too short, they would run into each other and nobody
    would notice that on a PNG.

    `fits` spans EVERY shape the band can take, so this asks the question once per shape
    rather than once per band."""
    for band in L.Layout(480, 320).bands:
        assert band.fits, "credits overlap week"


@pytest.mark.parametrize("mod, size", ((L, (480, 320)), (LW, (1280, 720))))
def test_fits_is_judged_per_cell_not_in_aggregate(mod, size, monkeypatch):
    """`_cells` shares the span out EQUALLY while the rungs are not equal, and `_rung`
    centres its content, so a rung too tall for its own cell spills at BOTH ends -- which
    the last-row check alone cannot see. An aggregate `sum(heights) + gaps <= span`
    swallows exactly that: one rung grows into its neighbour's slack and the total still
    fits.

    The test DISTINGUISHES the two predicates rather than merely exercising the strict
    one. Grow the session bar until `fits` goes red, then check that at that very size the
    aggregate sum still fits the span -- so the old check was green exactly where the new
    one is red. Both canvases, because they have different slack and a nudge that
    overflows one clears the other easily."""
    def widest_shape_is_over(band):
        """Whether the tallest rung of any shape has outgrown its cell's share."""
        return not band.fits

    def aggregate_still_fits(band):
        """The predicate this replaced, recomputed from the shape's own boxes."""
        for scoped, credits, glue in SHAPES:
            shape = band.shape(scoped, credits, glue)
            rows = [(r.label[1], r.bar[3]) for r in shape.rungs]
            if shape.pair is not None:
                rows.append((shape.pair.label[1], shape.pair.bars[-1][3]))
            span = (shape.credits[1] if shape.credits else band.bottom) - band.rows_top
            if sum(b - t for t, b in rows) + (len(rows) - 1) * mod.ROW_GAP > span:
                return False
        return True

    assert all(band.fits for band in mod.Layout(*size).bands), "green before the nudge"

    for extra in range(1, 60):
        monkeypatch.setattr(mod, "SES_BAR_H", mod.SES_BAR_H + 1)
        bands = mod.Layout(*size).bands
        if any(widest_shape_is_over(b) for b in bands):
            assert all(aggregate_still_fits(b) for b in bands), (
                "at +%d the old aggregate check was ALREADY red, so this nudge proves "
                "nothing about per-cell containment" % extra)
            return
    pytest.fail("`fits` never went red: growing a rung by 59 px must overflow its cell")


def test_everything_fits_within_screen():
    """Every box of every shape, on both canvases -- a shape is only reached by the data
    that selects it, so a band that fits in one of them says nothing about the others."""
    for mod, size in ((L, (480, 320)), (LW, (1280, 720))):
        lay = mod.Layout(*size)
        for band in lay.bands:
            for scoped, credits, glue in SHAPES:
                shape = band.shape(scoped, credits, glue)
                boxes = [("header", band.header)]
                boxes += [(r.key + " label", r.label) for r in shape.rungs]
                boxes += [(r.key + " bar", r.bar) for r in shape.rungs]
                if shape.pair is not None:
                    boxes.append(("pair label", shape.pair.label))
                    boxes += [("pair track", bar) for bar in shape.pair.bars]
                if shape.credits is not None:
                    boxes.append(("credits", shape.credits))
                for name, (x0, y0, x1, y1) in boxes:
                    where = "%s (%dx%d, scoped=%s credits=%s glue=%s)" % (
                        name, size[0], size[1], scoped, credits, glue)
                    assert 0 <= x0 < x1 <= lay.width, "%s runs off horizontally" % where
                    assert band.top <= y0 < y1 <= band.bottom, \
                        "%s runs off vertically" % where


def test_the_rungs_do_not_run_into_one_another():
    """The share-out gives each row its own cell; nothing may cross a cell boundary."""
    for mod, size in ((L, (480, 320)), (LW, (1280, 720))):
        for band in mod.Layout(*size).bands:
            for scoped, credits, glue in SHAPES:
                shape = band.shape(scoped, credits, glue)
                assert band.header[3] <= shape.rungs[0].top
                for a, b in zip(shape.rungs, shape.rungs[1:]):
                    assert a.bottom < b.top, "%s runs into %s" % (a.key, b.key)
                lowest = shape.rungs[-1]
                if shape.pair is not None:
                    assert lowest.bottom < shape.pair.top, "session runs into the pair"
                    lowest = shape.pair
                if shape.credits is not None:
                    assert lowest.bottom <= shape.credits[1]


def test_the_shape_table_is_the_rule_it_was_asked_for():
    """The shapes ARE the decision, so they are asserted rather than described: a scoped
    window adds a third row, and the two weekly windows share it only when the band also
    carries the credits AND the configuration allows it."""
    for mod, size in ((L, (480, 320)), (LW, (1280, 720))):
        band = mod.Layout(*size).band_a
        for scoped, credits, glue in SHAPES:
            shape = band.shape(scoped, credits, glue)
            glued = scoped and credits and glue
            keys = [r.key for r in shape.rungs]
            assert keys == (["session"] if glued else
                            ["session", "week", "scoped"] if scoped else
                            ["session", "week"]), keys
            assert (shape.pair is not None) is glued
            if glued:
                assert shape.pair.keys == ("week", "scoped")
            assert (shape.credits is not None) is credits


def test_the_pair_never_appears_without_the_credits_that_call_for_it():
    """The glue is not a way of saving room -- three rungs and the credits fit on both
    canvases -- so it must never fire on a band that is not that dense."""
    for mod, size in ((L, (480, 320)), (LW, (1280, 720))):
        for band in mod.Layout(*size).bands:
            assert band.shape(True, False, True).pair is None
            assert band.shape(False, True, True).pair is None
            assert band.shape(True, True, False).pair is None
            assert band.shape(True, True, True).pair is not None


def test_number_column_and_bar_column_do_not_overlap():
    band = L.Layout(480, 320).band_a
    assert band.num_right < band.block_x0


# --- typography -------------------------------------------------------------

def test_three_digit_value_fits_in_column():
    """That is exactly why the mockup drops from 42 to 34 px at 100%. Without it
    the number would force the column wider and the bars would no longer start level."""
    f_big = draw.font(L.F_SES_NUM)
    f_tight = draw.font(L.F_SES_NUM_TIGHT)
    f_pct = draw.font(L.F_SES_PCT)
    width = lambda f: draw.text_width("100", f) + L.PCT_GAP + draw.text_width("%", f_pct)
    assert width(f_big) > L.NUM_W, "if it fit, the step-down would be unnecessary"
    assert width(f_tight) <= L.NUM_W


# The accented glyphs below are the measurement, not text. 'Ń' reaches higher than any
# unaccented capital and the sample carries every tail the panel can clip, so replacing
# either with plain letters makes this test pass while measuring nothing.
def test_descenders_are_not_clipped():
    """Measured from the actual glyph outline, not from the nominal size: 'Ń' reaches
    higher than capital letters carrying no diacritic."""
    for size in (10, 11, 12, 13, 15):
        f = draw.font(size)
        assert draw.line_height(f) >= f.getbbox("TYDZIEŃ")[3] - f.getbbox("TYDZIEŃ")[1]
        assert draw.text_width("Zażółć gęślą jaźń", f) > 0


def test_ellipsize_does_not_exceed_width():
    f = draw.font(15)
    long_name = "very.long.account.name.that.does.not.fit@example.example.org"
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
    were they to blend after quantization, the card would lose all separation between its areas.
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
        render.ScreenState(clock=CLOCK, link="live", bands=bands))
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
            render.ScreenState(clock=CLOCK, link="live", bands=bands)).rgb565("be")
    assert build_payload() == build_payload()


def test_status_card_instead_of_bands():
    frame = render.Renderer().frame(render.ScreenState(
        message=["No configuration", "panel.json"]))
    assert frame.image.size == (480, 320)


def test_empty_slot_does_not_blow_up():
    frame = render.Renderer().frame(
        render.ScreenState(clock=CLOCK, link="down", bands=[None, None]))
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
            render.ScreenState(clock=CLOCK, link=link, bands=bands)).rgb565("be")
    assert len({build_frame("live"), build_frame("reconnecting"), build_frame("down")}) == 3


def test_age_takes_the_older_of_two_series():
    """The age label is the ONLY carrier of freshness here and appears only next to
    the session row. The backend confirms each series separately, so were it to take
    the session stamp, the panel would write "1 min ago" beside a confidently drawn
    week bar from three days back."""
    from panel import fmt
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    acc = fixtures.account(
        "u", "who@example.org",
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


# --- the scoped window that stops being reported ----------------------------

def _scoped_scene(scoped_confirmed, scoped_util=50):
    """A band whose session and week are live and whose scoped window is as given."""
    live = "2026-07-26T19:06:40Z"
    return fixtures.account(
        "u", "who@example.org",
        series=[fixtures.series("limit:session|session|-|-", "Session",
                                kind="session", bucketKey="five_hour", utilization=31,
                                confirmedAt=live, capturedAt=live),
                fixtures.series("limit:weekly_all|weekly|-|-", "Week",
                                kind="weekly_all", bucketKey="seven_day", utilization=30,
                                confirmedAt=live, capturedAt=live),
                fixtures.series("limit:weekly_scoped|weekly|fable|-", "Week — Fable",
                                kind="weekly_scoped", utilization=scoped_util,
                                confirmedAt=scoped_confirmed,
                                capturedAt=scoped_confirmed)])


def test_a_scoped_window_nobody_reports_any_more_is_withdrawn():
    """Its row survives forever once it has carried a value and its confirmation never
    advances again, so drawn it would show a measurement from an unbounded time ago AND
    drag the band's one freshness label back with it."""
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    fresh = render.band_state(_scoped_scene("2026-07-26T19:06:40Z"), now_ms=now_ms)
    assert fresh.scoped is not None and fresh.ago == "1 min ago"

    # An hour behind the live windows: past the threshold, so the rung goes and the age
    # describes exactly what is left on the glass.
    gone = render.band_state(_scoped_scene("2026-07-26T18:07:40Z"), now_ms=now_ms)
    assert gone.scoped is None
    assert gone.ago == "1 min ago", "a withdrawn rung must not pin the band's age"


def test_the_threshold_sits_above_what_a_reported_window_ever_lagged():
    """MEASURED on this deployment: 5817 readings where the scoped window appears beside
    the band's other windows, worst lag 445 s, none over an hour. The threshold has to
    clear that with room and still be well under the backend's own 6 h silence horizon."""
    assert 445 * 2 < render.SCOPED_WITHDRAWN_LAG_S < 6 * 3600

    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    # The worst lag production has ever shown is still being reported.
    worst = render.band_state(_scoped_scene("2026-07-26T18:59:15Z"), now_ms=now_ms)
    assert worst.scoped is not None, "445 s behind is a late batch, not a withdrawal"


def test_a_client_gone_quiet_does_not_withdraw_anything():
    """Judged on the stamps, not on freshness: when the client itself stops reporting,
    every window turns stale TOGETHER and the old age is the truth. Only a window that
    falls behind the band's own live ones has been withdrawn."""
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    old = "2026-07-20T19:07:40Z"
    acc = fixtures.account(
        "u", "who@example.org",
        series=[fixtures.series("limit:session|session|-|-", "Session",
                                kind="session", bucketKey="five_hour", utilization=31,
                                confirmedAt=old, capturedAt=old),
                fixtures.series("limit:weekly_all|weekly|-|-", "Week",
                                kind="weekly_all", bucketKey="seven_day", utilization=30,
                                confirmedAt=old, capturedAt=old),
                fixtures.series("limit:weekly_scoped|weekly|fable|-", "Week — Fable",
                                kind="weekly_scoped", utilization=50,
                                confirmedAt=old, capturedAt=old)])
    assert render.band_state(acc, now_ms=now_ms).scoped is not None


def test_a_withdrawn_series_cannot_outrank_a_live_one():
    """The ordering defect: the freshness test used to be applied to the WINNER. A stale
    series carrying the higher number won the ranking and was then rejected, leaving an
    account whose scoped window is live with no scoped rung at all."""
    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    live = "2026-07-26T19:06:40Z"
    acc = fixtures.account(
        "u", "who@example.org",
        series=[fixtures.series("limit:session|session|-|-", "Session",
                                kind="session", bucketKey="five_hour", utilization=31,
                                confirmedAt=live, capturedAt=live),
                fixtures.series("limit:weekly_all|weekly|-|-", "Week",
                                kind="weekly_all", bucketKey="seven_day", utilization=30,
                                confirmedAt=live, capturedAt=live),
                # Withdrawn, and the higher of the two.
                fixtures.series("limit:weekly_scoped|weekly|opus|-", "Week — Opus",
                                kind="weekly_scoped", utilization=90,
                                confirmedAt="2026-07-25T19:07:40Z",
                                capturedAt="2026-07-25T19:07:40Z"),
                # Live, and the lower.
                fixtures.series("limit:weekly_scoped|weekly|fable|-", "Week — Fable",
                                kind="weekly_scoped", utilization=12,
                                confirmedAt=live, capturedAt=live)])
    band = render.band_state(acc, now_ms=now_ms)
    assert band.scoped is not None, "the live scoped window still has a rung"
    assert band.scoped_label == "FABLE"


# Pixel packing has a file of its own: tests/test_pixels.py.


def test_withdrawn_credits_still_get_drawn():
    """Regression on a real case: the organization cuts credits off, the rung drops to `off`
    and the amounts row USED TO VANISH from the band. The panel then lost the only number it had
    about spending — and the amounts keep coming, because the backend reports them from the last
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
            render.ScreenState(clock=CLOCK, link="live", bands=[band, None])).rgb565("be")

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
    # shorten itself rather than leave empty space — see AlertSolo.detail_box.
    ("Dump scope: session only, session and week, or all the limit windows", 1),
    ("A 6-step plan: layout.Alert, render._alert, AlertState, geometry and "
     "quantization tests, plus one more sentence for good measure", 2),
    # An accented line repeated 20 times is the densest overflow this wrapper can be
    # given; the accents also prove the width measurement is not ASCII-only.
    ("Zażółć gęślą jaźń " * 20, 2),
    ("", 0),
])
def test_wrap_lines_does_not_exceed_limits(text, lines):
    """`detail` is sometimes a sentence, and the tile has two lines and a width of
    420 px. Neither of those must depend on the content."""
    f = draw.font(12)
    out = draw.wrap_lines(text, f, 420, 2)
    assert len(out) == lines
    for line in out:
        assert draw.text_width(line, f) <= 420


def test_wrap_lines_truncates_last_line_with_ellipsis():
    f = draw.font(12)
    # Same sample, same reason as the parametrized case above.
    out = draw.wrap_lines("Zażółć gęślą jaźń " * 20, f, 420, 2)
    assert out[-1].endswith("…"), "the overflow must be visible, not silently cut off"


def test_wrap_lines_survives_word_longer_than_line():
    """Regression: without this one long word either vanished or sent the function into
    an endless loop."""
    f = draw.font(12)
    out = draw.wrap_lines("a" * 400, f, 100, 2)
    assert len(out) == 1 and draw.text_width(out[0], f) <= 100
