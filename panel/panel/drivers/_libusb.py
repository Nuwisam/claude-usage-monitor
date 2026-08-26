"""libusb-1.0, the parts two drivers need identically.

Extracted from ax206.py when the TURZX became the second screen on this library. What
moved is what is genuinely common: finding and loading the DLL, the process-wide context,
the error formatter, the device descriptor and the port-chain key. What deliberately did
NOT move is everything that carries a device's own identity — `Found`, `find_all` and the
vendor/product filter stay in the driver that owns them, because a `repr` naming one panel
and a filter over one PID are not shared code, and `ax206.py` has no off-hardware test to
catch a mistake in them.

The reason the project is on libusb-1.0 at all is `libusb_get_port_numbers()`: it reports
the port chain straight from the handle being opened, while the legacy 0.1 API reported no
topology (`bus-0`, `devnum=0`) and the module had to be matched to the handle by ordering
in the Windows registry — that is, guessed. That motivation is the same for every driver
here, which is why this file exists rather than a second copy of it.

The DRIVER a device is bound to is a separate question from the LIBRARY talking to it: the
AX206 stays on libusb-win32 (`libusb0.sys`), the TURZX is on WinUSB, and the libusb-1.0
Windows backend handles both without rebinding anything.
"""
import ctypes as C

from .base import DriverError


class LibusbError(DriverError):
    """The library could not be loaded or initialised.

    A `DriverError`, so `link.py` treats it as "display problem, back off" like any
    other — the distinct name only keeps the message honest about which layer failed.
    """


# Depth of the USB tree: libusb documents a maximum of 7 hubs between the host and
# the device, so 8 bytes are enough for any real chain.
MAX_PORT_DEPTH = 8


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
        # No sentence about which kernel driver the device is bound to: that differs
        # per screen (libusb-win32 for the AX206, WinUSB for the TURZX) and naming one
        # of them here would be wrong advice for the other.
        raise LibusbError(
            "cannot load libusb-1.0.dll. Tried: %s. The library comes from the "
            "`libusb` package in requirements.txt." % "; ".join(tried))

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
            raise LibusbError("libusb_init: %s" % strerror(dll, rc))
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


def port_chain(dll, dev):
    """The port chain of one device, as a tuple. Empty when libusb cannot report it."""
    buf = (C.c_ubyte * MAX_PORT_DEPTH)()
    n = dll.libusb_get_port_numbers(dev, buf, MAX_PORT_DEPTH)
    return tuple(buf[j] for j in range(n)) if n > 0 else ()
