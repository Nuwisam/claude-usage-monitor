"""Device selection: what is allowed, what is not, and what has to be loud.

This whole file guards one rule from the header of device.py: the client never
reaches for "the first one free". Every case in which the indication is uncertain
has to end in DeviceNotFound — because a silent swap of the target (another program
taking the screen over at the moment this one restarts) is a failure that cannot be
tied to its cause from the outside.

No hardware: we substitute dummies for the devices.
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


# --- port chain -------------------------------------------------------------


@pytest.mark.parametrize("ports,want", [
    ((3, 4), "3.4"),
    ((1,), "1"),
    ((3, 4, 2, 1), "3.4.2.1"),
    ((), ""),                    # a device at the root of the bus
])
def test_format_port_path(ports, want):
    assert ax206.format_port_path(ports) == want


def test_port_path_nie_zawiera_magistrali():
    """The bus number is a synthetic index of the controller — the same nature as
    the `Hub_#` that wrecked the previous selector. It does not go into the key."""
    t = targets(((3, 4), 7))[0]
    assert t.port_path == "3.4"
    assert "7" not in t.port_path


# --- selection --------------------------------------------------------------


def test_wybor_po_port_path():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    assert device.select(ts, {"port_path": "3.2"}).index == 1


def test_brak_wskazanego_port_path_to_blad_nie_podmiana():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    with pytest.raises(DeviceNotFound) as e:
        device.select(ts, {"port_path": "5.1"})
    # The message has to list what is visible — without that the only diagnosis is "absent".
    assert "3.4" in str(e.value) and "3.2" in str(e.value)


def test_ten_sam_port_path_na_dwoch_magistralach_nie_wybiera_zadnego():
    """Possible with two USB controllers. One of those screens may belong to
    another program — so neither of them gets picked."""
    ts = targets(((3, 4), 1), ((3, 4), 2))
    with pytest.raises(DeviceNotFound) as e:
        device.select(ts, {"port_path": "3.4"})
    assert "index" in str(e.value)


def test_wybor_po_indeksie():
    ts = targets(((3, 4), 1), ((3, 2), 1))
    assert device.select(ts, {"index": 1}).port_path == "3.2"


def test_index_jako_napis_daje_czytelny_blad():
    # panel.json is sometimes edited by hand; "1" instead of 1 has to give DeviceNotFound,
    # not a TypeError out of formatting the message.
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
    # The message has to lead to the NEW shape of the file, otherwise the instruction
    # would steer straight into the "device and panels at once" error.
    assert "port_path" in str(e.value) and "panels" in str(e.value)


def test_pusta_lista():
    with pytest.raises(DeviceNotFound):
        device.select([], {"port_path": "3.4"})


def test_index_liczy_sie_w_obrebie_sterownika():
    """Two drivers have their own numbering, so {"index": 0} means what it always
    means — the first screen of THAT driver, not the first one on the desk."""
    ts = targets(((3, 4), 1)) + targets(((8, 4), 1), backend="turing-a")
    assert [t.id for t in ts] == ["ax206#0", "turing-a#0"]


# --- references -------------------------------------------------------------


def test_release_oddaje_wszystkie_poza_zatrzymanym():
    ts = targets(((3, 4), 1), ((3, 2), 1), ((3, 1), 1))
    device.release(ts, keep=ts[1])
    assert [t.handle.releases for t in ts] == [1, 0, 1]


def test_release_bez_uchwytu_nie_wybucha():
    """Not every driver holds a reference to hand back — a serial port has none.
    The shared code must not break on that."""
    device.release([Target(backend="x", index=0, port_path="1")])


# --- configuration ----------------------------------------------------------


def test_stary_location_jest_bledem_z_instrukcja():
    """A silent migration is out: it would take guessing which screen the author had
    in mind. One readable sentence in the log, not a traceback under pythonw."""
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
