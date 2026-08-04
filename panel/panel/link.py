"""Nadzor nad panelem: jedyne miejsce w kliencie, ktore dotyka libusb-1.0.dll.

Rzeczy, ktore ta warstwa ma zalatwiac same z siebie:
  * wskazany modul jest zajety albo wypiety      -> backoff, log RAZ na zmiane stanu
  * panel przestal potwierdzac                   -> reset i pelne przerysowanie
  * obraz sie nie zmienil                        -> nie wysylamy nic

Czego NIE robi: nie czysci ekranu przy zamknieciu. Panel trzyma ostatnia klatke
bez podlaczonego hosta i to jest zamierzone — po wylaczeniu komputera na biurku
zostaje ostatni znany stan limitow.
"""
import time

from . import device, surface as surfaces
from .drivers.base import DriverError
from .log import get as log

BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)

# Ile nieodebranych CSW pod rzad znaczy "panel zamilkl". Poprawnie uformowane
# pelne klatki potwierdzaja sie ZAWSZE, wiec kilka z rzedu to juz nie przypadek.
MISSED_CSW_LIMIT = 3

# Bledy, na ktore ta warstwa odpowiada backoffem, a nie smiercia procesu.
# Ponizej `ensure()` i `blit()` siedzi surowe ctypes do libusb-1.0.dll (find_all,
# libusb_open, libusb_claim_interface, bulk transfer). Wypiety modul albo
# rozjezdzajacy sie sterownik zglasza sie stamtad OSError-em, nie AX206Error-em
# — i taki wyjatek szedl przez tick() i run() az do excepthooka, konczac proces.
# `drop()` i `close()` byly na to odporne od poczatku; sciezka otwarcia nie.
#
# Swiadomie NIE ma tu ctypes.ArgumentError: on znaczy zla sygnature wywolania,
# czyli blad w nas. Taki ma dojsc do excepthooka i zostac naprawiony, nie
# wpasc w petle ponawiania.
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

    # -- polaczenie --------------------------------------------------------

    @property
    def up(self):
        return self.dev is not None

    def ensure(self):
        """Otwiera modul, jesli trzeba. Zwraca True, gdy panel jest gotowy."""
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
        # Log RAZ na zmiane stanu. Panel zajety przez inny program potrafi byc zajety
        # godzinami i linia co sekunde zalalaby plik.
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
        """Twarde odzyskanie panelu. Po nim ekran jest nieznany, wiec kasujemy
        pamiec ostatniej klatki."""
        log().warning("%s: reset (%s)", self.tag, why)
        try:
            self.dev.reset()
            self.dev.set_brightness(self.brightness)
            self.surface.invalidate()
            self._missed_run = 0
            return True
        except DEVICE_ERRORS + (AttributeError,) as e:
            # AttributeError: self.dev bywa None, gdy reset zbiegnie sie z drop().
            # DEVICE_ERRORS: AX206.reset() otwiera modul OD NOWA, wiec idzie przez
            # te same surowe wywolania ctypes co ensure() — a odkad prog CSW
            # osiaga sie w sekundach, reset zdarza sie czesciej, nie rzadziej.
            self.drop("reset nieudany: %s: %s" % (type(e).__name__, e))
            return False

    # -- wysylka -----------------------------------------------------------

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
                log().info("%s: pierwsza klatka po otwarciu (status=%s, %.0f ms)",
                           self.tag, status, ms)
            else:
                # Deliberately different wording: this driver confirms nothing, so
                # the line proves the bytes were accepted, not that anything is
                # visible. An unplugged or scrambled screen would log the same.
                # ASCII only: panel.log is written in the system encoding, and a
                # dash outside it comes back as mojibake in every reader.
                log().info("%s: wyslano pierwsza klatke po otwarciu "
                           "(bez potwierdzenia - sprawdz wzrokiem, %.0f ms)",
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
                self.reset("%d klatek bez potwierdzenia" % self._missed_run)
        else:
            self._missed_run = 0
        return True

    def close(self):
        """Zamyka uchwyt, ale NIE czysci ekranu — ostatnia klatka ma zostac."""
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
            self.caps = None
