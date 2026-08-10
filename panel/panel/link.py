"""Panel supervision: the only place in the client that touches libusb-1.0.dll.

Things this layer is meant to settle on its own:
  * the named module is busy or unplugged        -> backoff, log ONCE per state change
  * the panel stopped acknowledging              -> reset and a full repaint
  * the image did not change                     -> nothing is sent

What it does NOT do: it does not clear the screen on close. The panel holds its
last frame with no host attached and that is intended — after the computer is
switched off, the last known limit state stays on the glass.
"""
import time

from . import device, surface as surfaces
from .drivers.base import DriverError
from .log import get as log

BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)

# How many unreceived CSWs in a row mean "the panel went quiet". Well-formed full
# frames are ALWAYS acknowledged, so a few in a row is no longer chance.
MISSED_CSW_LIMIT = 3

# Errors this layer answers with backoff rather than with the death of the process.
# Below `ensure()` and `blit()` sits raw ctypes into libusb-1.0.dll (find_all,
# libusb_open, libusb_claim_interface, bulk transfer). An unplugged module or a
# driver drifting out of step reports from there with an OSError, not an
# AX206Error — and such an exception travelled through tick() and run() all the
# way to the excepthook, ending the process. `drop()` and `close()` were proof
# against that from the start; the open path was not.
#
# ctypes.ArgumentError is deliberately NOT here: it means a bad call signature,
# that is, a bug in us. That one is to reach the excepthook and be fixed, not to
# fall into a retry loop.
DEVICE_ERRORS = (DriverError, OSError)


class PanelLink:
    def __init__(self, spec, cfg):
        self.spec = spec
        self.cfg = cfg
        self.tag = spec.tag
        self.dev = None
        # The driver's capabilities composed with how THIS panel is mounted. Set
        # on open, because a driver only knows its geometry once it has a device.
        # Everything downstream reads this, never dev.caps: with two sources of
        # truth the surface would rotate while the acknowledgement ladder consulted
        # an unrotated copy.
        self.caps = None
        # Resolved on open: a panel entry may leave it out, and the default is the
        # driver's, because the scales are not comparable across displays.
        self.brightness = spec.brightness
        # What we believe is on the glass. None means "no display open"; a fresh
        # Surface means "open, but we know nothing about its pixels yet".
        self.surface = None
        # Full repaints are timed separately from writes. Once a display can take
        # partial updates, a write happens every time the clock ticks, so timing
        # the periodic repaint off "last write" would mean it never happens - and
        # on a link that acknowledges nothing, that repaint is the only way back
        # from a silent desync.
        self.last_full_sent = 0.0
        self._attempt = 0
        self._next_try = 0.0
        self._last_error = None
        self._missed_run = 0

    # -- connection --------------------------------------------------------

    @property
    def up(self):
        return self.dev is not None

    def ensure(self):
        """Opens the module if need be. Returns True when the panel is ready."""
        if self.dev is not None:
            return True
        now = time.monotonic()
        if now < self._next_try:
            return False
        dev = None
        try:
            dev = device.open_panel(self.spec, device.options_for(self.cfg))
            # DriverError on an unsupported angle, which is why it sits inside the
            # try: validate() rejects those, but run.pyw's error card never
            # validates, and a bad angle there must mean backoff, not a traceback.
            caps = dev.caps.rotated(self.spec.rotate)
            if caps.reset_on_open:
                # Reset on EVERY open. 03.08, after taking the module over from
                # another process, the panel acknowledged every frame (status=0)
                # and drew nothing; the same code after reset() drew. The resync()
                # inside open() was not enough - the pipe was clear, the firmware
                # was dirty. MISSED_CSW_LIMIT cannot catch that, because it counts
                # frames WITHOUT acknowledgement. It costs ~1.5 s, but ensure()
                # opens at startup and after a failure, not every tick.
                dev.reset()
            self.brightness = (self.spec.brightness if self.spec.brightness is not None
                               else caps.brightness.default)
            dev.set_brightness(self.brightness)
        except DEVICE_ERRORS as e:
            # Whatever failed happened AFTER open_panel() as often as before it
            # (reset, brightness, an impossible mounting angle), and a handle we
            # never store is a handle nobody can close - on the AX206 an exclusive
            # one, which would lock the module out of the next attempt too.
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
            self._fail("%s: %s" % (type(e).__name__, e))
            return False
        self.dev = dev
        self.caps = caps
        self._attempt = 0
        self._last_error = None
        self._missed_run = 0
        # We do not know what is on the screen after another process had it, so a
        # brand new Surface (which knows nothing) forces the next frame out whole.
        self.surface = surfaces.for_caps(caps, log=log().warning)
        if self.spec.rotate:
            log().info("%s: otwarty (jasnosc %s, obrot %d st.)",
                       self.tag, self.brightness, self.spec.rotate)
        else:
            log().info("%s: otwarty (jasnosc %s)", self.tag, self.brightness)
        return True

    def _fail(self, message):
        # Log ONCE per state change. A panel held by another program can stay held
        # for hours, and a line every second would flood the file.
        if message != self._last_error:
            log().warning("%s: %s", self.tag, message)
            self._last_error = message
        delay = BACKOFF[min(self._attempt, len(BACKOFF) - 1)]
        self._attempt += 1
        self._next_try = time.monotonic() + delay

    def drop(self, why):
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.caps = None
        self.surface = None
        self._fail(why)

    def reset(self, why):
        """Hard recovery of the panel. Afterwards the screen is unknown, so the
        memory of the last frame is cleared."""
        log().warning("%s: reset (%s)", self.tag, why)
        try:
            self.dev.reset()
            self.dev.set_brightness(self.brightness)
            self.surface.invalidate()
            self._missed_run = 0
            return True
        except DEVICE_ERRORS + (AttributeError,) as e:
            # AttributeError: self.dev is sometimes None when a reset coincides
            # with a drop(). DEVICE_ERRORS: AX206.reset() opens the module FROM
            # SCRATCH, so it goes through the same raw ctypes calls as ensure() —
            # and since the CSW threshold is reached in seconds, a reset happens
            # more often, not less.
            self.drop("reset nieudany: %s: %s" % (type(e).__name__, e))
            return False

    # -- sending -----------------------------------------------------------

    def send(self, frame, force=False):
        """Put `frame` on the glass, writing as little as the display allows.

        What to write is the Surface's decision; whether we can trust our belief
        about the glass is this layer's. The three cases where we cannot trust it
        all end in `invalidate()`, never in "skip the comparison": with a diff
        engine those are different things, and only the first one actually draws.
        """
        if not self.ensure():
            return False
        caps = self.caps
        # Periodic full repaint, timed from the last FULL write. On a display that
        # acknowledges nothing this is the only way back from a silently
        # desynchronised screen, so it must not be reset by ordinary partial writes.
        heal = (self.cfg.heal_repaint_sec
                and time.monotonic() - self.last_full_sent >= self.cfg.heal_repaint_sec)
        # An open run of unacknowledged frames cancels our belief about the glass.
        # If we do not know whether the display took the last frame, "the image did
        # not change" stops meaning "the right image is on the screen".
        #
        # Only for displays that acknowledge at all: where status is always None by
        # design, this would be permanently true and every tick would repaint.
        unsure = caps.acked and self._missed_run > 0
        if force or heal or unsure:
            self.surface.invalidate()
        first = self.surface.blank
        update = self.surface.plan(frame)
        if not update:
            return True
        try:
            t0 = time.monotonic()
            status = None
            for rect, payload in update.writes:
                status = self.dev.write(payload, rect)
        except DEVICE_ERRORS as e:
            # A partially written frame leaves the glass in a state we cannot
            # describe, so the next plan has to be a whole one.
            self.surface.invalidate()
            self.drop("zapis nieudany: %s: %s" % (type(e).__name__, e))
            return False

        if first:
            # ONE line per open, not per frame. "panel: otwarty" only says we hold a
            # handle, and a silent screen behind an open handle looks exactly like
            # correct operation in the log.
            ms = (time.monotonic() - t0) * 1000
            if caps.acked:
                log().info("%s: first frame after opening (status=%s, %.0f ms)",
                           self.tag, status, ms)
            else:
                # Deliberately different wording: this driver confirms nothing, so
                # the line proves the bytes were accepted, not that anything is
                # visible. An unplugged or scrambled screen would log the same.
                # ASCII only: panel.log is written in the system encoding, and a
                # dash outside it comes back as mojibake in every reader.
                log().info("%s: first frame sent after opening "
                           "(not acknowledged - check visually, %.0f ms)",
                           self.tag, ms)

        # Commit BEFORE the acknowledgement ladder below: reset() invalidates the
        # surface, and committing afterwards would quietly undo that.
        self.surface.commit(update)
        if update.full:
            self.last_full_sent = time.monotonic()
        if not caps.acked:
            return True
        if status is None:
            self._missed_run += 1
            if self._missed_run >= MISSED_CSW_LIMIT:
                # No CSW for a well-formed frame means the pipe is dirty.
                self.reset("%d frames not acknowledged" % self._missed_run)
        else:
            self._missed_run = 0
        return True

    def close(self):
        """Closes the handle, but does NOT clear the screen — the last frame stays."""
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
            self.caps = None
