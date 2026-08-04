"""Composing a mounting angle onto a driver's own rotation.

`Caps.rotate` carries two things at once: how the logical canvas maps onto this
model's framebuffer, and how the glass hangs on the wall. Only the sum is ever
read, so the sum is what has to be right - and it has to stay inside the set of
angles the renderer can actually produce.
"""
import pytest

from panel import render
from panel.drivers import ax206, turing_rev_a
from panel.drivers.base import DriverError
from panel.pixels import BIG, LITTLE


def test_zero_returns_the_same_object():
    """The common case must not allocate: every panel that is mounted the ordinary
    way goes through here on every open."""
    caps = ax206.caps_for((480, 320))
    assert caps.rotated(0) is caps


def test_a_half_turn_on_a_driver_that_does_not_rotate():
    caps = ax206.caps_for((480, 320)).rotated(180)
    assert caps.rotate == 180
    assert caps.native == (480, 320) and caps.canvas == (480, 320)


def test_a_half_turn_composes_with_the_drivers_quarter_turn():
    caps = turing_rev_a.caps_for().rotated(180)
    assert caps.rotate == 270
    assert caps.native == (320, 480), "a half turn must not swap the framebuffer"


@pytest.mark.parametrize("caps", [ax206.caps_for((480, 320)),
                                  turing_rev_a.caps_for()])
def test_the_composed_angle_is_one_the_renderer_can_draw(caps):
    """The whole scheme rests on this: 90 + 180 = 270 is in the table, so no new
    rotation path is needed anywhere."""
    assert caps.rotated(180).rotate in render._ROTATIONS


def test_everything_except_the_angle_survives():
    before = turing_rev_a.caps_for()
    after = before.rotated(180)
    for field in ("name", "canvas", "native", "byte_order", "rect_updates",
                  "acked", "reset_on_open", "brightness", "bytes_per_sec"):
        assert getattr(after, field) == getattr(before, field), field
    assert after.byte_order == LITTLE
    assert ax206.caps_for((480, 320)).rotated(180).byte_order == BIG


@pytest.mark.parametrize("angle", [90, 270, 45, -180, 360])
def test_anything_but_a_half_turn_is_refused(angle):
    """A quarter turn would swap `native` with no portrait canvas to fill it. The
    refusal is a DriverError so PanelLink backs off instead of dying."""
    with pytest.raises(DriverError):
        turing_rev_a.caps_for().rotated(angle)
