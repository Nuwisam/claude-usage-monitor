"""Geometry of layout 4a on a 1280x720 canvas.

The same layout as `layout.py`, SCALED — not redrawn. Every class here inherits its
arithmetic from the narrow one and overrides nothing but numbers, so the two canvases
stack identically by construction and can differ only where the mockup differs.

Where the numbers come from: the 1280x720 mockup, read out of the browser that rendered
it rather than off the pixels — every box position and every text baseline was taken
from `getBoundingClientRect()`, with the baseline read off a zero-height inline box
aligned to it. Measuring ink instead would have needed to know, per string, whether it
carries a descender.

There is no single multiplier and none may be derived. Type sizes come out at exactly
2x, but the geometry does not: PAD_X 14->32 is 2.29x, NUM_W 76->168 is 2.21x, RAIL_W
6->14 is 2.33x, and the bars 12/9/5->22/17/10 are 1.83x/1.89x/2.00x. A value that looks
like a clean double is a coincidence, not a rule.

Two of the stack's constants are the SHARE-OUT of a distance, not a CSS box, and they
are the only place this module departs from what the mockup's markup says:

  HEADER_H 33 + ROW_GAP 18 = 51, the mockup's 37.5 px header box plus its 14 px gap;
  LINE_H 30, between the session caption's line box (34.09) and the week's (26.39),
  because one constant serves both and the mockup's own two rows differ.

`Band` is not subclassed. It reads every number through the metrics handle already, so
a subclass would be an empty one.
"""
import sys

from . import layout as L

METRICS = sys.modules[__name__]

PAD_X = 32
PAD_TOP = 20
PAD_BOT = 20
ROW_GAP = 18
INNER_GAP = 9

NUM_W = 168         # the percentage column; the % sign ends level with PAD_X + NUM_W
NUM_GAP = 26
PCT_GAP = 4         # gap between the number and the % sign

HEADER_H = 33
LABEL_H = 20
LINE_H = 30

SES_BAR_H = 22
WK_BAR_H = 17
CREDITS_BAR_H = 10
CREDITS_H = 43

# Fonts
F_NAME = 30
F_PLAN = 20
F_CLOCK = 28
F_LABEL = 20
F_RESET = 24
F_AGO = 22
F_SES_NUM = 84
F_SES_NUM_TIGHT = 68    # named in the mockup: at 100 % the session percentage steps down
F_SES_PCT = 28
F_WK_NUM = 60
F_WK_PCT = 24
F_CREDITS_USED = 28
F_CREDITS_LIMIT = 22
F_WORDS = 26            # "unknown" instead of a number

DIVIDER_H = 1           # a hairline at any size: the mockup's bands are 1 px apart here too

F_REASON = 20
REASON_GAP = 14

# The header's three groups, right to left: the clock, then the plan badge with the
# block reason beside it, then the account name. The mockup spaces the groups 22 px
# apart and the badge from the reason 14 px.
HEAD_GAP = 22
CLOCK_MARK_W = 28
CLOCK_DY = 3
BADGE_DY = 12
# Not zero, unlike the narrow layout: at 30 px the account name and at 20 px the window
# labels land 3 and 4 px below the mockup's ink when anchored by the ascender.
NAME_DY = -3
LABEL_DY = -4
NUM_DY = -3
CREDITS_DY = -4
LINK_DX = 8
LINK_DY = 19
LINK_R = 6
LINK_CROSS = 8

GLYPH_R = 11            # the mockup draws the clock glyph as a 22 px box
GLYPH_ADV = 34
AGO_W = 170
AGO_DOT_GAP = 16
AGO_DOT_R = 5

CREDITS_LABEL_DY = 15
CREDITS_ARROW = (29, 11, 7, 11)
CREDITS_NODATA_GAP = 18
CREDITS_TAIL_GAP = 7
CREDITS_TAIL_DY = 2
CREDITS_BAR_GAP = 18

F_EMPTY = 26


# --- blocked-session card ----------------------------------------------------
ALERT_PAD_X = 40
BANNER_H = 76
BANNER_BASE = 48
F_BANNER = 30
BANNER_TRACK = 4        # 0.13em at 30 px
F_BANNER_AT = 30
BANNER_AT_GAP = 26

RAIL_W = 14

META_DOT_GAP = 14

MARK_W = 9              # the mockup names it: 9 px, not the 8 that doubling would give


class AlertSolo(L.AlertSolo):
    M = METRICS
    PROJECT_BASE = 187
    META_BASE = 230
    WAITED_BASE = 319
    DETAIL_TOP = 370
    DETAIL_LABEL_BASE = 39
    DETAIL_TEXT_BASE = 76
    MODE_BASE = 693        # the strip's CAPTION, as in the narrow layout — not its label,
                           # which the mockup sets 1.45 px higher and the renderer shares

    F_PROJECT = 68
    F_PROJECT_TIGHT = 52
    F_META = 24
    F_WAITED = 52
    F_DETAIL_LABEL = 20
    LH_DETAIL_LABEL = 20
    F_DETAIL = 24
    DETAIL_LINE = 32        # 1.35 x 24 px, rounded down
    DETAIL_LINE_10 = 324
    DETAIL_PAD_X = 26
    DETAIL_PAD_Y = 22
    DETAIL_GAP = 10
    DETAIL_RADIUS = 8
    MODE_H = 68
    F_MODE_LABEL = 20
    F_MODE = 22
    MODE_GAP = 18


class AlertPair(L.AlertPair):
    M = METRICS
    F_SHORT = 20
    SHORT_TRACK = 2         # 0.09em at 20 px
    F_WAITED = 34
    F_PROJECT = 60
    F_META = 22
    F_DETAIL = 24
    GAP = 22

    SHORT_BASE = 99
    PROJECT_BASE = 168
    META_BASE = 208
    DETAIL_BASE = 244


class AlertList(L.AlertList):
    M = METRICS
    FOOTER_H = 101
    REASON_W = 116
    COL_GAP = 26
    TIME_W = 132

    F_REASON = 20
    REASON_TRACK = 2        # 0.09em at 20 px
    F_PROJECT = 38
    F_META = 22
    F_TIME = 28
    F_FOOT_LABEL = 20
    F_FOOT = 24

    REASON_BASE = 97
    PROJECT_BASE = 89
    META_BASE = 124
    TIME_BASE = 99
    FOOT_LABEL_BASE = 36
    FOOT_TEXT_BASE = 71


class AlertMany(AlertList):
    FOOTER_H = 79
    F_PROJECT = 34

    REASON_BASE = 100
    PROJECT_BASE = 92
    META_BASE = 125
    TIME_BASE = 103
    FOOT_BASE = 46
    FOOT_GAP = 22


class Layout(L.Layout):
    M = METRICS
    SOLO = AlertSolo
    PAIR = AlertPair
    LIST = AlertList
    MANY = AlertMany

    def __init__(self, width=1280, height=720):
        L.Layout.__init__(self, width, height)
