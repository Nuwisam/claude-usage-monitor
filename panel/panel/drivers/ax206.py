"""AX206 panel driver (VID 1908 / PID 0102) — Windows + libusb-1.0.

Two things that are easy to confuse: the device DRIVER is still libusb-win32
(`libusb0.sys`, the same one the off-the-shelf tools for these displays use), while
the LIBRARY used to talk to it is libusb-1.0. The libusb-1.0 Windows backend
handles devices bound to libusb0.sys — measured, see below. Switching the driver
over to WinUSB is unnecessary and would break those tools.

There is one reason for the change of library: `libusb_get_port_numbers()` reports
the PORT CHAIN from the very handle being opened. The legacy 0.1 API from
libusb-win32 reported no topology at all (`bus-0`, `devnum=0`), so the module had
to be looked up in the Windows registry and matched to the handle by ordering —
that is, guessed. Details in the header of device.py.

Protocol after dpf-ax (hackfin/dreamlayers): the vendor SCSI command 0xCD wrapped
in a CBW/CSW pair of the USB Bulk-Only Mass Storage transport, EP 0x01 OUT /
0x81 IN.

Deliberately ONLY set-property and blit are here. The flash commands (0xCB, SPI
erase and write) are not in this file and must not be — those are the ones that
can kill a panel.

Seven properties of this display model, measured, that appear in no reference
source:

  1. The geometry query command (`0xCD .. byte5=2`) is UNRELIABLE: under the same
     conditions it once returns a correct 480x320 and once stays silent until the
     timeout, and a failed attempt costs one lost CSW in the NEXT command. Hence
     the geometry is a configuration parameter, and `probe_geometry()`
     serves diagnostics only and is called last, never before the real work.
  2. The panel SOMETIMES stops returning CSWs even though it accepts and draws
     every frame correctly. A missing CSW is a warning here (the `missed_csw`
     count), never an error — otherwise the client would fall over on working
     hardware.

     One case is REPEATABLE, not random: the FIRST frame after a cold open of the
     handle never gets a CSW. Measured three times each on both libraries —
     libusb-win32 and libusb-1.0 behave identically, so it is a property of the
     firmware, not of the transport layer. A retry after `clear_halt` does not
     rescue it. Later frames are acknowledged normally: 1200 in a row,
     `missed_csw == 0`. So `missed_csw == 1` right after the client starts is a
     NORMAL state and does not mean a bug in us.
  3. A device reset reliably restores acknowledgements. The handle is invalid
     after a reset, so `reset()` MUST open the device again.
  4. FULL_FRAME_ONLY — and this is the most dearly bought finding in this file.

     The rectangle in the command is NOT "the area to repaint". It sets the
     drawing WINDOW, into which the firmware pours the WHOLE stream it receives,
     WRAPPING it. The transfer must be exactly 307200 B (a full frame) — otherwise
     the transaction does not close, there is no CSW and there is no drawing.

     The consequence for a window smaller than the screen: a full frame goes into
     it several times over, so what stays on the glass is the TAIL of the payload,
     not its start.

     "DOL" is the exact payload that was drawn onto the glass on the bench, and
     what the glass then showed is the whole finding. Changing the string would
     rewrite the record of a measurement rather than the prose around it.

     Measured: a 480x60 window with a payload carrying the word
     "DOL" ("bottom") along its foot showed exactly that word; a payload padded
     with zeros gave a BLACK window. The earlier "black frames" were exactly this
     effect, not a failure to draw.

     This does not save any transfer: a 480x160 rectangle was given a
     payload exactly large enough to fill the window once and was not acknowledged
     either. The number of bytes on the wire is constant, so every update costs
     307200 B regardless of how many pixels actually change. Hence
     `blit()` accepts the full screen only. Indirect confirmation: pyax206,
     dumped from the same model, has a full-screen rectangle hardwired into it.

  5. A missing CSW is NOT a whim of the panel — it is the EFFECT of an earlier
     badly formed transaction. A repeated, identical command was acknowledged
     normally as long as it was not preceded by a blit with the wrong byte count; after
     one, the pipe went quiet until `reset()`. Given nothing but correct full
     frames, the panel acknowledges every one. The `missed_csw` count is therefore
     a signal of A BUG IN US, not of a hardware failure.

  6. Frame time DEPENDS ON CONTENT, even though the number of bytes on the wire is
     constant. Measured on this model, the same 307200 B blit, after a reset, over
     five repetitions:

         test card (dark, like the target layout)    354 ms
         full black                               356 ms
         bands of saturated colors                514 ms

     Different dark payloads give the same time, so it is not a matter of
     repeating the same frame. It looks like a cost inside the LCD controller, not
     the transport. The practical conclusion: the number that holds for this
     screen is ~355 ms (~2.8 fps) — the layout is dark. Synthetic tests with
     saturated bands measure the worst case and their result (~515 ms) does not
     describe the client's work.

     Beware the measurement pitfall: the first measurements of the migration to
     libusb-1.0 gave 515 ms and looked like a regression against the documented
     ~376 ms. There was no regression — the test payload had changed. libusb-win32
     on the same bands gave 503-533 ms (measured in parallel).

  7. Of the dpf-ax command set this firmware supports ONLY blit and
     SETPROPERTY/BRIGHTNESS. Checked after a reset, on a clean pipe: FILLRECT
     (0x11), COPYRECT (0x13) and SETPROPERTY with the FGCOLOR token (0x02) are not
     acknowledged. It is a shame: FILLRECT would draw the bars with no payload at
     all — but it is not there. That also explains why the off-the-shelf tools push
     nothing but full frames here.
"""
import ctypes as C
import time

from ..pixels import BIG, pack_rgb565, rgb565_bytes as pack_pixel
from .base import (Caps, DriverError, Scale, Target, check_rect, release,
                   select)

VID, PID = 0x1908, 0x0102
EP_OUT, EP_IN = 0x01, 0x81

DIR_OUT, DIR_IN = 0, 1

# libusb-1.0 error codes handled by name. The rest go through libusb_strerror and
# end up inside the message.
LIBUSB_ERROR_NOT_FOUND = -5
LIBUSB_ERROR_TIMEOUT = -7
LIBUSB_ERROR_PIPE = -9

# Depth of the USB tree: libusb documents a maximum of 7 hubs between the host and
# the device, so 8 bytes are enough for any real chain.
MAX_PORT_DEPTH = 8

USBCMD_SETPROPERTY = 0x01
USBCMD_BLIT = 0x12

PROPERTY_BRIGHTNESS = 0x01
PROPERTY_ORIENTATION = 0x10

DEFAULT_WIDTH, DEFAULT_HEIGHT = 480, 320


class AX206Error(DriverError):
    """Kept as its own name because the messages are protocol-specific, but it is
    a DriverError: link.py catches the whole family, not this one."""


NAME = "ax206"

# Measured on this model with the (dark) layout: 307200 B in ~355 ms. Used for
# write timeouts, so it is deliberately the throughput actually observed rather
# than any nominal bus figure.
BYTES_PER_SEC = 870_000


# --- libusb-1.0 structures and bindings -------------------------------------


class DevDesc(C.Structure):
    """`libusb_device_descriptor`. The layout is the same as in the 0.1 API — it is
    simply the descriptor from the USB standard, byte for byte."""

    _fields_ = [
        ("bLength", C.c_ubyte), ("bDescriptorType", C.c_ubyte),
        ("bcdUSB", C.c_ushort), ("bDeviceClass", C.c_ubyte),
        ("bDeviceSubClass", C.c_ubyte), ("bDeviceProtocol", C.c_ubyte),
        ("bMaxPacketSize0", C.c_ubyte), ("idVendor", C.c_ushort),
        ("idProduct", C.c_ushort), ("bcdDevice", C.c_ushort),
        ("iManufacturer", C.c_ubyte), ("iProduct", C.c_ubyte),
        ("iSerialNumber", C.c_ubyte), ("bNumConfigurations", C.c_ubyte),
    ]


_dll = None
_ctx = None


def _dll_candidates(dll_path):
    """Where to take libusb-1.0.dll from, in the order tried.

    The `libusb` package from PyPI carries a win-amd64 binary and is the default
    route — thanks to that, installing the panel is one `pip install -r`, with
    no manual steps on a new machine. An explicit path beats everything, because
    the scheduled task starts with a different PATH than the shell.
    """
    if dll_path:
        yield dll_path
    try:
        from libusb._platform import DLL_PATH
    except Exception:
        pass
    else:
        yield DLL_PATH
    yield "libusb-1.0.dll"


def load(dll_path=None):
    """libusb-1.0.dll, once per process."""
    global _dll
    if _dll is not None:
        return _dll
    tried = []
    dll = None
    for candidate in _dll_candidates(dll_path):
        try:
            dll = C.CDLL(candidate)
            break
        except OSError as e:
            tried.append("%s (%s)" % (candidate, e))
    if dll is None:
        raise AX206Error(
            "cannot load libusb-1.0.dll. Tried: %s. The library comes from the "
            "`libusb` package in requirements.txt; the device driver stays "
            "libusb-win32." % "; ".join(tried))

    dll.libusb_init.argtypes = [C.POINTER(C.c_void_p)]
    dll.libusb_exit.argtypes = [C.c_void_p]
    dll.libusb_get_device_list.argtypes = [C.c_void_p,
                                           C.POINTER(C.POINTER(C.c_void_p))]
    dll.libusb_get_device_list.restype = C.c_ssize_t
    dll.libusb_free_device_list.argtypes = [C.POINTER(C.c_void_p), C.c_int]
    dll.libusb_get_device_descriptor.argtypes = [C.c_void_p, C.POINTER(DevDesc)]
    dll.libusb_get_bus_number.argtypes = [C.c_void_p]
    dll.libusb_get_bus_number.restype = C.c_ubyte
    dll.libusb_get_device_address.argtypes = [C.c_void_p]
    dll.libusb_get_device_address.restype = C.c_ubyte
    dll.libusb_get_port_numbers.argtypes = [C.c_void_p, C.POINTER(C.c_ubyte), C.c_int]
    dll.libusb_ref_device.argtypes = [C.c_void_p]
    dll.libusb_ref_device.restype = C.c_void_p
    dll.libusb_unref_device.argtypes = [C.c_void_p]
    dll.libusb_open.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
    dll.libusb_close.argtypes = [C.c_void_p]
    dll.libusb_claim_interface.argtypes = [C.c_void_p, C.c_int]
    dll.libusb_release_interface.argtypes = [C.c_void_p, C.c_int]
    dll.libusb_clear_halt.argtypes = [C.c_void_p, C.c_ubyte]
    dll.libusb_reset_device.argtypes = [C.c_void_p]
    dll.libusb_bulk_transfer.argtypes = [C.c_void_p, C.c_ubyte, C.c_char_p, C.c_int,
                                         C.POINTER(C.c_int), C.c_uint]
    dll.libusb_get_string_descriptor_ascii.argtypes = [C.c_void_p, C.c_ubyte,
                                                       C.c_char_p, C.c_int]
    dll.libusb_strerror.argtypes = [C.c_int]
    dll.libusb_strerror.restype = C.c_char_p
    _dll = dll
    return dll


def context(dll_path=None):
    """The libusb context, once per process. `libusb_exit` is deliberately not
    called: the context lives as long as the process, and closing it midway would
    invalidate the device pointers held by open handles."""
    global _ctx
    dll = load(dll_path)
    if _ctx is None:
        ctx = C.c_void_p()
        rc = dll.libusb_init(C.byref(ctx))
        if rc != 0:
            raise AX206Error("libusb_init: %s" % strerror(dll, rc))
        _ctx = ctx
    return _ctx


def strerror(dll, code):
    return "%d (%s)" % (code, dll.libusb_strerror(code).decode(errors="replace"))


def format_port_path(ports):
    """The port chain in the form that goes into panel.json: "3.4".

    The bus number deliberately does NOT go into the key. It is a synthetic
    controller index assigned at enumeration — the same nature as the registry's
    `Hub_#`, which broke the previous version of the selector. The port chain
    describes physical sockets and does not have that problem.
    """
    return ".".join(str(p) for p in ports)


class Found:
    """A module that was found: it HOLDS a reference to the libusb device.

    The reference belongs to this object and has to be handed back through
    `release()` — otherwise every enumeration (and there are as many of those as
    there are panel opens) leaves behind a device libusb will not free. The 0.1
    API had no such problem, because the device list lived in the library; here
    it lives in the client.
    """

    __slots__ = ("ptr", "bus", "ports", "address", "iserial", "index", "_dll")

    def __init__(self, dll, ptr, bus, ports, address, iserial, index):
        self._dll = dll
        self.ptr = ptr
        self.bus = bus
        self.ports = ports
        self.address = address
        self.iserial = iserial
        self.index = index

    @property
    def port_path(self):
        return format_port_path(self.ports)

    def release(self):
        """Hands the reference back. Idempotent — called both after a successful
        open and when a module is rejected, so a repeat must not hurt."""
        if self.ptr is not None:
            self._dll.libusb_unref_device(self.ptr)
            self.ptr = None

    def __repr__(self):
        return "<AX206 #%d bus=%s ports=%s addr=%s>" % (
            self.index, self.bus, self.port_path, self.address)


def find_all(dll_path=None):
    """Every matching module, in libusb enumeration order.

    Refreshes the list on every call — after a reset the old pointers are invalid
    and the search has to start over. EVERY `Found` returned carries a reference;
    whoever does not use one is obliged to call `release()` (`release_all` does).
    """
    dll = load(dll_path)
    ctx = context(dll_path)
    lst = C.POINTER(C.c_void_p)()
    count = dll.libusb_get_device_list(ctx, C.byref(lst))
    if count < 0:
        raise AX206Error("libusb_get_device_list: %s" % strerror(dll, count))
    out = []
    try:
        for i in range(count):
            dev = lst[i]
            desc = DevDesc()
            if dll.libusb_get_device_descriptor(dev, C.byref(desc)) != 0:
                continue
            if (desc.idVendor, desc.idProduct) != (VID, PID):
                continue
            buf = (C.c_ubyte * MAX_PORT_DEPTH)()
            n = dll.libusb_get_port_numbers(dev, buf, MAX_PORT_DEPTH)
            ports = tuple(buf[j] for j in range(n)) if n > 0 else ()
            out.append(Found(dll, dll.libusb_ref_device(dev),
                             dll.libusb_get_bus_number(dev), ports,
                             dll.libusb_get_device_address(dev),
                             desc.iSerialNumber, len(out)))
    finally:
        # The list goes away, but the devices kept live on their own references.
        dll.libusb_free_device_list(lst, 1)
    return out


def release_all(found, keep=None):
    """Hands back the references of every module except `keep`."""
    for f in found:
        if f is not keep:
            f.release()


# --- driver module API (see drivers/base.py) --------------------------------

# What may appear in a panel entry as a selector for THIS driver. Anything else
# is a config error: an unknown key silently matched nothing and fell through to
# "the only device there is", which is how a stale `location` selector used to
# quietly take over whichever module happened to be plugged in.
SELECTOR_KEYS = ("port_path", "index")


def caps_for(canvas=None):
    """What this driver promises, without opening anything.

    Config validation needs it before any device exists - and this module happens
    to take the canvas it is given, which is exactly why the check has to ask the
    driver rather than assume: another screen has a fixed native size and must be
    able to say so.
    """
    size = tuple(canvas or (DEFAULT_WIDTH, DEFAULT_HEIGHT))
    return Caps(name=NAME, canvas=size, native=size, rotate=0,
                byte_order=BIG, rect_updates=False, acked=True,
                reset_on_open=True,
                brightness=Scale("steps", 0, 7, 5),
                bytes_per_sec=BYTES_PER_SEC)


def unavailable(options=None):
    """None when this driver can run on this machine, otherwise the reason.

    Never raises: `--list` has to be able to say why a driver is missing while
    still listing the ones that work.
    """
    try:
        load((options or {}).get("dll"))
    except (DriverError, OSError) as e:
        return "libusb-1.0 unavailable (%s)" % e
    return None


def discover(options=None):
    """Every AX206 on the bus, as neutral Targets.

    Each carries a live libusb reference; whoever does not open it must call
    `release()`.
    """
    options = options or {}
    return [Target(backend=NAME, index=f.index, port_path=f.port_path, handle=f,
                   bus=f.bus, address=f.address)
            for f in find_all(options.get("dll"))]


def open_panel(selector, options=None):
    """Open the module a selector points at and return the driver.

    The search runs again on EVERY open, deliberately: after a reset the old
    libusb pointers are invalid and the module gets a new bus address.
    """
    options = options or {}
    width, height = options.get("canvas", (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def finder():
        targets = discover(options)
        picked = None
        try:
            picked = select(targets, selector,
                            what="module %04x:%04x" % (VID, PID))
        finally:
            release(targets, keep=picked)
        return picked.handle

    return AX206(finder=finder, width=width, height=height,
                 dll_path=options.get("dll")).open()


def first_finder(dll_path=None):
    """The default selector: the first matching module.

    For a single panel this is entirely enough. With two modules do NOT use this
    in the client — see device.py; the choice has to be explicit, or the client
    can take over a panel belonging to another program.
    """
    def finder():
        found = find_all(dll_path)
        picked = found[0] if found else None
        release_all(found, keep=picked)
        return picked
    return finder


# --- pixel packing ----------------------------------------------------------

# The packing itself lives in panel/pixels.py, shared with every other driver.
# These two names stay here because "RGB565 high byte first" is what the AX206
# protocol calls for; callers that mean "this display's byte order" should keep
# saying it through the driver.


def image_to_rgb565(img):
    """PIL image -> RGB565 for this display (high byte first)."""
    return pack_rgb565(img, BIG)


def rgb565_bytes(r, g, b):
    """One pixel for this display (high byte first)."""
    return pack_pixel(r, g, b, BIG)


# --- panel ------------------------------------------------------------------


class AX206:
    def __init__(self, finder=None, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                 dll_path=None):
        self.dll = load(dll_path)
        self.finder = finder or first_finder(dll_path)
        self.h = None
        self.width = width
        self.height = height
        self.missed_csw = 0
        self.serial = None
        self.port_path = None

    # -- lifecycle ---------------------------------------------------------

    def open(self):
        found = self.finder()
        if found is None:
            raise AX206Error("the named module %04x:%04x was not found" % (VID, PID))
        # The `found` reference belongs to this function and must go back to libusb
        # on EVERY exit from it. A successful `libusb_open` takes a reference of
        # its own, so the handle lives on without this one — and on an error there
        # is all the less to hold.
        try:
            handle = C.c_void_p()
            rc = self.dll.libusb_open(found.ptr, C.byref(handle))
            if rc != 0:
                raise AX206Error("libusb_open: %s" % self._err(rc))
            self.h = handle
            # No libusb_set_configuration(): it zeroes the toggle bits and costs
            # the first CSW. dpf-ax likewise only claims the interface.
            rc = self.dll.libusb_claim_interface(self.h, 0)
            if rc != 0:
                err = self._err(rc)
                self.dll.libusb_close(self.h)
                self.h = None
                raise AX206Error("libusb_claim_interface: %s (panel held by "
                                 "another process?)" % err)
            self.port_path = found.port_path
            self.serial = self._string(found.iserial)
        finally:
            found.release()
        self.resync()
        return self

    def reset(self, settle=1.5):
        """libusb_reset_device + a reopen. The remedy for a panel that has stopped
        returning CSWs. The handle can be invalid after a reset — hence the fresh
        search every time.

        `LIBUSB_ERROR_NOT_FOUND` is not a failure here: it means the device was
        re-enumerated and has to be opened anew. That is exactly what happens below.
        """
        if self.h:
            rc = self.dll.libusb_reset_device(self.h)
            if rc not in (0, LIBUSB_ERROR_NOT_FOUND):
                # No abort here: a reopen is the only way out regardless, and
                # the message is useful in the log.
                pass
            try:
                self.dll.libusb_close(self.h)
            except OSError:
                pass
            self.h = None
        time.sleep(settle)
        deadline = time.monotonic() + 10.0
        last = None
        while time.monotonic() < deadline:
            try:
                return self.open()
            except AX206Error as e:
                last = e
                time.sleep(0.5)
        raise AX206Error("the module did not come back after the reset: %s" % last)

    def resync(self):
        """Clears halts and drains the replies left behind by a previous process."""
        self.dll.libusb_clear_halt(self.h, EP_IN)
        self.dll.libusb_clear_halt(self.h, EP_OUT)
        drained = 0
        while True:
            rc, n, _ = self._bulk(EP_IN, None, 4096, 200)
            if rc != 0 or n <= 0:
                break
            drained += n
        return drained

    def close(self):
        if self.h:
            try:
                self.dll.libusb_release_interface(self.h, 0)
                self.dll.libusb_close(self.h)
            except OSError:
                pass
            self.h = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _err(self, code):
        return strerror(self.dll, code)

    def _string(self, index):
        if not index:
            return None
        buf = C.create_string_buffer(256)
        n = self.dll.libusb_get_string_descriptor_ascii(self.h, index, buf, 256)
        return buf.value.decode(errors="replace") if n > 0 else None

    def _bulk(self, endpoint, data, length, timeout):
        """One transfer. Returns (rc, byte_count, buffer).

        This is where libusb-1.0 differs from the 0.1 API most: the error code
        and the number of bytes transferred are TWO separate values, not one
        signed number. Every call has to look at both — `n == length` alone is
        not enough, and `rc == 0` alone even less so.
        """
        n = C.c_int(0)
        buf = data if data is not None else C.create_string_buffer(length)
        rc = self.dll.libusb_bulk_transfer(self.h, endpoint, buf, length,
                                           C.byref(n), timeout)
        return rc, n.value, buf

    # -- transport ---------------------------------------------------------

    def _scsi(self, cmd, direction=DIR_OUT, data=None, length=0, timeout=15000):
        """The Bulk-Only Mass Storage wrapper. `cmd` is a 16-byte vendor command.

        Mirrors emulate_scsi() from dpf-ax, including leaving bmCBWFlags at 0 for
        transfers to the host as well — the firmware ignores that bit, and setting
        it correctly ends in a stall on EP IN (checked).
        """
        if not self.h:
            raise AX206Error("the panel is not open")
        cbw = bytearray(31)
        cbw[0:4] = b"USBC"
        cbw[4:8] = bytes((0xDE, 0xAD, 0xBE, 0xEF))
        cbw[8:12] = int(length).to_bytes(4, "little")
        cbw[14] = len(cmd)
        cbw[15:15 + len(cmd)] = bytes(cmd)

        rc, n, _ = self._bulk(EP_OUT, bytes(cbw), len(cbw), 1000)
        if rc != 0 or n != len(cbw):
            raise AX206Error("CBW write failed (%d/%d): %s"
                             % (n, len(cbw), self._err(rc)))

        out = None
        if length:
            if direction == DIR_OUT:
                rc, n, _ = self._bulk(EP_OUT, bytes(data), length, timeout)
                if rc != 0 or n != length:
                    raise AX206Error("data write torn (%d/%d): %s"
                                     % (n, length, self._err(rc)))
            else:
                rc, n, buf = self._bulk(EP_IN, None, length, timeout)
                if rc != 0:
                    raise AX206Error("data read failed: %s" % self._err(rc))
                out = buf.raw[:n]

        # A full frame takes ~0.5 s, so a healthy CSW fits inside 1.5 s. The tight
        # limit matters: a handle taken over in a dirty state loses the first one
        # or two CSWs, and a long timeout turns that into a stall of several seconds.
        rc, n, csw = self._bulk(EP_IN, None, 13, 1500)
        if rc != 0:
            # The stall has to be cleared before the CSW can be read (the BOT
            # procedure). The retry follows EVERY error, not only after
            # LIBUSB_ERROR_PIPE: measured, a hung CSW comes back after clear_halt
            # also when the first attempt ended in a plain timeout.
            self.dll.libusb_clear_halt(self.h, EP_IN)
            rc, n, csw = self._bulk(EP_IN, None, 13, 500)
        if rc != 0 or n != 13 or csw.raw[:4] != b"USBS":
            # This firmware sometimes skips the CSW even though it took the data
            # and drew it. Note it, but do NOT abort the frame — see the module
            # header.
            self.missed_csw += 1
            return None, out
        return csw.raw[12], out

    # -- commands ----------------------------------------------------------

    def _excmd(self, sub):
        cmd = bytearray(16)
        cmd[0] = 0xCD
        cmd[5] = 6
        cmd[6] = sub
        return cmd

    def set_property(self, token, value):
        cmd = self._excmd(USBCMD_SETPROPERTY)
        cmd[7] = token & 0xFF
        cmd[8] = (token >> 8) & 0xFF
        cmd[9] = value & 0xFF
        cmd[10] = (value >> 8) & 0xFF
        return self._scsi(cmd, DIR_OUT)[0]

    def set_brightness(self, level):
        """0 (dimmed) .. 7 (brightest)."""
        return self.set_property(PROPERTY_BRIGHTNESS, max(0, min(7, int(level))))

    @property
    def caps(self):
        return caps_for((self.width, self.height))

    def write(self, rgb565, rect):
        """Driver contract (drivers/base.py). Here it is one line over blit(),
        so the partial-rect rejection, the byte count and the missed_csw
        bookkeeping stay exactly where they were measured."""
        check_rect(rect, self.width, self.height, len(rgb565))
        return self.blit(rgb565, rect)

    def blit(self, rgb565, rect=None):
        """Sends RGB565 pixels. rect = (x0, y0, x1, y1), x1/y1 exclusive.

        The full screen ONLY — see FULL_FRAME_ONLY in the module header. A partial
        rectangle is rejected deliberately: the firmware takes it without blinking
        and does NOT draw, and a silent no-op is the worst behavior possible here.
        """
        if rect is None:
            rect = (0, 0, self.width, self.height)
        x0, y0, x1, y1 = rect
        if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
            raise AX206Error("rectangle %r falls outside %dx%d"
                             % (rect, self.width, self.height))
        if (x0, y0, x1, y1) != (0, 0, self.width, self.height):
            raise AX206Error(
                "partial rectangle %r: this firmware draws the FULL screen ONLY "
                "(0,0,%d,%d). Measured: every partial blit is accepted, never "
                "acknowledged and never drawn."
                % (rect, self.width, self.height))
        expect = (x1 - x0) * (y1 - y0) * 2
        if len(rgb565) != expect:
            raise AX206Error("the buffer has %d B, and rectangle %r needs %d B"
                             % (len(rgb565), rect, expect))
        cmd = self._excmd(USBCMD_BLIT)
        cmd[7] = x0 & 0xFF
        cmd[8] = (x0 >> 8) & 0xFF
        cmd[9] = y0 & 0xFF
        cmd[10] = (y0 >> 8) & 0xFF
        cmd[11] = (x1 - 1) & 0xFF
        cmd[12] = ((x1 - 1) >> 8) & 0xFF
        cmd[13] = (y1 - 1) & 0xFF
        cmd[14] = ((y1 - 1) >> 8) & 0xFF
        return self._scsi(cmd, DIR_OUT, rgb565, expect)[0]

    def blit_image(self, img, at=(0, 0)):
        """Sends a PIL image at the point `at`."""
        x0, y0 = at
        return self.blit(image_to_rgb565(img),
                         (x0, y0, x0 + img.width, y0 + img.height))

    def probe_geometry(self):
        """Diagnostics: ask the module about its own geometry.

        THIS model does not support it — the data read times out, so None is
        returned. Kept because another module may answer, and then it is worth
        seeing in `--probe`. A failed attempt MUST be followed by a resync: the
        transaction is left torn in half and the next command would hit a dirty
        endpoint (that is exactly how the panel used to hang during the survey).
        """
        try:
            _, buf = self._scsi(bytes([0xCD, 0, 0, 0, 0, 2] + [0] * 10),
                                DIR_IN, length=5, timeout=2000)
        except AX206Error:
            self.resync()
            return None
        if not buf or len(buf) < 4:
            return None
        return buf[0] | (buf[1] << 8), buf[2] | (buf[3] << 8)
