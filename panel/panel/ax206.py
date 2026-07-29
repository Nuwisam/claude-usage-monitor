"""Sterownik panelu AX206 (VID 1908 / PID 0102) — Windows + libusb-win32.

Protokol wg dpf-ax (hackfin/dreamlayers): firmowa komenda SCSI 0xCD opakowana
w pare CBW/CSW transportu USB Bulk-Only Mass Storage, EP 0x01 OUT / 0x81 IN.

Swiadomie sa tu TYLKO set-property i blit. Komend flash (0xCB, kasowanie
i zapis SPI) w tym pliku nie ma i byc nie moze — to one potrafia zabic panel.

Trzy wlasciwosci TEGO egzemplarza, zmierzone, ktorych nie ma w zadnym zrodle
referencyjnym (szczegoly w docs/POC-FINDINGS.md):

  1. Komenda odpytania o geometrie (`0xCD .. bajt5=2`) jest ZAWODNA: w tych samych
     warunkach raz zwraca poprawne 480x320, raz milczy az do przeterminowania,
     a nieudana proba kosztuje jedno zgubione CSW w NASTEPNEJ komendzie. Dlatego
     geometria jest parametrem z konfiguracji, a `probe_geometry()` sluzy tylko
     diagnostyce i wola sie ja na koncu, nigdy przed wlasciwa praca.
  2. Panel BYWA, ze przestaje odsylac CSW, choc kazda ramke przyjmuje i rysuje
     poprawnie. Brak CSW jest tu ostrzezeniem (licznik `missed_csw`), nigdy
     bledem — inaczej klient wywala sie na dzialajacym sprzecie.
  3. `usb_reset()` niezawodnie przywraca potwierdzenia. Uchwyt po resecie jest
     niewazny, wiec `reset()` MUSI otworzyc urzadzenie ponownie.
  4. FULL_FRAME_ONLY — i to jest najdrozej okupione ustalenie tego pliku.

     Prostokat w komendzie NIE jest "obszarem do przerysowania". Ustawia OKNO
     rysowania, w ktore firmware wlewa CALY otrzymany strumien, ZAWIJAJAC go.
     Transfer musi miec dokladnie 307200 B (pelna klatka) — inaczej transakcja
     nie domyka sie, nie ma CSW i nie ma rysunku.

     Skutek dla okna mniejszego niz ekran: pelna klatka wchodzi w nie wielokrotnie,
     wiec na ekranie zostaje OGON ladunku, nie jego poczatek. Zmierzone: okno
     480x60 z ladunkiem, ktory na dole miał napis "DOL", pokazalo wlasnie ten
     napis; ladunek dopelniony zerami dawal okno CZARNE. Wczesniejsze "czarne
     ramki" to byl dokladnie ten efekt, nie brak rysowania.

     Nie da sie na tym zaoszczedzic transferu: prostokat 480x160 dostal ladunek
     rowny dokladnie jednemu wypelnieniu okna i tez nie zostal potwierdzony.
     Ilosc bajtow na drucie jest stala, wiec kazda aktualizacja kosztuje 307200 B
     (~376 ms) niezaleznie od tego, ile pikseli faktycznie sie zmienia. Dlatego
     `blit()` przyjmuje wylacznie pelny ekran. Posrednie potwierdzenie: pyax206,
     zrzucony z tego samego modelu, ma pelnoekranowy prostokat zaszyty na sztywno.

  5. Brak CSW NIE jest kaprysem panelu — to SKUTEK wczesniejszej zle uformowanej
     transakcji. Powtorzona, identyczna komenda potwierdzala sie normalnie, dopoki
     przed nia nie poszedl blit o zlej liczbie bajtow; potem potok milkl az do
     `reset()`. Przy samych poprawnych pelnych klatkach panel potwierdza kazda.
     Licznik `missed_csw` jest wiec sygnalem BLEDU W NAS, nie awarii sprzetu.

  6. Z zestawu komend dpf-ax ten firmware ma TYLKO blit i SETPROPERTY/BRIGHTNESS.
     Sprawdzone po resecie, na czystym potoku: FILLRECT (0x11), COPYRECT (0x13)
     ani SETPROPERTY z tokenem FGCOLOR (0x02) nie sa potwierdzane. Szkoda, bo
     FILLRECT rysowalby paski bez zadnego ladunku — ale go nie ma. To tez tlumaczy,
     czemu gotowe narzedzia pchaja tu wylacznie pelne klatki.
"""
import ctypes as C
import time

VID, PID = 0x1908, 0x0102
EP_OUT, EP_IN = 0x01, 0x81
PATH_MAX = 512

DIR_OUT, DIR_IN = 0, 1

USBCMD_SETPROPERTY = 0x01
USBCMD_BLIT = 0x12

PROPERTY_BRIGHTNESS = 0x01
PROPERTY_ORIENTATION = 0x10

DEFAULT_WIDTH, DEFAULT_HEIGHT = 480, 320


class AX206Error(RuntimeError):
    pass


# --- struktury libusb-win32 (API legacy 0.1) --------------------------------


class DevDesc(C.Structure):
    _fields_ = [
        ("bLength", C.c_ubyte), ("bDescriptorType", C.c_ubyte),
        ("bcdUSB", C.c_ushort), ("bDeviceClass", C.c_ubyte),
        ("bDeviceSubClass", C.c_ubyte), ("bDeviceProtocol", C.c_ubyte),
        ("bMaxPacketSize0", C.c_ubyte), ("idVendor", C.c_ushort),
        ("idProduct", C.c_ushort), ("bcdDevice", C.c_ushort),
        ("iManufacturer", C.c_ubyte), ("iProduct", C.c_ubyte),
        ("iSerialNumber", C.c_ubyte), ("bNumConfigurations", C.c_ubyte),
    ]


class Device(C.Structure):
    pass


class Bus(C.Structure):
    pass


Device._fields_ = [
    ("next", C.POINTER(Device)), ("prev", C.POINTER(Device)),
    ("filename", C.c_char * PATH_MAX), ("bus", C.POINTER(Bus)),
    ("descriptor", DevDesc), ("config", C.c_void_p), ("dev", C.c_void_p),
    ("devnum", C.c_ubyte), ("num_children", C.c_ubyte),
    ("children", C.POINTER(C.POINTER(Device))),
]
Bus._fields_ = [
    ("next", C.POINTER(Bus)), ("prev", C.POINTER(Bus)),
    ("dirname", C.c_char * PATH_MAX), ("devices", C.POINTER(Device)),
    ("location", C.c_uint), ("root_dev", C.POINTER(Device)),
]

_dll = None


def load(dll_path=None):
    """libusb0.dll, raz na proces.

    `dll_path` istnieje, bo zadanie harmonogramu startuje z innym PATH niz
    powloka i biblioteka bywa wtedy nieodnajdywalna po samej nazwie.
    """
    global _dll
    if _dll is not None:
        return _dll
    try:
        dll = C.CDLL(dll_path or "libusb0.dll")
    except OSError as e:
        raise AX206Error(
            "nie moge zaladowac libusb0.dll (%s) — czy sterownik libusb-win32 "
            "jest zainstalowany dla tego urzadzenia?" % e
        ) from e
    dll.usb_get_busses.restype = C.POINTER(Bus)
    dll.usb_open.restype = C.c_void_p
    dll.usb_open.argtypes = [C.POINTER(Device)]
    dll.usb_close.argtypes = [C.c_void_p]
    dll.usb_reset.argtypes = [C.c_void_p]
    dll.usb_strerror.restype = C.c_char_p
    dll.usb_claim_interface.argtypes = [C.c_void_p, C.c_int]
    dll.usb_release_interface.argtypes = [C.c_void_p, C.c_int]
    dll.usb_clear_halt.argtypes = [C.c_void_p, C.c_uint]
    dll.usb_get_string_simple.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_size_t]
    for fn in ("usb_bulk_write", "usb_bulk_read"):
        getattr(dll, fn).argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_int, C.c_int]
    _dll = dll
    return dll


class Found:
    """Znaleziony modul. `ptr` traci waznosc po kazdej re-enumeracji."""

    __slots__ = ("ptr", "filename", "bus", "devnum", "index")

    def __init__(self, ptr, filename, bus, devnum, index):
        self.ptr = ptr
        self.filename = filename
        self.bus = bus
        self.devnum = devnum
        self.index = index

    def __repr__(self):
        return "<AX206 #%d %s bus=%s dev=%d>" % (
            self.index, self.filename, self.bus, self.devnum)


def find_all(dll_path=None):
    """Wszystkie pasujace moduly, w kolejnosci enumeracji libusb.

    Za kazdym wywolaniem odswieza liste — po `usb_reset` stare wskazniki sa
    niewazne i trzeba szukac od nowa.
    """
    dll = load(dll_path)
    dll.usb_init()
    dll.usb_find_busses()
    dll.usb_find_devices()
    out = []
    bus = dll.usb_get_busses()
    while bus:
        dev = bus.contents.devices
        while dev:
            d = dev.contents.descriptor
            if (d.idVendor, d.idProduct) == (VID, PID):
                out.append(Found(
                    dev,
                    dev.contents.filename.decode(errors="replace"),
                    bus.contents.dirname.decode(errors="replace"),
                    dev.contents.devnum,
                    len(out),
                ))
            dev = dev.contents.next
        bus = bus.contents.next
    return out


def first_finder(dll_path=None):
    """Domyslny selektor: pierwszy pasujacy modul.

    Dla jednego panelu w zupelnosci wystarcza. Przy dwoch modulach NIE uzywaj
    tego w kliencie — patrz device.py; wybor musi byc jawny, inaczej klient
    potrafi przejac panel nalezacy do innego programu.
    """
    def finder():
        found = find_all(dll_path)
        return found[0] if found else None
    return finder


# --- pakowanie pikseli ------------------------------------------------------

_T_R = bytes(i & 0xF8 for i in range(256))
_T_GH = bytes(i >> 5 for i in range(256))
_T_GL = bytes((i & 0x1C) << 3 for i in range(256))
_T_B = bytes(i >> 3 for i in range(256))


def image_to_rgb565(img):
    """Obraz PIL -> RGB565, starszy bajt pierwszy (kolejnosc RGB565_0/_1 z dpf-ax).

    Kanaly skladane sa przez translacje tablicowa i JEDNA operacje OR na duzych
    liczbach calkowitych — obie ida w C. Pelna klatka 480x320 pakuje sie w ~3 ms
    zamiast ~36 ms petla po pikselach; numpy jest tu niepotrzebne.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    r, g, b = img.split()
    rb, gb, bb = r.tobytes(), g.tobytes(), b.tobytes()
    n = len(rb)
    hi = (int.from_bytes(rb.translate(_T_R), "big")
          | int.from_bytes(gb.translate(_T_GH), "big")).to_bytes(n, "big")
    lo = (int.from_bytes(gb.translate(_T_GL), "big")
          | int.from_bytes(bb.translate(_T_B), "big")).to_bytes(n, "big")
    out = bytearray(2 * n)
    out[0::2] = hi
    out[1::2] = lo
    return bytes(out)


def rgb565_bytes(r, g, b):
    """Jeden piksel, starszy bajt pierwszy."""
    return bytes(((r & 0xF8) | ((g & 0xE0) >> 5), ((g & 0x1C) << 3) | ((b & 0xF8) >> 3)))


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

    # -- cykl zycia --------------------------------------------------------

    def open(self):
        found = self.finder()
        if found is None:
            raise AX206Error("nie znalazlem wskazanego modulu %04x:%04x" % (VID, PID))
        self.h = self.dll.usb_open(found.ptr)
        if not self.h:
            raise AX206Error("usb_open: %s" % self._strerror())
        # Bez usb_set_configuration(): zeruje bity toggle i kosztuje pierwsze
        # CSW. dpf-ax tez tylko przejmuje interfejs.
        if self.dll.usb_claim_interface(self.h, 0) < 0:
            err = self._strerror()
            self.dll.usb_close(self.h)
            self.h = None
            raise AX206Error("usb_claim_interface: %s (panel zajety przez inny "
                             "proces?)" % err)
        self.serial = self._string(found)
        self.resync()
        return self

    def reset(self, settle=1.5):
        """usb_reset + ponowne otwarcie. Lekarstwo na panel, ktory przestal
        odsylac CSW. Uchwyt po resecie jest niewazny — stad ponowne szukanie."""
        if self.h:
            try:
                self.dll.usb_reset(self.h)
            except OSError:
                pass
            try:
                self.dll.usb_close(self.h)
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
        raise AX206Error("modul nie wrocil po resecie: %s" % last)

    def resync(self):
        """Czysci zatory i wypija odpowiedzi zostawione przez poprzedni proces."""
        self.dll.usb_clear_halt(self.h, EP_IN)
        self.dll.usb_clear_halt(self.h, EP_OUT)
        buf = C.create_string_buffer(4096)
        drained = 0
        while True:
            n = self.dll.usb_bulk_read(self.h, EP_IN, buf, 4096, 200)
            if n <= 0:
                break
            drained += n
        return drained

    def close(self):
        if self.h:
            try:
                self.dll.usb_release_interface(self.h, 0)
                self.dll.usb_close(self.h)
            except OSError:
                pass
            self.h = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _strerror(self):
        return self.dll.usb_strerror().decode(errors="replace")

    def _string(self, found):
        idx = found.ptr.contents.descriptor.iSerialNumber
        if not idx:
            return None
        buf = C.create_string_buffer(256)
        n = self.dll.usb_get_string_simple(self.h, idx, buf, 256)
        return buf.value.decode(errors="replace") if n > 0 else None

    # -- transport ---------------------------------------------------------

    def _scsi(self, cmd, direction=DIR_OUT, data=None, length=0, timeout=15000):
        """Opakowanie Bulk-Only Mass Storage. `cmd` to 16-bajtowa komenda firmowa.

        Odwzorowuje emulate_scsi() z dpf-ax, razem z zostawieniem bmCBWFlags na 0
        takze dla transferow do hosta — firmware ignoruje ten bit, a ustawienie
        go poprawnie konczy sie zatorem na EP IN (sprawdzone).
        """
        if not self.h:
            raise AX206Error("panel nie jest otwarty")
        cbw = bytearray(31)
        cbw[0:4] = b"USBC"
        cbw[4:8] = bytes((0xDE, 0xAD, 0xBE, 0xEF))
        cbw[8:12] = int(length).to_bytes(4, "little")
        cbw[14] = len(cmd)
        cbw[15:15 + len(cmd)] = bytes(cmd)

        n = self.dll.usb_bulk_write(self.h, EP_OUT, bytes(cbw), len(cbw), 1000)
        if n != len(cbw):
            raise AX206Error("zapis CBW nieudany (%d): %s" % (n, self._strerror()))

        out = None
        if length:
            if direction == DIR_OUT:
                n = self.dll.usb_bulk_write(self.h, EP_OUT, bytes(data), length, timeout)
                if n != length:
                    raise AX206Error("zapis danych urwany (%d/%d)" % (n, length))
            else:
                buf = C.create_string_buffer(length)
                n = self.dll.usb_bulk_read(self.h, EP_IN, buf, length, timeout)
                if n < 0:
                    raise AX206Error("odczyt danych nieudany (%d)" % n)
                out = buf.raw[:n]

        # Pelna klatka idzie ~0,4 s, wiec zdrowe CSW miesci sie w 1,5 s. Ciasny
        # limit ma znaczenie: uchwyt przejety w zabrudzonym stanie gubi pierwsze
        # jedno-dwa CSW, a dlugi timeout zamienia to w kilkusekundowy przestoj.
        csw = C.create_string_buffer(13)
        n = self.dll.usb_bulk_read(self.h, EP_IN, csw, 13, 1500)
        if n < 0:
            # Zator trzeba wyczyscic, zanim CSW da sie odczytac (procedura BOT).
            self.dll.usb_clear_halt(self.h, EP_IN)
            n = self.dll.usb_bulk_read(self.h, EP_IN, csw, 13, 500)
        if n != 13 or csw.raw[:4] != b"USBS":
            # Ten firmware bywa, ze pomija CSW, mimo ze dane przyjal i narysowal.
            # Odnotuj, ale NIE przerywaj klatki — patrz naglowek modulu.
            self.missed_csw += 1
            return None, out
        return csw.raw[12], out

    # -- komendy -----------------------------------------------------------

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
        """0 (przygaszony) .. 7 (najjasniejszy)."""
        return self.set_property(PROPERTY_BRIGHTNESS, max(0, min(7, int(level))))

    def blit(self, rgb565, rect=None):
        """Wysyla piksele RGB565. rect = (x0, y0, x1, y1), x1/y1 wylaczne.

        TYLKO pelny ekran — patrz FULL_FRAME_ONLY w naglowku modulu. Prostokat
        czesciowy jest odrzucany celowo: firmware przyjmuje go bez mrugniecia
        i NIE rysuje, a cichy no-op jest tu najgorszym mozliwym zachowaniem.
        """
        if rect is None:
            rect = (0, 0, self.width, self.height)
        x0, y0, x1, y1 = rect
        if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
            raise AX206Error("prostokat %r wychodzi poza %dx%d"
                             % (rect, self.width, self.height))
        if (x0, y0, x1, y1) != (0, 0, self.width, self.height):
            raise AX206Error(
                "prostokat czesciowy %r: ten firmware rysuje WYLACZNIE pelny "
                "ekran (0,0,%d,%d). Zmierzone: kazdy blit czesciowy jest "
                "przyjmowany, nie potwierdzany i nie rysowany."
                % (rect, self.width, self.height))
        expect = (x1 - x0) * (y1 - y0) * 2
        if len(rgb565) != expect:
            raise AX206Error("bufor ma %d B, a prostokat %r wymaga %d B"
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
        """Wysyla obraz PIL w punkcie `at`."""
        x0, y0 = at
        return self.blit(image_to_rgb565(img),
                         (x0, y0, x0 + img.width, y0 + img.height))

    def probe_geometry(self):
        """Diagnostyka: zapytaj modul o wlasna geometrie.

        TEN egzemplarz tego nie obsluguje — odczyt danych sie przeterminowuje,
        wiec zwracamy None. Zostawione, bo inny modul moze odpowiedziec i wtedy
        warto to zobaczyc w `--probe`. Po nieudanej probie MUSI byc resync:
        transakcja zostaje urwana w polowie i nastepna komenda trafialaby
        w zabrudzony endpoint (tak wlasnie zawieszal sie panel w rozpoznaniu).
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
