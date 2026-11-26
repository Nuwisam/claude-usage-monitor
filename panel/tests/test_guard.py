"""The guardian, with fake processes: what ends it, and what only restarts.

The rule under test is the one the whole chain turns on: the guardian ends ONLY when
the supervisor exits with keep.EXIT_QUIT and tray.state agrees. Zero is an ordinary
exit, and an ordinary exit is a failure here.
"""
from datetime import datetime, timezone

import pytest

pytest.importorskip("panel.winjob")

from panel import guard as G, keep    # noqa: E402


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeChild:
    def __init__(self, pid):
        self.pid = pid
        self.code = None
        self.killed = False
        self.closed = False

    def wait(self, timeout):
        return self.code

    def poll(self):
        return self.code

    def kill(self, timeout=10.0):
        self.killed = True
        if self.code is None:
            self.code = 0xDEAD
        return True

    def close(self):
        self.closed = True

    def pids(self):
        return [self.pid]


class FakeBeat:
    def __init__(self):
        self.reason = None

    def arm(self, now):
        self.reason = None

    def check(self, now):
        return self.reason


class Log:
    def __init__(self):
        self.lines = []

    def _add(self, msg, args):
        self.lines.append(msg % args if args else msg)

    def info(self, msg, *args, **kw):
        self._add(msg, args)

    warning = error = info


class World:
    def __init__(self):
        self.clock = Clock()
        self.children = []
        self.cards = []
        self.quit = False
        self.beat = FakeBeat()
        self.log = Log()
        self.spawn_error = None

    def spawn(self):
        if self.spawn_error:
            raise self.spawn_error
        c = FakeChild(10 + len(self.children))
        self.children.append(c)
        return c

    def spawn_card(self, lines):
        c = FakeChild(900 + len(self.cards))
        c.lines = lines
        self.cards.append(c)
        return c

    def make(self):
        return G.Guard(self.spawn, self.spawn_card, self.beat, lambda: self.quit,
                       self.log, clock=self.clock,
                       wall=lambda: datetime(2026, 11, 25, 20, 7, tzinfo=timezone.utc))

    def run(self, g, seconds):
        for _ in range(int(seconds)):
            self.clock.t += 1.0
            g.step()
            if g.done:
                return

    @property
    def child(self):
        return self.children[-1]


def started(w):
    g = w.make()
    g.step()
    assert len(w.children) == 1
    return g


def test_quit_with_the_state_file_agreeing_ends_the_guardian():
    w = World()
    g = started(w)
    w.quit = True
    w.child.code = keep.EXIT_QUIT
    g.step()
    assert g.done and g.exit_code == 0
    assert w.child.closed


def test_quit_without_the_state_file_is_a_failure():
    w = World()
    g = started(w)
    w.child.code = keep.EXIT_QUIT
    g.step()
    assert not g.done
    w.run(g, 3)
    assert len(w.children) == 2


@pytest.mark.parametrize("code", [0, 1, 2, 0xC0000409, 0xDEAD])
def test_every_other_exit_restarts_zero_included(code):
    w = World()
    w.quit = True               # even with "quit" in the file: the code must say it too
    g = started(w)
    w.child.code = code
    g.step()
    assert not g.done
    w.run(g, 3)
    assert len(w.children) == 2


def test_the_ladder_and_the_card_once_per_series():
    w = World()
    g = started(w)
    for _ in range(7):
        w.child.code = 1
        g.step()
        for card in w.cards:
            card.code = 0
        w.run(g, 61)
    assert len(w.children) == 8
    assert len(w.cards) == 1
    assert w.cards[0].lines[0] == "Panel supervisor failing"
    assert w.cards[0].lines[2] == "25.11.2026 " + w.cards[0].lines[2].split()[1]


def test_a_card_holds_the_next_start_until_it_ends_or_its_limit():
    w = World()
    g = started(w)
    for _ in range(G.LOOP_FAILURES):
        w.child.code = 1
        g.step()
        if not w.cards:
            w.run(g, 61)
    count = len(w.children)
    # The fifth rung is 60 s, the card limit 30 s: the card is killed first, and
    # nothing started while it was up.
    w.run(g, G.CARD_LIMIT_SEC + 1)
    assert w.cards[0].killed and len(w.children) == count
    w.run(g, 31)
    assert len(w.children) == count + 1


def test_busy_retries_on_its_own_ladder_without_a_card():
    w = World()
    g = started(w)
    for _ in range(8):
        w.child.code = keep.EXIT_BUSY
        g.step()
        w.run(g, 61)
    assert not w.cards
    assert g.backoff.failures == 0, "busy is not a failure of the supervisor"
    assert not g.done


def test_a_silent_heartbeat_kills_and_restarts():
    w = World()
    g = started(w)
    first = w.child
    w.beat.reason = "no heartbeat 180 s after start"
    g.step()
    assert first.killed and first.closed
    w.run(g, 3)
    assert len(w.children) == 2


def test_a_failed_start_does_not_end_the_loop():
    w = World()
    w.spawn_error = OSError(2, "not found")
    g = w.make()
    g.step()
    assert not w.children and g.next_start > w.clock.t
    w.spawn_error = None
    w.run(g, 3)
    assert len(w.children) == 1


def test_the_job_pids_are_logged_once():
    w = World()
    g = started(w)
    w.run(g, 10)
    assert sum("supervisor job holds pids" in line for line in w.log.lines) == 1


def test_shutdown_kills_what_is_left():
    w = World()
    g = started(w)
    g.shutdown()
    assert w.children[0].killed and w.children[0].closed
