"""Turing Smart Screen rev A over a USB serial port (VID 1A86 / PID 5722).

Measured on this display model, because none of it is documented:

  * 320x480 portrait natively. Our 480x320 layout is rotated 90 degrees HOST-SIDE
    and fills it exactly. SET_ORIENTATION is never sent - see below.
  * RGB565 LITTLE-endian (the AX206 wants the other order).
  * 6-byte command packet, then the pixels, row-major inside the rectangle.
  * PARTIAL RECTANGLES ARE REAL. Unlike the AX206, whose rect sets a wrap window
    and whose every update costs a whole frame, this one updates exactly the
    rectangle you name and leaves the rest of the glass alone. Verified by eye
    with 24x24 squares in the corners.
  * Windows sees it as a plain CDC-ACM serial device: HardwareIds
    USB\VID_1A86&PID_5722, CompatibleIds USB\Class_02&SubClass_02&Prot_01,
    driven by the in-box usbser.sys - NOT by a CH34x vendor driver. So a fresh
    machine needs no driver install, and BusReportedDeviceDesc says "UsbMonitor".
  * It does not answer the rev B handshake (0xCA 'HELLO' .. 0xCA) at 115200 or at
    1 Mbaud, and it does draw correctly for every rev A command we use. Measured,
    not assumed: the revision is what the firmware answers to, and this one
    answers to rev A only.
  * Throughput is strictly linear at 6.1 us/byte with ZERO fixed overhead:
    512 B -> 3.1 ms, 307200 B -> 1873 ms, same MB/s at every size. So a full
    frame is 1.87 s and partial updates are the only way to keep a 1 s tick.
  * The configured baud rate is IGNORED: 115200, 1M and 2M all measure the same.
    We open at 115200 because that is what the reference implementations use.
  * NOTHING IS EVER ACKNOWLEDGED. There is no CSW analogue and no read-back.

That last point shapes the whole driver. Two consequences worth stating:

  1. A torn write is unrecoverable by reopening the port. The firmware is midway
     through counting pixel bytes; closing the COM port does not touch the MCU,
     so the next command's 6 bytes get eaten as pixels and the stream is
     desynchronised for good - silently, and with an odd offset that means
     permanently byte-swapped colour. The only recovery with a mechanism behind
     it is padding the exact remainder, which is what _write_all does. If that
     fails too, the screen needs unplugging; the README says so.
  2. Any write can block forever. pyserial's default write_timeout is None, which
     on Windows leaves COMMTIMEOUTS zeroed and waits in GetOverlappedResult; with
     rtscts a screen that deasserts CTS stalls the write, and with it the single
     tick loop and THE OTHER SCREEN. Every write here is bounded by a timeout
     derived from the measured throughput.

RESET (101) is deliberately not used: unmeasured, and it cannot help anyway,
because a firmware mid-payload would eat its bytes as pixels. SET_ORIENTATION
(121) is deliberately not used either: rotation host-side is measured to work,
and with no acknowledgement a coordinate-space mistake would show up as a
scrambled screen rather than an error.
"""
import time

from ..pixels import LITTLE, pack_rgb565
from .base import Caps, DriverError, Scale, Target, check_rect, release, select

try:
    import serial
    from serial.tools import list_ports
except ImportError:                      # pragma: no cover - depends on the venv
    serial = None
    list_ports = None

NAME = "turing-rev-a"
SELECTOR_KEYS = ("port_path", "index", "com")

VID, PID = 0x1A86, 0x5722
# A firmware constant, not a unit id - the same trap as the AX206's "WCH32", so it
# is never a selector. It IS a filter, though: 1A86:5722 is a WCH USB-serial id
# that other devices use as well (an Arduino-class board, a plain adapter), and
# sending bitmap commands to one of those would be writing pixel data into
# somebody else's hardware.
SERIAL_HINT = "USB35INCHIPSV2"

NATIVE = (320, 480)
CANVAS = (480, 320)
ROTATE = 90
BAUD = 115200
# Measured: 6.1 us/byte, zero fixed overhead.
BYTES_PER_SEC = 164_000
# Bounded writes. Small enough that a stall is noticed within a fraction of a
# frame, large enough that the per-call overhead stays invisible.
CHUNK = 8192

RESET = 101
CLEAR = 102
TO_BLACK = 103
SCREEN_OFF = 108
SCREEN_ON = 109
SET_BRIGHTNESS = 110
SET_ORIENTATION = 121
DISPLAY_BITMAP = 197


def command(cmd, x=0, y=0, ex=0, ey=0):
    """The 6-byte packet: two 10-bit corners packed into 5 bytes, then the code.

    ex/ey are INCLUSIVE here - the wire format's own convention. Callers work in
    the exclusive rectangles the driver contract uses and convert once, in write().
    """
    return bytes([
        (x >> 2) & 0xFF,
        (((x & 3) << 6) + (y >> 4)) & 0xFF,
        (((y & 15) << 4) + (ex >> 6)) & 0xFF,
        (((ex & 63) << 2) + (ey >> 8)) & 0xFF,
        ey & 0xFF,
        cmd & 0xFF,
    ])


def port_path_from_location(loc):
    """pyserial reports "1-8.4" (bus-port chain). We keep the CHAIN only.

    Same reasoning as ax206.format_port_path dropping the bus number: a bus is a
    synthetic controller index, the same nature as the registry's Hub_# counter
    that broke the previous selector. The ":1.0" interface suffix that composite
    devices carry is dropped too - this chip has none, others do.
    """
    if not loc or "-" not in loc:
        return None
    return loc.split("-", 1)[1].split(":", 1)[0] or None


def caps_for(canvas=None):
    """Fixed geometry, unlike the AX206: this screen is 320x480 and nothing else.

    `canvas` is accepted and ignored on purpose - config validation compares what
    we return against the configured drawing canvas, which is how a mismatch
    becomes a startup error instead of a wrongly drawn screen.
    """
    return Caps(name=NAME, canvas=CANVAS, native=NATIVE, rotate=ROTATE,
                byte_order=LITTLE, rect_updates=True, acked=False,
                reset_on_open=False,
                brightness=Scale("percent", 0, 100, 40),
                bytes_per_sec=BYTES_PER_SEC)


def unavailable(options=None):
    if serial is None:
        return ("brak pakietu pyserial (uruchom "
                "pip install -r panel/requirements.txt)")
    return None


def discover(options=None):
    """Every screen of this kind on the bus, as neutral Targets.

    No references are held: a serial port is opened by name, so there is nothing
    to give back and Target.release() is a no-op here.
    """
    if serial is None:
        return []
    out = []
    for info in list_ports.comports():
        if (info.vid, info.pid) != (VID, PID):
            continue
        if (info.serial_number or "") != SERIAL_HINT:
            continue
        out.append(Target(backend=NAME, index=len(out),
                          port_path=port_path_from_location(info.location),
                          handle=info.device, ident=info.device))
    return out


def open_panel(selector, options=None):
    """Open the screen a selector points at and return the driver."""
    if serial is None:
        raise DriverError(unavailable())
    selector = dict(selector or {})
    com = selector.pop("com", None)
    targets = discover(options)
    if com:
        # A COM name is accepted but resolved THROUGH enumeration: after a
        # re-enumeration COM4 can be a different device, and opening a bare name
        # would mean writing bitmap commands into whatever answered.
        targets = [t for t in targets if t.handle == com]
        if not targets:
            raise DriverError("nie widze ekranu %s na porcie %s" % (NAME, com))
    picked = select(targets, selector, what="ekran %s" % NAME)
    release(targets, keep=picked)
    return Turing(picked.handle).open()


class Turing:
    def __init__(self, com, native=NATIVE):
        self.com = com
        self.width, self.height = native
        self.port = None
        self.torn = 0            # writes we could not finish; each one is serious

    @property
    def caps(self):
        return caps_for()

    # -- lifecycle ---------------------------------------------------------

    def open(self):
        try:
            self.port = serial.Serial(self.com, BAUD, timeout=1, rtscts=True,
                                      write_timeout=self._timeout_for(CHUNK))
        except serial.SerialException as e:
            if isinstance(e.__cause__, PermissionError) or "Access is denied" in str(e):
                # The same words the AX206 path uses for a module held by another
                # program, because the installer greps for exactly this phrase and
                # the advice ("stop that program, we retry by ourselves") is the
                # same. A COM holder is worse than a USB one: it is invisible in
                # Device Manager.
                raise DriverError("%s: panel held by another process (%s)"
                                  % (self.com, e)) from e
            raise DriverError("%s: %s" % (self.com, e)) from e
        except OSError as e:
            raise DriverError("%s: %s" % (self.com, e)) from e
        self._send(command(SCREEN_ON))
        return self

    def close(self):
        """Closes the port and leaves the last frame on the glass, deliberately."""
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
            self.port = None

    def reset(self):
        """Reopen the port.

        Honest about what this is: it clears OUR state, not the firmware's. If the
        display's pixel counter is mid-bitmap, nothing reachable from the host
        fixes it - see the module docstring. The caller repaints in full
        afterwards, which cures staleness but not a byte-offset desync.
        """
        com = self.com
        self.close()
        time.sleep(0.2)
        self.com = com
        return self.open()

    # -- writing -----------------------------------------------------------

    def set_brightness(self, level):
        """0..100 percent. The wire value is inverted: 0 is brightest."""
        pct = max(0, min(100, int(level)))
        self._send(command(SET_BRIGHTNESS, x=int(255 - (pct / 100) * 255)))

    def write(self, rgb565, rect):
        """Driver contract (base.py). Returns None: nothing is ever acknowledged."""
        expect = check_rect(rect, self.width, self.height, len(rgb565))
        x0, y0, x1, y1 = rect
        self._send(command(DISPLAY_BITMAP, x=x0, y=y0, ex=x1 - 1, ey=y1 - 1),
                   payload=rgb565, expect=expect)
        return None

    def write_image(self, img, at=(0, 0)):
        """Diagnostics only - the client packs frames once and cuts rects."""
        x0, y0 = at
        return self.write(pack_rgb565(img, LITTLE),
                          (x0, y0, x0 + img.width, y0 + img.height))

    # -- transport ---------------------------------------------------------

    def _timeout_for(self, nbytes):
        """Three times the measured transfer time, plus half a second of slack.

        The point is not accuracy, it is having ANY bound: without one a stalled
        write hangs the whole client with nothing in the log and a scheduled task
        that never restarts, because the process is still alive.
        """
        return nbytes / float(BYTES_PER_SEC) * 3 + 0.5

    def _send(self, cmd, payload=b"", expect=0):
        if self.port is None:
            raise DriverError("port nie jest otwarty")
        self._write_all(cmd, tail=expect)
        if payload:
            self._write_all(payload)

    def _write_all(self, data, tail=0):
        """Write every byte, or leave the firmware's byte counter where we can
        describe it.

        `tail` is how many payload bytes the firmware will expect after this
        write: a command that announces a bitmap must be padded together with its
        payload if the write tears, or the display keeps waiting for pixels and
        eats the next command instead.
        """
        view = memoryview(data)
        total = len(view)
        sent = 0
        self.port.write_timeout = self._timeout_for(min(total, CHUNK))
        while sent < total:
            chunk = view[sent:sent + CHUNK]
            try:
                written = self.port.write(chunk)
            except serial.SerialTimeoutException as e:
                sent += self._transferred()
                self._pad(total - sent + tail)
                raise DriverError("zapis przeterminowany po %d z %d B (%s)"
                                  % (sent, total, e)) from e
            except serial.SerialException as e:
                self._pad(total - sent + tail)
                raise DriverError("zapis nieudany po %d z %d B (%s)"
                                  % (sent, total, e)) from e
            if written is None:
                written = len(chunk)
            if written != len(chunk):
                # pyserial returns a short count WITHOUT raising when the IO was
                # cancelled (a close racing this write, for instance). Checking
                # only for exceptions would tear the payload silently.
                sent += written
                self._pad(total - sent + tail)
                raise DriverError("zapis skrocony: %d z %d B" % (sent, total))
            sent += written
        try:
            self.port.flush()
        except Exception as e:
            raise DriverError("flush nieudany: %s" % e) from e

    def _transferred(self):
        """How many bytes the last timed-out write actually delivered.

        pyserial reads the count from the OVERLAPPED structure and then throws it
        away when it raises, but the structure itself is still there. Private API,
        and acceptable for the same reason ax206.py reads libusb._platform:
        the package version is pinned. Returns 0 when it cannot be read - padding
        too little is as bad as padding too much, so an unknown count is treated
        as "nothing got through" and the caller repaints in full either way.
        """
        try:
            return int(self.port._overlapped_write.InternalHigh)
        except Exception:
            return 0

    def _pad(self, count):
        """Best effort: finish the bitmap the firmware is still counting.

        Zeros are as good as anything - the frame is lost regardless, and the
        caller repaints. What matters is that the NEXT command is read as a
        command and not as pixels.
        """
        if count <= 0 or self.port is None:
            return
        self.torn += 1
        try:
            self.port.write_timeout = 2.0
            self.port.write(b"\x00" * count)
            self.port.flush()
        except Exception:
            # Padding failed too: the stream is desynchronised and no host-side
            # command can fix it. The caller drops the connection; a human has to
            # replug the screen.
            pass
