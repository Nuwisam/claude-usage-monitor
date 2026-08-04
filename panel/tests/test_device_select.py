"""Wybor urzadzenia: co wolno, czego nie wolno i co ma byc glosne.

Caly ten plik pilnuje jednej zasady z naglowka device.py: klient nigdy nie
siega po "pierwszy wolny". Kazdy przypadek, w ktorym wskazanie jest niepewne,
ma konczyc sie DeviceNotFound — bo cicha podmiana celu (przejecie ekranu przez
inny program w chwili, gdy ten sie restartuje) jest awaria, ktorej z zewnatrz nie
da sie powiazac z przyczyna.

Bez sprzetu: podstawiamy atrapy zamiast urzadzen.
"""
import pytest

from panel import config as C, device
from panel.drivers import ax206
from panel.drivers.base import DeviceNotFound, Target


class FakeHandle:
    """Counts how many times its reference was handed back. That part of the
    contract moved onto us when the client switched to libusb-1.0, and a leak here
    grows all day: a busy screen is retried every few seconds."""

    def __init__(self):
        self.releases = 0

    def release(self):
        self.releases += 1


def targets(*specs, backend="ax206"):
    out = []
    for i, (ports, bus) in enumerate(specs):
        out.append(Target(backend=backend, index=i,
                          port_path=ax206.format_port_path(ports),
                          handle=FakeHandle(), bus=bus, address=10 + i))
    return out


# --- lancuch portow ---------------------------------------------------------


@pytest.mark.parametrize("ports,want", [
    ((3, 4), "3.4"),
    ((1,), "1"),
    ((3, 4, 2, 1), "3.4.2.1"),
    ((), ""),                    # urzadzenie na korzeniu magistrali
])
def test_format_port_path(ports, want):
    assert ax206.format_port_path(ports) == want


def test_port_path_nie_zawiera_magistrali():
    """Numer magistrali jest syntetycznym indeksem kontrolera — tej samej natury
    co `Hub_#`, ktory wywalil poprzedni selektor. Do klucza nie wchodzi."""
    t = targets(((3, 4), 7))[0]
    assert t.port_path == "3.4"
    assert "7" not in t.port_path


# --- wybor ------------------------------------------------------------------


def test_wybor_po_port_path():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    assert device.select(ts, {"port_path": "3.2"}).index == 1


def test_brak_wskazanego_port_path_to_blad_nie_podmiana():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    with pytest.raises(DeviceNotFound) as e:
        device.select(ts, {"port_path": "5.1"})
    # Komunikat ma wyliczyc, co widac — bez tego jedyna diagnoza jest "nie ma".
    assert "3.4" in str(e.value) and "3.2" in str(e.value)


def test_ten_sam_port_path_na_dwoch_magistralach_nie_wybiera_zadnego():
    """Mozliwe przy dwoch kontrolerach USB. Jeden z tych ekranow moze nalezec
    do innego programu — wiec zaden nie zostaje wybrany."""
    ts = targets(((3, 4), 1), ((3, 4), 2))
    with pytest.raises(DeviceNotFound) as e:
        device.select(ts, {"port_path": "3.4"})
    assert "index" in str(e.value)


def test_wybor_po_indeksie():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    assert device.select(ts, {"index": 1}).port_path == "3.2"


def test_index_jako_napis_daje_czytelny_blad():
    # panel.json bywa edytowany recznie; "1" zamiast 1 ma dac DeviceNotFound,
    # nie TypeError z formatowania komunikatu.
    ts = targets(((3, 4), 1))
    with pytest.raises(DeviceNotFound):
        device.select(ts, {"index": "1"})


def test_brak_selektora_przy_jednym_urzadzeniu():
    ts = targets(((3, 4), 1))
    assert device.select(ts, None).index == 0


def test_brak_selektora_przy_dwoch_urzadzeniach_to_blad():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    with pytest.raises(DeviceNotFound) as e:
        device.select(ts, None)
    # Komunikat ma prowadzic do NOWEGO ksztaltu pliku, inaczej instrukcja
    # kierowalaby wprost w blad "device i panels naraz".
    assert "port_path" in str(e.value) and "panels" in str(e.value)


def test_pusta_lista():
    with pytest.raises(DeviceNotFound):
        device.select([], {"port_path": "3.4"})


def test_index_liczy_sie_w_obrebie_sterownika():
    """Dwa sterowniki maja wlasne numeracje, wiec {"index": 0} znaczy to samo co
    zawsze — pierwszy ekran TEGO sterownika, nie pierwszy na biurku."""
    ts = targets(((3, 4), 1)) + targets(((8, 4), 1), backend="turing-a")
    assert [t.id for t in ts] == ["ax206#0", "turing-a#0"]


# --- referencje -------------------------------------------------------------


def test_release_oddaje_wszystkie_poza_zatrzymanym():
    ts = targets(((3, 4), 1), ((3, 2), 1), ((3, 1), 1))
    device.release(ts, keep=ts[1])
    assert [t.handle.releases for t in ts] == [1, 0, 1]


def test_release_bez_uchwytu_nie_wybucha():
    """Nie kazdy sterownik trzyma referencje do oddania — port szeregowy jej nie
    ma. Wspolny kod nie moze na tym pekac."""
    device.release([Target(backend="x", index=0, port_path="1")])


# --- konfiguracja -----------------------------------------------------------


def test_stary_location_jest_bledem_z_instrukcja():
    """Cicha migracja odpada: wymagalaby zgadywania, ktory ekran autor mial na
    mysli. Ma byc jedno czytelne zdanie w logu, nie traceback pod pythonw."""
    cfg = C.Config({"stream_token": "t", "account_1": {"uuid": "u"},
                    "device": {"location": "Port_#0004.Hub_#0005"}})
    problems = cfg.validate()
    hits = [p for p in problems if "location" in p]
    assert hits, "brak zdania o location"
    assert any("port_path" in p and "--list" in p for p in hits)


def test_port_path_przechodzi_walidacje():
    cfg = C.Config({"stream_token": "t", "account_1": {"uuid": "u"},
                    "device": {"port_path": "3.4"}})
    assert [p for p in cfg.validate() if "device" in p or "location" in p] == []


def test_device_nie_bedacy_obiektem():
    cfg = C.Config({"stream_token": "t", "account_1": {"uuid": "u"},
                    "device": "3.4"})
    assert any("port_path" in p for p in cfg.validate())
