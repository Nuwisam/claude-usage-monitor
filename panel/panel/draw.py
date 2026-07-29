"""Prymitywy rysunkowe: czcionki, tekst z wielokropkiem, paski, plakietki.

Czcionka: Segoe UI. Pillow ma tu `raqm: False`, wiec nie da sie poprosic
o `tabular-nums` z makiety — cyfry musza byc tabularne SAME Z SIEBIE. Segoe UI
Regular takie jest w kazdym zmierzonym rozmiarze; Segoe UI Light NIE (cyfra 1
jest tam wezsza od 0), wiec wielka liczba drgalaby przy kazdej zmianie.
"""
from PIL import ImageDraw, ImageFont

from . import theme

# Napis probny do mierzenia wysokosci: ogonki ida w gore, ogony w dol. Mierzenie
# po nominalnym rozmiarze obcinaloby "Ń" i "ą" przy 10-12 px.
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
    """Pozycja linii bazowej, przy ktorej `sample` stoi pionowo na srodku.

    Liczone z FAKTYCZNEGO obrysu, nie z nominalnego rozmiaru: cyfry nie siegaja
    ani ascendera, ani descendera, wiec centrowanie po metryce fontu klamie
    o kilka pikseli — a przy liczbie 42 px to widac.
    """
    box = f.getbbox(sample, anchor="ls")
    return int(round(centre_y - (box[1] + box[3]) / 2.0))


def ellipsize(text, f, max_w):
    """Ucina z wielokropkiem. Nazwy kont bywaja dluzsze niz 480 px pozwala."""
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


def text(d, xy, s, f, fill, anchor=None):
    if s:
        d.text(xy, s, font=f, fill=fill, anchor=anchor)


def tracked_width(s, f, tracking):
    if not s:
        return 0
    return sum(text_width(ch, f) for ch in s) + tracking * (len(s) - 1)


def text_tracked(d, xy, s, f, fill, tracking=1, anchor=None):
    """Etykiety wersalikami maja w makiecie letter-spacing ~0.07em. Przy 10 px
    to niecaly piksel, ale bez niego wersaliki zlewaja sie w plame."""
    if not s:
        return 0
    x, y = xy
    for ch in s:
        d.text((x, y), ch, font=f, fill=fill, anchor=anchor)
        x += text_width(ch, f) + tracking
    return x - tracking - xy[0]


def rounded(d, box, radius, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def dashed_rounded(d, box, radius, colour, dash=3, gap=3, width=1):
    """Kontur przerywany. Pillow nie ma kreskowania, wiec kladziemy segmenty
    recznie po obwodzie prostokata (zaokraglenia pomijamy — przy r<=6 i kresce
    3 px roznica jest niewidoczna, a kod prostszy o polowe)."""
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
    """Skos 135 stopni wewnatrz prostokata — sygnal 'to nie jest brak zuzycia,
    tylko brak wiedzy'. Odwzorowuje repeating-linear-gradient z makiety."""
    x0, y0, x1, y1 = (int(v) for v in box)
    h = y1 - y0
    for start in range(x0 - h, x1 + 1, spacing):
        for off in range(thickness):
            d.line([(start + off, y1), (start + off + h, y0)], fill=colour, width=1)


def bar(d, box, view, fill_colour, track_colour=theme.NEUTRAL_900):
    """Tor limitu w jednym z dwoch rysunkow.

    Dwa, nie cztery: panel nie rozroznia rysunkiem stanow swiezosci, bo obok stoi
    wiek odczytu (patrz view.py). Rozroznienie, ktore zostaje, jest fundamentalne:
    czy wartosc W OGOLE JEST.

    Przy 100% nie ma zadnego dodatkowego znacznika — pelny tor mowi to sam.
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

    # Ksztalt bez masy: kontur zamiast wypelnienia.
    if view.hatch:
        hatch(d, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), theme.TEXT_10)
    dashed_rounded(d, box, r, theme.TEXT_28)
    if view.stub:
        # Kikut przy zerze: reset wywnioskowany, nie zmierzony.
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
    """Ikonka zegara zamiast fontu Phosphor z makiety — jeden import mniej
    i pewnosc, ze przy 12 px nie zamieni sie w plame."""
    cx, cy = centre
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
              outline=colour, width=1)
    d.line((cx, cy, cx, cy - radius + 2), fill=colour, width=1)
    d.line((cx, cy, cx + radius - 2, cy), fill=colour, width=1)


def arrow_down_right(d, box, colour):
    """Strzalka przy kredytach: to one sa teraz biezacym szczeblem."""
    x0, y0, x1, y1 = box
    d.line((x0, y0, x1, y1), fill=colour, width=1)
    d.line((x1 - 3, y1, x1, y1), fill=colour, width=1)
    d.line((x1, y1 - 3, x1, y1), fill=colour, width=1)


def new_canvas(size, colour=theme.BG):
    from PIL import Image
    img = Image.new("RGB", size, colour)
    return img, ImageDraw.Draw(img)
