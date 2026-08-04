"""PanelLink against a fake driver. No hardware.

Until now nothing covered this layer, which is exactly why its regressions were
invisible: the equality skip, the missed-acknowledgement ladder, the periodic
repaint and the three places that must forget what is on the glass are all
mechanisms whose failure looks like normal operation from the outside.

Both capability shapes are exercised, because they pull in opposite directions:
one display acknowledges every frame and draws whole frames only, the other
acknowledges nothing.
"""
import pytest

from panel import config as C, link as link_mod, render, surface
from panel.drivers.base import Caps, DriverError, Scale
from panel.pixels import BIG, LITTLE


class FakeDriver:
    """Records writes. `fail_next` makes the next write raise, like a display
    that went away mid-frame."""

    def __init__(self, acked=True, rect_updates=False, native=(480, 320),
                 byte_order=BIG, rotate=0):
        self.caps = Caps(name="fake", canvas=(480, 320), native=native,
                         rotate=rotate, byte_order=byte_order,
                         rect_updates=rect_updates, acked=acked,
                         brightness=Scale("steps", 0, 7, 5),
                         bytes_per_sec=870_000)
        self.writes = []
        self.status = 0 if acked else None
        self.fail_next = False
        self.resets = 0
        self.brightness = None

    def write(self, payload, rect):
        if self.fail_next:
            self.fail_next = False
            raise DriverError("fake write failure")
        self.writes.append((rect, len(payload)))
        return self.status

    def set_brightness(self, level):
        self.brightness = level

    def reset(self):
        self.resets += 1

    def close(self):
        pass


class Clock:
    """Monotonic time we control; link.py reads it through time.monotonic."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(link_mod.time, "monotonic", c)
    return c


def cfg(**over):
    data = {"stream_token": "t", "account_1": {"uuid": "u"}, "brightness": 5}
    data.update(over)
    return C.Config(data)


def spec(**over):
    data = {"backend": "fake", "selector": {}, "brightness": 5}
    data.update(over)
    return C.PanelSpec(data["backend"], data["selector"], data["brightness"])


def make_link(dev, **over):
    """A PanelLink wired to `dev`, with the opening path stubbed out - opening is
    the device layer's business and has its own tests."""
    link = link_mod.PanelLink(spec(), cfg(**over))

    def ensure():
        if link.dev is None:
            link.dev = dev
            link.surface = surface.for_caps(dev.caps)
        return True

    link.ensure = ensure
    return link


def frames():
    """Two frames whose pixels genuinely differ (the empty-band screen ignores the
    clock, so two ScreenStates with different clocks would render the same)."""
    def one(text):
        return render.Renderer().frame(render.ScreenState(message=[text]))
    a, b = one("first"), one("second")
    assert a.rgb565(BIG) != b.rgb565(BIG), "fixture must produce different frames"
    return a, b


FULL = 480 * 320 * 2


def test_first_frame_after_open_is_written_whole(clock):
    dev = FakeDriver()
    link = make_link(dev)
    a, _ = frames()
    assert link.send(a) is True
    assert dev.writes == [((0, 0, 480, 320), FULL)]


def test_an_unchanged_frame_writes_nothing(clock):
    dev = FakeDriver()
    link = make_link(dev)
    a, _ = frames()
    link.send(a)
    link.send(a)
    assert len(dev.writes) == 1


def test_a_changed_frame_is_written(clock):
    dev = FakeDriver()
    link = make_link(dev)
    a, b = frames()
    link.send(a)
    link.send(b)
    assert len(dev.writes) == 2


def test_force_draws_even_when_nothing_changed(clock):
    """run_once() relies on this. `force` must INVALIDATE, not merely skip the
    comparison: skipping it would let a diff engine compute zero rectangles and
    write nothing over another process's image."""
    dev = FakeDriver()
    link = make_link(dev)
    a, _ = frames()
    link.send(a)
    link.send(a, force=True)
    assert len(dev.writes) == 2


def test_a_write_error_makes_the_next_frame_whole(clock):
    dev = FakeDriver()
    link = make_link(dev)
    a, _ = frames()
    link.send(a)
    dev.fail_next = True
    assert link.send(a, force=True) is False
    assert link.dev is None and link.surface is None
    clock.advance(60)                      # get past the backoff
    assert link.send(a) is True
    assert dev.writes[-1] == ((0, 0, 480, 320), FULL)


def test_periodic_repaint_fires_while_frames_keep_changing(clock):
    """The regression this guards is subtle: timing the repaint from the last
    WRITE means a display that gets a small write every minute never reaches the
    threshold. A test that stops feeding frames would pass either way, so this one
    keeps changing them."""
    dev = FakeDriver()
    link = make_link(dev, heal_repaint_sec=300)
    a, b = frames()
    link.send(a)
    for i in range(60):
        clock.advance(10)
        link.send(b if i % 2 else a)
    assert link.last_full_sent > 1000.0
    assert len(dev.writes) > 1


def test_missed_acknowledgements_trigger_a_reset(clock):
    dev = FakeDriver(acked=True)
    dev.status = None                      # accepted but never confirmed
    link = make_link(dev)
    a, _ = frames()
    for _ in range(link_mod.MISSED_CSW_LIMIT):
        link.send(a)
    assert dev.resets == 1


def test_an_unacknowledged_driver_never_resets(clock):
    """status is always None by design there. Sharing the ladder would repaint
    every tick and reset every third one, forever."""
    dev = FakeDriver(acked=False)
    link = make_link(dev)
    a, _ = frames()
    for _ in range(10):
        link.send(a)
    assert dev.resets == 0
    assert len(dev.writes) == 1            # unchanged frames still write nothing


def test_reset_invalidates_and_commit_does_not_undo_it(clock):
    """Ordering hazard: commit() runs before the acknowledgement ladder, so a
    reset() inside the ladder has the last word about what we believe."""
    dev = FakeDriver(acked=True)
    dev.status = None
    link = make_link(dev)
    a, _ = frames()
    for _ in range(link_mod.MISSED_CSW_LIMIT):
        link.send(a)
    assert link.surface.blank, "after a reset the glass must count as unknown"


def test_byte_order_and_rotation_come_from_the_driver(clock):
    dev = FakeDriver(native=(320, 480), byte_order=LITTLE, rotate=90)
    link = make_link(dev)
    a, _ = frames()
    link.send(a)
    assert dev.writes == [((0, 0, 320, 480), FULL)]


def test_failures_log_once_per_changed_message(clock, caplog):
    """A display held by another program can be held for hours; a line per second
    would drown the log."""
    link = link_mod.PanelLink(spec(), cfg())
    calls = []

    def failing_ensure():
        link._fail("busy")
        return False

    link.ensure = failing_ensure
    with caplog.at_level("WARNING"):
        for _ in range(5):
            link.send(None)
            clock.advance(60)
        calls = [r for r in caplog.records if "busy" in r.getMessage()]
    assert len(calls) == 1
