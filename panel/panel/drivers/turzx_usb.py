r"""TURZX 5.2" panel driver (VID 1CBE / PID 0050) — Windows + libusb-1.0 + WinUSB.

The third screen on this desk, and the first one that is neither a mass-storage
impersonator (AX206) nor a serial port (Turing rev A). Windows binds it to WinUSB
(`USB\MS_COMP_WINUSB`); the libusb-1.0 backend talks to that without rebinding
anything, exactly as it talks to the AX206's libusb0.

Descriptors, read off this unit rather than a datasheet: high speed (480 Mb/s),
vendor class FF/00/00, one interface, two bulk endpoints 0x81 IN / 0x01 OUT with a
512 B max packet, MaxPower 120 mA, strings `TURZX` / `TURZX1.0`.

The protocol is public — `mathoudebine/turing-smart-screen-python`,
`library/lcd/lcd_comm_turing_usb.py` (GPL-3.0) — so nothing here was guessed. Every
command is a 512 B packet whose first 504 bytes are DES-CBC ciphertext:

    [0:504]    DES-CBC(key = IV = b"slv3tuzx") of a 500 B header, zero-padded to 504
    [504:510]  six zero bytes
    [510:512]  0xA1 0x1A

and the 500 B plaintext header is

    [0]     command id
    [2:4]   0x1A 0x6D
    [4:8]   little-endian milliseconds since local midnight
    [8:12]  BIG-endian size of the payload that follows

The payload — for us always a PNG — is appended to the SAME bulk write.

Six things measured on this unit that appear in no reference source:

  1. THE PNG MUST BE RGBA. A three-channel PNG is accepted and acknowledged with the
     same 0xC8 as a good one, but the firmware walks the decoded buffer at four bytes
     per pixel: the frame comes out as a stack of ghost copies stepping down the glass,
     semi-transparent, with the boot wallpaper showing through. The acknowledgement does
     not distinguish the two cases, so this is not something the link can catch — it has
     to be right by construction.
  2. ROTATION IS COUNTER-CLOCKWISE. The buffer's own top edge lands on the RIGHT of the
     physical panel, so physical-up is the buffer's LEFT. Established with a probe frame
     carrying corner tags and ten numbered bands, not by trying rotations until one
     looked right.
  3. DRAIN THE PIPE ON OPEN. Acknowledgements left behind by a previous process (or a
     previous run of ours) sit in the IN endpoint, and then every read answers the
     PREVIOUS command. Measured twice: a run that looked like "sync returned the echo of
     command 102" was nothing but an undrained queue.
  4. THE ACKNOWLEDGEMENT IS THE ONLY FLOW CONTROL. Frames fired back to back queue their
     acknowledgements on the device — one run left 36 of them — and once that queue fills
     the bulk OUT write itself stalls until it times out (measured: 8 s). Consume exactly
     one acknowledgement per command.
  5. A ZERO-LENGTH READ IS NOT THE ANSWER. The device sometimes returns an empty packet
     before the real reply, so a single read is not enough: read until the echo of the
     command comes back, or until the attempts run out.
  6. ~70 ms PER FULL FRAME, 14 frames/s sustained (40 frames in 2809 ms). Almost all of
     that is device-side PNG decode and refresh: 72 kB is ~1.5 ms on the wire. Payload
     SIZE therefore barely matters and encode TIME does, which is why the PNG is written
     at compress_level 1 (5.7 ms) and not 9 (73 ms) — level 9 would buy 34 kB, worth
     about 0.7 ms of wire, for +67 ms of CPU.

COMMANDS THAT MUST NEVER APPEAR IN THIS FILE, for the same reason ax206.py keeps the
flash commands out of itself: 11 (restart), 38/39/40 (open/write/delete file) and 125
(save settings) all write to the device's own storage. `reset()` below reopens the
handle instead of sending 11 — a reopen is measured and a restart is not.

WHAT THIS DRIVER TAKES ON THE WIRE FROM US IS RGB565, not an image. That is deliberate
and it is the whole reason the shared layers are untouched by this screen: the panel
already packs 565 for the other two, PIL decodes it back with a native raw decoder
(`BGR;16`, measured 0.44 ms at 320x480 and 1.90 ms at 720x1280), and the 5/6/5 round trip
costs colour the other two screens have already spent. Verified byte-exact: pure red
survives as pure red and ACCENT (217,119,87) comes back as (222,117,82), which is what
5/6/5 quantisation predicts.
"""
import ctypes as C
import io
import struct
import time

from ..pixels import LITTLE
from ._libusb import DevDesc, context, format_port_path, load, port_chain, strerror
from .base import Caps, DriverError, Scale, Target, check_rect, release, select

try:
    from PIL import Image
except ImportError:                      # pragma: no cover - depends on the venv
    Image = None

try:
    from Crypto.Cipher import DES
except ImportError:                      # pragma: no cover - depends on the venv
    DES = None

NAME = "turzx-usb"
SELECTOR_KEYS = ("port_path", "index")

VID = 0x1CBE
# Only the model actually on this desk. Every protocol fact in the header above was
# measured on THIS unit; the rest of the family (8.8", 12.3") is hearsay here. An
# unknown TURZX must therefore be NOT SEEN rather than drawn wrongly, which is why
# discover() matches the (VID, PID) PAIR and never the vendor alone.
MODELS = {0x0050: (720, 1280)}          # PID -> native glass geometry, portrait

EP_OUT, EP_IN = 0x01, 0x81
INTERFACE = 0
PACKET = 512
HEADER = 500
TRAILER = (0xA1, 0x1A)
KEY = b"slv3tuzx"                        # firmware constant; obfuscation, not security

CMD_SYNC = 10
CMD_BRIGHTNESS = 14
CMD_UPLOAD_PNG = 102

# Stage 1 geometry. The renderer draws one 480x320 canvas for every screen, so this
# driver accepts that canvas rotated (320x480) and letterboxes it onto the real glass
# inside write(). BOTH of these go away once the client can render per canvas.
CANVAS = (480, 320)
ACCEPTS = (320, 480)                     # the buffer we take, NOT the glass
ROTATE = 90
GLASS = MODELS[0x0050]                   # (720, 1280) — what is actually out there
SCALE = 2                                # integer: the layout is drawn in hairlines

# Measured: 72 kB moved in ~1.5 ms once the pipe is free, so the wire is not the cost.
BYTES_PER_SEC = 36_000_000
# ...the firmware is. A full frame takes ~70 ms of decode and refresh regardless of size,
# so the write timeout is that fixed cost plus a generous multiple of the transfer.
FRAME_OVERHEAD_SEC = 0.5
PNG_LEVEL = 1
ACK_OK = 0xC8                            # 200
ACK_ATTEMPTS = 4


def caps_for(canvas=None):
    """Fixed geometry: `canvas` is accepted and ignored, as on the Turing rev A.

    `native` is the buffer this driver ACCEPTS, which in stage 1 is not the glass —
    the panel is 720x1280 and we take 320x480, then scale. That is a deliberate,
    temporary lie in a documented field, and it is why `--probe` prints 320x480 for a
    720x1280 screen until the client can render a second canvas.
    """
    return Caps(name=NAME, canvas=CANVAS, native=ACCEPTS, rotate=ROTATE,
                byte_order=LITTLE, rect_updates=False, acked=True,
                reset_on_open=False,
                brightness=Scale("percent", 0, 100, 40),
                bytes_per_sec=BYTES_PER_SEC)


def unavailable(options=None):
    """None, or why this machine cannot drive this screen. Never raises."""
    if Image is None:
        return ("the Pillow package is missing (run "
                "pip install -r panel/requirements.txt)")
    if DES is None:
        return ("the pycryptodome package is missing (run "
                "pip install -r panel/requirements.txt) — the firmware takes only "
                "DES-encrypted command packets")
    try:
        load((options or {}).get("dll"))
    except (DriverError, OSError) as e:
        return "libusb-1.0 unavailable (%s)" % e
    return None


# --- enumeration ------------------------------------------------------------


class Found:
    """One TURZX on the bus, holding a live libusb reference.

    Same contract as the AX206's: the reference belongs to this object and must be
    handed back through `release()`, or every enumeration leaks a device.
    """

    __slots__ = ("ptr", "bus", "ports", "address", "pid", "index", "_dll")

    def __init__(self, dll, ptr, bus, ports, address, pid, index):
        self._dll = dll
        self.ptr = ptr
        self.bus = bus
        self.ports = ports
        self.address = address
        self.pid = pid
        self.index = index

    @property
    def port_path(self):
        return format_port_path(self.ports)

    @property
    def glass(self):
        return MODELS[self.pid]

    def release(self):
        if self.ptr is not None:
            self._dll.libusb_unref_device(self.ptr)
            self.ptr = None

    def __repr__(self):
        return "<TURZX #%d %04x:%04x bus=%s ports=%s addr=%s>" % (
            self.index, VID, self.pid, self.bus, self.port_path, self.address)


def find_all(dll_path=None):
    """Every TURZX of a KNOWN model, in libusb enumeration order."""
    dll = load(dll_path)
    ctx = context(dll_path)
    lst = C.POINTER(C.c_void_p)()
    count = dll.libusb_get_device_list(ctx, C.byref(lst))
    if count < 0:
        raise DriverError("libusb_get_device_list: %s" % strerror(dll, count))
    out = []
    try:
        for i in range(count):
            dev = lst[i]
            desc = DevDesc()
            if dll.libusb_get_device_descriptor(dev, C.byref(desc)) != 0:
                continue
            if desc.idVendor != VID or desc.idProduct not in MODELS:
                continue
            out.append(Found(dll, dll.libusb_ref_device(dev),
                             dll.libusb_get_bus_number(dev), port_chain(dll, dev),
                             dll.libusb_get_device_address(dev),
                             desc.idProduct, len(out)))
    finally:
        dll.libusb_free_device_list(lst, 1)
    return out


def discover(options=None):
    """Every TURZX on the bus, as neutral Targets."""
    options = options or {}
    return [Target(backend=NAME, index=f.index, port_path=f.port_path, handle=f,
                   bus=f.bus, address=f.address)
            for f in find_all(options.get("dll"))]


def open_panel(selector, options=None):
    """Open the screen a selector points at and return the driver."""
    options = options or {}
    targets = discover(options)
    picked = select(targets, selector, what="screen %s" % NAME)
    release(targets, keep=picked)
    return Turzx(picked.handle, dll_path=options.get("dll")).open()


# --- the wire ---------------------------------------------------------------


def header(cmd, size=0):
    """The 500 B plaintext header. `size` is the payload that follows, big-endian."""
    p = bytearray(HEADER)
    p[0] = cmd & 0xFF
    p[2] = 0x1A
    p[3] = 0x6D
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    p[4:8] = struct.pack("<I", int((time.time() - midnight) * 1000) & 0xFFFFFFFF)
    p[8:12] = struct.pack(">I", size)
    return p


def sealed(plain):
    """Header -> the 512 B packet that goes on the wire.

    Zero padding, not PKCS7: 500 rounds up to 504 with four zero bytes, and the six
    bytes between the ciphertext and the trailer stay zero because the buffer starts
    that way. Written out rather than assumed — a misplaced trailer on a link whose
    only feedback is an acknowledgement shows up as "accepted, drew nothing".
    """
    cipher = DES.new(KEY, DES.MODE_CBC, KEY)
    padded = bytes(plain).ljust((len(plain) + 7) // 8 * 8, b"\x00")
    out = bytearray(PACKET)
    enc = cipher.encrypt(padded)
    out[:len(enc)] = enc
    out[510], out[511] = TRAILER
    return bytes(out)


class Turzx:
    def __init__(self, found, dll_path=None):
        self.found = found
        self.dll = load(dll_path)
        self.h = None
        self.width, self.height = ACCEPTS
        self.glass = found.glass if found is not None else GLASS
        self.missed_ack = 0          # acknowledgements we asked for and did not get

    @property
    def caps(self):
        return caps_for()

    # -- lifecycle ---------------------------------------------------------

    def open(self):
        if self.found is None or self.found.ptr is None:
            raise DriverError("the TURZX reference is gone; enumerate again")
        h = C.c_void_p()
        rc = self.dll.libusb_open(self.found.ptr, C.byref(h))
        if rc != 0:
            raise DriverError("libusb_open: %s" % self._err(rc))
        self.h = h
        rc = self.dll.libusb_claim_interface(self.h, INTERFACE)
        if rc != 0:
            self.dll.libusb_close(self.h)
            self.h = None
            # The same words the AX206 path uses, because the installer greps for this
            # phrase and the advice is identical: stop that program, we retry on our own.
            raise DriverError("libusb_claim_interface: %s (panel held by another "
                              "process?)" % self._err(rc))
        self._drain()
        self._command(CMD_SYNC)
        return self

    def close(self):
        """Closes the handle and leaves the last frame on the glass, deliberately."""
        if self.h is not None:
            try:
                self.dll.libusb_release_interface(self.h, INTERFACE)
            except Exception:
                pass
            try:
                self.dll.libusb_close(self.h)
            except Exception:
                pass
            self.h = None

    def reset(self):
        """Reopen the handle. NOT command 11.

        A restart is unmeasured on this unit, and the failure it would have to cure —
        a desynchronised acknowledgement queue — is cured by draining, which `open()`
        does anyway.
        """
        self.close()
        time.sleep(0.2)
        return self.open()

    # -- writing -----------------------------------------------------------

    def set_brightness(self, level):
        """0..100 percent. The wire takes 0..102, and 0 really is off.

        The value goes in header byte 8 — the same field an upload uses for its payload
        size. Wrong offset here is silent: the device acknowledges 0xC8 and goes dark.
        """
        pct = max(0, min(100, int(level)))
        return self._command(CMD_BRIGHTNESS, value=round(pct / 100 * 102))

    def write(self, rgb565, rect):
        """Driver contract (base.py): a full-screen RGB565 little-endian buffer.

        Returns the acknowledgement status byte, or None when the device did not
        acknowledge — `link.py` counts those and resets after three, so None must mean
        exactly that and nothing else.
        """
        check_rect(rect, self.width, self.height, len(rgb565))
        img = Image.frombytes("RGB", (self.width, self.height), bytes(rgb565),
                              "raw", "BGR;16")
        return self._send_image(self._fit(img))

    def write_image(self, img, at=(0, 0)):
        """Diagnostics only — the client packs frames once and hands over 565."""
        return self._send_image(self._fit(img.convert("RGB")))

    def _fit(self, img):
        """The accepted buffer, letterboxed onto the real glass. Stage 1 only.

        Integer scale, because this layout is built out of hairlines and a fractional
        one would smear them; RGBA by `convert`, never `putalpha`, because the image
        the renderer hands out is memoised and shared with the other panels — mutating
        it in place would corrupt their frame.
        """
        big = img.resize((img.width * SCALE, img.height * SCALE), Image.NEAREST)
        canvas = Image.new("RGBA", self.glass, (0, 0, 0, 255))
        canvas.paste(big, ((self.glass[0] - big.width) // 2,
                           (self.glass[1] - big.height) // 2))
        return canvas

    def _send_image(self, rgba):
        buf = io.BytesIO()
        rgba.save(buf, format="PNG", compress_level=PNG_LEVEL)
        png = buf.getvalue()
        return self._command(CMD_UPLOAD_PNG, payload=png)

    # -- transport ---------------------------------------------------------

    def _err(self, code):
        return strerror(self.dll, code)

    def _timeout_ms(self, nbytes):
        return int((nbytes / float(BYTES_PER_SEC) * 3 + FRAME_OVERHEAD_SEC) * 1000)

    def _command(self, cmd, value=None, payload=b""):
        """One command, one acknowledgement. Returns the status byte or None."""
        if self.h is None:
            raise DriverError("the panel is not open")
        head = header(cmd, len(payload))
        if value is not None:
            if payload:
                raise DriverError("a command cannot carry both a value and a payload: "
                                  "they share header byte 8")
            head[8] = value & 0xFF
        self._bulk_out(sealed(head) + payload)
        return self._ack(cmd)

    def _bulk_out(self, data):
        n = C.c_int(0)
        rc = self.dll.libusb_bulk_transfer(self.h, EP_OUT, data, len(data),
                                           C.byref(n), self._timeout_ms(len(data)))
        if rc != 0:
            raise DriverError("bulk write failed (%d/%d B): %s"
                              % (n.value, len(data), self._err(rc)))
        if n.value != len(data):
            raise DriverError("bulk write cut short: %d of %d B" % (n.value, len(data)))

    def _ack(self, cmd):
        """Read until the echo of `cmd` comes back. None when it never does.

        The device answers an empty packet before the real reply often enough that a
        single read is not a measurement — see finding 5 in the header. Anything that
        is not this command's echo is a stale reply, so it is dropped rather than
        returned: charging one frame's failure to another is worse than missing it.
        """
        buf = (C.c_ubyte * PACKET)()
        for _ in range(ACK_ATTEMPTS):
            n = C.c_int(0)
            rc = self.dll.libusb_bulk_transfer(
                self.h, EP_IN, C.cast(buf, C.c_char_p), PACKET, C.byref(n),
                self._timeout_ms(PACKET))
            if rc != 0:
                break
            if n.value >= 2 and buf[0] == (cmd & 0xFF):
                return buf[1]
        self.missed_ack += 1
        return None

    def _drain(self):
        """Swallow whatever a previous process left in the IN endpoint.

        Without this every read answers the previous command and the log lies about
        which one was acknowledged — measured, twice.
        """
        buf = (C.c_ubyte * PACKET)()
        for _ in range(16):
            n = C.c_int(0)
            rc = self.dll.libusb_bulk_transfer(
                self.h, EP_IN, C.cast(buf, C.c_char_p), PACKET, C.byref(n), 200)
            if rc != 0:
                break
