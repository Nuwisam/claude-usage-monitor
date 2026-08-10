"""The client loop — what it does with the SCREEN before it knows anything.

The panel holds its last frame with no host attached, so the image from the previous
run is the starting state of every start-up, not a blank sheet. This file guards the
one rule that follows from it: until we have data or a certain reason to paint over
the screen, we do not touch it at all.
"""
from panel import app as app_mod, config as C


def cfg(**kw):
    d = {"stream_token": "t", "account_1": {"uuid": "a"}}
    d.update(kw)
    return C.Config(d)


def test_przed_pierwszymi_danymi_nie_dotykamy_ekranu():
    """Regression: `splash_after_sec` gated the full-screen status card only, while
    the bands reading "no data from server" went to the panel on the very first tick.
    Every restart therefore wiped the image from the previous run — exactly what
    this threshold was there to prevent."""
    a = app_mod.App(cfg())
    assert a.holding()
    assert a.tick() is None, "tick nie moze zbudowac ani wyslac klatki"


def test_pierwsze_dane_otwieraja_rysowanie():
    a = app_mod.App(cfg())
    a.first_data_at = 1.0
    assert not a.holding()


def test_po_uplywie_progu_malujemy_karte_stanu():
    """When no data arrives at all, it has to be said out loud in the end —
    a silent panel with hour-old numbers lies more than a message does."""
    a = app_mod.App(cfg())
    a.started -= a.cfg.splash_after_sec + 1
    assert not a.holding()
    assert a.screen().message, "po progu ma byc karta stanu, nie puste pasy"


def test_nieznane_zdarzenie_jest_no_opem():
    """The whole backward compatibility of the stream rests on this: the server may add
    a frame this panel does not know, and it must neither move the model nor pass for
    fresh data. Until this test existed, the property was an accident of the
    construction, not an agreement."""
    a = app_mod.App(cfg())
    a.on_event("cos-czego-nie-znamy", {"serverNow": "2026-08-05T21:07:00Z",
                                       "account": {"uuid": "a"}})
    assert a.accounts == {}
    assert a.link_state == "down"
    assert a.first_data_at is None
    assert a.clock.anchored, "kotwica zegara jedzie z KAZDEJ ramki i to jest celowe"


def test_niezgodny_kontrakt_nie_czeka_na_prog():
    """The one thing we know right away that invalidates the previous image:
    since we do not understand the frames, the old numbers on the glass are
    decoration and nothing more."""
    a = app_mod.App(cfg())
    a.contract_mismatch = 4
    assert not a.holding()
    assert a.screen().message
