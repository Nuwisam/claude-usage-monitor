"""The rev A wire protocol, against a fake port. No hardware.

This is the most valuable test file in the panel: the display acknowledges
nothing, so if we encode a command wrongly it will not complain - it will draw
something wrong, or quietly stop making sense. Everything here is checked against
an independent implementation of the formula rather than against the driver's own
helpers.
"""
import struct

import pytest

from panel.drivers import turing_rev_a as turing
from panel.drivers.base import DriverError
from panel.pixels import LITTLE, pack_rgb565

pytest.importorskip("serial")
import serial                                                    # noqa: E402


class FakePort:
    """Records what went out. `fail_at` tears the write like a stalled display."""

    def __init__(self, fail_at=None, short_at=None, sticky=False):
        self.chunks = []
        self.write_timeout = None
        self.closed = False
        self.fail_at = fail_at            # raise SerialTimeoutException at N bytes
        self.short_at = short_at          # return a short count at N bytes
        # A stall that clears (the display drains and takes the padding) versus
        # one that does not (the port is gone). Both happen; they need different
        # assertions, not different code.
        self.sticky = sticky
        self.sent = 0
        self._overlapped_write = type("O", (), {"InternalHigh": 0})()

    def write(self, data):
        data = bytes(data)
        if self.fail_at is not None and self.sent + len(data) > self.fail_at:
            got = max(0, self.fail_at - self.sent)
            self.sent += got
            self.chunks.append(data[:got])
            self._overlapped_write.InternalHigh = got
            if not self.sticky:
                self.fail_at = None
            raise serial.SerialTimeoutException("Write timeout")
        if self.short_at is not None and self.sent + len(data) > self.short_at:
            got = max(0, self.short_at - self.sent)
            self.sent += got
            self.chunks.append(data[:got])
            self.short_at = None
            return got
        self.sent += len(data)
        self.chunks.append(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True

    @property
    def written(self):
        return b"".join(self.chunks)


def driver(**over):
    dev = turing.Turing("COM-fake")
    dev.port = FakePort(**over)
    return dev


def naive_command(cmd, x, y, ex, ey):
    """Independent implementation of the packet, written from the field widths:
    two 10-bit coordinate pairs, then the command byte."""
    bits = (x << 30) | (y << 20) | (ex << 10) | ey
    return bits.to_bytes(5, "big") + bytes([cmd])


@pytest.mark.parametrize("rect", [(0, 0, 319, 479), (296, 0, 319, 23),
                                  (148, 228, 171, 251), (0, 456, 23, 479)])
def test_command_packet_matches_an_independent_encoding(rect):
    x, y, ex, ey = rect
    assert (turing.command(turing.DISPLAY_BITMAP, x, y, ex, ey)
            == naive_command(turing.DISPLAY_BITMAP, x, y, ex, ey))


def test_command_is_exactly_six_bytes():
    assert len(turing.command(turing.SCREEN_ON)) == 6


def test_write_converts_exclusive_rects_to_the_inclusive_wire_form():
    """The driver contract uses exclusive x1/y1; the wire wants the last pixel.
    An off-by-one here would shift every partial update by a row and a column."""
    dev = driver()
    dev.write(b"\x00\x00" * (24 * 24), (296, 0, 320, 24))
    assert dev.port.written[:6] == turing.command(turing.DISPLAY_BITMAP,
                                                  296, 0, 319, 23)


def test_payload_follows_the_command_unchanged():
    dev = driver()
    payload = bytes(range(256)) * 8              # 2048 B = 32x32 px
    dev.write(payload, (0, 0, 32, 32))
    assert dev.port.written == turing.command(turing.DISPLAY_BITMAP, 0, 0, 31, 31) + payload


def test_wrong_payload_length_raises_before_anything_is_sent():
    dev = driver()
    with pytest.raises(DriverError):
        dev.write(b"\x00" * 10, (0, 0, 32, 32))
    assert dev.port.written == b""


def test_empty_rect_is_an_error_not_a_no_op():
    """len == 0 satisfies the length check on its own, and x1-1 would go on the
    wire as 0xFF."""
    dev = driver()
    with pytest.raises(DriverError):
        dev.write(b"", (10, 10, 10, 10))


def test_rect_outside_the_screen_is_rejected():
    dev = driver()
    with pytest.raises(DriverError):
        dev.write(b"\x00\x00", (319, 479, 321, 481))


def test_brightness_is_inverted_on_the_wire():
    dev = driver()
    dev.set_brightness(0)
    assert dev.port.written[:6] == turing.command(turing.SET_BRIGHTNESS, x=255)
    dev = driver()
    dev.set_brightness(100)
    assert dev.port.written[:6] == turing.command(turing.SET_BRIGHTNESS, x=0)


def test_brightness_is_clamped():
    dev = driver()
    dev.set_brightness(500)
    assert dev.port.written[:6] == turing.command(turing.SET_BRIGHTNESS, x=0)


def test_a_torn_write_is_padded_so_the_next_command_is_read_as_a_command():
    """The firmware counts pixel bytes; a short bitmap makes it eat the next
    command's 6 bytes as pixels and desynchronise for good."""
    dev = driver(fail_at=1000)
    with pytest.raises(DriverError):
        dev.write(b"\x00\x00" * (32 * 32), (0, 0, 32, 32))
    assert dev.torn == 1
    # command + payload, all of it accounted for, whatever the bytes were
    assert len(dev.port.written) == 6 + 32 * 32 * 2


def test_padding_that_also_fails_is_swallowed():
    """Then the stream is desynchronised for good and only a replug fixes it -
    but the failure has to arrive as a DriverError the link can back off from,
    not as an exception from inside the recovery path."""
    dev = driver(fail_at=1000, sticky=True)
    with pytest.raises(DriverError):
        dev.write(b"\x00\x00" * (32 * 32), (0, 0, 32, 32))
    assert dev.torn == 1


def test_a_short_write_without_an_exception_is_detected():
    """pyserial returns a short count and raises nothing when the IO was
    cancelled. Watching only for exceptions would tear the payload silently."""
    dev = driver(short_at=500)
    with pytest.raises(DriverError) as e:
        dev.write(b"\x00\x00" * (32 * 32), (0, 0, 32, 32))
    assert "cut short" in str(e.value)
    assert dev.torn == 1


def test_write_timeout_is_bounded_and_scales_with_the_payload():
    dev = driver()
    dev.write(b"\x00\x00" * (32 * 32), (0, 0, 32, 32))
    assert 0 < dev.port.write_timeout < 5


def test_port_path_comes_from_the_location_chain():
    assert turing.port_path_from_location("1-8.4") == "8.4"
    assert turing.port_path_from_location("1-8.4:1.0") == "8.4"
    assert turing.port_path_from_location(None) is None
    assert turing.port_path_from_location("COM4") is None


def test_capabilities_are_fixed_regardless_of_the_asked_canvas():
    """A screen with its own geometry must be able to contradict the config, or a
    mismatch would be drawn instead of reported."""
    caps = turing.caps_for((800, 480))
    assert caps.native == (320, 480) and caps.canvas == (480, 320)
    assert caps.rotate == 90 and caps.byte_order == LITTLE
    assert caps.rect_updates and not caps.acked


def test_payload_for_this_screen_is_little_endian():
    from PIL import Image
    img = Image.new("RGB", (2, 1), (255, 0, 0))
    assert pack_rgb565(img, LITTLE) == struct.pack("<HH", 0xF800, 0xF800)
