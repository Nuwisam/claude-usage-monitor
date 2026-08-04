"""RGB565 packing. The only place in the client where 5/6/5 bit math lives.

Byte order is a property of the DISPLAY, not of the format, and the two screens on
this desk disagree:

  * AX206 wants the high byte first ("be"), the RGB565_0/_1 order from dpf-ax,
  * the serial rev A screen wants the low byte first ("le").

The two encodings are the same pair of bytes with the interleave swapped, so both
share one channel split and differ in a single line.

Everything here returns `bytes`, never `bytearray` or a memoryview over a mutable
buffer: callers hash payloads (frame equality, tests putting frames in a set), and
a mutable buffer is unhashable. Returning an immutable object also means a payload
handed to a driver cannot be edited underneath it.
"""

BIG, LITTLE = "be", "le"

# Channel translation tables. Splitting the channels and OR-ing two big integers
# keeps the whole packing inside C: a full 480x320 frame takes ~3 ms instead of
# ~36 ms for a per-pixel loop. numpy is not needed and not wanted here.
_T_R = bytes(i & 0xF8 for i in range(256))
_T_GH = bytes(i >> 5 for i in range(256))
_T_GL = bytes((i & 0x1C) << 3 for i in range(256))
_T_B = bytes(i >> 3 for i in range(256))


def pack_rgb565(img, order=BIG):
    """PIL image -> RGB565 bytes, row-major, in the requested byte order."""
    if order not in (BIG, LITTLE):
        raise ValueError("unknown byte order %r (expected %r or %r)"
                         % (order, BIG, LITTLE))
    if img.mode != "RGB":
        img = img.convert("RGB")
    r, g, b = img.split()
    rb, gb, bb = r.tobytes(), g.tobytes(), b.tobytes()
    n = len(rb)
    hi = (int.from_bytes(rb.translate(_T_R), "big")
          | int.from_bytes(gb.translate(_T_GH), "big")).to_bytes(n, "big")
    lo = (int.from_bytes(gb.translate(_T_GL), "big")
          | int.from_bytes(bb.translate(_T_B), "big")).to_bytes(n, "big")
    out = bytearray(2 * n)
    if order == BIG:
        out[0::2], out[1::2] = hi, lo
    else:
        out[0::2], out[1::2] = lo, hi
    return bytes(out)


def rgb565_bytes(r, g, b, order=BIG):
    """A single pixel, same contract as pack_rgb565."""
    hi = (r & 0xF8) | ((g & 0xE0) >> 5)
    lo = ((g & 0x1C) << 3) | ((b & 0xF8) >> 3)
    return bytes((hi, lo)) if order == BIG else bytes((lo, hi))
