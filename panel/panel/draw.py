"""Drawing primitives: fonts, text with an ellipsis, bars, badges.

Font: Segoe UI. Pillow here has `raqm: False`, so there is no way to ask
for the mockup's `tabular-nums` — the digits have to be tabular BY THEMSELVES. Segoe UI
Regular is, at every measured size; Segoe UI Light is NOT (its digit 1
is narrower than its 0), so a large number would jitter on every change.
"""
from PIL import ImageDraw, ImageFont

from . import theme

# i18n-keep: these glyphs ARE the measurement, not text.
# Probe string for measuring height: the diacritics reach up, the tails reach down.
# Measuring by the nominal size would clip "Ń" and "ą" at 10-12 px.
# Keep these exact glyphs: swapping them for plain English letters would silently
# change every line height computed on the panel.
PROBE = "ĄĘŚŹŻgjpqy"

FONT_CANDIDATES = ("segoeui.ttf", "tahoma.ttf", "arial.ttf", "DejaVuSans.ttf")
FONT_BOLD_CANDIDATES = ("seguisb.ttf", "segoeuib.ttf", "tahomabd.ttf", "arialbd.ttf")

_cache = {}


def font(size, bold=False):
    key = (size, bold)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    for name in (FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        try:
            f = ImageFont.truetype(name, size)
            _cache[key] = f
            return f
        except OSError:
            continue
    f = ImageFont.load_default()
    _cache[key] = f
    return f


def text_width(text, f):
    return f.getbbox(text)[2] if text else 0


def line_height(f):
    box = f.getbbox(PROBE)
    return box[3] - box[1]


def baseline_for_centre(f, sample, centre_y):
    """Baseline position at which `sample` stands vertically centered.

    Computed from the ACTUAL bounding box, not from the nominal size: digits reach
    neither the ascender nor the descender, so centering by the font metrics lies
    by a few pixels — and at a 42 px number that shows.
    """
    box = f.getbbox(sample, anchor="ls")
    return int(round(centre_y - (box[1] + box[3]) / 2.0))


def baseline_for_top(f, top, line_height):
    """Baseline of text whose LINE BOX starts at `top`.

    The equivalent of the mockup's box: CSS knows the line height and spreads the excess
    evenly above and below the typeface, while Pillow knows the ascender alone. Without
    this arithmetic a "margin-top: 7px" under a 34 px name comes out negative on the panel —
    the ascender plus the descender of Segoe UI is a full 46 px at 34 px, which is 11 px more
    than the mockup's line-height of 1.02.
    """
    asc, desc = f.getmetrics()
    return int(round(top + (line_height - (asc + desc)) / 2.0 + asc))


def ellipsize(text, f, max_w):
    """Truncates with an ellipsis. Account names are sometimes longer than 480 px allows."""
    if not text or text_width(text, f) <= max_w:
        return text
    ell = "…"
    room = max_w - text_width(ell, f)
    if room <= 0:
        return ell
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_width(text[:mid], f) <= room:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def wrap_lines(text, f, max_w, max_lines=2):
    """Wraps on words up to `max_lines`; the overflow falls into the last line and is
    truncated there with an ellipsis.

    The only place in the project that wraps text — the `detail` from the alert box is
    sometimes a whole sentence. Truncating stays the job of `ellipsize`, so that there
    are not two truths about how an over-long string looks.
    """
    if not text:
        return []
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        # `not cur` lets through a word longer than a whole line: it stands alone and gets
        # an ellipsis, instead of falling into an infinite loop or vanishing.
        if not cur or text_width(trial, f) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return [ellipsize(line, f, max_w) for line in lines]


def text(d, xy, s, f, fill, anchor=None):
    if s:
        d.text(xy, s, font=f, fill=fill, anchor=anchor)


def tracked_width(s, f, tracking):
    if not s:
        return 0
    return sum(text_width(ch, f) for ch in s) + tracking * (len(s) - 1)


def ellipsize_tracked(s, f, max_w, tracking=1):
    """`ellipsize` for a string that will go through `text_tracked`.

    A separate function, because the spacing between letters counts into the width: with the
    banner's caps (tracking 2 at 15 px) a plain `ellipsize` would let a string through a dozen
    or so pixels too wide, and it would run straight into the hour on the right.
    """
    if not s or tracked_width(s, f, tracking) <= max_w:
        return s
    out = s
    while out and tracked_width(out + "…", f, tracking) > max_w:
        out = out[:-1]
    return out + "…" if out else "…"


def text_tracked(d, xy, s, f, fill, tracking=1, anchor=None):
    """Labels in caps have letter-spacing ~0.07em in the mockup. At 10 px that is
    less than a whole pixel, but without it the caps blur into a smudge."""
    if not s:
        return 0
    x, y = xy
    for ch in s:
        d.text((x, y), ch, font=f, fill=fill, anchor=anchor)
        x += text_width(ch, f) + tracking
    return x - tracking - xy[0]


def fill_rect(d, box, colour):
    """A rectangle in the HALF-OPEN convention: `x1` and `y1` are EXCLUSIVE, as in the mockup.

    Pillow counts the bounds inclusively, so a banner of (0, 0, 480, 38) comes out
    39 px tall there, and a tile ending at x1 = 462 eats a pixel of the right margin.
    A difference of one pixel, but the card is built out of fields that meet at their
    edges — one pixel too many in the banner shifts all the rest.
    """
    x0, y0, x1, y1 = box
    d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=colour)


def rounded(d, box, radius, fill=None, outline=None, width=1):
    """Like `fill_rect`, only with rounded corners — the same bounds convention."""
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius, fill=fill,
                        outline=outline, width=width)


def dashed_rounded(d, box, radius, colour, dash=3, gap=3, width=1):
    """A dashed outline. Pillow has no dashing, so the segments are laid down
    by hand along the perimeter of the rectangle (the roundings are skipped — at r<=6 and a
    3 px dash the difference is invisible, and the code is half as long)."""
    x0, y0, x1, y1 = box
    step = dash + gap
    for x in range(int(x0), int(x1), step):
        xe = min(x + dash, x1)
        d.line([(x, y0), (xe, y0)], fill=colour, width=width)
        d.line([(x, y1), (xe, y1)], fill=colour, width=width)
    for y in range(int(y0), int(y1), step):
        ye = min(y + dash, y1)
        d.line([(x0, y), (x0, ye)], fill=colour, width=width)
        d.line([(x1, y), (x1, ye)], fill=colour, width=width)


def hatch(d, box, colour, spacing=8, thickness=3):
    """A 135-degree diagonal inside the rectangle — the signal for 'this is not an absence
    of usage, only an absence of knowledge'. Reproduces the mockup's repeating-linear-gradient."""
    x0, y0, x1, y1 = (int(v) for v in box)
    h = y1 - y0
    for start in range(x0 - h, x1 + 1, spacing):
        for off in range(thickness):
            d.line([(start + off, y1), (start + off + h, y0)], fill=colour, width=1)


def bar(d, box, view, fill_colour, track_colour=theme.NEUTRAL_900):
    """The limit track in one of two drawings.

    Two, not four: the panel does not tell freshness states apart by the drawing, because
    the age of the reading stands right next to it (see view.py). The distinction that
    remains is fundamental: whether the value IS THERE AT ALL.

    At 100% there is no extra marker of any kind — a full track says it by itself.
    """
    x0, y0, x1, y1 = box
    h = y1 - y0
    r = max(1, h // 2)

    if view.measured:
        rounded(d, box, r, fill=track_colour)
        w = int(round((x1 - x0) * view.bar_pct / 100.0))
        if w > 0:
            rounded(d, (x0, y0, x0 + max(w, h), y1), r, fill=fill_colour)
        return

    # Shape without mass: an outline instead of a fill.
    if view.hatch:
        hatch(d, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), theme.TEXT_10)
    dashed_rounded(d, box, r, theme.TEXT_28)
    if view.stub:
        # A stub at zero: the reset was inferred, not measured.
        rounded(d, (x0 + 2, y0 + 2, x0 + 8, y1 - 2), max(1, r - 2), fill=theme.TEXT_28)
    if view.ghost:
        gx = x0 + int(round((x1 - x0) * view.ghost_pct / 100.0))
        gx = min(max(gx, x0), x1 - 2)
        d.rectangle((gx, y0 - 3, gx + 1, y1 + 3), fill=theme.GHOST)


def dot(d, centre, radius, colour):
    cx, cy = centre
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)


def ring(d, centre, radius, colour, width=1):
    cx, cy = centre
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
              outline=colour, width=width)


def cross(d, centre, radius, colour, width=1):
    cx, cy = centre
    d.line((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour, width=width)
    d.line((cx - radius, cy + radius, cx + radius, cy - radius), fill=colour, width=width)


def clock_glyph(d, centre, radius, colour):
    """A clock icon instead of the mockup's Phosphor font — one import fewer
    and the certainty that at 12 px it will not turn into a smudge."""
    cx, cy = centre
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
              outline=colour, width=1)
    d.line((cx, cy, cx, cy - radius + 2), fill=colour, width=1)
    d.line((cx, cy, cx + radius - 2, cy), fill=colour, width=1)


def arrow_down_right(d, box, colour):
    """The arrow next to credits: they are the current rung now."""
    x0, y0, x1, y1 = box
    d.line((x0, y0, x1, y1), fill=colour, width=1)
    d.line((x1 - 3, y1, x1, y1), fill=colour, width=1)
    d.line((x1, y1 - 3, x1, y1), fill=colour, width=1)


def new_canvas(size, colour=theme.BG):
    from PIL import Image
    img = Image.new("RGB", size, colour)
    return img, ImageDraw.Draw(img)
