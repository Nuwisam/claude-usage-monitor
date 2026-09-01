"""The SSE parser and the configuration.

The parser has one non-obvious duty: `data:` may appear MANY TIMES within
a single frame, because that is how the backend splits multi-line JSON (events.py:47-56).
Joining it back together is required, not cosmetic.
"""
import json

import pytest

from panel import config as C, stream


def events(*chunks):
    return list(stream.parse_events(iter(chunks)))


def test_simple_frame():
    assert events(b'event: ping\ndata: {"serverNow":"x"}\n\n') == \
        [("ping", {"serverNow": "x"})]


def test_data_split_across_multiple_lines_is_joined():
    """events.py:47-56 splits multi-line JSON into several `data:` fields. Were the
    parser to take the last one only, every frame with a newline inside would be wrong."""
    body = json.dumps({"text": "first\nsecond"})
    raw = b"event: account\n"
    for line in body.split("\n"):
        raw += b"data: " + line.encode() + b"\n"
    raw += b"\n"
    assert events(raw) == [("account", {"text": "first\nsecond"})]


def test_frame_split_across_reads():
    """The commonest case on a real network: the frame arrives in chunks."""
    assert events(b'event: pi', b'ng\ndata: {"a"', b': 1}\n\n') == \
        [("ping", {"a": 1})]


def test_several_frames_in_one_chunk():
    got = events(b'event: ping\ndata: {}\n\nevent: bye\ndata: {"reason":"lifetime"}\n\n')
    assert [e for e, _ in got] == ["ping", "bye"]


def test_retry_and_comments_are_skipped():
    got = events(b'retry: 3000\n\n: comment\nevent: ping\ndata: {}\n\n')
    assert got == [("ping", {})]


def test_crlf_line_endings():
    assert events(b'event: ping\r\ndata: {}\r\n\r\n') == [("ping", {})]


def test_bad_json_does_not_crash_the_parser():
    """A broken frame must not kill the panel — the next one carries the full state anyway."""
    got = events(b'event: account\ndata: {not json\n\nevent: ping\ndata: {}\n\n')
    assert got == [("account", None), ("ping", {})]


def test_empty_stream():
    assert events(b"", b"") == []


def test_space_after_colon_is_optional():
    """That is what the SSE grammar says. Our backend always sends the space, but replay.py
    is handed a recording that need not come from it — and a parser demanding the
    space would then return zero frames and not a single error."""
    assert events(b'event:ping\ndata:{"a":1}\n\n') == [("ping", {"a": 1})]


def test_data_field_without_a_value_does_not_break_the_frame():
    assert events(b'event: ping\ndata:\n\n') == [("ping", None)]


# --- configuration ----------------------------------------------------------

def cfg(**kw):
    d = {"stream_token": "t", "account_1": {"uuid": "a"}}
    d.update(kw)
    return C.Config(d)


def test_accounts_are_two_fields_in_band_order():
    """The shape of the configuration is the shape of the screen: a third account
    cannot be added by accident."""
    c = cfg(account_1={"uuid": "a", "name": "top"},
            account_2={"uuid": "b", "name": "bottom"})
    assert [a.name for a in c.accounts] == ["top", "bottom"]
    assert not c.validate()


def test_first_account_alone_is_enough():
    assert cfg(account_2=None).accounts[0].uuid == "a"
    assert not cfg(account_2=None).validate()


@pytest.mark.parametrize("change,fragment", [
    ({"stream_token": None}, "stream_token"),
    ({"account_1": None, "account_2": None}, "no account specified"),
    ({"account_2": {"uuid": "a"}}, "repeats the uuid"),
    ({"account_2": {"name": "no uuid"}}, "has no uuid"),
    ({"account_2": "not an object"}, "must be an object"),
    ({"brightness": 9}, "brightness"),
    ({"device": "not an object"}, "device must be an object"),
])
def test_validation_catches_errors(change, fragment):
    problems = " ".join(cfg(**change).validate())
    assert fragment in problems


@pytest.mark.parametrize("change,fragment", [
    ({"brightness": "bright"}, "brightness must be a number"),
    ({"tick_sec": "fast"}, "tick_sec must be a number"),
    ({"width": None}, "width must be a number"),
    ({"height": 0}, "height must be >= 1"),
    # json.load accepts a bare `Infinity`, and int(float("inf")) is an OverflowError,
    # not a ValueError — so a plain except (TypeError, ValueError) did not catch it.
    ({"brightness": float("inf")}, "brightness must be a number"),
    ({"tick_sec": float("inf")}, "tick_sec must be a finite number"),
    ({"tick_sec": float("nan")}, "tick_sec must be a finite number"),
])
def test_bad_numbers_give_a_problem_not_an_exception(change, fragment):
    """validate() promises to RETURN a list of problems. A bare `int(self.brightness)`
    threw a ValueError, which under pythonw ended in a traceback in the log
    and the task restarting every minute — instead of one sentence about what to fix."""
    problems = " ".join(cfg(**change).validate())
    assert fragment in problems


def test_number_in_quotes_is_converted():
    """Checking without writing back was only for show: `int("480")` succeeds, so validate()
    stayed silent, and Layout then computed `"480" - 1` and broke with a TypeError already
    AFTER the configuration had been declared fit for use."""
    c = cfg(width="480", tick_sec="2")
    assert not c.validate()
    assert c.width == 480 and c.tick_sec == 2.0


def test_missing_file_is_a_different_error_than_broken_json(tmp_path):
    """Two different messages: 'not installed yet' and 'broken while being edited'."""
    with pytest.raises(C.ConfigError, match="missing configuration file"):
        C.load(str(tmp_path / "missing.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(C.ConfigError, match="invalid JSON"):
        C.load(str(broken))


def test_default_values_are_available():
    c = cfg()
    assert c.width == 480 and c.height == 320 and c.tick_sec == 1.0
    assert c.log_file.endswith("panel.log")


def test_the_free_half_of_the_clock_face_is_on_the_costly_one_is_opt_in():
    """The date is ON out of the box, the seconds are NOT, and each switches ALONE.

    Two keys and not one because the halves cost differently — the date moves at midnight,
    the seconds cost a frame a second — so the point of the test is that neither switch
    drags the other with it.

    The booleans go the same way as `session_alerts`: through DEFAULTS and __getattr__, read
    by truthiness, with no validator. So this also guards that the keys EXIST — without a
    default, a typo in the name would read as an AttributeError at the first tick."""
    assert cfg().clock_date is True and cfg().clock_seconds is False
    on_seconds = cfg(clock_seconds=True)
    assert on_seconds.clock_seconds is True and on_seconds.clock_date is True
    off_date = cfg(clock_date=False)
    assert off_date.clock_date is False and off_date.clock_seconds is False


def test_a_panel_json_that_predates_clock_seconds_does_not_get_the_ticking_clock():
    """The reason the costly half is opt-in and not opt-out.

    DEFAULTS is merged UNDER the file (`Config.__init__`), so a `true` default reaches
    every panel.json ever written — including the old `{"device": {...}}` shape, which
    `_migrate_panels` turns into an `ax206` entry. That driver has no partial updates
    (drivers/ax206.py:258 `rect_updates=False`), so `surface.for_caps` gives it a
    FullFrameSurface and a header that ticks in seconds is a 307 200-byte write every
    `tick_sec` — 0.35 s of the 1.0 s tick, for as long as the panel is plugged in.
    Nothing in validate() can catch it: such a file is entirely correct."""
    legacy = C.Config({"stream_token": "t", "account_1": {"uuid": "a"},
                       "device": {"port_path": "3.4"}})
    assert not legacy.validate()
    assert [p.backend for p in legacy.panels] == ["ax206"]
    assert "clock_seconds" not in legacy._raw
    assert legacy.clock_seconds is False
