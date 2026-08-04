"""Which physical device each configured panel talks to.

Two screens on this desk, two buses, one rule: the client never reaches for "the
first free one". Taking over a display at the moment another program restarts
would be a silent failure that nobody could trace back to us, so an unclear
selector ends in waiting, never in a substitution.

Identity is the USB PORT CHAIN ("3.4"), for every driver:

  1. It contains no enumeration counter. An earlier version keyed off the
     registry's `LocationInformation` ("Port_#0004.Hub_#0005"), where `Hub_#NNNN`
     is an instance index handed out at discovery time. That index jumped without
     anyone touching a plug and the client stopped finding its module.
  2. The bus number is NOT part of the key - it is a synthetic controller index,
     the same nature as `Hub_#`. It is printed by `--list` as diagnostics. Two
     devices could therefore produce the same chain if they sat on identically
     numbered ports of two different controllers; that case ends in an error,
     never in a coin flip.
  3. Replugging changes the key. Unavoidable when identity is topology, which is
     why `--list` prints a ready-to-paste line.

Serial numbers are not identity: both screens report a firmware constant (`WCH32`
for the AX206, `USB35INCHIPSV2` for the serial one), identical across units, so
Windows derives identical instance IDs and container IDs from them.
"""
from .drivers import REGISTRY, get, known
from .drivers.base import DeviceNotFound, Target, release, select

# Re-exported so callers keep saying device.<name>. The classes live in
# drivers/base.py because they are driver-family concepts: a serial screen that is
# missing from the bus must raise the same thing as a missing libusb module.
__all__ = ["DeviceNotFound", "Target", "release", "select", "options_for",
           "list_targets", "unavailable", "open_panel"]


def options_for(cfg):
    """Everything a driver may need from the configuration, in one dict.

    Drivers take a plain dict rather than the Config object so they stay testable
    without one, and so a new driver cannot quietly grow a dependency on client
    configuration it has no business reading.
    """
    return {"dll": cfg.libusb_dll, "canvas": (cfg.width, cfg.height)}


def unavailable(options=None):
    """{driver name: reason} for every driver this machine cannot use."""
    out = {}
    for name, mod in REGISTRY.items():
        why = mod.unavailable(options)
        if why:
            out[name] = why
    return out


def list_targets(backend=None, options=None):
    """Devices from one driver, or from every driver that works here.

    A driver that cannot run (missing library) contributes nothing and no error:
    `--list` still has to show the screens that do work.
    """
    names = [backend] if backend else known()
    out = []
    for name in names:
        mod = get(name)
        if mod.unavailable(options):
            continue
        out.extend(mod.discover(options))
    return out


def open_panel(spec, options=None):
    """Open the device one panel entry points at. Returns the driver."""
    return get(spec.backend).open_panel(spec.selector, options)
