"""Display drivers. One file per screen, so `ls` is the list of what we support.

    ax206.py    AX206 over libusb (SCSI 0xCD in a USB Bulk-Only CBW/CSW pair)
    turing_rev_a.py   Turing Smart Screen rev A over a CH340 serial port

The registry below is an explicit dict on purpose: no directory scanning and no
import-by-guessed-name. A driver that is not listed here does not exist as far as
the client is concerned, and adding one is a visible edit in a reviewable place.

Every driver module provides the same six names:

    NAME            the string used in panel.json
    SELECTOR_KEYS   which keys may point at one of its devices
    unavailable()   None, or why this machine cannot use it (never raises)
    discover()      list of base.Target
    open_panel()    selector -> an opened driver instance
    (its driver class also exposes .caps, .write(), .set_brightness(),
     .reset(), .close() - see base.py)
"""
from . import ax206, turing_rev_a

REGISTRY = {
    ax206.NAME: ax206,
    turing_rev_a.NAME: turing_rev_a,
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
