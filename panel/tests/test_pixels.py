"""RGB565 packing, both byte orders. No hardware.

Two screens on this desk want opposite byte orders, and neither of them ever
tells us we got it wrong: the AX206 draws a scrambled frame and the serial one
acknowledges nothing at all. So the packer is pinned here against an independent
naive loop, per order, rather than against itself.
"""
import random
import struct

import pytest

from PIL import Image

from panel.pixels import BIG, LITTLE, pack_rgb565, rgb565_bytes


def noise(size=(64, 32), seed=11):
    random.seed(seed)
    img = Image.new("RGB", size)
    img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                 for _ in range(size[0] * size[1])])
    return img


def naive(img, order):
    """Independent implementation: one pixel at a time, no table tricks."""
    raw = img.convert("RGB").tobytes()
    out = bytearray()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out += struct.pack(">H" if order == BIG else "<H", value)
    return bytes(out)


@pytest.mark.parametrize("order", [BIG, LITTLE])
def test_fast_path_matches_a_naive_loop(order):
    img = noise()
    assert pack_rgb565(img, order) == naive(img, order)


def test_little_endian_is_big_endian_byte_swapped():
    img = noise()
    be = pack_rgb565(img, BIG)
    le = pack_rgb565(img, LITTLE)
    assert le[0::2] == be[1::2] and le[1::2] == be[0::2]


@pytest.mark.parametrize("order", [BIG, LITTLE])
def test_extremes(order):
    assert pack_rgb565(Image.new("RGB", (1, 1), (255, 255, 255)), order) == b"\xff\xff"
    assert pack_rgb565(Image.new("RGB", (1, 1), (0, 0, 0)), order) == b"\x00\x00"


@pytest.mark.parametrize("order", [BIG, LITTLE])
def test_a_crop_packs_to_the_row_slices_of_the_whole(order):
    """The rect payloads sent to a rect-capable screen are cut out of the
    already-packed full frame instead of being packed a second time. That is only
    sound while this holds, so it is a contract, not an optimisation detail."""
    img = noise((64, 32))
    whole = pack_rgb565(img, order)
    box = (8, 4, 40, 20)                       # x0, y0, x1, y1 - x1/y1 exclusive
    x0, y0, x1, y1 = box
    stride = img.width * 2
    cut = b"".join(whole[y * stride + x0 * 2:y * stride + x1 * 2]
                   for y in range(y0, y1))
    assert pack_rgb565(img.crop(box), order) == cut


@pytest.mark.parametrize("order", [BIG, LITTLE])
def test_payload_is_bytes_and_hashable(order):
    """Frames are compared and put in sets; a bytearray or a memoryview over a
    mutable buffer would be unhashable and could change under the driver."""
    payload = pack_rgb565(noise((8, 8)), order)
    assert type(payload) is bytes
    assert len({payload, pack_rgb565(noise((8, 8)), order)}) == 1


@pytest.mark.parametrize("order", [BIG, LITTLE])
def test_single_pixel_matches_the_image_packer(order):
    for colour in ((0, 0, 0), (255, 255, 255), (17, 200, 99), (255, 0, 128)):
        assert (rgb565_bytes(*colour, order=order)
                == pack_rgb565(Image.new("RGB", (1, 1), colour), order))


def test_unknown_byte_order_is_rejected():
    with pytest.raises(ValueError):
        pack_rgb565(Image.new("RGB", (1, 1)), "middle")
