"""Geometry of layout 4a. Every coordinate is DERIVED from a handful of constants.

4a: two accounts in full-width bands; in each band the percentage stands in a narrow
column BESIDE the block (label / bar / reset), and the credits sit at the bottom of
the band. The gain over a layout with the number above the bar: the label and the
caption share the height with the number instead of standing above and below a bar
that runs the full width.

The font sizes and bar heights come from the mockup and are justified there: three
text steps and nothing in between (10 px for uppercase labels only, 11 px for
secondary data, 12 px and up for content), and bars in three thicknesses by the
weight of the window.
"""

PAD_X = 14
PAD_TOP = 9
PAD_BOT = 9
ROW_GAP = 6
INNER_GAP = 4

NUM_W = 76          # the percentage column; deliberately tight so the bars all start level
NUM_GAP = 12
PCT_GAP = 2         # gap between the number and the % sign

HEADER_H = 17
LABEL_H = 11
LINE_H = 14

SES_BAR_H = 12
WK_BAR_H = 9
CREDITS_BAR_H = 5
CREDITS_H = 14

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
F_CREDITS_USED = 14
F_CREDITS_LIMIT = 11
F_WORDS = 13            # "unknown" instead of a number

DIVIDER_H = 1

# The block reason in the band header, next to the plan badge. The measured slack in the
# header with the longest real name is 202 px in the top band and 259 px in the bottom one,
# so a few dozen pixels for one uppercase word bite only when the name is truncated anyway.
F_REASON = 10
REASON_GAP = 6


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

# Rail on the left edge of the card, below the banner. It stands in BOTH frames:
# `NEUTRAL_900` in the resting one, `ACCENT` in the full one — flooding repaints it,
# it does not call it into being. The run of color takes the banner, and below that
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

    # BASELINES, measured on the rendered mockup — not computed from the CSS boxes.
    #
    # Why: the panel typeface (Segoe UI) has different vertical metrics than the mockup
    # font, so the same box model puts the ink 1-3 px elsewhere. The gap between boxes is
    # invisible, the position of the ink is visible — so the ink is the contract. Checked
    # by measurement in tests/test_alert.py::test_tusz_pasma_i_listwy_stoi_tam_gdzie_makieta.
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
    DETAIL_LINES = 2
    DETAIL_PAD_X = 12
    DETAIL_PAD_Y = 10
    DETAIL_GAP = 5
    DETAIL_RADIUS = 4
    MODE_H = 34
    F_MODE_LABEL = 10
    F_MODE = 11

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.banner = (0, 0, width, BANNER_H)
        self.x0 = ALERT_PAD_X
        self.x1 = width - ALERT_PAD_X

        self.project_base = self.PROJECT_BASE
        self.meta_base = self.META_BASE
        self.waited_base = self.WAITED_BASE
        self.detail_y = self.DETAIL_TOP

        self.mode = (0, height - self.MODE_H, width, height)
        self.mode_centre = height - self.MODE_H // 2

    def detail_box(self, lines):
        """The detail tile grows with the number of lines, not the other way round: a
        one-line `detail` has no reason to leave an empty field below itself.

        The height of the text block is truncated DOWNWARD. The browser keeps the block
        in fractions — one line at line-height 1.35 is 16.2 px — and only the tile EDGE
        lands on the pixel grid: 20 + 10 + 5 + 16.2 = 51.2 px paints as 51, not 52.
        Rounding the block itself up gave a tile one pixel too tall (measured on the
        rendered mockup: 51 px, the panel drew 52).
        """
        n = max(1, lines)
        block = (162 * n) // 10             # floor(16.2 x n)
        h = 2 * self.DETAIL_PAD_Y + self.F_DETAIL_LABEL + self.DETAIL_GAP + block
        return (self.x0, self.detail_y, self.x1, self.detail_y + h)

    def fits(self, lines):
        """Whether the detail tile stays clear of the mode strip. Held to it by
        tests/test_alert.py::test_uklady_nie_nachodza_na_siebie."""
        return self.detail_box(lines)[3] <= self.mode[1]


class AlertPair:
    """Two blocks — two equal halves. The same type size and the same set of fields
    in both: precedence is carried by the ORDER from `status.parse_frame`, not by
    typography. Up to two blocks the project name stays the hero, because there is a
    specific window to go back to and it has to be clear which one."""

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
    SHORT_BASE = 37         # the reason and the time stand on one baseline
    PROJECT_BASE = 73
    META_BASE = 91
    DETAIL_BASE = 113

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.x0 = ALERT_PAD_X
        self.x1 = width - ALERT_PAD_X
        # 282 px for two halves with one hairline of divider: 281 px of content splits
        # into 140.5 px each, and the browser gives the remainder to the BOTTOM half — the
        # divider lands on row 178, not 179. Measured on the rendered mockup; an earlier
        # comment claimed the opposite, and that is where that one line of difference came from.
        top = BANNER_H
        self.divider_y = top + (height - top - DIVIDER_H) // 2
        self.divider = (0, self.divider_y, width, self.divider_y + DIVIDER_H)
        self.halves = ((top, self.divider_y),
                       (self.divider_y + DIVIDER_H, height))


class AlertList:
    """Three blocks — three equal rows. The threshold is exactly here: three project
    names at 34 px do not exist, so the name drops to 19 px and the reason moves to a
    fixed column on the left. The NEWEST DETAIL lands in the footer — one of them,
    because three would not fit at any readable size."""

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

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.x0 = ALERT_PAD_X
        self.x1 = width - ALERT_PAD_X
        self.name_x = self.x0 + self.REASON_W + self.COL_GAP
        self.name_x1 = self.x1 - self.TIME_W - self.COL_GAP

        self.footer = (0, height - self.FOOTER_H, width, height)

    def rows(self, footer=True):
        """The rows with the footer and without it. With no detail the footer has nothing
        to show, and an empty band at the bottom would read as a cut-off screen.

        The remainder of the division goes where the browser puts it: the heights are
        computed from FRACTIONAL boundaries, not through `//`. With 242 px for three rows
        that gives 81 / 80 / 81, and not 80 / 80 / 80 plus two pixels of background at the footer.
        """
        top = BANNER_H
        bottom = self.footer[1] if footer else self.height
        span = bottom - top - (self.ROWS - 1) * DIVIDER_H
        out = []
        y = top
        for i in range(self.ROWS):
            h = (round(span * (i + 1) / self.ROWS)
                 - round(span * i / self.ROWS))
            out.append((y, y + h))
            y += h + DIVIDER_H
        return out


class AlertMany(AlertList):
    """Four blocks and more. The same three rows as in 1c, but the name drops one
    step and the tool disappears: with five blocks the detail of any one of them is
    arbitrary, so in its place the footer counts the rest and lists them by name.

    The meter in the banner counts ALL of them, not only the ones written out."""

    FOOTER_H = 38           # one line instead of two, because here the footer has no label above the text
    F_PROJECT = 17
    F_MACHINE = 11

    REASON_BASE = 44
    PROJECT_BASE = 39
    META_BASE = 55
    TIME_BASE = 45
    FOOT_BASE = 22          # the label and the names on ONE baseline
    FOOT_GAP = 10


class Band:
    """The rectangles of one account band, in SCREEN coordinates."""

    def __init__(self, top, height, width):
        self.top = top
        self.height = height
        self.bottom = top + height

        self.x0 = PAD_X
        self.x1 = width - PAD_X
        self.num_right = PAD_X + NUM_W
        self.block_x0 = self.num_right + NUM_GAP
        self.block_x1 = self.x1

        y = top + PAD_TOP
        self.header = (self.x0, y, self.x1, y + HEADER_H)
        y += HEADER_H + ROW_GAP

        self.ses_top = y
        self.ses_label = (self.block_x0, y, self.block_x1, y + LABEL_H)
        y += LABEL_H + INNER_GAP
        self.ses_bar = (self.block_x0, y, self.block_x1, y + SES_BAR_H)
        y += SES_BAR_H + INNER_GAP
        self.ses_line = (self.block_x0, y, self.block_x1, y + LINE_H)
        self.ses_bottom = y + LINE_H
        self.ses_centre = (self.ses_top + self.ses_bottom) // 2

        y = self.ses_bottom + ROW_GAP
        self.wk_top = y
        self.wk_label = (self.block_x0, y, self.block_x1, y + LABEL_H)
        y += LABEL_H + INNER_GAP
        self.wk_bar = (self.block_x0, y, self.block_x1, y + WK_BAR_H)
        y += WK_BAR_H + INNER_GAP
        self.wk_line = (self.block_x0, y, self.block_x1, y + LINE_H)
        self.wk_bottom = y + LINE_H
        self.wk_centre = (self.wk_top + self.wk_bottom) // 2

        # Credits glued to the bottom of the band (margin-top: auto in the mockup).
        cy = self.bottom - PAD_BOT - CREDITS_H
        self.credits = (self.x0, cy, self.x1, cy + CREDITS_H)
        self.credits_centre = cy + CREDITS_H // 2

    @property
    def fits(self):
        """Whether the credits stay clear of the week. Held to it by
        tests/test_render.py::test_kredyty_nie_wchodza_na_tydzien."""
        return self.credits[1] >= self.wk_bottom


class Layout:
    def __init__(self, width=480, height=320):
        self.width = width
        self.height = height
        # 319 px for two bands is 159.5 px each — the browser gives the extra pixel to
        # the TOP band, so the divider stands on row 160, not 159.
        band_h = (height - DIVIDER_H + 1) // 2
        self.band_a = Band(0, band_h, width)
        self.divider = (0, band_h, width, band_h + DIVIDER_H)
        self.band_b = Band(band_h + DIVIDER_H, height - band_h - DIVIDER_H, width)
        self.bands = (self.band_a, self.band_b)
        self.alert_solo = AlertSolo(width, height)
        self.alert_pair = AlertPair(width, height)
        self.alert_list = AlertList(width, height)
        self.alert_many = AlertMany(width, height)
