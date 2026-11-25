"""tray.py's pure parts: the icons, the words on them, the command file.

The icon loop itself is pystray's and needs a desktop; what can be checked without
one is checked here, and none of it imports pystray.
"""
import os
from datetime import datetime, timedelta, timezone

from panel import supervisor as S, tray

NOW = datetime(2026, 11, 25, 20, 7, tzinfo=timezone.utc)


def status(state="running", since=NOW, restarts=0, last=None, detail=None):
    return S.Status(state, since, restarts, last, detail)


def test_three_states_three_different_icons():
    images = dict((key, tray.icon_image(key).tobytes())
                  for key in ("running", "waiting", "stopped"))
    assert len(set(images.values())) == 3


def test_the_icon_key_of_each_state():
    assert tray.icon_key(None) == "waiting"
    assert tray.icon_key(status("running")) == "running"
    assert tray.icon_key(status("waiting")) == "waiting"
    assert tray.icon_key(status("stopped")) == "stopped"
    assert tray.icon_key(status("quitting")) == "stopped"


def test_the_tooltip_says_state_time_and_restarts():
    text = tray.tooltip(status(restarts=2, last="exit 1"), NOW)
    assert text.startswith("Claude panel: running since ")
    assert text.endswith("2 restarts, last: exit 1")
    assert tray.tooltip(status(restarts=1, last="exit 1"), NOW).endswith("1 restart, last: exit 1")


def test_the_tooltip_carries_the_date_when_it_is_not_today():
    text = tray.tooltip(status(since=NOW - timedelta(days=2)), NOW)
    assert "23.11 " in text


def test_the_tooltip_fits_the_notify_icon_buffer():
    text = tray.tooltip(status("waiting", detail="x" * 400), NOW)
    assert len(text) <= tray.TIP_MAX
    assert text.endswith("...")


def test_the_menu_status_line():
    assert tray.status_line(None, NOW) == "Starting"
    assert tray.status_line(status("waiting", detail="restart pending after exit 1"),
                            NOW) == "Waiting: restart pending after exit 1"
    assert tray.status_line(status("stopped"), NOW).startswith("Stopped since ")
    assert len(tray.status_line(status("waiting", detail="y" * 300), NOW)) <= tray.MENU_LINE_MAX


class Log:
    def __init__(self):
        self.lines = []

    def warning(self, msg, *args):
        self.lines.append(msg % args)


def test_the_command_file_is_read_once(tmp_path):
    path = tmp_path / "tray.cmd"
    path.write_text("Stop\r\n", encoding="ascii")
    reader = tray.CommandFile(str(path), Log())
    assert reader.read() == "stop"
    assert not path.exists()
    assert reader.read() is None


def test_the_command_file_in_utf16_from_powershell(tmp_path):
    path = tmp_path / "tray.cmd"
    path.write_bytes("restart\r\n".encode("utf-16"))     # with the BOM, as `>` writes it
    assert tray.CommandFile(str(path), Log()).read() == "restart"


def test_an_unknown_command_is_cleared_and_logged(tmp_path):
    path = tmp_path / "tray.cmd"
    path.write_text("reboot", encoding="ascii")
    log = Log()
    assert tray.CommandFile(str(path), log).read() is None
    assert not path.exists()
    assert "unknown command 'reboot'" in log.lines[0]


def test_an_empty_file_is_given_time_to_be_written(tmp_path):
    path = tmp_path / "tray.cmd"
    path.write_text("", encoding="ascii")
    t = [os.stat(path).st_mtime + 1]
    reader = tray.CommandFile(str(path), Log(), clock=lambda: t[0])
    assert reader.read() is None and path.exists()
    t[0] += 10
    assert reader.read() is None and not path.exists()
