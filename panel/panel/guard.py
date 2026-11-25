"""The guardian: the root of the scheduled task. It keeps the tray supervisor alive.

The top of the chain (keep.py). One process does not restart itself, so the process
that keeps the client alive (tray.py) needs one above it too — and this is the end of
that regress: a process that, on a healthy system, sleeps on a handle and looks at a
file every five seconds. No icon, no Pillow, no pystray, no renderer.

The task's lifetime IS this process's lifetime:
  * the supervisor exits with keep.EXIT_QUIT AND tray.state says "quit" for our epoch
    -> we exit 0, and the task is back to Ready until the next logon;
  * Stop-ScheduledTask kills us -> our job handle closes -> the supervisor and the
    client die with it (winjob.py);
  * anything else the supervisor does — any other exit code, 0 included, a kill, a
    silent heartbeat — is a failure, and it is started again on the ladder.

Every import is at the top of this file. The code is run from the repo, and the repo
can be on a network drive: an import in an error path would fail exactly when the
drive is gone, which is when the error path runs.
"""
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from . import config as C, fmt, keep, lock, log as logmod, winjob

HANG_SEC = 120.0
START_GRACE_SEC = 180.0         # the launcher alone may wait 120 s for the drive
HEALTHY_SEC = 300.0
LOOP_FAILURES = 5
CARD_LIMIT_SEC = 30.0
POLL_SEC = 5.0
PIDS_AFTER_SEC = 5.0

LOG = os.path.join(C.OUTDIR, keep.GUARD_LOG)


def _utc_now():
    return datetime.now(timezone.utc)


def _describe(code):
    if code == winjob.KILLED_EXIT:
        return "killed"
    if code is not None and code >= 0x80000000:
        return "0x%08X" % code
    return str(code)


class Guard:
    def __init__(self, spawn_supervisor, spawn_card, beat, quit_confirmed, logger,
                 clock=time.monotonic, wall=_utc_now, poll_sec=POLL_SEC):
        self.spawn_supervisor = spawn_supervisor
        self.spawn_card = spawn_card
        self.beat = beat
        self.quit_confirmed = quit_confirmed
        self.log = logger
        self.clock = clock
        self.wall = wall
        self.poll_sec = poll_sec
        self.backoff = keep.Backoff(healthy_sec=HEALTHY_SEC)
        # A held tray.lock gets a ladder of its own: it is not a failure of the
        # supervisor (another one is running), and must neither draw the
        # "failing" card nor push the real failures up the ladder.
        self.busy = keep.Backoff(healthy_sec=HEALTHY_SEC)
        self.child = None
        self.card = None            # (Child, deadline)
        self.next_start = clock()
        self.restarts = 0
        self.done = False
        self.exit_code = None
        self._started_at = None
        self._pids_logged = False

    def step(self):
        """One pass. Returns how long the loop may wait before the next one."""
        now = self.clock()
        self._reap_card(now)
        if self.child is None:
            if self.card is not None:
                return 1.0
            if now < self.next_start:
                return min(self.poll_sec, self.next_start - now)
            self._start(now)
            return 0.0
        # Waits ON THE PROCESS: an exit wakes us at once, a hang is looked for every
        # poll_sec.
        code = self.child.wait(self.poll_sec)
        now = self.clock()
        if code is None:
            reason = self.beat.check(now)
            if reason is None:
                self._periodic(now)
                return 0.0
            self.log.warning("supervisor %s; killing it", reason)
            self._close(kill=True)
            self._failed(now, reason)
            return 0.0
        self._close()
        if code == keep.EXIT_QUIT:
            if self.quit_confirmed():
                self.log.info("the owner chose Quit; the guardian ends")
                self.done = True
                self.exit_code = 0
                return 0.0
            self.log.warning("supervisor exited %d with no quit in tray.state; "
                             "treating it as a failure", code)
            self._failed(now, "exit %d without a quit" % code)
        elif code == keep.EXIT_BUSY:
            delay = self.busy.failed(now)
            self.log.warning("tray.lock is held by another supervisor; trying again "
                             "in %.0f s", delay)
            self.next_start = now + delay
        else:
            self.log.warning("supervisor exited: %s", _describe(code))
            self._failed(now, "exit %s" % _describe(code))
        return 0.0

    def _start(self, now):
        try:
            child = self.spawn_supervisor()
        except Exception as e:
            self.log.error("supervisor did not start: %s: %s", type(e).__name__, e)
            self._failed(now, "start failed: %s" % type(e).__name__)
            return
        self.child = child
        self.beat.arm(now)
        self.backoff.started(now)
        self.busy.started(now)
        self._started_at = now
        self._pids_logged = False
        self.log.info("supervisor started (pid %s)", getattr(child, "pid", "?"))

    def _failed(self, now, reason):
        delay = self.backoff.failed(now)
        self.restarts += 1
        self.next_start = now + delay
        self.log.warning("restarting the supervisor in %.0f s (failure %d in a row)",
                         delay, self.backoff.failures)
        if self.backoff.failures == LOOP_FAILURES:
            # The client dies with each supervisor, and nobody else draws: without
            # this card a supervisor that cannot start leaves the last numbers on the
            # glass for as long as it keeps failing. Once per series.
            self._show_card(["Panel supervisor failing",
                             "%d failures in a row, last: %s" % (LOOP_FAILURES, reason),
                             fmt.panel_clock(self.wall(), seconds=False)], now)

    def _periodic(self, now):
        if not self._pids_logged and now - self._started_at >= PIDS_AFTER_SEC:
            self._pids_logged = True
            try:
                self.log.info("supervisor job holds pids %s", self.child.pids())
            except OSError as e:
                self.log.warning("job query failed: %s", e)

    def _close(self, kill=False):
        child, self.child = self.child, None
        if child is None:
            return
        try:
            if kill and not child.kill(10.0):
                self.log.warning("supervisor job still not empty 10 s after the kill")
        finally:
            child.close()

    def _show_card(self, lines, now):
        try:
            self.card = (self.spawn_card(lines), now + CARD_LIMIT_SEC)
        except Exception as e:
            self.log.warning("card not started: %s: %s", type(e).__name__, e)

    def _reap_card(self, now):
        if self.card is None:
            return
        card, deadline = self.card
        code = card.poll()
        if code is None:
            if now < deadline:
                return
            card.kill(5.0)
            self.log.warning("card did not finish in %.0f s; killed", CARD_LIMIT_SEC)
        card.close()
        self.card = None

    def shutdown(self):
        self._close(kill=True)
        if self.card is not None:
            card = self.card[0]
            self.card = None
            try:
                card.kill(5.0)
            finally:
                card.close()


def _launcher(name):
    return os.path.join(C.OUTDIR, name)


def main():
    logger = logmod.setup(LOG, "INFO", console=False)
    try:
        held = lock.acquire(name="guard", timeout=20.0)
    except lock.AlreadyRunning as e:
        logger.warning("%s; exiting", e)
        return 0
    epoch = uuid.uuid4().hex
    # Inherited by the supervisor: its key to tray.state (keep.StateStore).
    os.environ[keep.EPOCH_ENV] = epoch
    logger.info("guardian started (pid %d, epoch %s)", os.getpid(), epoch[:8])

    def spawn_supervisor():
        # breakaway_ok: the tray menu opens the logs broken away from this job, so
        # a restart of the supervisor does not close the window being read.
        return winjob.spawn([sys.executable, _launcher(keep.TRAY_LAUNCHER)],
                            cwd=C.OUTDIR, breakaway_ok=True)

    def spawn_card(lines):
        return winjob.spawn([sys.executable, _launcher(keep.CLIENT_LAUNCHER), "--card"]
                            + list(lines), cwd=C.OUTDIR)

    state_path = os.path.join(C.OUTDIR, keep.TRAY_STATE)
    guard = Guard(
        spawn_supervisor=spawn_supervisor, spawn_card=spawn_card,
        beat=keep.Beat(os.path.join(C.OUTDIR, keep.TRAY_HEARTBEAT),
                       HANG_SEC, START_GRACE_SEC),
        quit_confirmed=lambda: keep.wanted(state_path, epoch)[0] == "quit",
        logger=logger)
    done = threading.Event()

    def step():
        delay = guard.step()
        if guard.done:
            done.set()
        return delay

    try:
        keep.run_loop(step, done, keep.Errors(logger))
    finally:
        guard.shutdown()
        held.close()
    return guard.exit_code if guard.exit_code is not None else 1
