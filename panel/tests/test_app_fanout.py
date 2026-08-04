"""One rendered frame, N screens. No hardware.

The point of the fan-out is that the desk degrades one screen at a time: a panel
held by another program must not stop the one next to it, and it must not be able
to make a run look successful either.
"""
from panel import app as app_mod, config as C


class FakeLink:
    def __init__(self, tag, ok=True):
        self.tag = tag
        self.ok = ok
        self.frames = []
        self.forced = []
        self.closed = 0

    def send(self, frame, force=False):
        self.frames.append(frame)
        self.forced.append(force)
        return self.ok

    def close(self):
        self.closed += 1


def cfg(**over):
    data = {"stream_token": "t", "account_1": {"uuid": "u"},
            "panels": [{"backend": "ax206", "port_path": "3.4"},
                       {"backend": "ax206", "index": 1}]}
    data.update(over)
    return C.Config(data)


def app_with(links, **over):
    app = app_mod.App(cfg(**over))
    app.panels = links
    app.first_data_at = 1.0                # past the "hold the previous image" gate
    return app


def test_construction_builds_one_link_per_entry_and_touches_no_hardware():
    app = app_mod.App(cfg())
    assert [l.spec.backend for l in app.panels] == ["ax206", "ax206"]
    assert all(l.dev is None for l in app.panels)


def test_each_link_carries_its_own_mounting_angle():
    """One frame, two screens, one of them upside down. The angle belongs to the
    panel, not to the render: the frame handed out is the same object for both."""
    app = app_mod.App(cfg(panels=[{"backend": "ax206", "port_path": "3.4"},
                                  {"backend": "ax206", "index": 1,
                                   "rotate": 180}]))
    assert [l.spec.rotate for l in app.panels] == [0, 180]


def test_one_frame_reaches_every_panel():
    a, b = FakeLink("a"), FakeLink("b")
    frame = app_with([a, b]).tick()
    assert a.frames == [frame] and b.frames == [frame]


def test_a_failing_panel_does_not_stop_the_others():
    dead, alive = FakeLink("dead", ok=False), FakeLink("alive")
    app_with([dead, alive]).tick()
    assert len(alive.frames) == 1


def test_holding_draws_on_nobody():
    """Before the first data the previous run's image is better than anything we
    could draw, on every screen alike."""
    a = FakeLink("a")
    app = app_mod.App(cfg())
    app.panels = [a]
    assert app.tick() is None and a.frames == []


def test_run_once_forces_every_panel_and_reports_each(monkeypatch):
    a, dead = FakeLink("a"), FakeLink("dead", ok=False)
    app = app_with([a, dead])

    class NoStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(app_mod.stream, "StreamClient", NoStream)
    got, frame, drew = app.run_once(wait_sec=0)
    assert got is True
    assert drew == [("a", True), ("dead", False)]
    assert a.forced == [True] and dead.forced == [True]
    assert a.closed == 1 and dead.closed == 1
