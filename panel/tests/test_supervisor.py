"""The supervisor's state machine, one second at a time, with nothing real in it.

Every row of the table in panel/README.md ("The tray icon and the watchdog") has a
test here. The processes, the lock, the heartbeat and the clock are fakes: what is
under test is the DECISION — what gets started, killed or drawn, and when.
"""
from datetime import datetime, timezone

import pytest

from panel import keep, supervisor as S


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeChild:
    def __init__(self, pid, kind):
        self.pid = pid
        self.kind = kind
        self.code = None
        self.killed = False
        self.closed = False

    def poll(self):
        return self.code

    def kill(self, timeout=10.0):
        self.killed = True
        if self.code is None:
            self.code = 0xDEAD
        return True

    def wait(self, timeout):
        return self.code

    def close(self):
        self.closed = True

    def pids(self):
        return [self.pid, self.pid + 1]

    def peak_memory(self):
        return 30 << 20, 32 << 20


class FakeBeat:
    def __init__(self):
        self.reason = None
        self.armed = []

    def arm(self, now):
        self.armed.append(now)
        self.reason = None

    def check(self, now):
        return self.reason


class FakeStore:
    def __init__(self, want=None, problem=None):
        self._want = want
        self.problem = problem
        self.saved = []

    def want(self):
        return self._want, self.problem

    def save(self, want):
        self.saved.append(want)
        self._want = want


class Log:
    def __init__(self):
        self.lines = []

    def _add(self, level, msg, *args, **kw):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args, **kw):
        self._add("info", msg, *args)

    def warning(self, msg, *args, **kw):
        self._add("warning", msg, *args)

    def error(self, msg, *args, **kw):
        self._add("error", msg, *args)

    def text(self):
        return "\n".join(line for _, line in self.lines)


class World:
    def __init__(self, want=None, problem=None):
        self.clock = Clock()
        self.clients = []
        self.cards = []
        self.lock = True            # panel.lock free
        self.launcher = True
        self.mtime = 1
        self.beat = FakeBeat()
        self.store = FakeStore(want, problem)
        self.log = Log()
        self.commands = []
        self.spawn_error = None
        self.changes = []
        self.backoff = None
        # What the client does when asked to stop: 0 = draws its stopped frame and
        # exits; keep.EXIT_STOPPED_BLANK = had nothing to draw; None = does not answer.
        self.stop_reply = 0
        self.stop_requests = 0
        self.stop_cleared = 0

    def request_stop(self):
        self.stop_requests += 1
        if self.clients and self.clients[-1].code is None:
            self.clients[-1].code = self.stop_reply

    def clear_stop(self):
        self.stop_cleared += 1

    def spawn_client(self):
        if self.spawn_error:
            raise self.spawn_error
        child = FakeChild(100 + 10 * len(self.clients), "client")
        self.clients.append(child)
        return child

    def spawn_card(self, lines):
        card = FakeChild(900 + len(self.cards), "card")
        card.lines = lines
        self.cards.append(card)
        return card

    def read_command(self):
        return self.commands.pop(0) if self.commands else None

    def make(self):
        sup = S.Supervisor(
            spawn_client=self.spawn_client, spawn_card=self.spawn_card,
            lock_free=lambda: self.lock, launcher_ok=lambda: self.launcher,
            beat=self.beat, config_mtime=lambda: self.mtime, store=self.store,
            logger=self.log, read_command=self.read_command, clock=self.clock,
            wall=lambda: datetime(2026, 11, 25, 20, 7, tzinfo=timezone.utc),
            backoff=self.backoff, request_stop=self.request_stop,
            clear_stop=self.clear_stop)
        sup.on_change = self.changes.append
        sup.begin()
        return sup

    def run(self, sup, seconds, dt=1.0):
        steps = int(round(seconds / dt))
        for _ in range(steps):
            self.clock.advance(dt)
            sup.step()

    @property
    def client(self):
        return self.clients[-1]


def started(world):
    w = world
    sup = w.make()
    sup.step()
    assert sup.state == "running"
    return sup


# -- starting and crashing --------------------------------------------------


def test_the_first_step_starts_the_client():
    w = World()
    sup = started(w)
    assert len(w.clients) == 1
    assert w.beat.armed == [w.clock.t]
    assert sup.status.state == "running"


def test_a_leftover_stop_request_is_cleared_before_the_client_starts():
    """Regression: app.main removed panel.stop at start, also a Stop written while the
    client was starting. The supervisor now clears a leftover BEFORE the spawn, and
    the client keeps whatever it finds."""
    w = World()
    sup = w.make()
    cleared_at_spawn = []
    real_spawn = w.spawn_client
    sup.spawn_client = lambda: cleared_at_spawn.append(w.stop_cleared) or real_spawn()
    sup.step()
    assert cleared_at_spawn == [1] and len(w.clients) == 1


def test_a_crash_restarts_on_the_ladder():
    w = World()
    sup = started(w)
    delays = []
    for _ in range(5):
        w.client.code = 1
        sup.step()
        assert sup.state == "waiting"
        before = len(w.clients)
        t0 = w.clock.t
        while len(w.clients) == before:
            w.clock.advance(1.0)
            sup.step()
            # the fifth failure's card holds the start back until it is done
            if w.cards and w.cards[-1].code is None:
                w.cards[-1].code = 0
        delays.append(w.clock.t - t0)
    assert delays == [2.0, 5.0, 10.0, 30.0, 60.0]
    assert sup.restarts == 5
    assert sup.last == "exit 1"


def test_the_ladder_tops_out_and_never_gives_up():
    w = World()
    sup = started(w)
    for _ in range(12):
        w.client.code = 1
        sup.step()
        if w.cards and w.cards[-1].code is None:
            w.cards[-1].code = 0
        w.run(sup, 61)
    assert len(w.clients) == 13
    assert sup.state == "running"


def test_a_healthy_run_puts_the_ladder_back_at_the_start():
    w = World()
    sup = started(w)
    w.client.code = 1
    sup.step()
    w.run(sup, 2)
    w.client.code = 1
    sup.step()
    w.run(sup, 5)                       # second rung: 5 s
    assert sup.state == "running"
    w.run(sup, S.HEALTHY_SEC)
    w.client.code = 1
    sup.step()
    assert sup.next_start - w.clock.t == 2.0


def test_a_failed_start_is_a_failure_and_the_loop_lives_on():
    w = World()
    w.spawn_error = MemoryError()
    sup = w.make()
    sup.step()
    assert sup.state == "waiting"
    assert "start failed: MemoryError" in sup.last
    w.spawn_error = None
    w.run(sup, 2)
    assert sup.state == "running"


def test_a_hang_kills_and_restarts():
    w = World()
    sup = started(w)
    first = w.client
    w.beat.reason = "no heartbeat for 120 s"
    sup.step()
    assert first.killed and first.closed
    assert sup.state == "waiting"
    assert sup.last == "no heartbeat for 120 s"
    w.run(sup, 2)
    assert len(w.clients) == 2 and sup.state == "running"


def test_the_fifth_failure_in_a_row_draws_the_card_once():
    w = World()
    sup = started(w)
    for n in range(1, 8):
        w.client.code = 1
        sup.step()
        if n < S.LOOP_FAILURES:
            assert not w.cards
        for card in w.cards:
            card.code = 0
        w.run(sup, 61)
    assert len(w.cards) == 1
    assert w.cards[0].lines[0] == "Panel restarting"
    assert "5 failures in a row, last: exit 1" in w.cards[0].lines[1]
    assert w.cards[0].lines[2] == "25.11.2026 " + w.cards[0].lines[2].split()[1]


def test_a_card_holds_the_next_start_back_until_it_is_done():
    """A card holds panel.lock and the device; a client started next to it would
    back off on the lock. With a 1 s ladder the next start falls inside the card."""
    w = World()
    w.backoff = keep.Backoff(steps=(1.0,))
    sup = started(w)
    for _ in range(S.LOOP_FAILURES):
        w.client.code = 1
        sup.step()
        if not w.cards:
            w.run(sup, 1)
    assert w.cards and w.cards[0].code is None
    count = len(w.clients)
    w.run(sup, 10)
    assert len(w.clients) == count
    w.cards[0].code = 0
    w.run(sup, 1)
    assert len(w.clients) == count + 1
    w.cards[0].code = 0
    w.run(sup, 1)
    assert len(w.clients) == count + 1


def test_a_card_that_never_ends_is_killed_after_its_limit():
    w = World()
    w.stop_reply = None                 # the client does not answer: the card goes up
    sup = started(w)
    w.commands.append("stop")
    sup.step()
    card = w.cards[0]
    w.run(sup, S.CARD_LIMIT_SEC + 1)
    assert card.killed and card.closed
    assert sup.card is None


def test_a_killed_card_gets_the_lock_grace_too():
    """A killed card may hold panel.lock a moment after its job: the next start waits
    it out quietly instead of reporting another instance for LOCK_RETRY_SEC."""
    w = World()
    w.backoff = keep.Backoff(steps=(1.0,))
    sup = started(w)
    for _ in range(S.LOOP_FAILURES):
        w.client.code = 1
        sup.step()
        if not w.cards:
            w.run(sup, 1)
    w.run(sup, S.CARD_LIMIT_SEC + 1)        # the card hangs and is killed
    assert w.cards[0].killed
    w.lock = False                          # the dying card's lock
    w.run(sup, 3)
    assert "panel already running" not in w.log.text()
    w.lock = True
    w.run(sup, 1)
    assert sup.state == "running"


# -- exit codes that are not crashes ------------------------------------------


def test_exit_2_waits_for_panel_json_to_change():
    w = World()
    sup = started(w)
    w.client.code = 2
    sup.step()
    assert "configuration error" in sup.detail
    w.run(sup, 120)
    assert len(w.clients) == 1          # no restart every minute
    w.mtime = 2
    w.run(sup, 1)
    assert len(w.clients) == 2
    assert sup.restarts == 0            # not a crash


def test_a_failed_start_after_a_config_change_waits_out_the_ladder():
    """The changed panel.json earns one retry. If that start fails, the 2 s rung
    holds: the old mtime must not skip it on every step."""
    w = World()
    sup = started(w)
    w.client.code = 2
    sup.step()
    w.mtime = 2
    w.spawn_error = MemoryError()
    w.run(sup, 1)
    assert sup.restarts == 1
    w.run(sup, 1)
    assert sup.restarts == 1, "the 2 s rung must hold"
    w.run(sup, 1)
    assert sup.restarts == 2


def test_a_held_lock_after_a_config_change_is_probed_every_30_s():
    w = World()
    sup = started(w)
    w.client.code = 2
    sup.step()
    w.run(sup, S.LOCK_GRACE_SEC + 1)
    w.lock = False                      # someone else took panel.lock
    w.mtime = 2
    w.run(sup, 10)
    assert w.log.text().count("panel.json changed") == 1, "one retry, not one per step"
    assert "panel already running" in sup.detail
    w.lock = True
    w.run(sup, S.LOCK_RETRY_SEC)
    assert len(w.clients) == 2


def test_a_config_change_inside_the_lock_grace_is_not_lost():
    """Our own client still holds panel.lock for a moment after exit 2. A change seen
    then is retried as soon as the lock is free, not after CONFIG_RETRY_SEC."""
    w = World()
    sup = started(w)
    w.client.code = 2
    w.lock = False                      # the exiting client, still dying
    sup.step()
    w.mtime = 2
    w.run(sup, 1)
    assert len(w.clients) == 1
    w.lock = True
    w.run(sup, 1)
    assert len(w.clients) == 2


def test_exit_2_is_retried_after_ten_minutes_anyway():
    w = World()
    sup = started(w)
    w.client.code = 2
    sup.step()
    w.run(sup, S.CONFIG_RETRY_SEC + 1)
    assert len(w.clients) == 2


def test_exit_0_means_the_lock_was_taken_and_waits_for_it():
    w = World()
    sup = started(w)
    w.client.code = 0
    w.lock = False
    sup.step()
    w.run(sup, S.LOCK_GRACE_SEC + 1)
    assert "panel already running" in sup.detail
    assert len(w.clients) == 1
    w.run(sup, 300)
    assert len(w.clients) == 1
    assert w.log.text().count("panel already running") == 1, "logged once, not per probe"
    w.lock = True
    w.run(sup, S.LOCK_RETRY_SEC)
    assert len(w.clients) == 2
    assert sup.restarts == 0


def test_our_own_dying_client_is_given_its_grace_on_the_lock():
    w = World()
    sup = started(w)
    w.client.code = 1
    sup.step()
    w.lock = False                      # the killed process has not let go yet
    w.run(sup, 3)
    assert "panel already running" not in w.log.text()
    w.lock = True
    w.run(sup, 1)
    assert sup.state == "running"


def test_a_missing_launcher_starts_nothing():
    w = World()
    w.launcher = False
    sup = w.make()
    w.run(sup, 100)
    assert not w.clients
    assert "launcher is missing" in sup.detail


# -- the owner's commands --------------------------------------------------------


def test_stop_asks_the_client_for_its_stopped_frame_and_draws_no_card():
    """The owner's rule: a stopped panel keeps its numbers, marked STOPPED — never a
    full-screen card over them, when the client can draw that frame itself."""
    w = World()
    sup = started(w)
    client = w.client
    sup.command("stop")
    sup.step()
    assert w.stop_requests == 1
    assert not client.killed and client.closed
    assert not w.cards
    assert sup.state == "stopped" and w.store.saved == ["stopped"]
    assert w.stop_cleared == 2, "once before the client started, once after its stop"
    assert "client drew its stopped frame" in w.log.text()
    w.run(sup, 3600)
    assert len(w.clients) == 1


@pytest.mark.parametrize("reply,why", [(keep.EXIT_STOPPED_BLANK,
                                        "drew no stopped frame on every screen"),
                                       (None, "did not stop within"),
                                       (1, "exited 1 instead")])
def test_stop_falls_back_to_the_card(reply, why):
    w = World()
    w.stop_reply = reply
    sup = started(w)
    client = w.client
    sup.command("stop")
    sup.step()
    assert client.killed and client.closed
    assert w.cards and w.cards[0].lines[0] == "Panel stopped"
    assert why in w.log.text()


def test_stop_while_nothing_runs_draws_the_card():
    """No client: nobody holds numbers to keep, so the card is all there is."""
    w = World()
    w.launcher = False
    sup = w.make()
    sup.step()
    sup.command("stop")
    sup.step()
    assert w.stop_requests == 0
    assert w.cards and w.cards[0].lines[0] == "Panel stopped"


def test_stop_holds_when_waiting_for_the_client_fails():
    """A failed wait (WaitForSingleObject / GetExitCodeProcess) must not lose the Stop:
    the client may have read panel.stop and exited 0, which _watch would read as the
    lock back-off and start the panel again at once."""
    w = World()
    sup = started(w)
    client = w.client

    def broken_wait(timeout):
        raise OSError("WaitForSingleObject failed")

    client.wait = broken_wait
    sup.command("stop")
    sup.step()
    assert client.killed and client.closed
    assert sup.state == "stopped" and w.store.saved == ["stopped"]
    assert w.stop_cleared == 2, "once before the client started, once after its stop"
    assert w.cards and w.cards[0].lines[0] == "Panel stopped"
    assert "waiting for the client failed" in w.log.text()
    w.run(sup, 3600)
    assert len(w.clients) == 1


def test_stop_kills_draws_the_card_and_stays_stopped():
    w = World()
    w.stop_reply = None
    sup = started(w)
    client = w.client
    sup.command("stop")
    sup.step()
    assert client.killed and client.closed
    assert sup.state == "stopped"
    assert w.store.saved == ["stopped"]
    assert w.cards[0].lines[0] == "Panel stopped"
    w.cards[0].code = 0
    w.run(sup, 3600)
    assert len(w.clients) == 1


def test_start_after_stop():
    w = World()
    sup = started(w)
    sup.command("stop")
    sup.step()
    sup.command("start")
    w.run(sup, 1)
    assert sup.state == "running" and len(w.clients) == 2
    assert w.store.saved == ["stopped", "running"]


def test_start_while_a_card_is_up_ends_the_card_first():
    w = World()
    w.stop_reply = keep.EXIT_STOPPED_BLANK
    sup = started(w)
    sup.command("stop")
    sup.step()
    card = w.cards[0]
    sup.command("start")
    w.run(sup, 1)
    assert card.killed and card.closed
    assert sup.state == "running"


def test_restart_during_a_ladder_wait_starts_at_once_and_resets_it():
    w = World()
    sup = started(w)
    for _ in range(3):
        w.client.code = 1
        sup.step()
        w.run(sup, 11)
    w.client.code = 1
    sup.step()
    assert sup.next_start - w.clock.t == 30.0
    sup.command("restart")
    w.run(sup, 1)
    assert sup.state == "running"
    w.client.code = 1
    sup.step()
    assert sup.next_start - w.clock.t == 2.0


def test_restart_while_running_kills_and_starts():
    w = World()
    sup = started(w)
    first = w.client
    sup.command("restart")
    w.run(sup, 1)
    assert first.killed and len(w.clients) == 2 and sup.state == "running"
    assert not w.cards, "a restart draws no card: the new client holds the old frame"


def test_quit_with_a_stopped_frame_is_done_at_once():
    w = World()
    sup = started(w)
    sup.command("quit")
    sup.step()
    assert not w.cards and not w.client.killed
    assert w.store.saved == ["quit"]
    sup.step()
    assert sup.done


def test_quit_after_stop_draws_nothing_more():
    w = World()
    sup = started(w)
    sup.command("stop")
    sup.step()
    sup.command("quit")
    sup.step()
    sup.step()
    assert sup.done and not w.cards


def test_quit_waits_for_its_card_then_is_done():
    w = World()
    w.stop_reply = None
    sup = started(w)
    sup.command("quit")
    sup.step()
    assert w.client.killed
    assert w.store.saved == ["quit"]
    assert sup.state == "quitting" and not sup.done
    w.run(sup, 3)
    assert not sup.done
    w.cards[0].code = 0
    w.run(sup, 1)
    assert sup.done
    sup.command("start")
    w.run(sup, 5)
    assert len(w.clients) == 1, "nothing is started once Quit is under way"


def test_the_commands_from_tray_cmd():
    w = World()
    sup = started(w)
    w.commands.append("stop")
    sup.step()
    assert sup.state == "stopped"
    assert "command from tray.cmd: stop" in w.log.text()


def test_an_unknown_command_is_refused():
    w = World()
    sup = w.make()
    with pytest.raises(ValueError):
        sup.command("reboot")


# -- what survives a restart of the supervisor -----------------------------------


def test_a_stop_from_before_the_restart_is_kept():
    w = World(want="stopped")
    sup = w.make()
    w.run(sup, 600)
    assert sup.state == "stopped" and not w.clients
    assert not w.cards, "the stop card is already on the glass"


def test_a_quit_from_before_the_restart_is_finished():
    w = World(want="quit")
    sup = w.make()
    sup.step()
    assert sup.done and not w.clients


def test_an_unreadable_state_file_starts_the_panel():
    w = World(problem="tray.state unreadable: garbage")
    sup = w.make()
    sup.step()
    assert sup.state == "running"
    assert "unreadable" in w.log.text()


# -- robustness and the view ------------------------------------------------------


def test_running_without_a_child_recovers():
    w = World()
    sup = started(w)
    sup.child = None
    sup.step()
    assert sup.state == "waiting"
    w.run(sup, 2)
    assert sup.state == "running"


def test_status_is_published_on_change_only():
    w = World()
    sup = started(w)
    n = len(w.changes)
    w.run(sup, 10)
    assert len(w.changes) == n
    w.client.code = 1
    sup.step()
    assert w.changes[-1].state == "waiting"
    assert w.changes[-1].restarts == 1


def test_the_job_pids_and_memory_go_to_the_log():
    w = World()
    sup = started(w)
    w.run(sup, S.PIDS_AFTER_SEC)
    assert "client job holds pids [100, 101]" in w.log.text()
    w.run(sup, S.MEMORY_EVERY_SEC)
    assert "client memory (running): peak 30.0 MB" in w.log.text()


def test_describe_exit():
    assert S.describe_exit(1) == "1"
    assert S.describe_exit(0xDEAD) == "killed"
    assert S.describe_exit(0xC0000409) == "0xC0000409"


def test_exit_codes_with_intent_are_not_zero():
    assert 0 not in (keep.EXIT_QUIT, keep.EXIT_BUSY)
