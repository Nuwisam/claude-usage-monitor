"""Display drivers. One file per screen, so `ls` is the list of what we support.

    ax206.py          AX206 over libusb (SCSI 0xCD in a USB Bulk-Only CBW/CSW pair)
    turing_rev_a.py   Turing Smart Screen rev A over a CH340 serial port
    turzx_usb.py      TURZX 5.2" over libusb (DES-sealed 512 B command + PNG, WinUSB)
    _libusb.py        NOT a driver: the libusb-1.0 plumbing ax206 and turzx_usb share

The registry below is an explicit dict on purpose: no directory scanning and no
import-by-guessed-name. A driver that is not listed here does not exist as far as
the client is concerned, and adding one is a visible edit in a reviewable place.

Every driver module provides the same six names. The count used to say six and list
five - `caps_for` was missing, and it is not optional: `config.validate()` calls it to
check a panel's `brightness` against that driver's own scale, so a module without it
fails configuration validation for every entry that sets one.

    NAME            the string used in panel.json
    SELECTOR_KEYS   which keys may point at one of its devices
    caps_for(canvas)        -> base.Caps; must work with no hardware and no argument
    unavailable(options)    None, or why this machine cannot use it (never raises)
    discover(options)       list of base.Target
    open_panel(selector, options)   -> an opened driver instance

`options` is the dict from `device.options_for()` and carries `dll` and `canvas`; a
driver that needs neither still accepts it, because the caller always passes it.

The instance an `open_panel()` returns exposes `.caps`, `.open()` (returning self),
`.write(payload, rect)`, `.set_brightness(level)`, `.reset()` and `.close()`.

`write()`'s RETURN VALUE is part of the contract, not an afterthought: `link.py` reads
it as the acknowledgement and counts `None` as "not acknowledged", resetting the panel
after three in a row. A driver that reports `acked=True` must therefore return
something truthy on success and `None` only when the device really did not answer; a
driver with `acked=False` returns `None` always and the ladder is disabled for it.
"""
from . import ax206, turing_rev_a, turzx_usb

REGISTRY = {
    ax206.NAME: ax206,
    turing_rev_a.NAME: turing_rev_a,
    turzx_usb.NAME: turzx_usb,
}


def known():
    """Driver names, sorted - for the error messages that have to list them."""
    return sorted(REGISTRY)


def get(name):
    """The driver module, or a DriverError naming the ones that exist."""
    try:
        return REGISTRY[name]
    except KeyError:
        from .base import DriverError
        raise DriverError("unknown driver %r (known: %s)"
                          % (name, ", ".join(known())))
