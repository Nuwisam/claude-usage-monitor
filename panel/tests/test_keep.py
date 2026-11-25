"""keep.py: the ladder, the heartbeat, tray.state, the loop that does not die."""
import threading

import pytest

from panel import keep


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class Mtime:
    """A heartbeat file whose mtime the test sets by hand."""

    def __init__(self, value=None):
        self.value = value

    def __call__(self, path):
        return self.value


# -- Backoff --------------------------------------------------------------------


def test_the_ladder_climbs_and_stays_on_top():
    b = keep.Backoff()
    delays = [b.failed(0.0) for _ in range(7)]
    assert delays == [2.0, 5.0, 10.0, 30.0, 60.0, 60.0, 60.0]
    assert b.failures == 7


def test_a_healthy_run_resets_the_ladder():
    b = keep.Backoff(healthy_sec=300.0)
    b.started(0.0)
    b.failed(10.0)
    b.started(20.0)
    assert b.failed(30.0) == 5.0
    b.started(40.0)
    assert b.failed(40.0 + 300.0) == 2.0
    assert b.failures == 1


def test_a_failure_without_a_start_does_not_count_as_healthy():
    b = keep.Backoff(healthy_sec=300.0)
    b.started(0.0)
    b.failed(1.0)
    assert b.failed(1000.0) == 5.0, "the start that failed was never healthy"


# -- Beat ---------------------------------------------------------------------------


def test_a_change_is_a_beat_and_silence_is_a_hang():
    m = Mtime(1)
    beat = keep.Beat("x", hang_sec=120, grace_sec=180, read=m)
    beat.arm(0.0)
    m.value = 2
    assert beat.check(5.0) is None and beat.ever
    for t in range(10, 125, 5):
        assert beat.check(float(t)) is None or t >= 125
    assert beat.check(125.0) == "no heartbeat for 120 s"


def test_the_predecessor_file_is_not_a_beat():
    m = Mtime(12345)            # left behind by the killed process
    beat = keep.Beat("x", hang_sec=120, grace_sec=180, read=m)
    beat.arm(0.0)
    for t in range(5, 180, 5):
        assert beat.check(float(t)) is None
    assert beat.check(180.0) == "no heartbeat 180 s after start"
    assert not beat.ever


def poll(beat, start, end, every=5.0):
    """Checks at the watchers' real cadence; the last answer."""
    t, got = start, None
    while t <= end:
        got = beat.check(t)
        t += every
    return got


def test_no_file_at_all_is_caught_by_the_grace():
    beat = keep.Beat("x", hang_sec=120, grace_sec=180, read=Mtime(None))
    beat.arm(0.0)
    assert poll(beat, 5.0, 175.0) is None
    assert beat.check(180.0) is not None


def test_a_gap_in_our_own_polling_is_not_the_other_side_hanging():
    m = Mtime(1)
    beat = keep.Beat("x", hang_sec=120, grace_sec=180, gap_sec=15, read=m)
    beat.arm(0.0)
    m.value = 2
    beat.check(5.0)
    # the computer slept for an hour
    assert beat.check(3605.0) is None
    assert poll(beat, 3610.0, 3605.0 + 115) is None
    assert beat.check(3605.0 + 120) is not None


def test_a_long_gap_during_the_grace_restarts_the_grace():
    beat = keep.Beat("x", hang_sec=120, grace_sec=180, gap_sec=15, read=Mtime(None))
    beat.arm(0.0)
    poll(beat, 5.0, 100.0)
    assert beat.check(1000.0) is None
    assert poll(beat, 1005.0, 1175.0) is None
    assert beat.check(1180.0) is not None


def test_an_mtime_that_goes_backwards_still_counts():
    """A clock step backwards: a different value is a beat, whichever way it moved."""
    m = Mtime(1000)
    beat = keep.Beat("x", hang_sec=120, grace_sec=180, read=m)
    beat.arm(0.0)
    m.value = 10
    assert beat.check(5.0) is None and beat.ever


def test_check_before_arm_is_quiet():
    assert keep.Beat("x", 1, 1, read=Mtime(None)).check(1e9) is None


# -- Toucher ------------------------------------------------------------------------


def test_toucher_writes_at_most_every_period(tmp_path):
    clock = Clock(0.0)
    writes = []
    t = keep.Toucher(str(tmp_path / "hb"), every_sec=5.0, clock=clock,
                     write=writes.append)
    for second in range(12):
        clock.t = float(second)
        t.beat()
    assert len(writes) == 3          # 0, 5, 10
    assert t.beat(force=True)
    assert len(writes) == 4


def test_toucher_survives_an_unwritable_file():
    def broken(path):
        raise PermissionError("denied")
    assert keep.Toucher("x", write=broken).beat() is False


def test_touch_creates_then_moves_the_mtime(tmp_path):
    path = str(tmp_path / "hb")
    keep.touch(path)
    first = keep.mtime_ns(path)
    import os
    os.utime(path, ns=(first - 10**9, first - 10**9))
    keep.touch(path)
    assert keep.mtime_ns(path) > first - 10**9
    assert keep.mtime_ns(str(tmp_path / "missing")) is None


# -- tray.state ----------------------------------------------------------------------


def test_state_round_trip_for_one_epoch(tmp_path):
    path = str(tmp_path / "tray.state")
    keep.write_state(path, "e1", "stopped")
    assert keep.wanted(path, "e1") == ("stopped", None)
    assert keep.wanted(path, "e2") == (None, None), "another guardian's wish is none"
    assert keep.wanted(path, None) == (None, None)


def test_a_missing_state_file_is_no_wish_and_no_problem(tmp_path):
    assert keep.wanted(str(tmp_path / "nope"), "e1") == (None, None)


@pytest.mark.parametrize("content", ["{", "[]", '{"epoch": "e1", "want": "dance"}',
                                     '{"want": "stopped"}'])
def test_a_broken_state_file_is_a_problem_not_a_wish(tmp_path, content):
    path = tmp_path / "tray.state"
    path.write_text(content, encoding="utf-8")
    want, problem = keep.wanted(str(path), "e1")
    assert want is None and problem


def test_writing_an_unknown_wish_is_refused(tmp_path):
    with pytest.raises(ValueError):
        keep.write_state(str(tmp_path / "s"), "e1", "maybe")


def test_the_store_without_an_epoch_writes_nothing(tmp_path):
    path = tmp_path / "tray.state"
    store = keep.StateStore(str(path), None)
    store.save("stopped")
    assert not path.exists()
    assert store.want() == (None, None)


# -- the loop -----------------------------------------------------------------------


class Logger:
    def __init__(self):
        self.lines = []

    def error(self, msg, *args, **kw):
        self.lines.append(("error", msg % args if args else msg))

    def warning(self, msg, *args, **kw):
        self.lines.append(("warning", msg % args if args else msg))


def test_the_loop_survives_exceptions_and_pauses_after_them():
    done = threading.Event()
    calls = []
    waits = []

    class Done:
        def is_set(self):
            return done.is_set()

        def wait(self, t):
            waits.append(t)

    def step():
        calls.append(1)
        if len(calls) == 4:
            done.set()
            return None
        raise MemoryError("no memory")

    log = Logger()
    keep.run_loop(step, Done(), keep.Errors(log, clock=lambda: 0.0), period=1.0,
                  pause=5.0)
    assert len(calls) == 4
    assert waits == [5.0, 5.0, 5.0, 1.0], "every failed step is followed by a pause"
    assert [level for level, _ in log.lines] == ["error"], "a repeat is counted, not logged"


def test_errors_reports_a_repeat_count_and_a_new_kind():
    t = [0.0]
    log = Logger()
    errors = keep.Errors(log, every_sec=600.0, clock=lambda: t[0])
    for _ in range(3):
        errors.report(OSError("disk"))
    t[0] = 700.0
    errors.report(OSError("disk"))
    errors.report(ValueError("other"))
    kinds = [line for _, line in log.lines]
    assert kinds[0] == "loop step failed"
    assert "still failing: OSError: disk (3 more time(s))" in kinds[1]
    assert kinds[-1] == "loop step failed"


def test_a_step_may_set_its_own_wait():
    done = threading.Event()
    waits = []

    class Done:
        def is_set(self):
            return done.is_set()

        def wait(self, t):
            waits.append(t)
            done.set()

    keep.run_loop(lambda: 0.25, Done(), keep.Errors(Logger()))
    assert waits == [0.25]
