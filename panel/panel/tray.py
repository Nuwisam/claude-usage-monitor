"""The tray supervisor: keeps the panel client alive, and puts it on an icon.

The middle of the chain (keep.py): started by the guardian (guard.py), it starts the
client (run.pyw) through winjob.spawn and runs `supervisor.Supervisor` once a second
on its own thread. The icon — Restart, Stop/Start, the two logs, Quit — lives on the
main thread, because pystray's Win32 message loop must.

Order of start, and why:
  1. the log, so that anything below leaves a trace (pythonw has no console);
  2. the first heartbeat, before anything slow: the guardian calls a supervisor that
     never beats hung after its start grace;
  3. tray.lock — a second supervisor would fight the first over tray.state;
  4. the supervisor thread: from here on the panel is kept alive;
  5. pystray, last and lazily. If it does not load, or its loop ends on its own, the
     supervision goes on without an icon. Keeping the panel alive must never depend
     on the part that draws a picture of it.

Every call into pystray happens on the icon's thread. The Win32 backend builds its
menu ahead of time, and rebuilding it from another thread destroys a menu that may be
on screen; the supervisor thread therefore only POSTS a message to the icon's window
(`TrayUi.refresh`), and the menu is rebuilt right before it is shown. That takes two
hooks into pystray's private Win32 class (`_message_handlers`, `_on_notify`), which
is one reason requirements.txt pins its version.
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone

from . import config as C, fmt, keep, lock, log as logmod, supervisor as S, theme

LOG = os.path.join(C.OUTDIR, keep.TRAY_LOG)

WM_RBUTTONUP = 0x0205
WM_APP_REFRESH = 0x8000 + 0x51      # WM_APP + n: ours, nobody else posts it

TIP_MAX = 127                       # NOTIFYICONDATAW.szTip is 128 WCHARs with the NUL
MENU_LINE_MAX = 64


# -- the pure part: what the icon and its text look like ------------------------------

def icon_image(state, size=64):
    """The tray icon for a state: the web UI's favicon (frontend/public/favicon.svg),
    redrawn here because Pillow does not read SVG. Same 32-unit geometry, same three
    colours — the favicon's #1c1b19, #322f2b and #d97757 are theme.BG, NEUTRAL_900
    and ACCENT.

    The meter's FILL carries the state, by its shape and not only its colour — the
    project has no red, and colour alone is the first thing to go unnoticed:
      running   the favicon as it is: a filled bar
      waiting   the bar as an outline only (a restart is pending)
      stopped   the empty track, no bar at all
    """
    from PIL import Image, ImageDraw

    ss = 4                          # drawn 4x larger, then scaled down: smooth edges
    unit = size * ss / 32.0

    def box(x, y, w, h):
        return (round(x * unit), round(y * unit),
                round((x + w) * unit) - 1, round((y + h) * unit) - 1)

    img = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box(0, 0, 32, 32), round(7 * unit), fill=theme.BG)
    d.rounded_rectangle(box(6, 13, 20, 6), round(3 * unit), fill=theme.NEUTRAL_900)
    if state == "running":
        d.rounded_rectangle(box(6, 13, 12, 6), round(3 * unit), fill=theme.ACCENT)
    elif state == "waiting":
        d.rounded_rectangle(box(6, 13, 12, 6), round(3 * unit), outline=theme.ACCENT,
                            width=max(1, round(1.5 * unit)))
    return img.resize((size, size), Image.LANCZOS)


def icon_key(status):
    if status is None:
        return "waiting"
    return {"running": "running", "waiting": "waiting"}.get(status.state, "stopped")


def _when(since, now):
    """'21:07' today, '24.11 21:07' on another day (AGENTS.md, Locale)."""
    if since is None:
        return "?"
    if fmt.to_local(since).date() == fmt.to_local(now).date():
        return fmt.hm(since)
    return "%s %s" % (fmt.dm(since), fmt.hm(since))


def _clip(text, limit):
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def tooltip(status, now):
    if status is None:
        return "Claude panel: starting"
    text = "Claude panel: %s since %s" % (status.state, _when(status.since, now))
    if status.state == "waiting" and status.detail:
        text += " - %s" % status.detail
    elif status.restarts:
        text += " - %d restart%s, last: %s" % (
            status.restarts, "" if status.restarts == 1 else "s", status.last)
    return _clip(text, TIP_MAX)


def status_line(status, now):
    """The first, greyed-out line of the menu."""
    if status is None:
        return "Starting"
    if status.state == "waiting" and status.detail:
        return _clip("Waiting: %s" % status.detail, MENU_LINE_MAX)
    line = "%s since %s" % (status.state.capitalize(), _when(status.since, now))
    if status.restarts:
        line += ", %d restart%s" % (status.restarts, "" if status.restarts == 1 else "s")
    return _clip(line, MENU_LINE_MAX)


def panel_log_path():
    """Where the CLIENT writes: `log_path` from panel.json, relative to the task's
    working directory (C.OUTDIR), or the default."""
    try:
        path = C.load().log_file
    except Exception:
        return C.DEFAULT_LOG
    return path if os.path.isabs(path) else os.path.join(C.OUTDIR, path)


class CommandFile:
    """tray.cmd: one word — start, stop, restart, quit — the menu's commands without
    the mouse. Read once, then deleted, so a command never runs twice.

    `"stop" > tray.cmd` in Windows PowerShell 5.1 writes UTF-16 with a BOM and
    Set-Content writes the ANSI code page; both are read.
    """

    def __init__(self, path, logger, clock=time.time):
        self.path = path
        self.log = logger
        self.clock = clock

    def read(self):
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16", errors="replace")
        else:
            text = raw.decode("utf-8-sig", errors="replace")
        word = text.strip().lower()
        if not word:
            # Empty: being written this very moment, or left empty. Give a writer a
            # few seconds before the file is cleared away.
            try:
                if self.clock() - os.stat(self.path).st_mtime < 5:
                    return None
            except OSError:
                return None
        try:
            os.remove(self.path)
        except OSError:
            return None     # not consumable: acting on it would repeat it every second
        if word in S.COMMANDS:
            return word
        if word:
            self.log.warning("tray.cmd: unknown command %r (known: %s)", word,
                             ", ".join(S.COMMANDS))
        return None


# -- the processes -------------------------------------------------------------------

def _spawn(args):
    from . import winjob
    return winjob.spawn([sys.executable, os.path.join(C.OUTDIR, keep.CLIENT_LAUNCHER)]
                        + list(args), cwd=C.OUTDIR)


def build(logger, epoch):
    """The supervisor with the real world plugged in."""
    from . import winjob    # noqa: F401 - fail here, at start, not at the first spawn

    return S.Supervisor(
        spawn_client=lambda: _spawn([]),
        spawn_card=lambda lines: _spawn(["--card"] + list(lines)),
        lock_free=lambda: lock.is_free(name="panel"),
        launcher_ok=lambda: os.path.isfile(os.path.join(C.OUTDIR, keep.CLIENT_LAUNCHER)),
        beat=keep.Beat(os.path.join(C.OUTDIR, keep.CLIENT_HEARTBEAT),
                       S.HANG_SEC, S.START_GRACE_SEC),
        config_mtime=lambda: keep.mtime_ns(C.CONFIG_PATH),
        store=keep.StateStore(os.path.join(C.OUTDIR, keep.TRAY_STATE), epoch),
        logger=logger,
        read_command=CommandFile(os.path.join(C.OUTDIR, keep.TRAY_COMMAND), logger).read)


# -- the icon ------------------------------------------------------------------------

_user32 = None


def _post(hwnd, msg):
    global _user32
    if _user32 is None:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.PostMessageW.restype = wintypes.BOOL
        _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM)
    return bool(_user32.PostMessageW(hwnd, msg, 0, 0))


def _icon_class(pystray):
    class PanelIcon(pystray.Icon):
        def __init__(self, *args, ui=None, **kwargs):
            self._panel_ui = ui
            super().__init__(*args, **kwargs)
            self._message_handlers[WM_APP_REFRESH] = self._on_panel_refresh

        def _on_panel_refresh(self, wparam, lparam):
            self._panel_ui.apply()

        def _on_notify(self, wparam, lparam):
            if lparam == WM_RBUTTONUP:
                # Rebuilt here, on this thread, right before the menu is shown: it is
                # current, and no other thread ever touches it.
                self.update_menu()
            return super()._on_notify(wparam, lparam)

    return PanelIcon


class TrayUi:
    def __init__(self, sup, logger, now=lambda: datetime.now(timezone.utc)):
        self.sup = sup
        self.log = logger
        self.now = now
        self.icon = None
        self._images = {}
        self._shown = None
        self._stop_requested = False

    def run(self):
        """Main thread. Returns when the icon is stopped, or could not be had."""
        try:
            import pystray
        except Exception as e:
            self.log.error("no tray icon: pystray did not load (%s: %s); the panel is "
                           "supervised without it", type(e).__name__, e)
            return
        if self._stop_requested:
            return
        try:
            self._images = dict((key, icon_image(key))
                                for key in ("running", "waiting", "stopped"))
            self.icon = _icon_class(pystray)("claude-panel", self._images["waiting"],
                                             "Claude panel", menu=self._menu(pystray),
                                             ui=self)
            self.icon.run(setup=self._ready)
        except Exception as e:
            self.log.error("tray icon failed: %s: %s", type(e).__name__, e,
                           exc_info=True)
        finally:
            self.icon = None

    def _ready(self, icon):
        """pystray's setup thread, once the icon's loop runs. It only asks the icon's
        own thread to show itself (`apply`) — and honours a stop that came before the
        loop was there to receive it (Quit through tray.cmd in the first second)."""
        if self._stop_requested:
            icon.stop()
            return
        self.refresh()

    def refresh(self, status=None):
        """Any thread: tell the icon's thread to bring itself up to date."""
        icon = self.icon
        hwnd = getattr(icon, "_hwnd", None) if icon is not None else None
        if hwnd:
            _post(hwnd, WM_APP_REFRESH)

    def apply(self):
        """The icon's thread only."""
        icon = self.icon
        if icon is None:
            return
        try:
            status = self.sup.status
            key = icon_key(status)
            if key != self._shown:
                icon.icon = self._images[key]
                self._shown = key
            title = tooltip(status, self.now())
            if title != icon.title:
                icon.title = title
            if not icon.visible:
                icon.visible = True
        except Exception as e:
            self.log.warning("tray icon not updated: %s: %s", type(e).__name__, e)

    def stop(self):
        # The flag first: a stop() that lands before the loop is ready is a no-op in
        # pystray, and `_ready` reads the flag once it is.
        self._stop_requested = True
        icon = self.icon
        if icon is not None:
            try:
                icon.stop()
            except Exception as e:
                self.log.warning("tray icon did not stop: %s", e)

    def _menu(self, pystray):
        Item, Menu = pystray.MenuItem, pystray.Menu

        def status():
            return self.sup.status

        def is_stopped(item):
            return status() is not None and status().state == "stopped"

        def send(name):
            return lambda icon, item: self.sup.command(name)

        return Menu(
            Item(lambda item: status_line(status(), self.now()), None, enabled=False),
            Menu.SEPARATOR,
            Item("Restart panel", send("restart")),
            Item("Stop panel", send("stop"), visible=lambda item: not is_stopped(item)),
            Item("Start panel", send("start"), visible=is_stopped),
            Menu.SEPARATOR,
            Item("Open panel log", lambda icon, item: self.open(panel_log_path())),
            Item("Open tray log", lambda icon, item: self.open(LOG)),
            Menu.SEPARATOR,
            Item("Quit", send("quit")),
        )

    def open(self, path):
        from . import winjob
        if not os.path.exists(path):
            self.log.warning("cannot open %s: no such file", path)
            return
        # Through the shell, broken away from our job where the jobs above allow it,
        # so a restart of this supervisor does not close what the owner is reading.
        if not winjob.open_detached(["explorer.exe", path]):
            self.log.warning("cannot open %s", path)


# -- the process ---------------------------------------------------------------------

def main():
    logger = logmod.setup(LOG, "INFO", console=False)
    beat = keep.Toucher(os.path.join(C.OUTDIR, keep.TRAY_HEARTBEAT))
    beat.beat(force=True)
    try:
        held = lock.acquire(name="tray", timeout=20.0)
    except lock.AlreadyRunning as e:
        logger.warning("%s; backing off", e)
        return keep.EXIT_BUSY
    epoch = os.environ.get(keep.EPOCH_ENV)
    logger.info("tray supervisor started (pid %d)%s", os.getpid(),
                "" if epoch else ", no guardian: tray.state is not used")
    sup = build(logger, epoch)
    sup.begin()
    ui = TrayUi(sup, logger)
    sup.on_change = ui.refresh
    done = threading.Event()
    errors = keep.Errors(logger)

    def step():
        beat.beat()
        sup.step()
        if sup.done:
            done.set()

    def work():
        try:
            keep.run_loop(step, done, errors)
        finally:
            ui.stop()

    worker = threading.Thread(target=work, name="supervisor")
    worker.start()
    ui.run()
    if not done.is_set():
        logger.warning("the tray icon is gone; the panel stays supervised without it")
    worker.join()
    try:
        sup.shutdown()
    finally:
        held.close()
    if sup.done:
        logger.info("tray supervisor exiting: quit")
        return keep.EXIT_QUIT
    logger.warning("tray supervisor exiting without a quit")
    return 1
