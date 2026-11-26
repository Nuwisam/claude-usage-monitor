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


def test_a_long_tick_keeps_the_heartbeat_going(monkeypatch, tmp_path):
    """A 200 s tick is longer than supervisor.HANG_SEC: the wait between ticks is cut
    into slices of at most HEARTBEAT_EVERY_SEC, with a beat after each."""
    writes, waits = [], []
    a, _ = looping_app(monkeypatch, 2, heartbeat_path=str(tmp_path / "hb"))
    a.cfg = cfg(tick_sec=200.0)
    a.heartbeat.write = writes.append

    def wait(delay):
        waits.append(delay)
        a.monotonic.t += delay

    a.stop.wait = wait
    a.run()
    assert waits and max(waits) <= app_mod.HEARTBEAT_EVERY_SEC
    assert len(writes) == 41         # t=1, every 5 s through the wait, t=201


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
    supervisor instead — when there is one (the guardian's epoch says so)."""
    import pytest
    monkeypatch.setenv(app_mod.keep.EPOCH_ENV, "e1")
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


def test_a_tray_started_by_hand_still_gets_the_dead_stream(monkeypatch, tmp_path):
    """A tray supervisor started by hand has no guardian and so no epoch, but it does
    restart its client: its fresh heartbeat and its tray.lock are what say so. A
    tray.state left by an earlier installed chain does not count against it."""
    import pytest
    monkeypatch.delenv(app_mod.keep.EPOCH_ENV, raising=False)
    monkeypatch.setattr(app_mod.C, "OUTDIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "is_free", lambda name: name != "tray")
    app_mod.keep.touch(str(tmp_path / app_mod.keep.TRAY_HEARTBEAT))
    app_mod.keep.write_state(str(tmp_path / app_mod.keep.TRAY_STATE), "old", "stopped")
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


def test_a_supervisor_that_quit_is_none_despite_its_fresh_heartbeat(
        monkeypatch, tmp_path):
    """After Quit the heartbeat stays fresh for a while, but tray.lock is free."""
    monkeypatch.delenv(app_mod.keep.EPOCH_ENV, raising=False)
    monkeypatch.setattr(app_mod.C, "OUTDIR", str(tmp_path))
    app_mod.keep.touch(str(tmp_path / app_mod.keep.TRAY_HEARTBEAT))
    monkeypatch.setattr(app_mod, "is_free", lambda name: True)
    assert not app_mod.supervised()
    monkeypatch.setattr(app_mod, "is_free", lambda name: name != "tray")
    assert app_mod.supervised()


def test_under_a_guardian_a_stopped_supervisor_is_none(monkeypatch, tmp_path):
    """A stopped supervisor beats and holds its lock, but starts nothing: under a
    guardian, tray.state says so."""
    monkeypatch.delenv(app_mod.keep.EPOCH_ENV, raising=False)
    monkeypatch.setattr(app_mod.C, "OUTDIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "is_free", lambda name: False)
    app_mod.keep.touch(str(tmp_path / app_mod.keep.TRAY_HEARTBEAT))
    state = str(tmp_path / app_mod.keep.TRAY_STATE)
    assert app_mod.supervised(), "no tray.state: no wish"
    for want in ("stopped", "quit"):
        app_mod.keep.write_state(state, "e1", want)
        assert not app_mod.supervised(), want
    app_mod.keep.write_state(state, "e1", "running")
    assert app_mod.supervised()


def test_without_a_supervisor_a_dead_stream_thread_does_not_end_the_run(
        monkeypatch, tmp_path):
    """The old scheduled task, a run by hand: nobody would restart the process, so it
    must not exit. The loop goes on, so the age on the glass keeps growing, and no new
    stream thread is started. A tray heartbeat left behind long ago is no supervisor."""
    import os
    monkeypatch.delenv(app_mod.keep.EPOCH_ENV, raising=False)
    monkeypatch.setattr(app_mod.C, "OUTDIR", str(tmp_path))
    stale = str(tmp_path / app_mod.keep.TRAY_HEARTBEAT)
    app_mod.keep.touch(stale)
    old = 1_000_000_000
    os.utime(stale, (old, old))
    starts = []

    class Stream(FakeStream):
        def start(self):
            starts.append(1)

    a, count = looping_app(monkeypatch, 6)
    monkeypatch.setattr(app_mod.stream, "StreamClient", Stream)
    real_tick = a.tick

    def tick():
        real_tick()
        if count[0] == 3:
            FakeStream.alive = False

    a.tick = tick
    a.run()
    assert count[0] == 6, "the loop went on"
    assert len(starts) == 1, "no replacement thread"


def test_a_stop_request_ends_the_run_with_the_stopped_frame(monkeypatch, tmp_path):
    """The supervisor's Stop: the request is read before the next tick, consumed, and
    run() returns whether a stopped frame went out — no ordinary frame in between."""
    stop = tmp_path / "panel.stop"
    a, count = looping_app(monkeypatch, 100)
    a.stop_path = str(stop)
    painted = []
    a.paint_stopped = lambda: painted.append(count[0]) or True
    real_tick = a.tick

    def tick():
        real_tick()
        if count[0] == 2:
            stop.write_text("stop")

    a.tick = tick
    assert a.run() is True
    assert painted == [2], "drawn after the 2nd tick, with no 3rd tick before it"
    assert not stop.exists(), "the request is consumed"


def test_a_stop_request_cuts_a_long_tick_short(monkeypatch, tmp_path):
    """A 200 s tick is longer than supervisor.STOP_GRACE_SEC: a request written during
    the wait is seen at the next slice, not when the tick runs out — otherwise the
    supervisor kills the client and the stopped frame is lost."""
    from panel import supervisor
    stop = tmp_path / "panel.stop"
    a, count = looping_app(monkeypatch, 100)
    a.cfg = cfg(tick_sec=200.0)
    a.stop_path = str(stop)
    painted, waits = [], []
    a.paint_stopped = lambda: painted.append(count[0]) or True

    def wait(delay):
        waits.append(delay)
        a.monotonic.t += delay
        if len(waits) == 3:
            stop.write_text("stop")

    a.stop.wait = wait
    assert a.run() is True
    assert painted == [1], "drawn in the first tick's wait, with no 2nd tick"
    assert len(waits) == 3 and sum(waits) < supervisor.STOP_GRACE_SEC
    assert not stop.exists(), "the request is consumed"


def test_a_dead_stream_thread_cuts_a_long_tick_short(monkeypatch):
    """With no ceiling on tick_sec, a stream thread that dies during a 3600 s wait
    must end the wait at the next slice: the run's dead-stream check then fires
    instead of an hour of heartbeats over numbers that stopped moving."""
    import pytest
    monkeypatch.setenv(app_mod.keep.EPOCH_ENV, "e1")
    a, count = looping_app(monkeypatch, 100)
    a.cfg = cfg(tick_sec=3600.0)
    waits = []

    def wait(delay):
        waits.append(delay)
        a.monotonic.t += delay
        if len(waits) == 3:
            FakeStream.alive = False

    a.stop.wait = wait
    with pytest.raises(app_mod.StreamDied):
        a.run()
    # The wait ends at the slice that sees the dead thread; the next pass ticks once
    # and its dead-stream check fires — seconds after the death, not an hour.
    assert count[0] == 2
    assert len(waits) == 3 and sum(waits) < 10


def _main_sees_stop(monkeypatch, tmp_path):
    """Runs app.main() with everything but the stop-request handling stubbed out;
    returns whether panel.stop still existed when App.run started."""
    from panel import log as logmod
    monkeypatch.setattr(app_mod.C, "OUTDIR", str(tmp_path))
    monkeypatch.setattr(app_mod.C, "load", lambda: cfg())
    monkeypatch.setattr(logmod, "setup", lambda *a, **k: None)

    class Held:
        def close(self):
            pass

    monkeypatch.setattr(app_mod, "single_instance", lambda: Held())
    seen = []

    class Stub:
        def __init__(self, cfg, heartbeat_path=None, stop_path=None):
            self.stop_path = stop_path

        def run(self):
            import os
            seen.append(os.path.exists(self.stop_path))

    monkeypatch.setattr(app_mod, "App", Stub)
    (tmp_path / app_mod.keep.STOP_REQUEST).write_text("stop")
    app_mod.main()
    return seen == [True]


def test_without_a_supervisor_a_leftover_stop_request_is_removed(monkeypatch, tmp_path):
    """A supervised client killed before it read its request leaves panel.stop behind.
    A client run by hand has no supervisor to clear it, and would otherwise end on its
    first tick."""
    monkeypatch.delenv(app_mod.keep.EPOCH_ENV, raising=False)
    assert not _main_sees_stop(monkeypatch, tmp_path)


def test_under_a_supervisor_a_stop_request_found_at_start_is_kept(monkeypatch, tmp_path):
    """The supervisor clears leftovers before it starts a client, so a request found
    at start is its Stop, written while this client was starting."""
    monkeypatch.setenv(app_mod.keep.EPOCH_ENV, "e1")
    assert _main_sees_stop(monkeypatch, tmp_path)


def test_no_stop_path_never_reads_a_file(monkeypatch, tmp_path):
    a, count = looping_app(monkeypatch, 3)
    assert a.stop_path is None
    assert a.run() is None and count[0] == 3
