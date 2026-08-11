"""The scheduled task's target. The .pyw extension + pythonw.exe = no console window.

This file lives in the repo, but the task does NOT point at it directly. Under
%LOCALAPPDATA%\\claude-usage-monitor\\panel-run.pyw sits a dozen-odd lines of
redirection that run THIS file from the repo — the same convention as with the
probe (client/README.md). Thanks to that, editing in the repo takes effect at once,
without copying, and Windows does not demand administrator rights for a link.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _card(lines):
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
    frame = render.Renderer(cfg.width, cfg.height).frame(
        render.ScreenState(message=lines))
    for spec in cfg.panels:
        link = PanelLink(spec, cfg)
        try:
            link.send(frame, force=True)
        except Exception:
            # One busy screen must not take the card away from the others.
            pass
        finally:
            link.close()


def main():
    from panel.app import main as run
    from panel import config as C, log as logmod

    from panel.app import AlreadyRunning

    try:
        run()
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
