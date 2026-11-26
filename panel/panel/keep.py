"""Pure building blocks of keeping a process alive, shared by guard.py and tray.py.

Three processes watch each other (README, "Installation"):

    scheduled task -> guard.py (guardian) -> tray.py (supervisor + icon) -> run.pyw (client)

Each watcher does the same three things to the process under it: reads its exit code,
reads its heartbeat file, and restarts it on a ladder of delays. Those three live
here, with no Win32 and no clock of their own, so the tests can drive them second by
second. winjob.py has the Win32 half.
"""
import json
import os
import time

# Exit codes that carry INTENT. Zero is not one of them: it is what every ordinary
# clean exit produces — a `main()` that returned None, a bare sys.exit(), a branch
# that fell through — and reading it as "the owner chose Quit" would turn any such
# accident into a panel that stays off with the last numbers on the glass.
EXIT_QUIT = 75      # the supervisor was told to quit; it also says so in tray.state
EXIT_BUSY = 76      # the supervisor found tray.lock held and backed off
# The client, asked to stop (STOP_REQUEST), did not get a stopped frame onto every
# screen: it had no data to draw one from, or a screen refused it (App.paint_stopped).
# The supervisor then puts up its own card on every screen instead, over any screen
# that did take the frame. A stop whose frame every screen took exits 0.
EXIT_STOPPED_BLANK = 77
# The exit code a job kill leaves on its processes (winjob.Child.kill). Any value
# works — the watcher knows it killed — but a recognizable one reads better in a log
# than "1". Defined here, where the watchers' pure logic can read it without the
# Win32 half (supervisor.describe_exit), and re-exported by winjob.
KILLED_EXIT = 0xDEAD

# Files in config.OUTDIR (%LOCALAPPDATA%\claude-usage-monitor). The launchers are the
# few lines install-task.ps1 writes there, each runpy-executing one file of the repo.
GUARD_LAUNCHER = "panel-guard.pyw"
TRAY_LAUNCHER = "panel-tray.pyw"
CLIENT_LAUNCHER = "panel-run.pyw"
CLIENT_HEARTBEAT = "panel.heartbeat"
# Written by the supervisor on Stop/Quit. The client looks for it in every
# app.STOP_POLL_SEC slice of its wait (App._wait), then at the top of its loop (App.run)
# removes it, draws its last frame as a stopped one (App.stopped_screen) and exits.
STOP_REQUEST = "panel.stop"
TRAY_HEARTBEAT = "tray.heartbeat"
TRAY_STATE = "tray.state"
TRAY_COMMAND = "tray.cmd"
TRAY_LOG = "panel-tray.log"
GUARD_LOG = "panel-guard.log"

# The guardian's epoch, passed to the supervisor in the environment (see tray.state).
EPOCH_ENV = "PANEL_GUARD_EPOCH"

# How long a step of a watcher's loop pauses after it raised. Without a pause an
# exception raised before the loop's own wait turns the loop into a busy spin that
# fills the log faster than it rotates.
ERROR_PAUSE_SEC = 5.0


def touch(path):
    """Set the file's mtime to now, creating it if needed. Raises OSError."""
    try:
        os.utime(path, None)
    except FileNotFoundError:
        with open(path, "a"):
            pass


def mtime_ns(path):
    """The file's mtime in ns, or None when there is no such file (or no access)."""
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class Toucher:
    """Writes a heartbeat at most once every `every_sec` seconds.

    "At most", because the file lives next to everything else in %LOCALAPPDATA% and a
    write per tick would be a write per second, forever; 5 s is a twenty-fourth of the
    120 s the watcher waits before it calls the process hung.
    """

    def __init__(self, path, every_sec=5.0, clock=time.monotonic, write=touch):
        self.path = path
        self.every_sec = every_sec
        self.clock = clock
        self.write = write
        self._at = None

    def beat(self, force=False):
        now = self.clock()
        if not force and self._at is not None and now - self._at < self.every_sec:
            return False
        self._at = now
        try:
            self.write(self.path)
        except OSError:
            # A heartbeat that cannot be written must not stop the thing it reports
            # on. The watcher will see the silence and act on it: that is correct.
            return False
        return True


class Beat:
    """Liveness of another process, read from its heartbeat file.

    A beat is a CHANGE of the file's mtime, observed against our own monotonic clock.
    The mtime is never compared with the wall clock: an NTP step or a manual clock
    change would then read as hours of silence, or as a beat from the future.

    The reference point is taken at `arm()`, when the process is started, so a file
    left behind by its predecessor does not count as a beat of the new one.
    """

    def __init__(self, path, hang_sec, grace_sec, gap_sec=15.0, read=mtime_ns):
        self.path = path
        self.hang_sec = hang_sec
        self.grace_sec = grace_sec
        self.gap_sec = gap_sec
        self.read = read
        self._seen = None
        self._since = None
        self._armed_at = None
        self._polled = None
        self.ever = False

    def arm(self, now):
        self._seen = self.read(self.path)
        self._since = now
        self._armed_at = now
        self._polled = now
        self.ever = False

    def check(self, now):
        """None while the process is alive; otherwise one sentence why it is not."""
        if self._armed_at is None:
            return None
        if now - self._polled > self.gap_sec:
            # The computer slept, or this process was not scheduled for a while: our
            # silence, not the other process's. Count from here instead.
            self._since = now
            if not self.ever:
                self._armed_at = now
        self._polled = now
        seen = self.read(self.path)
        if seen is not None and seen != self._seen:
            self._seen = seen
            self._since = now
            self.ever = True
            return None
        if self.ever:
            if now - self._since >= self.hang_sec:
                return "no heartbeat for %.0f s" % (now - self._since)
        elif now - self._armed_at >= self.grace_sec:
            return "no heartbeat %.0f s after start" % (now - self._armed_at)
        return None


class Backoff:
    """The ladder of delays before a restart.

    `failures` counts consecutive failures since the last healthy run — a run that
    lasted `healthy_sec` puts the ladder back on its first rung. The watchers read
    `failures` to draw their "restarting" card once per series (`== LOOP_FAILURES`).
    """

    STEPS = (2.0, 5.0, 10.0, 30.0, 60.0)

    def __init__(self, steps=STEPS, healthy_sec=300.0):
        self.steps = tuple(steps)
        self.healthy_sec = healthy_sec
        self.failures = 0
        self._started_at = None

    def started(self, now):
        self._started_at = now

    def failed(self, now):
        """Records one failure; returns the delay before the next attempt."""
        if self._started_at is not None and now - self._started_at >= self.healthy_sec:
            self.failures = 0
        self._started_at = None
        delay = self.steps[min(self.failures, len(self.steps) - 1)]
        self.failures += 1
        return delay

    def reset(self):
        self.failures = 0
        self._started_at = None


class Errors:
    """Logs a watcher's loop exceptions: the first of a kind in full, repeats counted.

    The same exception every 5 s, each with its traceback, would push everything
    else out of a 2 MB x 3 log within hours — including the lines that say what
    started it.
    """

    def __init__(self, logger, every_sec=600.0, clock=time.monotonic):
        self.logger = logger
        self.every_sec = every_sec
        self.clock = clock
        self._key = None
        self._repeats = 0
        self._reported_at = None

    def report(self, exc):
        now = self.clock()
        key = (type(exc).__name__, str(exc))
        if key != self._key:
            if self._repeats:
                self.logger.warning("the previous error repeated %d more time(s)",
                                    self._repeats)
            self._key = key
            self._repeats = 0
            self._reported_at = now
            self.logger.error("loop step failed",
                              exc_info=(type(exc), exc, exc.__traceback__))
            return
        self._repeats += 1
        if now - self._reported_at >= self.every_sec:
            self.logger.warning("still failing: %s: %s (%d more time(s))",
                                key[0], key[1], self._repeats)
            self._repeats = 0
            self._reported_at = now


def run_loop(step, done, errors, period=1.0, pause=ERROR_PAUSE_SEC):
    """Calls `step()` every `period` seconds until `done` (a threading.Event) is set.

    `step` may return a number: the wait before the next call, for a step that knows
    better (the guardian waits on a process handle instead). An exception never ends
    the loop; it is reported and followed by `pause`.
    """
    while not done.is_set():
        try:
            delay = step()
            if delay is None:
                delay = period
        except Exception as exc:        # MemoryError included: it is an Exception
            try:
                errors.report(exc)
            except Exception:
                pass
            delay = pause
        if delay > 0:
            done.wait(delay)


# -- tray.state ------------------------------------------------------------
#
# What the owner asked for, kept across a restart of the supervisor. Scoped by the
# guardian's epoch: a supervisor restarted by the SAME guardian keeps "stopped"; a new
# guardian (a logon, Start-ScheduledTask) is a new epoch, and the file from before no
# longer applies.

WANTS = ("running", "stopped", "quit")


def read_state(path):
    """(epoch, want, problem). A missing file is (None, None, None), not a problem."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, None, None
    except (OSError, ValueError) as e:
        return None, None, "%s unreadable: %s" % (path, e)
    if not isinstance(data, dict):
        return None, None, "%s is not an object" % path
    epoch, want = data.get("epoch"), data.get("want")
    if not isinstance(epoch, str) or want not in WANTS:
        return None, None, "%s has no valid epoch/want" % path
    return epoch, want, None


def write_state(path, epoch, want):
    """Atomically: a half-written file must never read as another wish."""
    if want not in WANTS:
        raise ValueError("unknown want %r" % (want,))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"epoch": epoch, "want": want}, f)
    os.replace(tmp, path)


def wanted(path, epoch):
    """(want, problem) for THIS epoch: another epoch's wish is no wish at all."""
    if not epoch:
        return None, None
    got_epoch, want, problem = read_state(path)
    if problem:
        return None, problem
    if got_epoch != epoch:
        return None, None
    return want, None


class StateStore:
    """tray.state for one epoch. Without an epoch — a supervisor started by hand, not
    by a guardian — it reads nothing and writes nothing: there is nobody to restart
    that supervisor, so there is nothing to carry across a restart."""

    def __init__(self, path, epoch):
        self.path = path
        self.epoch = epoch or None

    def want(self):
        return wanted(self.path, self.epoch)

    def save(self, want):
        if self.epoch:
            write_state(self.path, self.epoch, want)
