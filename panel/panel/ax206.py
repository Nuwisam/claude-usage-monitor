"""Sterownik panelu AX206 (VID 1908 / PID 0102) — Windows + libusb-1.0.

Dwie rzeczy, ktore latwo pomylic: STEROWNIK urzadzenia to nadal libusb-win32
(`libusb0.sys`, ten sam, ktorego uzywaja gotowe narzedzia do tych wyswietlaczy), a BIBLIOTEKA, przez ktora z nim
rozmawiamy, to libusb-1.0. Backend windowsowy libusb-1.0 obsluguje urzadzenia
zwiazane z libusb0.sys — zmierzone, patrz nizej. Przepinanie sterownika na
WinUSB nie jest potrzebne i zerwaloby tamte narzedzia.

Powod zmiany biblioteki jest jeden: `libusb_get_port_numbers()` podaje LANCUCH
PORTOW z tego samego uchwytu, ktory otwieramy. API legacy 0.1 z libusb-win32 nie
podawalo zadnej topologii (`bus-0`, `devnum=0`), wiec modul trzeba bylo szukac
w rejestrze Windows i laczyc jedno z drugim po kolejnosci — czyli zgadywac.
Szczegoly w naglowku device.py.

Protokol wg dpf-ax (hackfin/dreamlayers): firmowa komenda SCSI 0xCD opakowana
w pare CBW/CSW transportu USB Bulk-Only Mass Storage, EP 0x01 OUT / 0x81 IN.

Swiadomie sa tu TYLKO set-property i blit. Komend flash (0xCB, kasowanie
i zapis SPI) w tym pliku nie ma i byc nie moze — to one potrafia zabic panel.

Trzy wlasciwosci TEGO egzemplarza, zmierzone, ktorych nie ma w zadnym zrodle
referencyjnym:

  1. Komenda odpytania o geometrie (`0xCD .. bajt5=2`) jest ZAWODNA: w tych samych
     warunkach raz zwraca poprawne 480x320, raz milczy az do przeterminowania,
     a nieudana proba kosztuje jedno zgubione CSW w NASTEPNEJ komendzie. Dlatego
     geometria jest parametrem z konfiguracji, a `probe_geometry()` sluzy tylko
     diagnostyce i wola sie ja na koncu, nigdy przed wlasciwa praca.
  2. Panel BYWA, ze przestaje odsylac CSW, choc kazda ramke przyjmuje i rysuje
     poprawnie. Brak CSW jest tu ostrzezeniem (licznik `missed_csw`), nigdy
     bledem — inaczej klient wywala sie na dzialajacym sprzecie.

     Jeden przypadek jest POWTARZALNY, nie losowy: PIERWSZA klatka po zimnym
     otwarciu uchwytu nigdy nie dostaje CSW. Zmierzone po trzy razy na obu
     bibliotekach — libusb-win32 i libusb-1.0 zachowuja sie identycznie, wiec to
     wlasciwosc firmware'u, nie warstwy transportu. Powtorka po `clear_halt` tego
     nie ratuje. Kolejne klatki potwierdzaja sie normalnie: 1200 pod rzad,
     `missed_csw == 0`. Czyli `missed_csw == 1` zaraz po starcie klienta jest
     stanem NORMALNYM i nie znaczy bledu w nas.
  3. Reset urzadzenia niezawodnie przywraca potwierdzenia. Uchwyt po resecie jest
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
     niezaleznie od tego, ile pikseli faktycznie sie zmienia. Dlatego
     `blit()` przyjmuje wylacznie pelny ekran. Posrednie potwierdzenie: pyax206,
     zrzucony z tego samego modelu, ma pelnoekranowy prostokat zaszyty na sztywno.

  5. Brak CSW NIE jest kaprysem panelu — to SKUTEK wczesniejszej zle uformowanej
     transakcji. Powtorzona, identyczna komenda potwierdzala sie normalnie, dopoki
     przed nia nie poszedl blit o zlej liczbie bajtow; potem potok milkl az do
     `reset()`. Przy samych poprawnych pelnych klatkach panel potwierdza kazda.
     Licznik `missed_csw` jest wiec sygnalem BLEDU W NAS, nie awarii sprzetu.

  6. Czas klatki ZALEZY OD TRESCI, mimo ze liczba bajtow na drucie jest stala.
     Zmierzone na tym module, ten sam blit 307200 B, po resecie, po pieć powtorzen:

         karta testowa (ciemna, jak uklad docelowy)   354 ms
         pelna czern                              356 ms
         pasy nasyconych kolorow                  514 ms

     Rozne ladunki ciemne daja ten sam czas, wiec to nie jest kwestia powtarzania
     tej samej klatki. Wyglada na koszt po stronie kontrolera LCD, nie transportu.
     Praktyczny wniosek: liczba, ktora obowiazuje dla tego ekranu, to ~355 ms
     (~2,8 kl./s) — uklad jest ciemny. Syntetyczne testy nasyconymi pasami mierza
     najgorszy przypadek i ich wynik (~515 ms) nie opisuje pracy klienta.

     Uwaga na pulapke pomiarowa: pierwsze pomiary migracji na libusb-1.0 dawaly
     515 ms i wygladaly na regresje wobec udokumentowanych ~376 ms. Regresji nie
     bylo — zmienil sie ladunek testowy. libusb-win32 na tych samych pasach
     dawal 503-533 ms (pomiar rownolegly).

  7. Z zestawu komend dpf-ax ten firmware ma TYLKO blit i SETPROPERTY/BRIGHTNESS.
     Sprawdzone po resecie, na czystym potoku: FILLRECT (0x11), COPYRECT (0x13)
     ani SETPROPERTY z tokenem FGCOLOR (0x02) nie sa potwierdzane. Szkoda, bo
     FILLRECT rysowalby paski bez zadnego ladunku — ale go nie ma. To tez tlumaczy,
     czemu gotowe narzedzia pchaja tu wylacznie pelne klatki.
"""
import ctypes as C
import time

VID, PID = 0x1908, 0x0102
EP_OUT, EP_IN = 0x01, 0x81

DIR_OUT, DIR_IN = 0, 1

# Kody bledow libusb-1.0, ktore obslugujemy z nazwy. Reszta idzie przez
# libusb_strerror i lezy w komunikacie.
LIBUSB_ERROR_NOT_FOUND = -5
LIBUSB_ERROR_TIMEOUT = -7
LIBUSB_ERROR_PIPE = -9

# Glebokosc drzewa USB: libusb dokumentuje maksimum 7 hubow miedzy hostem
# a urzadzeniem, wiec 8 bajtow starcza na kazdy realny lancuch.
MAX_PORT_DEPTH = 8

USBCMD_SETPROPERTY = 0x01
USBCMD_BLIT = 0x12

PROPERTY_BRIGHTNESS = 0x01
PROPERTY_ORIENTATION = 0x10

DEFAULT_WIDTH, DEFAULT_HEIGHT = 480, 320


class AX206Error(RuntimeError):
    pass


# --- struktury i wiazania libusb-1.0 ----------------------------------------


class DevDesc(C.Structure):
    """`libusb_device_descriptor`. Uklad jest ten sam co w API 0.1 — to po prostu
    deskryptor z normy USB, bajt w bajt."""

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
    """Skad brac libusb-1.0.dll, w kolejnosci prob.

    Pakiet `libusb` z PyPI niesie binarke dla win-amd64 i to on jest droga
    domyslna — dzieki temu instalacja panelu jest jednym `pip install -r`,
    bez krokow recznych na nowej maszynie. Jawna sciezka bije wszystko, bo
    zadanie harmonogramu startuje z innym PATH niz powloka.
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
    """libusb-1.0.dll, raz na proces."""
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
            "nie moge zaladowac libusb-1.0.dll. Proby: %s. Biblioteke daje "
            "pakiet `libusb` z requirements.txt; sterownikiem urzadzenia "
            "pozostaje libusb-win32." % "; ".join(tried))

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
    """Kontekst libusb, raz na proces. `libusb_exit` nie jest wolane celowo:
    kontekst zyje tyle, co proces, a zamykanie go w trakcie unieważniloby
    wskazniki urzadzen trzymane przez otwarte uchwyty."""
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
    """Lancuch portow w postaci, ktora idzie do panel.json: "3.4".

    Numer magistrali celowo NIE wchodzi do klucza. Jest syntetycznym indeksem
    kontrolera nadawanym przy enumeracji — czyli tej samej natury, co `Hub_#`
    z rejestru, ktory wywalil poprzednia wersje selektora. Lancuch portow opisuje
    fizyczne gniazda i tego problemu nie ma.
    """
    return ".".join(str(p) for p in ports)


class Found:
    """Znaleziony modul: TRZYMA referencje do urzadzenia libusb.

    Referencja jest wlasnoscia tego obiektu i musi zostac oddana przez
    `release()` — inaczej kazda enumeracja (a jest ich tyle, co otwarc panelu)
    zostawia po sobie urzadzenie, ktorego libusb nie zwolni. W API 0.1 problemu
    nie bylo, bo lista urzadzen zyla w bibliotece; tutaj zyje u nas.
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
        """Oddaje referencje. Idempotentne — wolane i po udanym otwarciu,
        i przy odrzuceniu modulu, wiec nie moze bolec przy powtorce."""
        if self.ptr is not None:
            self._dll.libusb_unref_device(self.ptr)
            self.ptr = None

    def __repr__(self):
        return "<AX206 #%d bus=%s ports=%s addr=%s>" % (
            self.index, self.bus, self.port_path, self.address)


def find_all(dll_path=None):
    """Wszystkie pasujace moduly, w kolejnosci enumeracji libusb.

    Za kazdym wywolaniem odswieza liste — po resecie stare wskazniki sa niewazne
    i trzeba szukac od nowa. KAZDY zwrocony `Found` niesie referencje; ten, kto
    ich nie uzyje, ma obowiazek wolac `release()` (robi to `release_all`).
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
        # Lista znika, ale zatrzymane urzadzenia zyja dzieki wlasnym referencjom.
        dll.libusb_free_device_list(lst, 1)
    return out


def release_all(found, keep=None):
    """Oddaje referencje wszystkich modulow poza `keep`."""
    for f in found:
        if f is not keep:
            f.release()


def first_finder(dll_path=None):
    """Domyslny selektor: pierwszy pasujacy modul.

    Dla jednego panelu w zupelnosci wystarcza. Przy dwoch modulach NIE uzywaj
    tego w kliencie — patrz device.py; wybor musi byc jawny, inaczej klient
    potrafi przejac panel nalezacy do innego programu.
    """
    def finder():
        found = find_all(dll_path)
        picked = found[0] if found else None
        release_all(found, keep=picked)
        return picked
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
        self.port_path = None

    # -- cykl zycia --------------------------------------------------------

    def open(self):
        found = self.finder()
        if found is None:
            raise AX206Error("nie znalazlem wskazanego modulu %04x:%04x" % (VID, PID))
        # Referencja `found` jest nasza i musi wrocic do libusb w KAZDYM
        # zakonczeniu tej funkcji. Udane `libusb_open` bierze wlasna referencje,
        # wiec uchwyt zyje dalej bez naszej — a przy bledzie tym bardziej nie ma
        # czego trzymac.
        try:
            handle = C.c_void_p()
            rc = self.dll.libusb_open(found.ptr, C.byref(handle))
            if rc != 0:
                raise AX206Error("libusb_open: %s" % self._err(rc))
            self.h = handle
            # Bez libusb_set_configuration(): zeruje bity toggle i kosztuje
            # pierwsze CSW. dpf-ax tez tylko przejmuje interfejs.
            rc = self.dll.libusb_claim_interface(self.h, 0)
            if rc != 0:
                err = self._err(rc)
                self.dll.libusb_close(self.h)
                self.h = None
                raise AX206Error("libusb_claim_interface: %s (panel zajety przez "
                                 "inny proces?)" % err)
            self.port_path = found.port_path
            self.serial = self._string(found.iserial)
        finally:
            found.release()
        self.resync()
        return self

    def reset(self, settle=1.5):
        """libusb_reset_device + ponowne otwarcie. Lekarstwo na panel, ktory
        przestal odsylac CSW. Uchwyt po resecie moze byc niewazny — stad ponowne
        szukanie za kazdym razem.

        `LIBUSB_ERROR_NOT_FOUND` nie jest tu awaria: znaczy, ze urzadzenie zostalo
        re-enumerowane i trzeba je otworzyc na nowo. Dokladnie to robimy nizej.
        """
        if self.h:
            rc = self.dll.libusb_reset_device(self.h)
            if rc not in (0, LIBUSB_ERROR_NOT_FOUND):
                # Nie przerywamy: ponowne otwarcie i tak jest nasza jedyna
                # sciezka wyjscia, a komunikat przyda sie w logu.
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
        raise AX206Error("modul nie wrocil po resecie: %s" % last)

    def resync(self):
        """Czysci zatory i wypija odpowiedzi zostawione przez poprzedni proces."""
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
        """Jeden transfer. Zwraca (rc, liczba_bajtow, bufor).

        To jest miejsce, w ktorym libusb-1.0 rozni sie od API 0.1 najbardziej:
        kod bledu i liczba przeslanych bajtow to DWIE osobne wartosci, a nie
        jedna liczba ze znakiem. Kazde wywolanie musi patrzec na obie — samo
        `n == length` nie wystarczy, a samo `rc == 0` tym bardziej.
        """
        n = C.c_int(0)
        buf = data if data is not None else C.create_string_buffer(length)
        rc = self.dll.libusb_bulk_transfer(self.h, endpoint, buf, length,
                                           C.byref(n), timeout)
        return rc, n.value, buf

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

        rc, n, _ = self._bulk(EP_OUT, bytes(cbw), len(cbw), 1000)
        if rc != 0 or n != len(cbw):
            raise AX206Error("zapis CBW nieudany (%d/%d): %s"
                             % (n, len(cbw), self._err(rc)))

        out = None
        if length:
            if direction == DIR_OUT:
                rc, n, _ = self._bulk(EP_OUT, bytes(data), length, timeout)
                if rc != 0 or n != length:
                    raise AX206Error("zapis danych urwany (%d/%d): %s"
                                     % (n, length, self._err(rc)))
            else:
                rc, n, buf = self._bulk(EP_IN, None, length, timeout)
                if rc != 0:
                    raise AX206Error("odczyt danych nieudany: %s" % self._err(rc))
                out = buf.raw[:n]

        # Pelna klatka idzie ~0,5 s, wiec zdrowe CSW miesci sie w 1,5 s. Ciasny
        # limit ma znaczenie: uchwyt przejety w zabrudzonym stanie gubi pierwsze
        # jedno-dwa CSW, a dlugi timeout zamienia to w kilkusekundowy przestoj.
        rc, n, csw = self._bulk(EP_IN, None, 13, 1500)
        if rc != 0:
            # Zator trzeba wyczyscic, zanim CSW da sie odczytac (procedura BOT).
            # Powtorka idzie po KAZDYM bledzie, nie tylko po LIBUSB_ERROR_PIPE:
            # zmierzone, ze zawieszone CSW wraca po clear_halt takze wtedy, gdy
            # pierwsza proba skonczyla sie zwyklym przeterminowaniem.
            self.dll.libusb_clear_halt(self.h, EP_IN)
            rc, n, csw = self._bulk(EP_IN, None, 13, 500)
        if rc != 0 or n != 13 or csw.raw[:4] != b"USBS":
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
