"""Partial updates: which tiles changed, and what gets written.

The strongest guarantee here is the reconstruction property - applying the
produced rectangles to the previous buffer must rebuild the new one byte for
byte. On a display that acknowledges nothing, that property is the only thing
standing between a diff bug and a screen that quietly shows stale pixels.
"""
import pytest

from PIL import Image

from panel import render, surface
from panel.drivers.base import Caps, Scale
from panel.pixels import BIG, LITTLE, pack_rgb565

W, H = 320, 480
TILE = surface.TILE


def caps(rect_updates=True, native=(W, H), rotate=0, byte_order=LITTLE):
    return Caps(name="fake", canvas=(480, 320), native=native, rotate=rotate,
                byte_order=byte_order, rect_updates=rect_updates, acked=False,
                brightness=Scale("percent", 0, 100, 40), bytes_per_sec=164_000)


def buffer_from(pixels):
    """A packed buffer built from an image, so the tests speak in pixels."""
    return pack_rgb565(pixels, LITTLE)


def image(fill=(0, 0, 0)):
    return Image.new("RGB", (W, H), fill)


def apply(prev, writes, width=W):
    """Replay the writes onto `prev` the way the display would."""
    out = bytearray(prev)
    stride = width * 2
    for (x0, y0, x1, y1), payload in writes:
        row = (x1 - x0) * 2
        for i, y in enumerate(range(y0, y1)):
            start = y * stride + x0 * 2
            out[start:start + row] = payload[i * row:(i + 1) * row]
    return bytes(out)


# --- dirty_tiles ------------------------------------------------------------


def test_no_change_finds_nothing():
    a = buffer_from(image())
    assert dirty(a, a) == set()


def dirty(prev, cur):
    return surface.dirty_tiles(prev, cur, W, H)


def test_one_pixel_dirties_exactly_one_tile():
    before = image()
    after = image()
    after.putpixel((100, 200), (255, 0, 0))
    assert dirty(buffer_from(before), buffer_from(after)) == {(100 // TILE, 200 // TILE)}


def test_a_sub_quantum_change_is_not_a_change():
    """565 has 5/6/5 bits. A difference the display cannot show must not cost a
    write - and comparing the PACKED buffer is what makes that true."""
    before = image((8, 8, 8))
    after = image((9, 8, 8))
    assert dirty(buffer_from(before), buffer_from(after)) == set()


def test_a_red_only_change_is_not_lost():
    """The trap the packed-buffer comparison avoids: a luma-weighted greyscale
    difference rounds a small red-only change to zero."""
    before = image((0, 0, 0))
    after = image((8, 0, 0))
    assert dirty(buffer_from(before), buffer_from(after))


def test_changes_in_the_last_row_and_column():
    before = image()
    after = image()
    after.putpixel((W - 1, H - 1), (255, 255, 255))
    assert dirty(buffer_from(before), buffer_from(after)) == {(W // TILE - 1, H // TILE - 1)}


# --- coalesce ---------------------------------------------------------------


def test_adjacent_tiles_in_a_row_become_one_rect():
    rects = surface.coalesce({(1, 0), (2, 0), (3, 0)})
    assert rects == [(TILE, 0, 4 * TILE, TILE)]


def test_rows_merge_only_when_the_span_matches():
    same = surface.coalesce({(1, 0), (1, 1)})
    assert same == [(TILE, 0, 2 * TILE, 2 * TILE)]
    wider = surface.coalesce({(1, 0), (1, 1), (2, 1)})
    assert len(wider) == 2, "roznie szerokie wiersze nie moga sie skleic"


def test_separate_regions_stay_separate():
    rects = surface.coalesce({(0, 0), (30, 40)})
    assert len(rects) == 2


def test_coalesce_never_covers_a_clean_tile():
    tiles = {(0, 0), (2, 0), (0, 5)}
    covered = set()
    for x0, y0, x1, y1 in surface.coalesce(tiles):
        for ty in range(y0 // TILE, y1 // TILE):
            for tx in range(x0 // TILE, x1 // TILE):
                covered.add((tx, ty))
    assert covered == tiles


# --- TiledSurface -----------------------------------------------------------


def frame_of(fill, mark=None):
    img = Image.new("RGB", (480, 320), fill)
    if mark:
        for x in range(mark[0], mark[0] + 20):
            for y in range(mark[1], mark[1] + 20):
                img.putpixel((x, y), (255, 255, 0))
    return render.Frame(img)


def test_first_plan_after_open_is_the_whole_screen():
    s = surface.for_caps(caps())
    update = s.plan(frame_of((0, 0, 0)))
    assert update.full and update.writes[0][0] == (0, 0, W, H)


def test_an_unchanged_frame_plans_nothing():
    s = surface.for_caps(caps())
    frame = frame_of((0, 0, 0))
    s.commit(s.plan(frame))
    assert not s.plan(frame)


def test_a_small_change_writes_far_less_than_a_frame():
    s = surface.for_caps(caps())
    s.commit(s.plan(frame_of((0, 0, 0))))
    update = s.plan(frame_of((0, 0, 0), mark=(100, 100)))
    assert update.writes and not update.full
    assert update.nbytes < W * H * 2 // 10


def test_applying_the_rects_reconstructs_the_frame_byte_for_byte():
    """The property everything else rests on."""
    s = surface.for_caps(caps())
    first = frame_of((0, 0, 0))
    s.commit(s.plan(first))
    prev = first.rgb565(LITTLE, 0)
    second = frame_of((0, 0, 0), mark=(37, 213))
    update = s.plan(second)
    assert apply(prev, update.writes) == second.rgb565(LITTLE, 0)


def test_a_big_change_falls_back_to_a_full_frame():
    """Not to a bounding box: on a scene change the box IS the whole frame, and a
    thousand crops on top of it are pure loss."""
    s = surface.for_caps(caps())
    s.commit(s.plan(frame_of((0, 0, 0))))
    update = s.plan(frame_of((10, 40, 90)))
    assert update.full and len(update.writes) == 1


def test_full_frame_only_drivers_get_the_other_surface():
    assert isinstance(surface.for_caps(caps(rect_updates=False)),
                      surface.FullFrameSurface)


def test_a_canvas_that_does_not_divide_by_the_tile_degrades_loudly():
    said = []
    s = surface.for_caps(caps(native=(300, 481)), log=said.append)
    assert isinstance(s, surface.FullFrameSurface)
    assert said and "pelne klatki" in said[0]


def test_rotation_and_byte_order_come_from_caps():
    s = surface.for_caps(caps(native=(320, 480), rotate=90, byte_order=LITTLE))
    update = s.plan(frame_of((0, 0, 0)))
    assert len(update.writes[0][1]) == 320 * 480 * 2


@pytest.mark.parametrize("order", [BIG, LITTLE])
def test_cut_matches_a_repacked_crop(order):
    img = Image.new("RGB", (64, 32), (3, 200, 90))
    img.putpixel((10, 10), (255, 0, 0))
    whole = pack_rgb565(img, order)
    rect = (8, 8, 24, 16)
    assert surface.cut(whole, 64, rect) == pack_rgb565(img.crop(rect), order)
