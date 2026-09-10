"""Geometry of layout 4a. Every coordinate is DERIVED from a handful of constants.

4a: two accounts in full-width bands; in each band the percentage sits in a narrow
column BESIDE the group (label / bar / reset), and the credits sit at the bottom of
the band. The gain over a layout with the number above the bar: the label and the
caption share the height with the number instead of sitting above and below a bar
that runs the full width.

The font sizes and bar heights come from the mockup and are justified there: three
text steps and nothing in between (10 px for uppercase labels only, 11 px for
secondary data, 12 px and up for content), and bars in three thicknesses by the
weight of the window.
"""
import sys

#: This module's own numbers, as a handle the geometry classes read through instead of
#: the globals. A second layout is a second module with the same names and different
#: values; pointing the classes at it reuses every line of arithmetic below verbatim,
#: which is the only way two layouts can be guaranteed to STACK the same way and to
#: differ only where the mockup differs. Callers that pass nothing get these.
METRICS = sys.modules[__name__]

PAD_X = 14
PAD_TOP = 9
PAD_BOT = 9
ROW_GAP = 6
INNER_GAP = 4

NUM_W = 76          # the percentage column; deliberately tight so the bars all start level
NUM_GAP = 12
PCT_GAP = 2         # gap between the number and the % sign

HEADER_H = 17
#: The label's line, which also carries the reset caption at its right end -- so it is
#: the CAPTION's size that governs the height, not the label's.
LABEL_H = 12

SES_BAR_H = 12
WK_BAR_H = 9
CREDITS_BAR_H = 5
CREDITS_H = 14

# --- the glued weekly pair (mockup 6g) ---------------------------------------
#
# The two weekly windows in ONE row of two tracks, under one label and one countdown.
# Not a way of saving room -- three separate rungs and the credits fit on both canvases
# -- but a way of reading: the two are the same seven days seen at two scopes, and
# "how much of it is the week and how much is the model" is one glance rather than a
# jump down the band. `glue_aligned_weekly_pair` in panel.json turns it off -- and note the
# `aligned`: the switch can withhold the gluing, never force it, because a row with one
# countdown is only honest while both windows close at the same instant.
PAIR_GAP = 4            # label to the first track, and track to track
PAIR_BAR_H = 8
PAIR_NUM_PITCH = 22     # between the CENTRES of the two stacked numbers

# Fonts
F_NAME = 15
F_PLAN = 10
F_CLOCK = 14
F_LABEL = 10
F_RESET = 12
F_AGO = 11
F_SES_NUM = 42
F_SES_NUM_TIGHT = 34    # a three-digit value drops one step, otherwise it does not fit
F_SES_PCT = 14
F_WK_NUM = 30
F_WK_PCT = 12
#: In the glued shape the session pays for the third rung: it steps down so the pair and
#: the credits have room, and the pair's own numbers are smaller again than a lone week's.
F_SES_NUM_PAIR = 34
F_SES_PCT_PAIR = 12
SES_BAR_H_PAIR = 11
F_PAIR_NUM = 20
F_PAIR_PCT = 10
F_CREDITS_USED = 14
F_CREDITS_LIMIT = 11
F_WORDS = 13            # "unknown" instead of a number

DIVIDER_H = 1

# The block reason in the band header, in the right-hand group.
#
# RE-MEASURED after the reading age moved into that group, which is what the earlier figures
# here (202 px / 259 px) predate: the room left for the account name is now 146 px in the top
# band and 296 px in the bottom one, and one uppercase word plus its gap costs 64 px of it.
# In the BOTTOM band that is still the old story — a fifth of the budget, biting only a name
# that was being truncated anyway. In the TOP band, which also carries the clock, it is 44 %
# of what the name has, so a blocked session there shortens the address visibly. That is the
# trade this constant makes, and it is worth restating whenever the header's group list moves.
F_REASON = 10
#: The plan badge, glued to the account name: the plan qualifies the ACCOUNT, so it reads
#: as part of the identifier. Tighter than HEAD_GAP, which separates the header's groups
#: from one another — this one joins two halves of one thing.
PLAN_GAP = 6

# --- the pieces render.py used to place with a bare number --------------------
#
# Each of these carries SIZE: a gap, a radius, an offset from the edge of a box. They
# were literals inside render.py, which is why they sit here rather than in a geometry
# class — a class holds rectangles, and the renderer needs these where it has no
# rectangle to hang them off. A layout on another canvas restates them; left behind,
# they would put 480 px gaps and a 5 px glyph on a canvas of any size.
HEAD_GAP = 8            # the clock to the plan badge, and the reason to the account name
CLOCK_MARK_W = 14       # room kept right of the clock for the link mark
CLOCK_DY = 1            # the clock's ascender, from the top of the header
BADGE_DY = 5            # the plan badge and the reason, from the top of the header
#: Two corrections for text this renderer anchors by its TOP. Pillow measures that top
#: from the font's ascender, CSS spreads the line box's slack evenly above and below the
#: typeface — so the same box puts the panel's ink lower, by more the larger the type.
#: Zero here: at this size the difference is under two pixels and the narrow layout was
#: measured with it already in.
NAME_DY = 0             # the account name, from the top of the header
LABEL_DY = 0            # SESSION 5 H / WEEK, from the top of their box
#: And one for text centred on a rectangle. The mockup sets the percentage in a line box
#: tighter than the digits (line-height 0.82 and 0.86), which lifts the ink above the
#: centre of the box it sits in; this renderer centres the ink itself.
NUM_DY = 0              # the percentage, from the centre of its window
CREDITS_DY = 0          # the amount and its "/ limit", from the centre of the credits row
LINK_DX = 4             # the link mark's centre, in from the right edge -- ALERT CARD only
#: In a band the mark stands between the reading age and the clock, on their shared
#: baseline, so what it needs is how far ABOVE that baseline its centre sits: roughly the
#: middle of the clock's x-height, or the dot rides on the digits' feet.
LINK_DY = 5
LINK_R = 3              # the dot, and the ring when it is not filled
LINK_CROSS = 4          # the arm of the cross over a dead link

#: The caption shares the label's line, at its right end. RESET_DY corrects its centre
#: against the centre of that box -- the label is anchored by its top and the caption by
#: its middle, so the two do not agree by construction. RESET_GAP is the least room left
#: between the label's tail and the caption before the label starts being cut.
RESET_DY = 0
RESET_GAP = 8
#: The reading age needs no constant of its own: it stands in the header on the CLOCK's
#: baseline, which `render._header` derives from CLOCK_DY and the clock's ascender. A dot
#: of its own would be a second accent dot beside the link mark, so it has none.

CREDITS_LABEL_DY = 5    # the CREDITS label rides above the centre line of the row
#: The arrow marking the rung that limits now, as (left, up, right, down) from the
#: label's left edge and the row's centre. One tuple: four names for one seven-pixel
#: glyph would read worse than the literal they replace.
CREDITS_ARROW = (11, 4, 4, 2)
CREDITS_NODATA_GAP = 8  # after "no data", before the dashed placeholder
CREDITS_TAIL_GAP = 4    # the amount, then "/ limit currency"
CREDITS_TAIL_DY = 1     # the tail rides one step lower: a smaller size on a common centre
CREDITS_BAR_GAP = 10    # ... and then the bar

F_EMPTY = 13            # "second account not configured"


# --- blocked-session card ----------------------------------------------------
#
# Its own padding, wider than in a band: the card has one column of content and a band
# has three, so the 14 px that is a compromise in a band would be cramped for no reason here.
ALERT_PAD_X = 18
BANNER_H = 38
# Baseline of the captions in the banner, MEASURED on the rendered mockup: the bottom of
# the clock digits sits there at 24.33 px, and Pillow with anchor="ls" puts the bottom of
# the ink at `base - 1`. It was 26, i.e. 1.67 px too low in each of the four layouts —
# not typeface noise, just the constant.
BANNER_BASE = 24
F_BANNER = 15
BANNER_TRACK = 2        # 0.13em at 15 px
F_BANNER_AT = 15
BANNER_AT_GAP = 12      # between the time and the tail of the caption

# Rail on the left edge of the card, below the banner. It is present in BOTH frames:
# `NEUTRAL_900` in the resting one, `ACCENT` in the full one — flooding repaints the rail,
# it does not conjure it into existence. The run of color takes up the banner's height, and below that
# only these 6 px — the card background stays `theme.BG`, because a full-screen field
# of accent at brightness 5 is a glare in the eyes and visible RGB565 banding.
RAIL_W = 6

# Gap on both sides of the dot in the "tool · machine" row — but ONLY in layout
# 1a, where the mockup builds that row out of three boxes with `gap: 7px` and a dimmed
# dot. Layouts 1b and 1c have ONE run of text there, with ordinary spaces.
META_DOT_GAP = 7

# Marker next to the account once the card is folded away. It sits inside the band's
# padding (PAD_X 14), so the layout of the bands does not shift by a single pixel.
MARK_W = 4


class AlertSolo:
    """A single block. The project name is the hero, because there is a specific window
    to go back to — and with one block it is clear which one."""

    #: Which numbers this class falls back to when the caller passes none. A subclass on
    #: another canvas points it at its own module; without that, a wide class built on
    #: its own would silently lay itself out with narrow metrics.
    M = METRICS

    # BASELINES, measured on the rendered mockup — not computed from the CSS boxes.
    #
    # Why: the panel typeface (Segoe UI) has different vertical metrics than the mockup
    # font, so the same box model puts the ink 1-3 px elsewhere. The gap between boxes is
    # invisible, the position of the ink is visible — so the ink is the contract. Checked
    # by measurement in tests/test_alert.py::test_banner_and_strip_ink_sits_where_the_mockup_says.
    PROJECT_BASE = 87
    META_BASE = 112
    WAITED_BASE = 158
    DETAIL_TOP = 180
    DETAIL_LABEL_BASE = 19  # both from the TOP EDGE of the tile, because the tile is the anchor
    DETAIL_TEXT_BASE = 37
    MODE_BASE = 306         # measured 306.33; the top edge of the strip agrees to the pixel

    F_PROJECT = 34
    F_PROJECT_TIGHT = 26    # like F_SES_NUM -> F_SES_NUM_TIGHT: one step down, not a new mechanism
    F_META = 12
    F_WAITED = 26
    F_DETAIL_LABEL = 10
    LH_DETAIL_LABEL = 10
    F_DETAIL = 12
    DETAIL_LINE = 16        # 1.35 x 12 px, rounded down, as the ADVANCE between lines
    #: The same 1.35 x F_DETAIL in TENTHS, because the tile's height is truncated once
    #: over the whole block and not per line — see `detail_box`.
    DETAIL_LINE_10 = 162
    DETAIL_LINES = 2
    DETAIL_PAD_X = 12
    DETAIL_PAD_Y = 10
    DETAIL_GAP = 5
    DETAIL_RADIUS = 4
    MODE_H = 34
    F_MODE_LABEL = 10
    F_MODE = 11
    MODE_GAP = 8            # between the MODE label and the value

    def __init__(self, width, height, m=None):
        m = m or self.M
        self.m = m
        self.width = width
        self.height = height
        self.banner = (0, 0, width, m.BANNER_H)
        self.x0 = m.ALERT_PAD_X
        self.x1 = width - m.ALERT_PAD_X

        self.project_base = self.PROJECT_BASE
        self.meta_base = self.META_BASE
        self.waited_base = self.WAITED_BASE
        self.detail_y = self.DETAIL_TOP

        self.mode = (0, height - self.MODE_H, width, height)
        self.mode_centre = height - self.MODE_H // 2

    def detail_box(self, lines):
        """The detail tile grows with the number of lines, not the other way round: a
        one-line `detail` has no reason to leave empty space below itself.

        The height of the text block is truncated DOWNWARD. The mockup's browser keeps the block
        in fractions — one line at line-height 1.35 is 16.2 px — and only the tile EDGE
        lands on the pixel grid: 20 + 10 + 5 + 16.2 = 51.2 px paints as 51, not 52.
        Rounding the block itself up gave a tile one pixel too tall (measured on the
        rendered mockup: 51 px, the panel drew 52).
        """
        n = max(1, lines)
        block = (self.DETAIL_LINE_10 * n) // 10
        h = 2 * self.DETAIL_PAD_Y + self.F_DETAIL_LABEL + self.DETAIL_GAP + block
        return (self.x0, self.detail_y, self.x1, self.detail_y + h)

    def fits(self, lines):
        """Whether the detail tile stays clear of the mode strip. Held to it by
        tests/test_alert.py::test_detail_tile_does_not_run_into_the_mode_strip."""
        return self.detail_box(lines)[3] <= self.mode[1]


class AlertPair:
    """Two blocks — two equal halves. The same type size and the same set of fields
    in both: precedence is carried by the ORDER from `status.parse_frame`, not by
    typography. Up to two blocks the project name stays the hero, because there is a
    specific window to go back to and it has to be clear which one."""

    M = METRICS
    F_SHORT = 10
    SHORT_TRACK = 1         # 0.09em at 10 px
    F_WAITED = 17
    F_PROJECT = 30
    F_META = 11
    F_DETAIL = 12
    GAP = 10                # between the reason and the time in the top row of a half

    # Baselines counted from the TOP OF THE HALF, measured on the mockup (as in AlertSolo).
    #
    # The mockup has halves of 140.5 px with the content centered vertically, so ITS OWN
    # two halves differ by a pixel — once the sub-pixel falls upward, once downward. That
    # is not reproduced here: both halves get the same offsets, because lying next to each
    # other they have to look the same. The error against the mockup is at most 1 px per half.
    SHORT_BASE = 37         # the reason and the time sit on one baseline
    PROJECT_BASE = 73
    META_BASE = 91
    DETAIL_BASE = 113

    def __init__(self, width, height, m=None):
        m = m or self.M
        self.m = m
        self.width = width
        self.height = height
        self.x0 = m.ALERT_PAD_X
        self.x1 = width - m.ALERT_PAD_X
        # 282 px for two halves with one hairline of divider: 281 px of content splits
        # into 140.5 px each, and the mockup's browser gives the remainder to the BOTTOM half — the
        # divider lands on row 178, not 179. Measured on the rendered mockup; an earlier
        # comment claimed the opposite, and that is where that one line of difference came from.
        top = m.BANNER_H
        self.divider_y = top + (height - top - m.DIVIDER_H) // 2
        self.divider = (0, self.divider_y, width, self.divider_y + m.DIVIDER_H)
        self.halves = ((top, self.divider_y),
                       (self.divider_y + m.DIVIDER_H, height))


class AlertList:
    """Three blocks — three equal rows. The threshold is exactly here: three project
    names at 34 px do not exist, so the name drops to 19 px and the reason moves to a
    fixed column on the left. The NEWEST DETAIL lands in the footer — one of them,
    because three would not fit at any readable size."""

    M = METRICS
    ROWS = 3
    FOOTER_H = 49
    REASON_W = 58
    COL_GAP = 12
    TIME_W = 62             # time column, flush right; fixed, so the names all end level

    F_REASON = 10
    REASON_TRACK = 1        # 0.09em at 10 px
    F_PROJECT = 19
    F_META = 11
    F_TIME = 14
    F_FOOT_LABEL = 10
    F_FOOT = 12

    # Baselines from the TOP OF THE ROW, measured on the mockup (the middle row — the
    # error then spreads to both outer rows instead of accumulating in one).
    REASON_BASE = 42
    PROJECT_BASE = 37
    META_BASE = 54
    TIME_BASE = 43
    # ... and from the top of the footer.
    FOOT_LABEL_BASE = 18
    FOOT_TEXT_BASE = 35

    FOOT_LABEL = "NEWEST DETAIL"

    def __init__(self, width, height, m=None):
        m = m or self.M
        self.m = m
        self.width = width
        self.height = height
        self.x0 = m.ALERT_PAD_X
        self.x1 = width - m.ALERT_PAD_X
        self.name_x = self.x0 + self.REASON_W + self.COL_GAP
        self.name_x1 = self.x1 - self.TIME_W - self.COL_GAP

        self.footer = (0, height - self.FOOTER_H, width, height)

    def rows(self, footer=True):
        """The rows with the footer and without it. With no detail the footer has nothing
        to show, and an empty band at the bottom would read as a cut-off screen.

        The remainder of the division goes where the mockup's browser puts it: the heights are
        computed from FRACTIONAL boundaries, not through `//`. With 242 px for three rows
        that gives 81 / 80 / 81, and not 80 / 80 / 80 plus two pixels of background at the footer.
        """
        top = self.m.BANNER_H
        bottom = self.footer[1] if footer else self.height
        span = bottom - top - (self.ROWS - 1) * self.m.DIVIDER_H
        out = []
        y = top
        for i in range(self.ROWS):
            h = (round(span * (i + 1) / self.ROWS)
                 - round(span * i / self.ROWS))
            out.append((y, y + h))
            y += h + self.m.DIVIDER_H
        return out


class AlertMany(AlertList):
    """Four blocks and more. The same three rows as in 1c, but the name drops one
    step and the tool disappears: with five blocks the detail of any one of them is
    arbitrary, so in its place the footer counts the rest and lists them by name.

    The counter in the banner counts ALL of them, not only the ones written out."""

    FOOTER_H = 38           # one line instead of two, because here the footer has no label above the text
    F_PROJECT = 17
    # The machine line takes F_META, inherited: `render._alert_row` reads that name in
    # both branches. A separate F_MACHINE stood here and nothing read it — a size that
    # a second layout would restate and still not be drawn with.

    REASON_BASE = 44
    PROJECT_BASE = 39
    META_BASE = 55
    TIME_BASE = 45
    FOOT_BASE = 22          # the label and the names on ONE baseline
    FOOT_GAP = 10


class Rung:
    """One window inside a band: its two boxes, and the type its number is set in.

    The sizes travel WITH the rung rather than being picked by the renderer from the
    rung's name, because the same window is set differently in different shapes -- the
    session steps down when the band has to carry three rungs and the credits.
    """

    __slots__ = ("key", "label", "bar", "top", "bottom", "centre",
                 "f_num", "f_num_tight", "f_pct")

    def __init__(self, key, label, bar, f_num, f_num_tight, f_pct):
        self.key = key
        self.label = label
        self.bar = bar
        self.top = label[1]
        self.bottom = bar[3]
        self.centre = (self.top + self.bottom) // 2
        self.f_num = f_num
        self.f_num_tight = f_num_tight
        self.f_pct = f_pct


class Pair:
    """The two weekly windows in ONE row: one label line, two tracks under it, and
    their two numbers stacked in the number column."""

    __slots__ = ("keys", "label", "bars", "centres", "top", "bottom", "centre",
                 "f_num", "f_num_tight", "f_pct")

    def __init__(self, keys, label, bars, centres, f_num, f_pct):
        self.keys = keys
        self.label = label
        self.bars = bars
        self.centres = centres
        self.top = label[1]
        self.bottom = bars[-1][3]
        self.centre = (self.top + self.bottom) // 2
        self.f_num = f_num
        # A pair's number never steps down: at this size "100" already fits the column,
        # so `big` and `tight` are one and the same.
        self.f_num_tight = f_num
        self.f_pct = f_pct


class BandShape:
    """What one band looks like for one KIND of account: the rungs it carries, whether
    two of them are glued into a pair, and whether the credits close it off."""

    __slots__ = ("rungs", "pair", "credits", "credits_centre", "fits")

    def __init__(self, rungs, pair, credits, credits_centre, fits):
        self.rungs = rungs
        self.pair = pair
        self.credits = credits
        self.credits_centre = credits_centre
        self.fits = fits


class Band:
    """The rectangles of one account band, in SCREEN coordinates.

    A band does not have ONE set of rectangles. An account with credits carries a row
    the next account does not, and the rows above it must give up the height for it --
    so the band computes every shape it can take at construction and hands one out per
    frame by lookup. Building them once keeps `Layout` built once, as it always was;
    the frame's data SELECTS a shape, it never builds one.
    """

    M = METRICS

    def __init__(self, top, height, width, m=None):
        m = m or self.M
        self.m = m
        self.top = top
        self.height = height
        self.bottom = top + height

        self.x0 = m.PAD_X
        self.x1 = width - m.PAD_X
        self.num_right = m.PAD_X + m.NUM_W
        self.block_x0 = self.num_right + m.NUM_GAP
        self.block_x1 = self.x1

        y = top + m.PAD_TOP
        self.header = (self.x0, y, self.x1, y + m.HEADER_H)
        self.rows_top = y + m.HEADER_H + m.ROW_GAP

        self.shapes = {k: self._shape(*k) for k in
                       [(s, c, g) for s in (False, True)
                        for c in (False, True) for g in (False, True)]}

    def shape(self, scoped, credits, glue=True):
        """The shape this band takes, given whether the account has a scoped weekly
        window, whether its credits are drawn, and whether the two weekly windows are
        allowed to share a row."""
        return self.shapes[(bool(scoped), bool(credits), bool(glue))]

    def _cells(self, top, bottom, n):
        """Share a span out between n rows, the way `AlertList.rows` does.

        The rows DIVIDE the band's free height instead of stacking from the top with the
        remainder left at the bottom. Stacked, three rungs read as top-heavy and nothing
        like the mockup; shared out, they land within a pixel of it. Boundaries are taken
        from the fractional split so the rounding error never accumulates.
        """
        m = self.m
        span = bottom - top - (n - 1) * m.ROW_GAP
        out, y = [], top
        for i in range(n):
            h = round(span * (i + 1) / n) - round(span * i / n)
            out.append((y, y + h))
            y += h + m.ROW_GAP
        return out

    def _rung(self, key, cell, bar_h, f_num, f_num_tight, f_pct):
        """One rung, its content centred in the cell it was given."""
        m = self.m
        content = m.LABEL_H + m.INNER_GAP + bar_h
        y = cell[0] + (cell[1] - cell[0] - content) // 2
        label = (self.block_x0, y, self.block_x1, y + m.LABEL_H)
        y += m.LABEL_H + m.INNER_GAP
        bar = (self.block_x0, y, self.block_x1, y + bar_h)
        return Rung(key, label, bar, f_num, f_num_tight, f_pct)

    def _pair(self, cell):
        """The two weekly windows glued into one cell."""
        m = self.m
        content = m.LABEL_H + 2 * (m.PAIR_GAP + m.PAIR_BAR_H)
        y = cell[0] + (cell[1] - cell[0] - content) // 2
        label = (self.block_x0, y, self.block_x1, y + m.LABEL_H)
        y += m.LABEL_H + m.PAIR_GAP
        bars = []
        for _ in range(2):
            bars.append((self.block_x0, y, self.block_x1, y + m.PAIR_BAR_H))
            y += m.PAIR_BAR_H + m.PAIR_GAP
        pair = Pair(("week", "scoped"), label, bars, None,
                    m.F_PAIR_NUM, m.F_PAIR_PCT)
        # The numbers are stacked on the ROW's centre rather than on their own tracks:
        # at these sizes a number is taller than the 8 px track it belongs to, so
        # centring each on its track would overlap them.
        half = m.PAIR_NUM_PITCH // 2
        pair.centres = (pair.centre - half, pair.centre - half + m.PAIR_NUM_PITCH)
        return pair

    def _shape(self, scoped, credits, glue):
        m = self.m
        if credits:
            # Credits glued to the bottom of the band (margin-top: auto in the mockup);
            # the rungs share out what is left above them.
            cy = self.bottom - m.PAD_BOT - m.CREDITS_H
            box = (self.x0, cy, self.x1, cy + m.CREDITS_H)
            centre = cy + m.CREDITS_H // 2
            bottom = cy - m.ROW_GAP
        else:
            box, centre = None, None
            bottom = self.bottom - m.PAD_BOT

        # The two weekly windows share a row only when the band ALSO carries the credits
        # -- not because three rungs and the credits would not fit (they do, on both
        # canvases), but because at that density the pair reads better side by side than
        # stacked. `glue` is the panel.json switch that says otherwise.
        glued = scoped and credits and glue

        # The scoped window is built like the aggregate week and set in the same type:
        # both are seven-day windows, and one of them being narrower in scope is not a
        # reason to make it look like a lesser KIND of thing.
        if glued:
            spec = [("session", m.SES_BAR_H_PAIR, m.F_SES_NUM_PAIR,
                     m.F_SES_NUM_PAIR, m.F_SES_PCT_PAIR)]
            heights = [m.LABEL_H + m.INNER_GAP + m.SES_BAR_H_PAIR,
                       m.LABEL_H + 2 * (m.PAIR_GAP + m.PAIR_BAR_H)]
        else:
            spec = [("session", m.SES_BAR_H, m.F_SES_NUM, m.F_SES_NUM_TIGHT,
                     m.F_SES_PCT),
                    ("week", m.WK_BAR_H, m.F_WK_NUM, m.F_WK_NUM, m.F_WK_PCT)]
            if scoped:
                spec.append(("scoped", m.WK_BAR_H, m.F_WK_NUM, m.F_WK_NUM, m.F_WK_PCT))
            heights = [m.LABEL_H + m.INNER_GAP + s[1] for s in spec]

        n = len(spec) + (1 if glued else 0)
        cells = self._cells(self.rows_top, bottom, n)
        rungs = [self._rung(key, cell, *sizes)
                 for cell, (key, *sizes) in zip(cells, spec)]
        pair = self._pair(cells[-1]) if glued else None

        lowest = pair.bottom if pair else rungs[-1].bottom
        # Per CELL, not in aggregate. `_cells` shares the span out EQUALLY while the
        # rungs are not equal -- the session bar is SES_BAR_H and the weekly ones
        # WK_BAR_H -- so a sum that fits the span does not mean the tallest rung fits
        # its own share; and `_rung`/`_pair` CENTRE their content, so a rung too tall
        # for its cell overflows at both ends, which `lowest <= bottom` only catches
        # for the last one. `heights` is one entry per cell in both shapes (the pair's
        # content is its own entry), which is the docstring's promise exactly.
        fits = lowest <= bottom and \
            all(h <= cell[1] - cell[0] for cell, h in zip(cells, heights))
        return BandShape(rungs, pair, box, centre, fits)

    @property
    def fits(self):
        """Whether EVERY shape this band can take still fits inside it -- the rungs in
        their share of the height, and clear of the credits when those are drawn. Held to
        it by tests/test_render.py::test_credits_do_not_overlap_week."""
        return all(s.fits for s in self.shapes.values())


class Layout:
    #: Which numbers and which geometry classes this layout is made of. A second layout
    #: overrides these and inherits every line of arithmetic.
    M = METRICS
    BAND = Band
    SOLO = AlertSolo
    PAIR = AlertPair
    LIST = AlertList
    MANY = AlertMany

    def __init__(self, width=480, height=320):
        m = self.M
        self.m = m
        self.width = width
        self.height = height
        # 319 px for two bands is 159.5 px each — the mockup's browser gives the extra pixel to
        # the TOP band, so the divider falls on row 160, not 159.
        band_h = (height - m.DIVIDER_H + 1) // 2
        self.band_a = self.BAND(0, band_h, width, m)
        self.divider = (0, band_h, width, band_h + m.DIVIDER_H)
        self.band_b = self.BAND(band_h + m.DIVIDER_H, height - band_h - m.DIVIDER_H,
                                width, m)
        self.bands = (self.band_a, self.band_b)
        self.alert_solo = self.SOLO(width, height, m)
        self.alert_pair = self.PAIR(width, height, m)
        self.alert_list = self.LIST(width, height, m)
        self.alert_many = self.MANY(width, height, m)
