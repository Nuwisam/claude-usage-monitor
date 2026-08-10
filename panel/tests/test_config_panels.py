"""The `panels` list: migration from the old single-screen shape, and validation.

The rule the whole file is about: validate() APPENDS problems and never raises.
Under pythonw there is no console, so an exception here becomes a traceback
nobody sees plus a task that restarts every minute - which is exactly what the
older single-screen version did before its numeric checks were tamed.
"""
import pytest

from panel import config as C


def base(**over):
    data = {"stream_token": "t", "stream_url": "https://x/api/stream",
            "account_1": {"uuid": "u"}}
    data.update(over)
    return C.Config(data)


def problems(**over):
    return base(**over).validate()


# --- migration --------------------------------------------------------------


def test_old_shape_becomes_one_panel():
    cfg = base(device={"port_path": "3.4"}, brightness=6)
    spec, = cfg.panels
    assert spec.backend == "ax206"
    assert spec.selector == {"port_path": "3.4"}
    assert spec.brightness == 6
    assert [p for p in cfg.validate() if "panels" in p] == []


def test_old_shape_without_a_device_still_yields_a_panel():
    """One module, no selector - the case the client shipped with."""
    spec, = base().panels
    assert spec.backend == "ax206" and spec.selector == {}


def test_migration_happens_in_the_constructor():
    """Not in validate(): run.pyw's error card and the tests build a Config and
    never validate it, and `panels` has to exist for them too."""
    assert C.Config({}).panels


def test_a_stray_backend_inside_device_cannot_flip_the_driver():
    spec, = base(device={"backend": "turing-a", "port_path": "3.4"}).panels
    assert spec.backend == "ax206"


def test_device_and_panels_together_is_an_error():
    """Never merged, never one-wins: both were written by someone who meant
    something, and picking would be guessing."""
    ps = problems(device={"port_path": "3.4"},
                  panels=[{"backend": "ax206", "port_path": "3.4"}])
    assert any("device" in p and "panels" in p for p in ps)


def test_legacy_location_survives_migration():
    ps = problems(device={"location": "Port_#0004.Hub_#0005"})
    assert any("location" in p and "port_path" in p for p in ps)


def test_legacy_location_inside_a_panel_entry_too():
    ps = problems(panels=[{"backend": "ax206", "location": "Port_#0004.Hub_#0005"}])
    assert any("location" in p for p in ps)


# --- validation -------------------------------------------------------------


def test_unknown_backend_lists_the_known_ones():
    ps = problems(panels=[{"backend": "turing-z"}])
    assert any("turing-z" in p and "ax206" in p for p in ps)


def test_empty_list_is_an_error():
    assert any("panel" in p for p in problems(panels=[]))


def test_panels_must_be_a_list():
    assert any("list" in p for p in problems(panels={"backend": "ax206"}))


def test_unknown_selector_key_is_rejected():
    """It used to match nothing and fall through to "the only device there is",
    so a typo quietly aimed the client at whatever was plugged in."""
    ps = problems(panels=[{"backend": "ax206", "prot_path": "3.4"}])
    assert any("prot_path" in p and "port_path" in p for p in ps)


def test_two_entries_for_one_device_are_rejected():
    ps = problems(panels=[{"backend": "ax206", "port_path": "3.4"},
                          {"backend": "ax206", "port_path": "3.4"}])
    assert any("the same device" in p for p in ps)


def test_two_entries_without_selectors_are_not_flagged():
    """Two different drivers, each with a single device - the normal two-screen
    desk. Only an identical selector means a collision."""
    ps = problems(panels=[{"backend": "ax206"}, {"backend": "ax206", "index": 1}])
    assert not any("the same device" in p for p in ps)


@pytest.mark.parametrize("value", ["jasno", None, float("inf"), float("nan"), 9])
def test_bad_brightness_is_a_problem_not_an_exception(value):
    ps = problems(panels=[{"backend": "ax206", "port_path": "3.4",
                           "brightness": value}])
    if value is None:
        assert not any("brightness" in p for p in ps)     # omitted = driver default
    else:
        assert any("brightness" in p for p in ps)


def test_top_level_brightness_with_panels_is_an_error():
    """The scales are not comparable: 5 is mid-range on one screen and nearly
    dark on another, so a single top-level number cannot mean both."""
    ps = problems(brightness=5, panels=[{"backend": "ax206"}])
    assert any("per panel" in p for p in ps)


def test_top_level_brightness_still_checked_in_the_old_shape():
    assert any("brightness" in p for p in problems(brightness=9))


# --- orientation ------------------------------------------------------------


def test_a_half_turn_reaches_the_spec_and_not_the_selector():
    """The trap: any key not explicitly excluded becomes a selector, where
    select() ignores it silently and the panel is picked by something else."""
    spec, = base(panels=[{"backend": "ax206", "port_path": "3.4",
                          "rotate": 180}]).panels
    assert spec.rotate == 180
    assert spec.selector == {"port_path": "3.4"}


def test_rotate_is_not_an_unknown_key():
    ps = problems(panels=[{"backend": "ax206", "port_path": "3.4", "rotate": 180}])
    assert not any("unknown keys" in p for p in ps)


def test_an_omitted_angle_means_the_ordinary_mounting():
    spec, = base(panels=[{"backend": "ax206"}]).panels
    assert spec.rotate == 0


def test_a_quarter_turn_says_why_not():
    """Not "invalid value": someone asking for 90 wants a portrait screen, and the
    answer is that the layout does not exist, not that they mistyped."""
    ps = problems(panels=[{"backend": "turing-rev-a", "rotate": 90}])
    assert any("rotate" in p and "portrait" in p for p in ps)


@pytest.mark.parametrize("value", ["do gory nogami", float("nan"), True])
def test_a_nonsense_angle_is_a_problem_not_an_exception(value):
    ps = problems(panels=[{"backend": "ax206", "rotate": value}])
    assert any("rotate" in p for p in ps)


def test_two_entries_differing_only_by_the_angle_are_still_one_device():
    """Orientation is not identity: pointing two entries at one screen and turning
    one of them would mean the screen flips with every tick."""
    ps = problems(panels=[{"backend": "ax206", "port_path": "3.4"},
                          {"backend": "ax206", "port_path": "3.4",
                           "rotate": 180}])
    assert any("the same device" in p for p in ps)


def test_broken_width_does_not_break_panel_validation():
    """The canvas is validated further down; asking a driver for its capabilities
    must not be the place that trips over "480"."""
    ps = problems(width="szeroko", panels=[{"backend": "ax206", "brightness": 5}])
    assert any("width" in p for p in ps)
