"""Wybor modulu: co wolno, czego nie wolno i co ma byc glosne.

Caly ten plik pilnuje jednej zasady z naglowka device.py: klient nigdy nie
siega po "pierwszy wolny". Kazdy przypadek, w ktorym wskazanie jest niepewne,
ma konczyc sie DeviceNotFound — bo cicha podmiana celu (przejecie panelu
inny program w chwili, gdy ten sie restartuje) jest awaria, ktorej z zewnatrz nie da
sie powiazac z przyczyna.

Bez sprzetu: podstawiamy atrapy zamiast urzadzen libusb.
"""
import pytest

from panel import ax206, config as C, device


class FakeFound:
    """Atrapa `ax206.Found`. Liczy oddania referencji, bo to jest ta czesc
    kontraktu, ktora po przejsciu na libusb-1.0 spoczywa na nas."""

    def __init__(self, index, ports, bus=1, address=10):
        self.index = index
        self.ports = ports
        self.bus = bus
        self.address = address
        self.iserial = 3
        self.ptr = object()
        self.releases = 0

    @property
    def port_path(self):
        return ax206.format_port_path(self.ports)

    def release(self):
        self.releases += 1
        self.ptr = None


def panels(*specs):
    return [device.PanelInfo(FakeFound(i, ports, bus=bus))
            for i, (ports, bus) in enumerate(specs)]


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
    p = device.PanelInfo(FakeFound(0, (3, 4), bus=7))
    assert p.port_path == "3.4"
    assert "7" not in p.port_path


# --- wybor ------------------------------------------------------------------


def test_wybor_po_port_path():
    ps = panels(((3, 4), 1), ((3, 2), 1))
    assert device.select(ps, {"port_path": "3.2"}).index == 1


def test_brak_wskazanego_port_path_to_blad_nie_podmiana():
    ps = panels(((3, 4), 1), ((3, 2), 1))
    with pytest.raises(device.DeviceNotFound) as e:
        device.select(ps, {"port_path": "5.1"})
    # Komunikat ma wyliczyc, co widac — bez tego jedyna diagnoza jest "nie ma".
    assert "3.4" in str(e.value) and "3.2" in str(e.value)


def test_ten_sam_port_path_na_dwoch_magistralach_nie_wybiera_zadnego():
    """Mozliwe przy dwoch kontrolerach USB. Jeden z tych modulow moze nalezec
    do innego programu — wiec zaden nie zostaje wybrany."""
    ps = panels(((3, 4), 1), ((3, 4), 2))
    with pytest.raises(device.DeviceNotFound) as e:
        device.select(ps, {"port_path": "3.4"})
    assert "index" in str(e.value)


def test_wybor_po_indeksie():
    ps = panels(((3, 4), 1), ((3, 2), 1))
    assert device.select(ps, {"index": 1}).port_path == "3.2"


def test_index_jako_napis_daje_czytelny_blad():
    # panel.json bywa edytowany recznie; "1" zamiast 1 ma dac DeviceNotFound,
    # nie TypeError z formatowania komunikatu.
    ps = panels(((3, 4), 1))
    with pytest.raises(device.DeviceNotFound):
        device.select(ps, {"index": "1"})


def test_brak_selektora_przy_jednym_module():
    ps = panels(((3, 4), 1))
    assert device.select(ps, None).index == 0


def test_brak_selektora_przy_dwoch_modulach_to_blad():
    ps = panels(((3, 4), 1), ((3, 2), 1))
    with pytest.raises(device.DeviceNotFound) as e:
        device.select(ps, None)
    assert "port_path" in str(e.value)


def test_pusta_lista():
    with pytest.raises(device.DeviceNotFound):
        device.select([], {"port_path": "3.4"})


# --- referencje -------------------------------------------------------------


def test_release_oddaje_wszystkie_poza_zatrzymanym():
    ps = panels(((3, 4), 1), ((3, 2), 1), ((3, 1), 1))
    device.release(ps, keep=ps[1])
    assert [p.found.releases for p in ps] == [1, 0, 1]


def test_finder_oddaje_referencje_takze_gdy_wybor_sie_nie_udal(monkeypatch):
    """Panel bywa zajety przez inny program godzinami, a klient probuje co 30 s.
    Gdyby nieudana proba zostawiala referencje, wyciek rosl by przez caly ten
    czas — i to w sciezce, ktora z zalozenia biegnie w kolko."""
    made = []

    def fake_list(dll_path=None):
        ps = panels(((3, 4), 1), ((3, 2), 1))
        made.append(ps)
        return ps

    monkeypatch.setattr(device, "list_panels", fake_list)
    finder = device.finder_for({"port_path": "9.9"})
    with pytest.raises(device.DeviceNotFound):
        finder()
    assert [p.found.releases for p in made[0]] == [1, 1]


def test_finder_zostawia_referencje_wybranego(monkeypatch):
    monkeypatch.setattr(device, "list_panels",
                        lambda dll_path=None: panels(((3, 4), 1), ((3, 2), 1)))
    picked = device.finder_for({"port_path": "3.4"})()
    assert picked.port_path == "3.4"
    assert picked.releases == 0


# --- konfiguracja -----------------------------------------------------------


def test_stary_location_jest_bledem_z_instrukcja():
    """Cicha migracja odpada: wymagalaby zgadywania, ktory modul autor mial na
    mysli. Ma byc jedno czytelne zdanie w logu, nie traceback pod pythonw."""
    cfg = C.Config({"stream_token": "t", "account_1": {"uuid": "u"},
                    "device": {"location": "Port_#0004.Hub_#0005"}})
    problems = cfg.validate()
    hits = [p for p in problems if "location" in p]
    assert len(hits) == 1
    assert "port_path" in hits[0] and "--list" in hits[0]


def test_port_path_przechodzi_walidacje():
    cfg = C.Config({"stream_token": "t", "account_1": {"uuid": "u"},
                    "device": {"port_path": "3.4"}})
    assert [p for p in cfg.validate() if "device" in p or "location" in p] == []


def test_device_nie_bedacy_obiektem():
    cfg = C.Config({"stream_token": "t", "account_1": {"uuid": "u"},
                    "device": "3.4"})
    assert any("port_path" in p for p in cfg.validate())
