"""Ktory modul AX206 ma obsluzyc ten klient.

Docelowo na magistrali sa DWA takie same wyswietlacze: jeden pod innym programem, drugi
pod monitor limitow. Rozroznia je WYLACZNIE to, gdzie siedza — i to nie jest
wybor projektowy, tylko jedyna mozliwosc. Zmierzone na tym egzemplarzu:

  * oba zglaszaja sie jako VID_1908&PID_0102,
  * oba maja seryjny "WCH32" — stala firmware'u, nie numer egzemplarza,
  * Windows wyprowadza z tego seryjnego ID instancji (`USB\\VID_1908&PID_0102
    \\WCH32`) ORAZ ContainerID (UUID v5, nazwowy), wiec obie te wartosci beda
    dla dwoch modulow IDENTYCZNE. Jako selektory sa bezuzyteczne — nie dlatego,
    ze powtarzaja informacje o porcie, tylko dlatego, ze nie odrozniaja niczego.

Kluczem jest wiec LANCUCH PORTOW z `libusb_get_port_numbers()`, np. "3.4" —
port 3 kontrolera, port 4 huba na tym porcie. Trzy rzeczy, ktore o nim wiedziec:

  1. Nie zawiera zadnego licznika enumeracji. Poprzednia wersja tego pliku brala
     `LocationInformation` z rejestru ("Port_#0004.Hub_#0005"), gdzie `Hub_#NNNN`
     jest indeksem instancji huba nadawanym przy wykrywaniu. Ten indeks
     przeskoczyl przy niezmienionej wtyczce i klient przestal widziec swoj modul.
  2. Numer magistrali (`bus`) do klucza NIE wchodzi — jest syntetycznym indeksem
     kontrolera, czyli tej samej natury co `Hub_#`. Jest drukowany w `--list`
     jako diagnostyka. Skutek uboczny: dwa moduly moglyby dac ten sam lancuch,
     gdyby siedzialy na tym samym numerze portu DWOCH ROZNYCH kontrolerow.
     Ten przypadek konczy sie bledem, nigdy wyborem na chybil trafil.
  3. Fizyczne przelozenie wtyczki zmienia klucz. To nieuniknione przy
     identyfikacji po topologii i dlatego `--list` wypisuje gotowa linie do
     wklejenia.

ZASADA: klient nigdy nie siega po "pierwszy wolny". Przejecie cudzego panelu
w chwili, gdy tamten program akurat sie restartuje, byloby awaria cicha i trudna do
powiazania z przyczyna — wiec brak wskazanego modulu konczy sie czekaniem,
a nie podmiana celu.

STAN WERYFIKACJI: lancuch portow jest zmierzony i zgodny z topologia z rejestru
(`DEVPKEY_Device_LocationPaths` = ...#USB(3)#USB(4) wobec ports=(3,4)), stabilny
przez reset urzadzenia mimo skaczacego adresu USB. Sprawdzone dla JEDNEGO
modulu naraz. Roznica wobec poprzedniej wersji
jest jednak jakosciowa: nie ma juz czego z czym parowac, bo lancuch przychodzi
z tej samej enumeracji, co otwierany uchwyt.
"""
from . import ax206


class PanelInfo:
    """Modul widziany przez libusb. Komplet danych z jednej enumeracji."""

    __slots__ = ("index", "bus", "ports", "port_path", "address", "found")

    def __init__(self, found):
        # `found` pochodzi z TEJ enumeracji i tylko z niej — wskaznik traci
        # waznosc po resecie, wiec nie wolno go przechowywac dluzej.
        self.found = found
        self.index = found.index
        self.bus = found.bus
        self.ports = found.ports
        self.port_path = found.port_path
        self.address = found.address

    def describe(self):
        return "#%d  port_path=%s  bus=%s  address=%s" % (
            self.index, self.port_path or "?", self.bus, self.address)

    def __repr__(self):
        return "<PanelInfo %s>" % self.describe()


def list_panels(dll_path=None):
    """Wszystkie moduly widziane przez libusb.

    UWAGA na cykl zycia: kazdy `PanelInfo` trzyma referencje do urzadzenia
    libusb. Kto nie otwiera modulu, ten oddaje referencje przez `release()`
    — inaczej co otwarcie panelu zostaje jedna niezwolniona.
    """
    return [PanelInfo(f) for f in ax206.find_all(dll_path)]


def release(panels, keep=None):
    """Oddaje referencje wszystkich modulow poza `keep` (PanelInfo albo None)."""
    for p in panels:
        if p is not keep:
            p.found.release()


class DeviceNotFound(ax206.AX206Error):
    """Wskazanego modulu nie ma. Klient ma czekac, nie brac innego."""


def select(panels, selector):
    """Wybiera modul wg selektora z konfiguracji. Zwraca PanelInfo.

    Kolejnosc rozstrzygania: port_path -> index. Brak selektora jest dozwolony
    TYLKO wtedy, gdy modul jest dokladnie jeden.
    """
    if not panels:
        raise DeviceNotFound("nie widze zadnego modulu %04x:%04x"
                             % (ax206.VID, ax206.PID))
    selector = selector or {}

    port_path = selector.get("port_path")
    if port_path:
        hits = [p for p in panels if p.port_path == port_path]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise DeviceNotFound(
                "nie ma modulu o port_path=%s; widze: %s"
                % (port_path, ", ".join(p.describe() for p in panels)))
        # Mozliwe tylko przy dwoch kontrolerach USB o tym samym numerze portu.
        # Wybor "pierwszego z brzegu" bylby tu cicha podmiana celu.
        raise DeviceNotFound(
            "port_path=%s wskazuje na %d moduly (rozne magistrale: %s) — "
            "rozstrzygnij przez index"
            % (port_path, len(hits), ", ".join(str(p.bus) for p in hits)))

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
        "`python -m panel --list`, potem `--identify`, i wpisz wybrany lancuch "
        "portow do panel.json jako {\"device\": {\"port_path\": \"...\"}}"
        % len(panels))


def finder_for(selector, dll_path=None, on_pick=None):
    """Zwraca funkcje bez argumentow, ktora AX206 wola przy kazdym otwarciu.

    Szukanie MUSI biec za kazdym razem od nowa: po resecie wskazniki libusb traca
    waznosc, a modul dostaje nowy adres na magistrali.

    Referencje modulow odrzuconych oddajemy TU, w kazdym wyjsciu — takze gdy
    `select()` rzuca. Panel bywa zajety przez inny program godzinami, a klient probuje
    co 30 s; bez tego `finally` kazda nieudana proba zostawialaby po sobie
    niezwolnione urzadzenie.
    """
    def finder():
        panels = list_panels(dll_path)
        picked = None
        try:
            picked = select(panels, selector)
        finally:
            release(panels, keep=picked)
        if on_pick:
            on_pick(picked)
        return picked.found
    return finder
