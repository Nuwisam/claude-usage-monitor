"""The 1280x720 layout: that it is picked, that it is complete, and that it fits.

The narrow layout's own geometry tests are not repeated here — the wide classes inherit
every line of that arithmetic. What is tested is what a second layout can get wrong and
the first one cannot: a constant restated in one module and not the other, a column
sized for one set of captions, and the mockup measurements this layout was built from.
"""
import re

import pytest

from panel import draw, fmt, layout as L, layout_wide as W, render, status, theme

CANVAS = (1280, 720)
NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")


def metrics(module):
    """The names a geometry class can reach through its metrics handle."""
    return {n for n in dir(module) if NAME.match(n)}


# --- picking the layout ------------------------------------------------------

def test_the_wide_canvas_gets_the_wide_layout():
    assert render.layout_for(*CANVAS) is W
    assert render.layout_for(480, 320) is L


def test_an_unknown_canvas_is_refused_by_name():
    """Not a fallback: a layout is a table of positions measured for one canvas, and on
    another it draws the same picture stranded in a corner. That reads as a broken
    screen, which is worse than a startup error naming the size."""
    with pytest.raises(ValueError) as e:
        render.layout_for(800, 480)
    assert "800x480" in str(e.value)
    assert "1280x720" in str(e.value) and "480x320" in str(e.value)


def test_config_catches_an_undrawable_canvas_before_the_renderer_does():
    """Validation, not a traceback out of `App.__init__`: under pythonw that one has no
    console to appear in and the task simply restarts every minute."""
    from panel import config as C

    ok = C.Config({"stream_token": "t", "width": 1280, "height": 720})
    assert [p for p in ok.validate() if "layout" in p] == []

    bad = C.Config({"stream_token": "t", "width": 800, "height": 480})
    problems = [p for p in bad.validate() if "layout" in p]
    assert len(problems) == 1 and "800x480" in problems[0]


# --- completeness ------------------------------------------------------------

def test_both_layouts_carry_the_same_metrics():
    """The wide classes inherit their arithmetic and read every number through the
    metrics handle, so a constant added to one module and forgotten in the other would
    silently draw the wide canvas with a narrow value."""
    assert metrics(L) == metrics(W)


@pytest.mark.parametrize("cls", ["AlertSolo", "AlertPair", "AlertList", "Layout"])
def test_the_wide_classes_descend_from_the_narrow_ones(cls):
    assert issubclass(getattr(W, cls), getattr(L, cls))


def test_the_wide_many_restates_everything_the_narrow_many_overrides():
    """`AlertMany` descends from `AlertList` in both modules, mirroring the narrow
    structure — so the wide one does NOT see `layout.AlertMany`. Anything the narrow
    `AlertMany` overrides and the wide one leaves out would therefore fall through to
    the wide LIST value: a name at 38 px where 1d wants 34, and no error anywhere."""
    assert issubclass(W.AlertMany, W.AlertList)
    narrow = {n for n in vars(L.AlertMany) if NAME.match(n)}
    wide = {n for n in vars(W.AlertMany) if NAME.match(n)}
    assert narrow <= wide, "not restated for 1280x720: %s" % sorted(narrow - wide)


@pytest.mark.parametrize("cls", ["AlertSolo", "AlertPair", "AlertList", "AlertMany"])
def test_a_wide_class_built_on_its_own_uses_wide_metrics(cls):
    """The geometry classes take their numbers as an argument and fall back to a class
    attribute. Falling back to the module they were DEFINED in would lay a wide class
    out with narrow padding — silently, since every name exists in both."""
    geo = getattr(W, cls)(*CANVAS)
    assert geo.m is W
    assert geo.x0 == W.ALERT_PAD_X


def test_the_band_is_shared_not_copied():
    """`Band` reads every number through the handle already, so a wide subclass would be
    an empty one. The wide Layout has to be building the narrow class."""
    assert W.Layout.BAND is L.Band


# --- what the mockup measured ------------------------------------------------

def test_banner_and_strip_ink_sits_where_the_mockup_says():
    """The same guard as the narrow layout's, against the 1280x720 mockup: the banner
    caption at 48.25 px and the `MODE` caption at 693.95 px, read out of the browser
    that rendered it. Pillow with anchor="ls" puts the bottom of the ink at `base - 1`.
    """
    assert W.BANNER_BASE == 48, "mockup measurement: banner baseline at 48.25 px"
    assert W.AlertSolo.MODE_BASE == 693, "mockup measurement: MODE caption at 693.95 px"

    # A MADE-UP project name, as everywhere else in this repo: the mockup this layout
    # was measured against carries real ones, and they do not travel with the geometry.
    # DERIVED, never hand-typed: a literal is a second definition of the face, and when
    # the format moves this keeps measuring the old width while still passing.
    at = fmt.panel_clock(fmt.parse_utc("2026-08-05T17:07:00Z"))
    state = render.ScreenState(alert=render.AlertState(
        title="QUESTION FOR YOU", at=at,
        rows=[render.AlertRow(short="question", project="reporting-panel",
                              tool="AskUserQuestion", machine="desktop",
                              waited="4 min", mode="default")]))
    px = render.Renderer(*CANVAS).frame(state).image.load()

    # The time in the banner: digits do not go below the baseline. The window is COMPUTED
    # from the string drawn — a hard-coded one keeps passing on the tail of a longer face
    # while measuring something else.
    at_x1 = CANVAS[0] - W.ALERT_PAD_X
    at_x0 = at_x1 - draw.text_width(at, draw.font(W.F_BANNER_AT))
    assert _ink_bottom(px, at_x0, at_x1 + 1, 0, W.BANNER_H,
                       theme.ACCENT_800) == W.BANNER_BASE - 1
    # The strip: neither "MODE" nor "default" has a descender.
    assert _ink_bottom(px, 40, 700, 720 - W.AlertSolo.MODE_H, 720,
                       theme.SUNKEN) == W.AlertSolo.MODE_BASE - 1


def test_the_reason_column_fits_every_word_it_can_hold():
    """`ALLOW` is a width constraint, not a synonym — and the constraint has to hold on
    both canvases. `question` is the longest of them and the tightest fit."""
    for module in (L, W):
        cls = module.AlertList
        font = draw.font(cls.F_REASON)
        for word in set(status.SHORT.values()):
            w = draw.tracked_width(word.upper(), font, cls.REASON_TRACK)
            assert w <= cls.REASON_W, (
                "%s overruns the reason column: %d px against %d"
                % (word.upper(), w, cls.REASON_W))


# --- the geometry still closes ----------------------------------------------

def test_the_bands_do_not_run_into_their_credits():
    lay = W.Layout(*CANVAS)
    for band in lay.bands:
        assert band.fits


@pytest.mark.parametrize("lines", [1, 2])
def test_the_detail_tile_stays_clear_of_the_mode_strip(lines):
    assert W.Layout(*CANVAS).alert_solo.fits(lines)


def test_the_list_rows_fill_the_card_exactly():
    """The remainder of the division goes where the mockup's browser puts it, so the
    rows plus their dividers plus the footer have to come to the full height."""
    for cls in (W.AlertList, W.AlertMany):
        geo = cls(*CANVAS)
        rows = geo.rows(footer=True)
        assert rows[0][0] == W.BANNER_H
        assert rows[-1][1] == 720 - cls.FOOTER_H
        for (_, bottom), (top, _) in zip(rows, rows[1:]):
            assert top - bottom == W.DIVIDER_H


def _ink_bottom(px, x0, x1, y0, y1, bg, threshold=25):
    """The last row in which there is ink inside the given rectangle."""
    last = None
    for y in range(y0, y1):
        for x in range(x0, x1):
            if max(abs(px[x, y][i] - bg[i]) for i in range(3)) > threshold:
                last = y
                break
    return last
