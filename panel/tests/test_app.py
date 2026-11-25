"""The client loop — what it does with the SCREEN before it knows anything.

The panel holds its last frame with no host attached, so the image from the previous
run is the starting state of every start-up, not a blank slate. This file guards the
one rule that follows from it: until we have data or a certain reason to paint over
the screen, we do not touch it at all.
"""
from panel import app as app_mod, config as C


def cfg(**kw):
    d = {"stream_token": "t", "account_1": {"key": "a:o"}}
    d.update(kw)
    return C.Config(d)


def test_before_first_data_we_do_not_touch_the_screen():
    """Regression: `splash_after_sec` gated the full-screen status card only, while
    the bands reading "no data from server" went to the panel on the very first tick.
    Every restart therefore wiped the image from the previous run — exactly what
    this threshold was there to prevent."""
    a = app_mod.App(cfg())
    assert a.holding()
    assert a.tick() is None, "tick must not build or send a frame"


def test_first_data_lets_us_draw():
    a = app_mod.App(cfg())
    a.first_data_at = 1.0
    assert not a.holding()


def test_after_the_threshold_elapses_we_paint_the_status_card():
    """When no data arrives at all, it has to be said out loud in the end —
    a silent panel with hour-old numbers lies more than a message does."""
    a = app_mod.App(cfg())
    a.started -= a.cfg.splash_after_sec + 1
    assert not a.holding()
    assert a.screen().message, "after the threshold there must be a status card, not empty bands"


def test_unknown_event_is_a_no_op():
    """The whole backward compatibility of the stream rests on this: the server may add
    a frame this panel does not know, and it must neither move the model nor pass for
    fresh data. Until this test existed, the property was an accident of the
    implementation, not an agreement."""
    a = app_mod.App(cfg())
    a.on_event("something-we-dont-know", {"serverNow": "2026-08-05T21:07:00Z",
                                          "account": {"key": "a:o"}})
    assert a.accounts == {}
    assert a.link_state == "down"
    assert a.first_data_at is None
    assert a.clock.anchored, "the clock anchor rides on EVERY frame and that is deliberate"


def test_contract_mismatch_does_not_wait_for_the_threshold():
    """The one thing we know right away that invalidates the previous image:
    since we do not understand the frames, the old numbers on the glass are
    decoration and nothing more."""
    a = app_mod.App(cfg())
    a.contract_mismatch = 4
    assert not a.holding()
    assert a.screen().message


# -- run(): what the supervisor relies on ------------------------------------------


class FakeStream:
    """Stands in for stream.StreamClient: no network, alive until told otherwise."""
    alive = True

    def __init__(self, cfg, keys, q, stop):
        pass

    def start(self):
        pass

    def is_alive(self):
        return FakeStream.alive


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def looping_app(monkeypatch, ticks, heartbeat_path=None):
    """An App whose run() does `ticks` ticks of 1 s on a fake clock, then stops."""
    FakeStream.alive = True
    monkeypatch.setattr(app_mod.stream, "StreamClient", FakeStream)
    clock = Clock()
    a = app_mod.App(cfg(), monotonic=clock, heartbeat_path=heartbeat_path)
    a.stop.wait = lambda delay: None
    count = [0]

    def tick():
        count[0] += 1
        clock.t += 1.0
        if count[0] >= ticks:
            a.stop.set()

    a.tick = tick
    return a, count


def test_no_heartbeat_path_writes_nothing(monkeypatch):
    """The default is silence: a test that builds an App must never touch the file the
    live supervisor watches."""
    a, _ = looping_app(monkeypatch, 3)
    assert a.heartbeat is None
    a.run()


def test_the_heartbeat_follows_the_ticks_at_most_every_5_s(monkeypatch, tmp_path):
    writes = []
    a, _ = looping_app(monkeypatch, 12, heartbeat_path=str(tmp_path / "hb"))
    a.heartbeat.write = writes.append
    a.run()
    assert len(writes) == 3          # ticks at t=1, 6, 11


def test_an_unwritable_heartbeat_does_not_stop_the_loop(monkeypatch, tmp_path):
    def broken(path):
        raise PermissionError("denied")
    a, count = looping_app(monkeypatch, 8, heartbeat_path=str(tmp_path / "hb"))
    a.heartbeat.write = broken
    a.run()
    assert count[0] == 8


def test_a_dead_stream_thread_ends_the_run(monkeypatch):
    """A MemoryError in the SSE thread kills only that thread; the loop would go on
    drawing numbers that never move again. Ending the process hands it to the
    supervisor instead."""
    import pytest
    a, count = looping_app(monkeypatch, 100)
    real_tick = a.tick

    def tick():
        real_tick()
        if count[0] == 3:
            FakeStream.alive = False

    a.tick = tick
    with pytest.raises(app_mod.StreamDied):
        a.run()
    assert count[0] == 3
