"""Ktory modul AX206 ma obsluzyc ten klient.

Docelowo na magistrali sa DWA takie same wyswietlacze: jeden pod innym programem, drugi
pod monitor limitow. Oba zglaszaja sie jako VID_1908&PID_0102 i — zmierzone —
oba maja ten sam numer seryjny "WCH32", bo to stala firmware'u, nie numer
egzemplarza. Rozroznia je wiec wylacznie FIZYCZNY PORT z rejestru Windows
(`LocationInformation`, np. "Port_#0004.Hub_#0006"), stabilny dopoki nie
przelozysz wtyczki do innego gniazda.

ZASADA: klient nigdy nie siega po "pierwszy wolny". Przejecie cudzego panelu
w chwili, gdy tamten program akurat sie restartuje, byloby awaria cicha i trudna do
powiazania z przyczyna — wiec brak wskazanego modulu konczy sie czekaniem,
a nie podmiana celu.

STAN WERYFIKACJI: laczenie wpisu z rejestru z urzadzeniem libusb jest sprawdzone
tylko dla JEDNEGO modulu (wtedy jest trywialne). libusb-win32 nie udostepnia
sciezki portu, wiec przy dwoch modulach parujemy po kolejnosci i mowimy o tym
glosno w logu. Do potwierdzenia `--identify`, gdy drugi modul bedzie na biurku.
"""
import ctypes
import winreg

from . import ax206
from .log import get as log

REG_PATH = r"SYSTEM\CurrentControlSet\Enum\USB\VID_%04X&PID_%04X" % (
    ax206.VID, ax206.PID)
DEVICE_FILTER = r"USB\VID_%04X&PID_%04X" % (ax206.VID, ax206.PID)

CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
CM_GETIDLIST_FILTER_PRESENT = 0x00000100


def present_instances():
    """Nazwy instancji tego VID/PID, ktore Windows widzi JAKO OBECNE.

    `Enum\\USB` to trwala baza PnP, nie lista sprzetu na magistrali: kazde
    gniazdo, w ktorym modul kiedykolwiek siedzial, zostawia tam wpis na zawsze
    i nikt go nie sprzata. Bez odsiania duchow liczba wpisow przestawala sie
    zgadzac z liczba modulow po pierwszym przelozeniu wtyczki — a wtedy
    `list_panels` nie doklejalo juz zadnego `location` i selektor z konfiguracji
    nie trafial w nic. Na stale.

    Po podkluczach rejestru poznac tego NIE DA SIE. Sprawdzone na tym module:
    ma Status=OK, Present=True, Service=libusb0 — i nie ma podklucza `Control`
    wcale. Filtr oparty na `Control` wyciolby jedyne prawdziwe urzadzenie
    i zamienil usterke warunkowa w trwala. Pyta sie wiec menedzera konfiguracji,
    bo tylko on wie, co jest wpiete.

    Zwraca None, gdy zapytanie zawiedzie — wtedy nie filtrujemy NICZEGO. Lepiej
    dokleic za duzo (najwyzej `paired=False`) niz stracic dzialajacy modul.
    """
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    try:
        cfgmgr = ctypes.WinDLL("cfgmgr32.dll")
        size = ctypes.c_ulong(0)
        if cfgmgr.CM_Get_Device_ID_List_SizeW(
                ctypes.byref(size), ctypes.c_wchar_p(DEVICE_FILTER), flags) != 0:
            return None
        buf = ctypes.create_unicode_buffer(size.value)
        if cfgmgr.CM_Get_Device_ID_ListW(
                ctypes.c_wchar_p(DEVICE_FILTER), buf, size.value, flags) != 0:
            return None
    except (OSError, AttributeError, ValueError):
        return None
    return {i.rsplit("\\", 1)[-1].upper()
            for i in buf[:size.value].split("\0") if i}


class PanelInfo:
    """Modul widziany z obu stron naraz: libusb i rejestru Windows."""

    __slots__ = ("index", "filename", "bus", "devnum", "found",
                 "instance", "location", "address", "container", "paired")

    def __init__(self, found):
        # `found` pochodzi z TEJ enumeracji i tylko z niej — wskaznik traci
        # waznosc po kazdym usb_reset, wiec nie wolno go przechowywac dluzej.
        self.found = found
        self.index = found.index
        self.filename = found.filename
        self.bus = found.bus
        self.devnum = found.devnum
        self.instance = None
        self.location = None
        self.address = None
        self.container = None
        self.paired = False   # czy dane z rejestru sa PEWNE, czy tylko zgadniete

    def describe(self):
        loc = self.location or "?"
        return "#%d  %s  port=%s%s" % (
            self.index, self.filename, loc, "" if self.paired else "  (zgadniete)")

    def __repr__(self):
        return "<PanelInfo %s>" % self.describe()


def windows_instances():
    """Wpisy tego VID/PID z rejestru. Tylko odczyt, bez praw administratora."""
    out = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_PATH)
    except OSError:
        return out
    present = present_instances()
    with key:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            if present is not None and name.upper() not in present:
                continue        # duch po innym gniezdzie, nie sprzet na magistrali
            entry = {"instance": name, "location": None, "address": None,
                     "container": None}
            try:
                with winreg.OpenKey(key, name) as sub:
                    for field, value in (("location", "LocationInformation"),
                                         ("address", "Address"),
                                         ("container", "ContainerID")):
                        try:
                            entry[field] = winreg.QueryValueEx(sub, value)[0]
                        except OSError:
                            pass
            except OSError:
                pass
            out.append(entry)
    # Sortujemy po Address: to numer portu, wiec kolejnosc jest powtarzalna
    # miedzy uruchomieniami, w przeciwienstwie do kolejnosci z EnumKey.
    out.sort(key=lambda e: (e["address"] if isinstance(e["address"], int) else 9999,
                            e["instance"]))
    return out


def list_panels(dll_path=None):
    """Wszystkie moduly, z doklejonymi danymi z rejestru."""
    panels = [PanelInfo(f) for f in ax206.find_all(dll_path)]
    reg = windows_instances()

    if len(panels) == 1 and len(reg) == 1:
        # Jedyny mozliwy przypadek, w ktorym polaczenie jest PEWNE.
        _apply(panels[0], reg[0], paired=True)
    elif panels and len(panels) == len(reg):
        # Parowanie po kolejnosci: libusb wg enumeracji, rejestr wg numeru portu.
        # To ZALOZENIE — stad `paired=False` i ostrzezenie w --list.
        for panel, entry in zip(panels, reg):
            _apply(panel, entry, paired=False)
    elif panels:
        # Zostaje `location = None` na kazdym module, czyli selektor z konfiguracji
        # nie trafi w nic. To nie moze byc ciche: z zewnatrz wyglada jak "panel
        # zniknal z magistrali", a przyczyna siedzi w rejestrze.
        log().warning("rejestr: %d wpisow na %d modulow — nie wiem, ktory jest "
                      "ktory, wiec selektor `location` nie zadziala",
                      len(reg), len(panels))
    return panels


def _apply(panel, entry, paired):
    panel.instance = entry["instance"]
    panel.location = entry["location"]
    panel.address = entry["address"]
    panel.container = entry["container"]
    panel.paired = paired


class DeviceNotFound(ax206.AX206Error):
    """Wskazanego modulu nie ma. Klient ma czekac, nie brac innego."""


def select(panels, selector):
    """Wybiera modul wg selektora z konfiguracji. Zwraca PanelInfo.

    Kolejnosc rozstrzygania: location -> serial -> index. Brak selektora jest
    dozwolony TYLKO wtedy, gdy modul jest dokladnie jeden.
    """
    if not panels:
        raise DeviceNotFound("nie widze zadnego modulu %04x:%04x"
                             % (ax206.VID, ax206.PID))
    selector = selector or {}

    location = selector.get("location")
    if location:
        hits = [p for p in panels if p.location == location]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise DeviceNotFound(
                "nie ma modulu na porcie %s; widze: %s"
                % (location, ", ".join(p.describe() for p in panels)))
        raise DeviceNotFound("port %s wskazuje na %d modulow — uzyj index"
                             % (location, len(hits)))

    index = selector.get("index")
    if index is not None:
        hits = [p for p in panels if p.index == index]
        if not hits:
            # %r, nie %d: `index` przychodzi z panel.json i bywa napisem. Formatowanie
            # go przez %d rzucalo TypeError zamiast czytelnego DeviceNotFound.
            raise DeviceNotFound("nie ma modulu o indeksie %r (widze %d)"
                                 % (index, len(panels)))
        return hits[0]

    if len(panels) == 1:
        return panels[0]
    raise DeviceNotFound(
        "widze %d modulow i zaden nie jest wskazany w konfiguracji. Uruchom "
        "`python -m panel --list`, potem `--identify`, i wpisz wybrany port "
        "do panel.json jako {\"device\": {\"location\": \"...\"}}" % len(panels))


def finder_for(selector, dll_path=None, on_pick=None):
    """Zwraca funkcje bez argumentow, ktora AX206 wola przy kazdym otwarciu.

    Szukanie MUSI biec za kazdym razem od nowa: po `usb_reset` wskazniki libusb
    traca waznosc, a modul dostaje nowy wpis w enumeracji.
    """
    def finder():
        picked = select(list_panels(dll_path), selector)
        if on_pick:
            on_pick(picked)
        return picked.found
    return finder
