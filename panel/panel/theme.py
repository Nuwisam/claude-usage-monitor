"""Palette — from frontend/src/styles/theme.css, as RGB tuples.

In CSS the translucency is done by `color-mix(... , transparent)`. The panel draws on
an opaque background, so we mix up front: `mix()` yields a finished color and at draw
time there is no alpha at all.

Every background/foreground pair has to survive RGB565 (5/6/5) quantization —
tests/test_render.py::test_kolory_przezywaja_kwantyzacje holds it to that.
"""

BG = (0x1C, 0x1B, 0x19)
SURFACE = (0x26, 0x25, 0x23)
SUNKEN = (0x21, 0x1F, 0x1D)
TEXT = (0xF0, 0xEE, 0xE6)

ACCENT = (0xD9, 0x77, 0x57)
ACCENT_800 = (0x46, 0x28, 0x1D)
ACCENT_700 = (0x8A, 0x4A, 0x33)
ACCENT_500 = (0xD9, 0x77, 0x57)
ACCENT_300 = (0xE8, 0x94, 0x77)
ACCENT_200 = (0xF0, 0xAB, 0x90)
ACCENT_100 = (0xF7, 0xCB, 0xB8)

NEUTRAL_900 = (0x32, 0x2F, 0x2B)
NEUTRAL_800 = (0x41, 0x3D, 0x37)
NEUTRAL_600 = (0x7A, 0x74, 0x6A)
NEUTRAL_400 = (0x9A, 0x93, 0x88)
NEUTRAL_100 = (0xD6, 0xD2, 0xC6)


def mix(fg, pct, bg=BG):
    """`color-mix(in srgb, fg pct%, transparent)` laid over `bg`."""
    k = pct / 100.0
    return tuple(int(round(f * k + b * (1 - k))) for f, b in zip(fg, bg))


# Named shades from mockup 4a — kept here so that draw.py and render.py do not
# carry a single raw percentage figure.
TEXT_78 = mix(TEXT, 78)     # clock
TEXT_70 = mix(TEXT, 70)     # session reset caption
TEXT_62 = mix(TEXT, 62)     # tool and machine on card 1a
TEXT_60 = mix(TEXT, 60)     # week label, week reset caption
TEXT_58 = mix(TEXT, 58)     # the same on the half-card 1b — tighter, so quieter
TEXT_55 = mix(TEXT, 55)     # percent sign
TEXT_52 = mix(TEXT, 52)     # reading age
TEXT_50 = mix(TEXT, 50)     # plan, credits label when they are not the current one
TEXT_45 = mix(TEXT, 45)     # credits limit
TEXT_40 = mix(TEXT, 40)
TEXT_28 = mix(TEXT, 28)     # dashed outline
TEXT_26 = mix(TEXT, 26)
TEXT_25 = mix(TEXT, 25)     # credits track when there is no data
TEXT_10 = mix(TEXT, 10)     # the diagonal hatching inside the outline
DIVIDER = mix(TEXT, 14)     # band separator
GHOST = mix(TEXT, 45)       # tick mark of the last measurement

# Shades laid on a background OTHER than `BG`.
#
# In CSS `color-mix(..., transparent)` mixes with whatever happens to be underneath, so
# the same percentage over the `Detail` tile and over the card's background are TWO
# DIFFERENT colors. The panel has no alpha — it mixes up front — so every pair
# (percentage, background) has to be its own constant. Using `TEXT_45` here instead of
# `TEXT_45_SURFACE` is not a rounding: the backgrounds differ by (5, 4, 4), which after
# 5/6/5 quantization still shows on the green channel.
TEXT_78_SURFACE = mix(TEXT, 78, SURFACE)    # body text in the `Detail` tile
TEXT_45_SURFACE = mix(TEXT, 45, SURFACE)    # the label in that tile
TEXT_72_SUNKEN = mix(TEXT, 72, SUNKEN)      # detail of the newest one in the list footer
TEXT_70_SUNKEN = mix(TEXT, 70, SUNKEN)      # value in the `Mode` strip
TEXT_62_SUNKEN = mix(TEXT, 62, SUNKEN)      # names of the rest in layout 1d's footer
TEXT_45_SUNKEN = mix(TEXT, 45, SUNKEN)      # labels in the strip and in the footer


def to_rgb565_pair(c):
    """The color after the panel's quantization — for the legibility tests."""
    r, g, b = c
    return (r >> 3, g >> 2, b >> 3)
