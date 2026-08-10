"""What every display driver promises, and the errors it is allowed to raise.

This module imports nothing but the standard library on purpose. `libusb` and
`pyserial` are per-driver dependencies, and a machine missing one of them must
still be able to import the registry, list the drivers it does have, and say why
the other one is unavailable.

The capability flags exist because the two screens disagree about things the
layers above them cannot guess:

  * the AX206 acknowledges every frame (CSW) and draws ONLY full frames,
  * the serial rev A screen acknowledges nothing at all and updates arbitrary
    rectangles.

A shared code path that assumed either answer would be silently wrong on the
other screen - and "silently" is the operative word, because a display gives no
feedback that a human is not looking at.
"""


class DriverError(RuntimeError):
    """Anything that went wrong between us and a display.

    Every driver wraps its transport's exceptions in this: pyserial raises
    OSError subclasses, libusb comes through raw ctypes. Callers that want "a
    display problem, back off and retry" catch this; callers that want "a bug in
    us" (ctypes.ArgumentError, ValueError) deliberately do not.
    """


class DeviceNotFound(DriverError):
    """The device we were pointed at is not there.

    The client waits for it. It never picks a different one: taking over the
    screen another process owns is a failure nobody can trace back to us.
    """


class Scale:
    """One driver's brightness scale.

    There is no conversion between scales and there must not be one: 5 means the
    middle of the range on an AX206 and a nearly dark screen on a percent-scale
    display. Config validation uses this to reject a value that would be legal on
    the other screen.
    """

    __slots__ = ("kind", "lo", "hi", "default")

    def __init__(self, kind, lo, hi, default):
        self.kind = kind                  # "steps" | "percent"
        self.lo = lo
        self.hi = hi
        self.default = default

    def clamp(self, value):
        return max(self.lo, min(self.hi, int(value)))

    def describe(self):
        return "%d..%d%s" % (self.lo, self.hi, " %" if self.kind == "percent" else "")

    def __repr__(self):
        return "<Scale %s %s>" % (self.kind, self.describe())


class Caps:
    """What a driver promises. Read by link.py and config validation, never by
    the renderer - the renderer draws one logical canvas and knows nothing about
    displays."""

    __slots__ = ("name", "canvas", "native", "rotate", "byte_order",
                 "rect_updates", "acked", "reset_on_open", "brightness",
                 "bytes_per_sec")

    def __init__(self, name, canvas, native, rotate, byte_order, rect_updates,
                 acked, brightness, bytes_per_sec, reset_on_open=False):
        self.name = name
        # Logical canvas the driver wants to be handed, in renderer coordinates.
        # Cross-checked against the configured width/height so a screen with a
        # different aspect ratio fails at startup instead of being drawn wrong.
        self.canvas = canvas
        # Native framebuffer geometry, in device coordinates.
        self.native = native
        # Degrees the logical canvas is rotated by to reach `native`. Host-side.
        self.rotate = rotate
        self.byte_order = byte_order        # pixels.BIG | pixels.LITTLE
        self.rect_updates = rect_updates    # can it draw a sub-rectangle?
        self.acked = acked                  # does a write confirm anything?
        # Does opening a handle leave the device in a state we can draw on, or
        # does it need a hard reset first? Measured on the AX206: after taking the
        # module over from another process it acknowledged every frame and drew
        # nothing until reset.
        self.reset_on_open = reset_on_open
        self.brightness = brightness        # Scale
        self.bytes_per_sec = bytes_per_sec  # measured, used for write timeouts

    def rotated(self, extra):
        """These capabilities as seen by a panel MOUNTED `extra` degrees round.

        `rotate` here means two different things composed into one number: how the
        logical canvas maps onto this model's framebuffer (the driver's business)
        and how the glass hangs on the wall (the owner's). Composing them here
        rather than in every reader keeps `caps.rotate` the single value that
        surface.py, link.py and the CLI already trust.

        Only half turns. A quarter turn would swap `native` and demand a portrait
        canvas the renderer does not draw; config validation says so with a
        readable message long before this. The raise is the guard for the paths
        that never validate - run.pyw's error card builds a Config by hand.
        """
        if not extra:
            return self
        if extra != 180:
            raise DriverError(
                "rotation %r is not supported: 0 or 180 only (a quarter turn would "
                "need a portrait layout, and there is none)" % (extra,))
        return Caps(name=self.name, canvas=self.canvas, native=self.native,
                    rotate=(self.rotate + extra) % 360,
                    byte_order=self.byte_order, rect_updates=self.rect_updates,
                    acked=self.acked, brightness=self.brightness,
                    bytes_per_sec=self.bytes_per_sec,
                    reset_on_open=self.reset_on_open)

    def __repr__(self):
        return "<Caps %s %dx%d %s%s%s>" % (
            self.name, self.native[0], self.native[1], self.byte_order,
            " rects" if self.rect_updates else " full-frame-only",
            " acked" if self.acked else " unacked")


class Target:
    """One device, as seen by one driver's enumeration.

    `index` counts WITHIN a backend, so {"index": 0} keeps meaning what it always
    meant. `handle` is whatever the driver needs to open it (a libusb device
    reference, a port name) and is opaque to everyone else - including its
    lifetime, which is why `release()` goes through here.

    `port_path` is the USB port chain ("3.4"), the only identity that survives a
    device reset and does not contain an enumeration counter. It is deliberately
    the same key for both buses: libusb reports it directly, and pyserial's
    location ("1-8.4") carries it after the bus number.
    """

    __slots__ = ("backend", "index", "port_path", "bus", "address", "ident", "handle")

    def __init__(self, backend, index, port_path=None, handle=None, bus=None,
                 address=None, ident=None):
        self.backend = backend
        self.index = index
        self.port_path = port_path
        self.handle = handle
        self.bus = bus
        self.address = address
        self.ident = ident              # diagnostics only, never a selector

    @property
    def id(self):
        return "%s#%d" % (self.backend, self.index)

    def describe(self):
        bits = ["%-12s port_path=%s" % (self.id, self.port_path or "?")]
        if self.bus is not None:
            bits.append("bus=%s" % self.bus)
        if self.address is not None:
            bits.append("address=%s" % self.address)
        if self.ident:
            bits.append(self.ident)
        return "  ".join(bits)

    def release(self):
        handle = self.handle
        if handle is not None and hasattr(handle, "release"):
            handle.release()

    def __repr__(self):
        return "<Target %s>" % self.describe()


def release(targets, keep=None):
    """Hand back every enumeration reference except `keep`.

    Whoever enumerates owns the references. A driver that is busy for hours is
    retried every few seconds, so a leak on the failure path is a leak that grows
    all day - which is why this runs from a `finally`, not from the happy path.
    """
    for t in targets:
        if t is not keep:
            t.release()


def select(targets, selector, what="module"):
    """Pick the device a selector points at. Order: port_path -> index -> the
    only one there is.

    The client NEVER takes "the first free one". Every uncertain case ends in
    DeviceNotFound, because quietly taking over the screen another program owns is
    a failure that cannot be traced back from the outside.
    """
    if not targets:
        raise DeviceNotFound("no device found (%s)" % what)
    selector = selector or {}

    port_path = selector.get("port_path")
    if port_path:
        hits = [t for t in targets if t.port_path == port_path]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise DeviceNotFound(
                "there is no device with port_path=%s; found: %s"
                % (port_path, ", ".join(t.describe() for t in targets)))
        # Only possible with two USB controllers using the same port number. One
        # of these could belong to another program, so neither is picked.
        raise DeviceNotFound(
            "port_path=%s points at %d devices (different buses: %s) — "
            "settle it with index"
            % (port_path, len(hits), ", ".join(str(t.bus) for t in hits)))

    index = selector.get("index")
    if index is not None:
        hits = [t for t in targets if t.index == index]
        if not hits:
            # %r, not %d: `index` comes from panel.json and is sometimes a string.
            raise DeviceNotFound("there is no device at index %r (found %d)"
                                 % (index, len(targets)))
        return hits[0]

    if len(targets) == 1:
        return targets[0]
    raise DeviceNotFound(
        "found %d devices and none of them is named in the configuration. Run "
        "`python -m panel --list`, then `--identify`, and write the chosen port "
        "chain into panel.json as "
        "{\"panels\": [{\"backend\": \"...\", \"port_path\": \"...\"}]}"
        % len(targets))


def check_rect(rect, width, height, payload_len):
    """Shared guard for driver.write(). Raises DriverError, never returns False.

    An empty rectangle is an ERROR, not a no-op: (x0, x0) would pass a length
    check with len == 0 and put x0-1 on the wire as the inclusive end coordinate.
    Hence the strict `<`, which is also why this guard runs before the length
    check rather than after it.
    """
    x0, y0, x1, y1 = rect
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise DriverError("rect %r is empty or outside %dx%d" % (rect, width, height))
    expect = (x1 - x0) * (y1 - y0) * 2
    if payload_len != expect:
        raise DriverError("payload is %d B, rect %r needs %d B"
                          % (payload_len, rect, expect))
    return expect
