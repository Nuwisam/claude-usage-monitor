"""What is on the glass, and what has to be written to change it.

`PanelLink` owns the connection; a `Surface` owns the belief about pixels. The
split matters because of one thing that is easy to get wrong: with a diff engine,
"send unconditionally" is NOT the same as "skip the equality check". Skipping the
check still lets the engine compute zero rectangles and write nothing. So the
connection layer never says "force" - it says `invalidate()`, meaning "forget what
you think is on the glass", and the next plan is a full frame by construction.

Every path that can leave a display showing something we did not put there calls
`invalidate()`: claiming a module another process was using, a write error, a
reset, and `run_once`. Missing one of them means the first frame afterwards can be
skipped as "identical" while the glass shows someone else's image.
"""


class Update:
    """One plan: what to write, and whether it repaints the whole screen."""

    __slots__ = ("writes", "state", "full", "nbytes")

    def __init__(self, writes, state, full):
        self.writes = writes            # [((x0, y0, x1, y1), payload), ...]
        self.state = state              # opaque; handed back to commit()
        self.full = full
        self.nbytes = sum(len(p) for _, p in writes)

    def __bool__(self):
        return bool(self.writes)


class FullFrameSurface:
    """For displays that draw whole frames only (see drivers/ax206.py).

    The saving here is the one the AX206 firmware leaves us: compare the packed
    payload with the last one and send nothing when they match. Comparing 307 kB
    costs a fraction of a millisecond against a 355 ms transfer.
    """

    def __init__(self, caps):
        self.caps = caps
        self.prev = None

    @property
    def blank(self):
        """True when we do not know what the display is showing."""
        return self.prev is None

    def invalidate(self):
        self.prev = None

    def plan(self, frame):
        payload = frame.rgb565(self.caps.byte_order, self.caps.rotate)
        if payload == self.prev:
            return Update([], payload, False)
        w, h = self.caps.native
        return Update([((0, 0, w, h), payload)], payload, True)

    def commit(self, update):
        self.prev = update.state


# --- partial updates --------------------------------------------------------

# 320 and 480 are both multiples of 8, so the tile grid has no remainder in either
# orientation. Measured on real frames (cost = bytes x 6.1 us on the serial link):
#
#                       TILE=16                TILE=8
#   clock +60 s         3072 B, 18.7 ms        1792 B, 10.9 ms
#   link marker         2048 B, 12.5 ms         512 B,  3.1 ms
#   base -> edges     200704 B,  1224 ms      141440 B,   863 ms
#
# Smaller tiles win in BOTH regimes - at rest and on a scene change - at the price
# of a longer scan and more rectangles, which is what MAX_RECTS is for.
TILE = 8

# A rectangle costs 6 B on the wire and ~0.016 ms to cut, so this cap is about
# bounding the Python loop, not the transfer.
MAX_RECTS = 256

# Past this much of the frame, send it whole: one command, a known `prev`, and no
# thousand crops for nothing. Scene transitions land here (they dirty 40-70% of
# the tiles), and they are exactly the case where the bookkeeping is pure loss.
FULL_AT = 0.60


def dirty_tiles(prev, cur, width, height, tile=TILE):
    """Tiles whose pixels differ, as (tx, ty).

    Two levels, both leaning on C: whole rows first (one memoryview compare each),
    then columns only inside rows that differ. Measured at TILE=16: 0.43-0.52 ms
    whether nothing changed or everything did.

    The comparison runs on the PACKED 565 buffer, never on RGB. ImageChops
    difference plus convert("L") would apply luma weights, so a (1,0,0) difference
    rounds to zero and the change is lost; comparing what actually goes on the
    wire is lossless by construction and reuses a buffer we have to build anyway.
    """
    stride = width * 2
    span = tile * 2
    cols = width // tile
    pv, cv = memoryview(prev), memoryview(cur)
    out = set()
    for y in range(height):
        base = y * stride
        if pv[base:base + stride] == cv[base:base + stride]:
            continue
        ty = y // tile
        for tx in range(cols):
            if (tx, ty) in out:
                continue
            a = base + tx * span
            if pv[a:a + span] != cv[a:a + span]:
                out.add((tx, ty))
    return out


def coalesce(tiles, tile=TILE):
    """Dirty tiles -> pixel rectangles, without ever adding a pixel.

    Adjacent tiles in a row become one run; runs in consecutive rows merge only
    when their column span is IDENTICAL. Anything looser would cover clean pixels,
    and on a link that is linear with zero fixed overhead those cost real
    milliseconds and buy nothing.
    """
    rows = {}
    for tx, ty in tiles:
        rows.setdefault(ty, []).append(tx)

    runs = []
    for ty in sorted(rows):
        xs = sorted(rows[ty])
        start = prev = xs[0]
        for x in xs[1:]:
            if x == prev + 1:
                prev = x
                continue
            runs.append((ty, start, prev + 1))
            start = prev = x
        runs.append((ty, start, prev + 1))

    merged = []
    at = {}
    for ty, tx0, tx1 in runs:
        key = (tx0, tx1)
        i = at.get(key)
        if i is not None and merged[i][1] == ty:
            y0, _, a, b = merged[i]
            merged[i] = (y0, ty + 1, a, b)
        else:
            merged.append((ty, ty + 1, tx0, tx1))
            at[key] = len(merged) - 1
    return [(tx0 * tile, ty0 * tile, tx1 * tile, ty1 * tile)
            for ty0, ty1, tx0, tx1 in merged]


def cut(buf, width, rect):
    """The payload of one rectangle, sliced out of the whole-frame payload.

    Not packed a second time: pack(crop) is provably the row slices of
    pack(whole) (tests/test_pixels.py pins it), so there is no second packing path
    that could drift from the buffer the diff was computed on.
    """
    x0, y0, x1, y1 = rect
    stride = width * 2
    return b"".join(buf[y * stride + x0 * 2:y * stride + x1 * 2]
                    for y in range(y0, y1))


class TiledSurface:
    """For displays that update arbitrary rectangles (drivers/turing_rev_a.py).

    Everything happens in DEVICE space, after one rotation, on the packed buffer.
    """

    def __init__(self, caps, tile=TILE):
        width, height = caps.native
        if width % tile or height % tile:
            raise ValueError("plotno %dx%d nie dzieli sie przez kafel %d"
                             % (width, height, tile))
        self.caps = caps
        self.tile = tile
        self.prev = None

    @property
    def blank(self):
        return self.prev is None

    def invalidate(self):
        self.prev = None

    def plan(self, frame):
        payload = frame.rgb565(self.caps.byte_order, self.caps.rotate)
        width, height = self.caps.native
        full = (0, 0, width, height)
        if self.prev is None:
            return Update([(full, payload)], payload, True)

        tiles = dirty_tiles(self.prev, payload, width, height, self.tile)
        if not tiles:
            return Update([], payload, False)
        rects = coalesce(tiles, self.tile)
        nbytes = sum((x1 - x0) * (y1 - y0) * 2 for x0, y0, x1, y1 in rects)
        if len(rects) > MAX_RECTS or nbytes >= FULL_AT * len(payload):
            return Update([(full, payload)], payload, True)
        return Update([(r, cut(payload, width, r)) for r in rects],
                      payload, False)

    def commit(self, update):
        # Only a fully written update may become our belief: a partial one leaves
        # the glass in a state we cannot describe, and the caller invalidates.
        self.prev = update.state


def for_caps(caps, log=None):
    """The surface a driver's capabilities call for.

    A tiled surface is structurally unattachable to a full-frame-only driver:
    such a driver rejects a partial rect, which would become a dropped connection
    and permanent backoff while the old image sits on the glass - frozen,
    credible-looking numbers, the failure mode this project refuses.
    """
    if not caps.rect_updates:
        return FullFrameSurface(caps)
    try:
        return TiledSurface(caps)
    except ValueError as e:
        # Unreachable with today's screens (320 and 480 both divide by 8), but
        # width/height are user-editable, and degrading loudly beats drawing a
        # partial-tile rectangle nobody ever tested.
        if log:
            log("%s: %s — wysylam pelne klatki" % (caps.name, e))
        return FullFrameSurface(caps)
