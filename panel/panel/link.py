"""Nadzor nad panelem: jedyne miejsce w kliencie, ktore dotyka libusb0.dll.

Rzeczy, ktore ta warstwa ma zalatwiac same z siebie:
  * wskazany modul jest zajety albo wypiety      -> backoff, log RAZ na zmiane stanu
  * panel przestal potwierdzac                   -> reset i pelne przerysowanie
  * obraz sie nie zmienil                        -> nie wysylamy nic

Czego NIE robi: nie czysci ekranu przy zamknieciu. Panel trzyma ostatnia klatke
bez podlaczonego hosta i to jest zamierzone — po wylaczeniu komputera na biurku
zostaje ostatni znany stan limitow.
"""
import time

from . import ax206, device
from .log import get as log

BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)

# Ile nieodebranych CSW pod rzad znaczy "panel zamilkl". Poprawnie uformowane
# pelne klatki potwierdzaja sie ZAWSZE, wiec kilka z rzedu to juz nie przypadek.
MISSED_CSW_LIMIT = 3

# Bledy, na ktore ta warstwa odpowiada backoffem, a nie smiercia procesu.
# Ponizej `ensure()` i `blit()` siedzi surowe ctypes do libusb0.dll (find_all,
# usb_open, usb_claim_interface, bulk read/write). Wypiety modul albo
# rozjezdzajacy sie sterownik zglasza sie stamtad OSError-em, nie AX206Error-em
# — i taki wyjatek szedl przez tick() i run() az do excepthooka, konczac proces.
# `drop()` i `close()` byly na to odporne od poczatku; sciezka otwarcia nie.
#
# Swiadomie NIE ma tu ctypes.ArgumentError: on znaczy zla sygnature wywolania,
# czyli blad w nas. Taki ma dojsc do excepthooka i zostac naprawiony, nie
# wpasc w petle ponawiania.
DEVICE_ERRORS = (ax206.AX206Error, OSError)


class PanelLink:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dev = None
        self.last_payload = None
        self.last_sent = 0.0
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
        try:
            finder = device.finder_for(self.cfg.device, self.cfg.libusb_dll)
            dev = ax206.AX206(finder=finder, width=self.cfg.width,
                              height=self.cfg.height,
                              dll_path=self.cfg.libusb_dll).open()
            dev.set_brightness(self.cfg.brightness)
        except DEVICE_ERRORS as e:
            self._fail("%s: %s" % (type(e).__name__, e))
            return False
        self.dev = dev
        self._attempt = 0
        self._last_error = None
        self._missed_run = 0
        # Nie wiemy, co jest na ekranie po cudzym procesie — nastepna klatka
        # musi pojsc bezwarunkowo.
        self.last_payload = None
        log().info("panel: otwarty (%s, jasnosc %s)", dev.serial, self.cfg.brightness)
        return True

    def _fail(self, message):
        # Log RAZ na zmiane stanu. Panel zajety przez inny program potrafi byc zajety
        # godzinami i linia co sekunde zalalaby plik.
        if message != self._last_error:
            log().warning("panel: %s", message)
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
        self.last_payload = None
        self._fail(why)

    def reset(self, why):
        """Twarde odzyskanie panelu. Po nim ekran jest nieznany, wiec kasujemy
        pamiec ostatniej klatki."""
        log().warning("panel: reset (%s)", why)
        try:
            self.dev.reset()
            self.dev.set_brightness(self.cfg.brightness)
            self.last_payload = None
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
        """Wysyla klatke, jesli rozni sie od tego, co panel juz pokazuje.

        Porownanie 307 kB bajtow kosztuje ulamek milisekundy, a transfer 376 ms —
        wiec to porownanie jest tu glownym mechanizmem oszczedzania, jedynym
        jaki ten firmware zostawia (blitu czesciowego nie ma, patrz ax206.py).
        """
        if not self.ensure():
            return False
        heal = (self.cfg.heal_repaint_sec
                and time.monotonic() - self.last_sent >= self.cfg.heal_repaint_sec)
        # Otwarty ciag klatek bez potwierdzenia ZNOSI skrot po rownosci. Skoro
        # nie wiemy, czy panel przyjal ostatnia klatke, to "obraz sie nie zmienil"
        # przestaje znaczyc "na szkle jest to, co trzeba".
        #
        # Bez tego prog MISSED_CSW_LIMIT byl w praktyce martwy: licznik rosl
        # wylacznie przy faktycznej wysylce, a wysylamy mniej wiecej raz na
        # minute — wiec trzy klatki to bylo od trzech minut do kwadransa
        # (przy nieruchomym obrazie dopiero heal_repaint_sec ruszal licznik).
        # Teraz prog jest osiagany w kolejnych tickach, czyli w sekundach.
        unsure = self._missed_run > 0
        if not force and not heal and not unsure and frame.payload == self.last_payload:
            return True
        first = self.last_payload is None
        try:
            t0 = time.monotonic()
            status = self.dev.blit(frame.payload)
        except DEVICE_ERRORS as e:
            self.drop("blit nieudany: %s: %s" % (type(e).__name__, e))
            return False

        if first:
            # JEDNA linia na otwarcie, nie co klatke. "panel: otwarty" mowi tylko,
            # ze mamy uchwyt — a milczacy ekran przy otwartym uchwycie wyglada
            # w logu identycznie jak poprawna praca. Bez tej linii jedynym
            # sposobem na rozstrzygniecie bylo zatrzymanie zadania i --probe.
            log().info("panel: pierwsza klatka po otwarciu (status=%s, %.0f ms)",
                       status, (time.monotonic() - t0) * 1000)

        self.last_payload = frame.payload
        self.last_sent = time.monotonic()
        if status is None:
            self._missed_run += 1
            if self._missed_run >= MISSED_CSW_LIMIT:
                # Brak CSW przy poprawnej klatce znaczy, ze potok jest zabrudzony.
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
