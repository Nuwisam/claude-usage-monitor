"""The scheduled task's target. The .pyw extension + pythonw.exe = no console window.

This file lives in the repo, but the task does NOT point at it directly. Under
%LOCALAPPDATA%\\claude-usage-monitor\\panel-run.pyw sits a dozen or so lines of
redirection that run THIS file from the repo — the same convention as with the
probe (client/README.md). Thanks to that, editing in the repo takes effect at once,
without copying, and Windows does not demand administrator rights for a link.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Exit codes of `--card`. 4 is the one `python -m panel` already gives for a lock
# that someone else holds (panel/__main__.py), so the two entry points agree.
CARD_DRAWN = 0
CARD_PARTIAL = 1
CARD_LOCKED = 4


def _card(lines, retry_sec=0.0):
    """A full-screen card on the panel.

    ConfigError promises in its own docstring that a configuration error is VISIBLE
    ON THE PANEL, and Renderer._message repeats the same sentence. Neither of them
    was true: app.main() raises before App comes into being, so nobody ever reached
    the device. What stayed on the desk was the last good image — frozen,
    credible-looking numbers, that is exactly the failure mode rule 4 of AGENTS.md
    defends against. A log is no substitute: nobody opens it until they see that
    something is wrong.

    The card goes to ALL configured screens, each one separately: half a desk with
    an error and half with frozen, credible-looking numbers would be worse than the
    state before this function existed.

    Returns (screens drawn, screens configured). A screen that does not take the card
    is tried again for up to `retry_sec` seconds: right after the client was killed,
    the device can still be held by the process that is dying.
    """
    from panel import config as C, render
    from panel.drivers import REGISTRY
    from panel.link import PanelLink

    # From the file we take ONLY the screen selector and the path to libusb, and only
    # when they have the right shape. The reason for "we take": validation rejects a
    # configuration most often for a reason that has nothing to do with the choice of
    # device (a missing token, a bad uuid), and with two screens the default absence of
    # a selector would hit neither. The reason for "only": this is the path that shows
    # a CONFIGURATION ERROR, so it must not be fed unchecked fields from that same
    # file — `"device": "something"` would raise AttributeError in select(), and
    # `"brightness": "a lot"` a ValueError in set_brightness(). An error while showing
    # an error leaves the glass dark.
    try:
        raw = C.load()._d
    except C.ConfigError:
        raw = {}
    safe = {}
    if isinstance(raw.get("libusb_dll"), str):
        safe["libusb_dll"] = raw["libusb_dll"]
    entries = []
    for entry in (raw.get("panels") or []):
        if not isinstance(entry, dict) or entry.get("backend") not in REGISTRY:
            continue
        mod = REGISTRY[entry["backend"]]
        # Sanitization per entry: the backend and a well-typed selector stay.
        # `brightness` DROPS OUT deliberately — a value from a broken file would go
        # straight into set_brightness(), and that is the one thing this function
        # has to survive.
        clean = {"backend": entry["backend"]}
        for key in mod.SELECTOR_KEYS:
            value = entry.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                clean[key] = value
        # `rotate` STAYS, unlike brightness: a screen mounted upside down would get
        # the error card upside down, that is unreadable — and that is precisely the
        # one thing this function is there to show. Safe, because we let through only
        # two known values; anything else means 0 and a visible card, not dark glass.
        if entry.get("rotate") in C.ROTATIONS:
            clean["rotate"] = entry["rotate"]
        entries.append(clean)
    if not entries:
        # Nothing survived — we fall back to today's semantics: no selector, that is
        # "exactly one screen or DeviceNotFound". By taking everything in sight we
        # would break the rule from device.py's header precisely where nothing can
        # be checked.
        entries = [{"backend": name} for name in sorted(REGISTRY)]
    safe["panels"] = entries

    cfg = C.Config(safe)
    state = render.ScreenState(message=lines)
    # One card per canvas, drawn when a screen of that size first asks for it. The
    # fallback above widens `entries` to every driver in the registry, so this path
    # can easily be looking at two sizes at once — and a card of the wrong size is a
    # card nobody reads, which is the one thing this function exists to prevent.
    cards = {}
    pending = list(cfg.panels)
    deadline = time.monotonic() + retry_sec
    while True:
        failed = []
        for spec in pending:
            drawn = False
            link = PanelLink(spec, cfg)
            try:
                frame = cards.get(link.canvas)
                if frame is None:
                    frame = cards[link.canvas] = render.Renderer(*link.canvas).frame(state)
                drawn = bool(link.send(frame, force=True))
            except Exception:
                # One busy screen must not take the card away from the others.
                pass
            finally:
                link.close()
            if not drawn:
                failed.append(spec)
        pending = failed
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    return len(cfg.panels) - len(pending), len(cfg.panels)


def _card_main(lines):
    """`run.pyw --card <title> [<line>...]`: one card on every screen, then exit.

    Run by the supervisor (tray.py) and the guardian (guard.py) when they stop the
    client or give up on it for a while: a stopped panel keeps its last frame, and
    frozen, credible-looking numbers are the failure mode rule 4 of AGENTS.md exists
    for. The words are theirs; this only draws them.

    It takes panel.lock like the client does, so a card and a client never share a
    device. Nothing written here directly says "panel already running" or "first
    frame", the phrases deploy/panel-common.ps1 reads as a client's start. One does
    reach panel.log all the same: PanelLink.send logs "first frame after opening" for
    every screen the card opens. A card drawn while an installer waits would read as
    a client starting; it is accepted, because cards are drawn only on Stop / Quit
    and after five failed starts, neither of which happens inside an install's wait.
    """
    from panel import config as C, log as logmod
    from panel.lock import AlreadyRunning, acquire

    try:
        logger = logmod.setup(C.DEFAULT_LOG, "INFO", console=False)
    except Exception:
        logger = None
    try:
        # Retried: the card follows a kill, and the killed client can hold the lock
        # for a moment after its job reports empty.
        held = acquire(timeout=10.0)
    except AlreadyRunning:
        if logger:
            logger.info("card not shown: the panel lock is held")
        return CARD_LOCKED
    try:
        drawn, total = _card(lines, retry_sec=10.0)
    finally:
        held.close()
    if logger:
        logger.info("card %r shown on %d of %d screen(s)", lines[0], drawn, total)
    return CARD_DRAWN if drawn == total else CARD_PARTIAL


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--card"]:
        # Before `panel.app`: a card needs the renderer and the drivers, not the
        # stream client or the main loop.
        return _card_main(argv[1:] or ["Panel stopped"])

    from panel.app import main as run
    from panel import config as C, log as logmod

    from panel.app import AlreadyRunning

    try:
        drew = run()
        if drew is False:
            # Stopped from the tray, and the stopped frame did not reach every screen:
            # no data came in yet, or a screen refused it. The supervisor puts up its
            # own card on every screen (keep.EXIT_STOPPED_BLANK).
            from panel import keep
            return keep.EXIT_STOPPED_BLANK
        return 0
    except AlreadyRunning as e:
        try:
            logmod.setup(C.DEFAULT_LOG, "INFO", console=False).info("%s", e)
        except Exception:
            pass
        return 0
    except C.ConfigError as e:
        # With no console the message has to reach the log — otherwise a restart loop
        # every minute leaves nothing behind it.
        try:
            logmod.setup(C.DEFAULT_LOG, "INFO", console=False).error(
                "config error: %s", e)
        except Exception:
            pass
        try:
            _card(["Configuration error", str(e), C.CONFIG_PATH])
        except Exception:
            # The panel is at times busy or unplugged. The log message has already
            # gone out, and falling over here would turn a bad config into a hard
            # failure.
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
