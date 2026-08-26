"""TURZX 5.2" wire protocol, against a fake libusb.

Modelled on test_turing_protocol.py, with two deliberate departures from it.

FIRST: there is no `importorskip`. The rev A file skips when pyserial is missing because
what it guards is a transport for hardware nobody may have. Here the thing under test is
a CIPHER: if `pycryptodome` is absent this file must go RED, not print a quiet `s`. A
wrong packet on this link is not reported by anything — the firmware answers 0xC8 to a
frame it then fails to draw — so the encoder check is the only place the mistake can be
caught, and a skipped check is worse than no check because it reads as a pass.

SECOND: the cipher is checked against a KNOWN-ANSWER VECTOR written out below as a
literal, not against a second implementation. For rev A an independent encoder is three
lines of bit shifts and worth writing twice; for DES-CBC it would be the same library
called twice, which proves nothing.
"""
import io
import struct

import pytest
from PIL import Image

from panel.drivers import turzx_usb as t
from panel.pixels import LITTLE, pack_rgb565

# The vector: a 500 B header for command 102, timestamp 0x01020304, payload size
# 0x00BEEF, zero-padded to 504 and sealed with key = IV = b"slv3tuzx". Produced once,
# on the bench, and pasted here — if this changes, the wire changed.
VECTOR_HEAD = bytes.fromhex("d508e75bad1759cb6d6d7ae2ca84094e")
VECTOR_TAIL = bytes.fromhex("a62c5113a6953877")


class FakeDll:
    """Enough of libusb-1.0 to run the driver with no hardware.

    `byref(x)._obj` is how the transferred-byte count is handed back, the same private
    detail the rev A driver reads out of pyserial. It is stable, and the alternative is
    not testing the transport at all.
    """

    def __init__(self, replies=()):
        self.writes = []
        self.replies = list(replies)
        self.reads = 0
        self.opened = 0
        self.claimed = 0
        self.released = 0
        self.closed = 0

    # -- the calls the driver makes ---------------------------------------

    def libusb_open(self, ptr, handle_ref):
        self.opened += 1
        handle_ref._obj.value = 0xBEEF
        return 0

    def libusb_claim_interface(self, h, iface):
        self.claimed += 1
        return 0

    def libusb_release_interface(self, h, iface):
        self.released += 1
        return 0

    def libusb_close(self, h):
        self.closed += 1
        return 0

    def libusb_strerror(self, code):
        return b"fake"

    def libusb_bulk_transfer(self, h, ep, data, length, n_ref, timeout):
        import ctypes as C
        n = n_ref._obj
        if ep == t.EP_OUT:
            self.writes.append(bytes(data[:length]))
            n.value = length
            return 0
        self.reads += 1
        if not self.replies:
            return -7                      # LIBUSB_ERROR_TIMEOUT
        reply = self.replies.pop(0)
        if reply is None:
            return -7
        C.memmove(data, reply, len(reply))
        n.value = len(reply)
        return 0


def ack_for(cmd, status=0xC8):
    """What the device sends back: the command echoed, then the status."""
    return bytes([cmd, status]) + bytes(510)


def driver(monkeypatch, replies=()):
    dll = FakeDll(replies)
    monkeypatch.setattr(t, "load", lambda dll_path=None: dll)
    found = t.Found(dll, ptr=1, bus=1, ports=(5,), address=13, pid=0x0050, index=0)
    return t.Turzx(found), dll


def opened(monkeypatch, replies=()):
    """A driver past open(), with the sync acknowledged and the log cleared.

    The leading `None` is not padding: `open()` drains the IN endpoint BEFORE it sends
    anything, so a queue that starts with a real reply would have it swallowed by the
    drain. That is the behaviour under test in
    `test_open_drains_whatever_the_previous_process_left`; here it just has to be fed
    an empty pipe, which is what a `None` (a timeout) means to the fake.
    """
    dev, dll = driver(monkeypatch, [None, ack_for(t.CMD_SYNC)] + list(replies))
    dev.open()
    dll.writes.clear()
    return dev, dll


# -- the packet --------------------------------------------------------------


def test_the_packet_is_512_bytes_with_the_trailer_where_the_firmware_looks():
    packet = t.sealed(t.header(t.CMD_SYNC))
    assert len(packet) == t.PACKET == 512
    assert packet[510] == 0xA1 and packet[511] == 0x1A
    # Six zero bytes between the ciphertext and the trailer. Stated in the header of
    # the driver because 504 + 2 does not reach 512 and the gap has to go somewhere.
    assert packet[504:510] == bytes(6)


def test_des_matches_the_known_vector():
    plain = bytearray(t.HEADER)
    plain[0] = t.CMD_UPLOAD_PNG
    plain[2] = 0x1A
    plain[3] = 0x6D
    plain[4:8] = struct.pack("<I", 0x01020304)
    plain[8:12] = struct.pack(">I", 0x00BEEF)
    packet = t.sealed(plain)
    assert packet[:16] == VECTOR_HEAD
    assert packet[496:504] == VECTOR_TAIL


def test_the_header_carries_the_payload_size_big_endian():
    head = t.header(t.CMD_UPLOAD_PNG, 0x00BEEF)
    assert head[0] == t.CMD_UPLOAD_PNG
    assert bytes(head[2:4]) == b"\x1a\x6d"
    assert bytes(head[8:12]) == struct.pack(">I", 0x00BEEF)


def test_only_the_three_measured_commands_exist():
    """The flash-equivalent opcodes must not be reachable from this module.

    11 restarts the device; 38/39/40 open, write and delete files in its storage; 125
    saves settings. None of them is needed to draw, and all of them are one typo away
    from bricking a panel — so the module must not name them at all.
    """
    commands = {v for k, v in vars(t).items()
                if k.startswith("CMD_") and isinstance(v, int)}
    assert commands == {t.CMD_SYNC, t.CMD_BRIGHTNESS, t.CMD_UPLOAD_PNG}
    assert commands.isdisjoint({11, 38, 39, 40, 125})


# -- the payload -------------------------------------------------------------


def test_rgb565_comes_back_as_the_pixels_that_went_in():
    """The whole reason this driver takes 565 instead of an image.

    Red must stay red: a raw mode with the channels the other way round would swap R
    and B and nothing downstream would notice.
    """
    src = Image.new("RGB", (4, 1))
    src.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (217, 119, 87)])
    packed = pack_rgb565(src, LITTLE)
    back = Image.frombytes("RGB", (4, 1), packed, "raw", "BGR;16")
    assert list(back.get_flattened_data()) == [
        (255, 0, 0), (0, 255, 0), (0, 0, 255),
        # ACCENT, quantised to 5/6/5 and expanded again — measured, not rounded by hand.
        (222, 117, 82),
    ]


def test_the_png_on_the_wire_is_rgba(monkeypatch):
    """Three channels are accepted and acknowledged, and draw ghosts. See the header."""
    dev, dll = opened(monkeypatch, [ack_for(t.CMD_UPLOAD_PNG)])
    dev.write(pack_rgb565(Image.new("RGB", t.ACCEPTS, (10, 20, 30)), LITTLE),
              (0, 0, *t.ACCEPTS))
    png = dll.writes[0][t.PACKET:]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(png)).mode == "RGBA"


def test_the_frame_is_letterboxed_onto_the_real_glass(monkeypatch):
    dev, dll = opened(monkeypatch, [ack_for(t.CMD_UPLOAD_PNG)])
    dev.write(pack_rgb565(Image.new("RGB", t.ACCEPTS, (10, 20, 30)), LITTLE),
              (0, 0, *t.ACCEPTS))
    img = Image.open(io.BytesIO(dll.writes[0][t.PACKET:]))
    assert img.size == t.GLASS == (720, 1280)
    # The content is centred and the border is black, not the frame's own colour.
    assert img.convert("RGB").getpixel((0, 0)) == (0, 0, 0)
    assert img.convert("RGB").getpixel((t.GLASS[0] // 2, t.GLASS[1] // 2)) != (0, 0, 0)


def test_a_buffer_of_the_wrong_size_is_refused_before_anything_goes_out(monkeypatch):
    dev, dll = opened(monkeypatch)
    with pytest.raises(Exception):
        dev.write(b"\x00" * 10, (0, 0, *t.ACCEPTS))
    assert dll.writes == []


# -- flow control ------------------------------------------------------------


def test_open_drains_whatever_the_previous_process_left(monkeypatch):
    """Without this every read answers the previous command. Measured twice."""
    stale = [ack_for(t.CMD_UPLOAD_PNG), ack_for(t.CMD_UPLOAD_PNG), None]
    dev, dll = driver(monkeypatch, stale + [ack_for(t.CMD_SYNC)])
    dev.open()
    # Two stale replies swallowed, the empty one ended the drain, then sync got its own.
    assert dll.reads >= 4
    assert dev.missed_ack == 0


def test_one_frame_takes_exactly_one_acknowledgement(monkeypatch):
    dev, dll = opened(monkeypatch, [ack_for(t.CMD_UPLOAD_PNG),
                                    ack_for(t.CMD_UPLOAD_PNG)])
    payload = pack_rgb565(Image.new("RGB", t.ACCEPTS, (1, 2, 3)), LITTLE)
    before = dll.reads
    assert dev.write(payload, (0, 0, *t.ACCEPTS)) == 0xC8
    assert dll.reads == before + 1
    assert dev.write(payload, (0, 0, *t.ACCEPTS)) == 0xC8
    assert dev.missed_ack == 0


def test_an_empty_packet_is_not_the_answer_and_is_read_past(monkeypatch):
    """The device sends a zero-length reply before the real one often enough that a
    single read is not a measurement."""
    dev, dll = opened(monkeypatch, [b"", ack_for(t.CMD_UPLOAD_PNG)])
    assert dev.write(pack_rgb565(Image.new("RGB", t.ACCEPTS), LITTLE),
                     (0, 0, *t.ACCEPTS)) == 0xC8
    assert dev.missed_ack == 0


def test_no_acknowledgement_returns_none_so_the_link_can_count_it(monkeypatch):
    """`link.py` resets after three `None`s — so `None` must mean exactly 'no answer'."""
    dev, dll = opened(monkeypatch, [])
    assert dev.write(pack_rgb565(Image.new("RGB", t.ACCEPTS), LITTLE),
                     (0, 0, *t.ACCEPTS)) is None
    assert dev.missed_ack == 1


def test_a_reply_for_another_command_is_not_counted_as_this_ones(monkeypatch):
    """Charging one frame's failure to another is worse than missing it."""
    dev, dll = opened(monkeypatch, [ack_for(t.CMD_SYNC)] * t.ACK_ATTEMPTS)
    assert dev.write(pack_rgb565(Image.new("RGB", t.ACCEPTS), LITTLE),
                     (0, 0, *t.ACCEPTS)) is None
    assert dev.missed_ack == 1


def test_brightness_is_clamped_and_scaled_to_the_wire_range(monkeypatch):
    dev, dll = opened(monkeypatch, [ack_for(t.CMD_BRIGHTNESS)] * 3)
    dev.set_brightness(500)
    dev.set_brightness(-5)
    dev.set_brightness(50)
    # The value rides in the encrypted header, so read it back the only honest way:
    # decrypt what actually went out. Byte 8 — the same field an upload uses for its
    # payload size; at any other offset the panel acknowledges and goes dark.
    from Crypto.Cipher import DES
    seen = []
    for packet in dll.writes:
        plain = DES.new(t.KEY, DES.MODE_CBC, t.KEY).decrypt(packet[:504])
        assert plain[0] == t.CMD_BRIGHTNESS
        seen.append(plain[8])
    assert seen == [102, 0, 51]


def test_a_command_carrying_a_value_carries_no_payload(monkeypatch):
    """Byte 8 is shared with the upload's size field, so the two must never meet."""
    dev, _ = opened(monkeypatch)
    with pytest.raises(Exception):
        dev._command(t.CMD_BRIGHTNESS, value=40, payload=b"x")


def test_capabilities_are_fixed_regardless_of_the_asked_canvas():
    caps = t.caps_for((800, 480))
    assert caps.canvas == (480, 320)
    assert caps.native == (320, 480)
    assert caps.rotate == 90
    assert caps.byte_order == LITTLE
    assert caps.rect_updates is False
    assert caps.acked is True


def test_only_a_known_model_is_claimed():
    """An unknown TURZX must be NOT SEEN rather than drawn with wrong geometry."""
    assert set(t.MODELS) == {0x0050}
    assert t.MODELS[0x0050] == (720, 1280)
