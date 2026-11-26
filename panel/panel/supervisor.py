"""The supervisor's state machine: keeps the panel client alive and obeys the tray.

Pure logic. Every effect — starting a process, killing it, probing panel.lock,
reading a heartbeat, drawing a card — comes in through the constructor, and the
clock with it, so the tests can drive a day of crashes in a millisecond. tray.py
wires the real ones in (winjob.py, lock.py, keep.py) and runs `step()` once a second
on its own thread.

States:
  waiting   no client; one is started at `next_start` (after a crash, a restart
            command, a lock held by somebody else, a configuration error)
  running   a client is up; its exit code and heartbeat are watched
  stopped   the owner said Stop; nothing is started until Start or Restart
  quitting  the owner said Quit; once the card is out, `done` is set and the
            process exits with keep.EXIT_QUIT
"""
import collections
import queue
import time
from datetime import datetime, timezone

from . import fmt, keep

HANG_SEC = 120.0            # heartbeat silence that counts as a hang
START_GRACE_SEC = 180.0     # first heartbeat due by then; the launcher alone may wait
                            # 120 s for the network drive (install-task.ps1)
HEALTHY_SEC = 300.0         # a run this long puts the restart ladder back at the start
LOOP_FAILURES = 5           # the failure that draws the "restarting" card, once
LOCK_RETRY_SEC = 30.0       # how often a lock held by someone else is probed again
LOCK_GRACE_SEC = 20.0       # our own killed client may hold panel.lock this long
CONFIG_RETRY_SEC = 600.0    # a configuration error is retried this often at most
CARD_LIMIT_SEC = 30.0       # a card process is killed after this
STOP_GRACE_SEC = 10.0       # a client asked to stop gets this long to draw its last frame
MEMORY_EVERY_SEC = 3600.0   # how often the client's peak memory goes to the log
PIDS_AFTER_SEC = 5.0        # when the client's job membership goes to the log

COMMANDS = ("start", "stop", "restart", "quit")

# What the tray reads. A namedtuple, replaced whole on every change: the icon thread
# reads `status` while the supervisor thread writes it, and one reference swap is
# atomic where five attribute writes are not.
Status = collections.namedtuple("Status", "state since restarts last detail")


def _utc_now():
    return datetime.now(timezone.utc)


def describe_exit(code):
    """An exit code as a reader wants it: NTSTATUS values in hex, a job kill by name."""
    if code is None:
        return "unknown"
    if code == keep.KILLED_EXIT:
        return "killed"
    if code >= 0x80000000:
        return "0x%08X" % code
    return str(code)


class Supervisor:
    def __init__(self, spawn_client, spawn_card, lock_free, launcher_ok, beat,
                 config_mtime, store, logger, read_command=None,
                 clock=time.monotonic, wall=_utc_now, backoff=None,
                 request_stop=None, clear_stop=None):
        self.spawn_client = spawn_client
        # Writes keep.STOP_REQUEST for the client / removes a leftover one. None: the
        # client is never asked, it is killed and the card goes up (the old way).
        self.request_stop = request_stop
        self.clear_stop = clear_stop or (lambda: None)
        self.spawn_card = spawn_card
        self.lock_free = lock_free
        self.launcher_ok = launcher_ok
        self.beat = beat
        self.config_mtime = config_mtime
        self.store = store
        self.log = logger
        self.read_command = read_command
        self.clock = clock
        self.wall = wall
        self.backoff = backoff or keep.Backoff(healthy_sec=HEALTHY_SEC)

        self._commands = queue.Queue()
        self.state = None
        self.since = wall()
        self.child = None
        self.card = None            # (Child, deadline, title)
        self.next_start = None
        self.detail = None
        self.restarts = 0
        self.last = None
        self.done = False
        self.on_change = None
        self.status = None

        self._lock_grace_until = None
        self._config_wait = False
        self._config_seen = None
        self._logged_wait = None
        self._started_at = None
        self._pids_logged = False
        self._memory_at = None

    # -- the outside world talks to us through these ------------------------

    def command(self, name):
        """From any thread. Executed by the next `step()`, on the supervisor's thread:
        the icon thread must never wait for a process to die."""
        if name not in COMMANDS:
            raise ValueError("unknown command %r" % (name,))
        self._commands.put(name)

    def begin(self):
        """The first decision, from tray.state: what the owner wanted before we were
        (re)started by the guardian."""
        now = self.clock()
        # A supervisor started after a crash finds the previous client dying with
        # the previous job, and its panel.lock not yet released.
        self._lock_grace_until = now + LOCK_GRACE_SEC
        want, problem = self.store.want()
        if problem:
            self.log.warning("%s; starting the panel", problem)
        if want == "stopped":
            self.log.info("the owner stopped the panel before this supervisor started; "
                          "staying stopped")
            self._enter("stopped", "stopped by the owner")
        elif want == "quit":
            self.log.info("the owner chose Quit before this supervisor started; quitting")
            self._enter("quitting", None)
        else:
            self._enter("waiting", None)
            self.next_start = now
        self._publish()

    def step(self):
        now = self.clock()
        if self.read_command is not None:
            name = self.read_command()
            if name:
                self.log.info("command from tray.cmd: %s", name)
                self.command(name)
        while True:
            try:
                name = self._commands.get_nowait()
            except queue.Empty:
                break
            self._command(name, now)
        self._reap_card(now)
        if self.state == "running":
            self._watch(now)
        elif self.state == "waiting":
            self._maybe_start(now)
        elif self.state == "quitting" and self.card is None:
            self.done = True
        self._publish()

    def shutdown(self):
        """Ends whatever we started. For the exit path of the process."""
        self._end_child("supervisor exiting")
        self._end_card()

    # -- commands ----------------------------------------------------------

    def _command(self, name, now):
        if self.state == "quitting":
            return
        if name == "stop":
            if self.state == "stopped":
                return
            painted = self._stop_client("stop requested")
            self._save("stopped")
            self._enter("stopped", "stopped by the owner")
            self.log.info("panel stopped by the owner")
            if not painted:
                self._show_card(["Panel stopped", self._stamp(),
                                 "Start it again from the tray icon"], now)
        elif name in ("start", "restart"):
            if name == "start" and self.state == "running":
                return
            self._end_child("%s requested" % name)
            self._end_card()
            self.backoff.reset()
            self._config_wait = False
            self._save("running")
            self.log.info("panel %s by the owner", "restarted" if name == "restart"
                          else "started")
            self._enter("waiting", None)
            self.next_start = now
        elif name == "quit":
            painted = self._stop_client("quit requested")
            self._save("quit")
            self.log.info("quit by the owner")
            # From "stopped" the glass already holds the stopped frame or the card.
            if not painted and self.state != "stopped":
                self._show_card(["Panel stopped", self._stamp(),
                                 "It starts again at the next logon"], now)
            self._enter("quitting", None)

    def _stop_client(self, why):
        """Ends the client for Stop / Quit. True when it drew its own stopped frame.

        The client is ASKED first (keep.STOP_REQUEST): it holds the numbers on the
        glass, and its last frame keeps them, marked STOPPED, with nothing relative left
        to rot (App.stopped_screen). A full-screen card would throw that away. Only a
        client that does not answer in STOP_GRACE_SEC, or did not get its stopped frame
        onto every screen — no data yet, or a screen refused it
        (keep.EXIT_STOPPED_BLANK) — is killed, and then the caller draws the card.
        """
        child = self.child
        if child is None:
            return False
        if self.request_stop is None:
            self._end_child(why)
            return False
        try:
            self.request_stop()
        except OSError as e:
            self.log.warning("stop request not written (%s); killing the client", e)
            self._end_child(why)
            return False
        try:
            code = child.wait(STOP_GRACE_SEC)
        except OSError as e:
            # Escaping here would skip _save/_enter("stopped") while the client may
            # already have read panel.stop and exited 0, which _watch takes for the
            # lock back-off and starts the panel again at once.
            self.log.warning("waiting for the client failed (%s); killing it", e)
            self._end_child(why)
            self.clear_stop()
            return False
        if code == 0:
            self.log.info("client drew its stopped frame and exited (%s)", why)
            self._close_child(self.clock())
            self.clear_stop()
            return True
        if code is None:
            self.log.warning("client did not stop within %.0f s; killing it",
                             STOP_GRACE_SEC)
        elif code == keep.EXIT_STOPPED_BLANK:
            self.log.info("client drew no stopped frame on every screen (no data yet, "
                          "or a screen refused it)")
        else:
            self.log.warning("client exited %s instead of drawing its stopped frame",
                             describe_exit(code))
        self._end_child(why)
        self.clear_stop()
        return False

    def _save(self, want):
        try:
            self.store.save(want)
        except OSError as e:
            self.log.warning("tray.state not written: %s", e)

    # -- the client --------------------------------------------------------

    def _maybe_start(self, now):
        if self.card is not None:
            return          # a card holds panel.lock and the device
        if self.next_start is not None and now < self.next_start:
            if not self._config_wait or self.config_mtime() == self._config_seen:
                return
            self.log.info("panel.json changed; trying again")
        if not self.launcher_ok():
            self._wait(now, "the client's launcher is missing; run "
                            "deploy\\install-task.ps1")
            return
        if not self.lock_free():
            if self._lock_grace_until is not None and now < self._lock_grace_until:
                return      # our own client, still dying
            # The fixed phrase is the one deploy/panel-common.ps1 reads as "another
            # instance", so the installer reports it instead of a silent timeout.
            self._wait(now, "panel already running elsewhere (panel.lock is held); "
                            "not starting a second one")
            return
        # A stop request left from an earlier client goes now, before the new one
        # exists: from here on any request is this client's, and the client keeps it
        # (app.main removes none at start).
        self.clear_stop()
        try:
            child = self.spawn_client()
        except Exception as e:
            self.log.error("client did not start: %s: %s", type(e).__name__, e)
            self._failed(now, "start failed: %s" % type(e).__name__)
            return
        self.child = child
        self.beat.arm(now)
        self.backoff.started(now)
        self._started_at = now
        self._memory_at = now
        self._pids_logged = False
        self._config_wait = False
        self._lock_grace_until = None
        self._logged_wait = None
        self.log.info("client started (pid %s)", getattr(child, "pid", "?"))
        self._enter("running", None)

    def _wait(self, now, why, retry=LOCK_RETRY_SEC):
        # A new wait replaces the configuration-error one: left armed, the old mtime
        # would skip this wait on every step (_maybe_start).
        self._config_wait = False
        self.next_start = now + retry
        if why != self._logged_wait:
            self.log.warning("%s", why)
            self._logged_wait = why
        self._enter("waiting", why)

    def _watch(self, now):
        if self.child is None:
            # Only an exception in the middle of ending a client leaves this state
            # (a MemoryError inside a kill). Without this branch every later step
            # would fail on `None.poll()` and the panel would never come back.
            self._failed(now, "lost track of the client")
            return
        code = self.child.poll()
        if code is None:
            reason = self.beat.check(now)
            if reason is None:
                self._periodic(now)
                return
            self.log.warning("client %s; killing it", reason)
            self._close_child(now, kill=True)
            self._failed(now, reason)
            return
        self.log.info("client exited: %s", describe_exit(code))
        self._close_child(now)
        if code == 2:
            # run.pyw drew the configuration-error card and logged the reason. Every
            # minute would redraw it and log it again; a changed panel.json is the
            # only thing that can make a retry succeed.
            self._config_wait = True
            self._config_seen = self.config_mtime()
            self.next_start = now + CONFIG_RETRY_SEC
            self._enter("waiting", "configuration error (exit 2); waiting for "
                                   "panel.json to change")
        elif code == 0:
            # run.pyw has two exit-0 paths: it backed off on panel.lock, or it drew
            # its stopped frame after a stop request. A request we made is reaped by
            # _stop_client and never reaches here, so this is the first one — not a
            # crash, and the next start is due now.
            self.next_start = now
            self._enter("waiting", None)
        else:
            self._failed(now, "exit %s" % describe_exit(code))

    def _failed(self, now, reason):
        delay = self.backoff.failed(now)
        self._config_wait = False       # as in _wait: the ladder's delay must hold
        self.restarts += 1
        self.last = reason
        self.next_start = now + delay
        self.log.warning("restarting the client in %.0f s (failure %d in a row)",
                         delay, self.backoff.failures)
        self._enter("waiting", "restart pending after %s" % reason)
        if self.backoff.failures == LOOP_FAILURES:
            # Each new client keeps the old frame until it has data (App.holding), so
            # a client that keeps dying before its first frame leaves the last numbers
            # on the glass indefinitely. Once per series.
            self._show_card(["Panel restarting",
                             "%d failures in a row, last: %s" % (LOOP_FAILURES, reason),
                             self._stamp()], now)

    def _periodic(self, now):
        try:
            if not self._pids_logged and now - self._started_at >= PIDS_AFTER_SEC:
                self._pids_logged = True
                self.log.info("client job holds pids %s", self.child.pids())
            if now - self._memory_at >= MEMORY_EVERY_SEC:
                self._memory_at = now
                self._log_memory(self.child, "running")
        except OSError as e:
            self.log.warning("job query failed: %s", e)

    def _log_memory(self, child, when):
        peak_process, peak_job = child.peak_memory()
        self.log.info("client memory (%s): peak %.1f MB per process, %.1f MB job",
                      when, peak_process / 1048576.0, peak_job / 1048576.0)

    def _close_child(self, now, kill=False):
        child, self.child = self.child, None
        if child is None:
            return
        try:
            if kill and not child.kill(10.0):
                self.log.warning("client job still not empty 10 s after the kill")
            self._log_memory(child, "at exit")
        except OSError as e:
            self.log.warning("job query failed: %s", e)
        finally:
            child.close()
        self._lock_grace_until = now + LOCK_GRACE_SEC

    def _end_child(self, why):
        if self.child is not None:
            self.log.info("ending the client: %s", why)
            self._close_child(self.clock(), kill=True)

    # -- cards ---------------------------------------------------------------

    def _stamp(self):
        # Absolute, never "at 21:07": the card is static and may still be on the
        # glass tomorrow, when a time alone would read as today's.
        return fmt.panel_clock(self.wall(), seconds=False)

    def _show_card(self, lines, now):
        self._end_card()
        try:
            card = self.spawn_card(lines)
        except Exception as e:
            self.log.warning("card %r not started: %s: %s", lines[0],
                             type(e).__name__, e)
            return
        self.card = (card, now + CARD_LIMIT_SEC, lines[0])

    def _reap_card(self, now):
        if self.card is None:
            return
        card, deadline, title = self.card
        code = card.poll()
        if code is None:
            if now < deadline:
                return
            card.kill(5.0)
            self.log.warning("card %r did not finish in %.0f s; killed", title,
                             CARD_LIMIT_SEC)
            # A killed card, like a killed client, may hold panel.lock a moment
            # longer than its job: without the grace the next start reads it as
            # another instance and parks for LOCK_RETRY_SEC.
            self._lock_grace_until = now + LOCK_GRACE_SEC
        elif code != 0:
            self.log.warning("card %r: exit %s", title, describe_exit(code))
        card.close()
        self.card = None

    def _end_card(self):
        if self.card is not None:
            card = self.card[0]
            self.card = None
            try:
                card.kill(5.0)
            finally:
                card.close()
            self._lock_grace_until = self.clock() + LOCK_GRACE_SEC     # as in _reap_card

    # -- status ----------------------------------------------------------------

    def _enter(self, state, detail):
        if state != self.state:
            self.state = state
            self.since = self.wall()
        self.detail = detail

    def _publish(self):
        status = Status(self.state, self.since, self.restarts, self.last, self.detail)
        if status == self.status:
            return
        self.status = status
        if self.on_change is not None:
            try:
                self.on_change(status)
            except Exception:
                pass
