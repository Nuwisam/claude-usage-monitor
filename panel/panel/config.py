"""Panel configuration — %LOCALAPPDATA%\\claude-usage-monitor\\panel.json.

The same directory as the probe's config.json, but a SEPARATE file. The stream
token has a different scope than the ingest token (backend/app/auth.py:62-67
rejects an ingest one on /stream), and the probe's file gets overwritten when the
probe is updated — the panel settings must not be lost along the way.

The accounts are two NAMED fields, not a list. Layout 4a has exactly two bands, so
the shape of the configuration is the shape of the screen here: a third account
cannot be added by inattention, it takes a deliberate decision about which two
you watch.
"""
import json
import math
import os

APP_DIR_NAME = "claude-usage-monitor"

_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
OUTDIR = os.path.join(_base, APP_DIR_NAME)
CONFIG_PATH = os.path.join(OUTDIR, "panel.json")
DEFAULT_LOG = os.path.join(OUTDIR, "panel.log")

DEFAULTS = {
    "stream_url": "https://usage.example.org/claude-usage/api/stream",
    "stream_token": None,
    "account_1": None,
    "account_2": None,
    "device": None,          # {"port_path": "3.4"} or {"index": 0}
    "width": 480,
    "height": 320,
    "brightness": 5,
    "ca_bundle": None,
    "libusb_dll": None,
    "log_path": None,
    "log_level": "INFO",
    "tick_sec": 1.0,
    # The panel gets a frame only when the image differs. This threshold forces a
    # send despite there being no difference, so that a corrupted patch on the
    # glass does not stay there forever — the panel holds its last frame forever.
    "heal_repaint_sec": 300,
    # How long to wait for the first data before painting a status card over the
    # screen. Until then the panel keeps the image from the previous run — that is
    # deliberate.
    "splash_after_sec": 20,
    "record_sse": False,
    # --- blocked Claude Code sessions (the `alert` frame) ---
    # The switch for the whole feature. With `false` the frame is ignored and the
    # panel behaves exactly as before — the mark never lights up once.
    "session_alerts": True,
    # How long blocks TAKE the screen with a card. Afterwards it collapses into an
    # accent bar on the left edge of the account band; the entries live on, they
    # merely stop being takeover-worthy. Counted from the server's `since`, so a
    # panel restart does not resurrect an old card.
    #
    # The window belongs to the SET: the YOUNGEST block opens it, and the card then
    # shows all the waiting ones, including those whose own window has burnt out. An
    # entry without `since` always counts as fresh. This does not extend the card's
    # time on screen — it changes the contents.
    #
    # A number of seconds, 0 (the mark right away, no card) or "infinity" — the card
    # then stays until you answer, and usage is invisible for that whole time.
    "alert_takeover_sec": 300,
    # How long the card BLINKS, that is, swaps the blank frame for one flooded with
    # the accent. A number of seconds, 0 disables it, or "infinity" — it then blinks
    # for the card's whole life, that is, up to `alert_takeover_sec`.
    #
    # The flood repaints the banner and the 6 px rail (~13% of the frame, about
    # 0.24 s on the Turing) — the rail is there at rest too, in `NEUTRAL_900`, so
    # the color changes, not the layout. There is no stepped animation and there will
    # not be: the panel repaints line by line, so intermediate frames would tear over
    # the sweep. Every blink costs a full frame on the AX206 (355 ms), so "infinity"
    # occupies the link for the card's whole life. It lights up once per block KEY.
    "alert_flash_sec": 20,
    # How long a block has to last before the card comes in. Guards against a flash
    # when permission is granted right away. A JUDGEMENT, not a measurement — hence
    # the configuration key.
    "blocked_debounce_sec": 2,
    # How long the card stays after the last block is gone, FROZEN. A scene change
    # costs a full frame on both screens, so every switch saved is a real ~1.5 s in
    # which the panel refreshes nothing else.
    "blocked_linger_sec": 10,
}


class ConfigError(Exception):
    """A configuration error. Visible on the panel, not only in the log."""


class Account:
    __slots__ = ("uuid", "name", "slot")

    def __init__(self, uuid, name, slot):
        self.uuid = uuid
        self.name = name
        self.slot = slot

    def __repr__(self):
        return "<Account %s %s>" % (self.slot, self.name or self.uuid)


# Keys of a `panels` entry that describe the panel rather than pointing at a
# device. Everything else in the entry is a selector, so a key missing from here
# would travel into select() and be silently ignored there.
PANEL_KEYS = ("backend", "brightness", "name", "rotate")

# How the glass is mounted, in degrees counter-clockwise, ON TOP of whatever
# rotation the driver already applies. Half turns only: see Caps.rotated().
ROTATIONS = (0, 180)


class PanelSpec:
    """One screen from the `panels` list: which driver, which device, how bright,
    which way up."""

    __slots__ = ("backend", "selector", "brightness", "name", "index", "rotate")

    def __init__(self, backend, selector, brightness=None, name=None, index=0,
                 rotate=0):
        self.backend = backend
        self.selector = selector or {}
        self.brightness = brightness
        self.name = name
        self.index = index                  # position in the list, for messages
        self.rotate = rotate or 0

    @property
    def tag(self):
        """What this panel is called in the log. With more than one screen every
        line has to say which one it is about."""
        where = self.selector.get("port_path") or self.selector.get("com")
        if self.name:
            return "%s %s" % (self.backend, self.name)
        return "%s %s" % (self.backend, where) if where else self.backend

    def __repr__(self):
        return "<PanelSpec %s %r>" % (self.backend, self.selector)


class Config:
    def __init__(self, data, path=CONFIG_PATH):
        self.path = path
        # The raw file as written, kept for PRESENCE checks only - values are
        # always read from _d. Without it there is no way to tell "the user wrote
        # brightness" from "DEFAULTS put brightness there", and rules about which
        # keys may appear together become impossible to state.
        self._raw = dict(data or {})
        self._d = dict(DEFAULTS)
        self._d.update(data or {})
        self._d["panels"] = self._migrate_panels()

    def _migrate_panels(self):
        """The old single-screen shape becomes a one-entry list.

        This runs in __init__, not in validate(), because everything that builds a
        Config expects `panels` to exist - including run.pyw's error card path and
        the tests, neither of which validates first.

        Migrating `device` is safe precisely because the old value had exactly one
        possible meaning: there was one driver. That is the difference from the
        legacy `location` selector, which is rejected instead of migrated - there
        the VALUE itself was untrustworthy, so honouring it would mean guessing
        which module the author meant.
        """
        raw = self._raw.get("panels")
        if raw is not None:
            return raw if isinstance(raw, list) else []
        device = self._raw.get("device")
        entry = {}
        if isinstance(device, dict):
            entry.update(device)
        if "brightness" in self._raw:
            entry["brightness"] = self._raw["brightness"]
        # `backend` last: a file with a stray "device": {"backend": ...} must not
        # be able to point the old shape at a different driver.
        entry["backend"] = "ax206"
        return [entry]

    def __getattr__(self, name):
        try:
            return self._d[name]
        except KeyError:
            raise AttributeError(name)

    @property
    def log_file(self):
        return self.log_path or DEFAULT_LOG

    @property
    def accounts(self):
        """Accounts in band order: top, bottom. Empty slots are skipped."""
        out = []
        for slot in ("account_1", "account_2"):
            raw = self._d.get(slot)
            if not raw:
                continue
            out.append(Account(raw.get("uuid"), raw.get("name"), slot))
        return out

    @property
    def panels(self):
        """Configured screens, in file order. Malformed entries are skipped here
        and reported by validate() - this property is read by code that already
        passed validation."""
        out = []
        for i, raw in enumerate(self._d.get("panels") or []):
            if not isinstance(raw, dict) or not raw.get("backend"):
                continue
            selector = {k: v for k, v in raw.items() if k not in PANEL_KEYS}
            out.append(PanelSpec(raw["backend"], selector, raw.get("brightness"),
                                 raw.get("name"), i, raw.get("rotate")))
        return out

    def validate(self):
        """Returns a list of problems. An empty list = a usable configuration."""
        problems = []
        if not self.stream_token:
            problems.append("missing stream_token")
        if not self.stream_url:
            problems.append("missing stream_url")

        seen = set()
        for slot in ("account_1", "account_2"):
            raw = self._d.get(slot)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                problems.append("%s must be an object {\"uuid\": ...}" % slot)
                continue
            uuid = (raw.get("uuid") or "").strip()
            if not uuid:
                problems.append("%s has no uuid" % slot)
            elif uuid in seen:
                problems.append("%s repeats the uuid from the previous slot" % slot)
            else:
                seen.add(uuid)
        if not seen:
            problems.append("no account specified (account_1 / account_2)")

        self._check_panels(problems)

        dev = self._d.get("device")
        if dev is not None and not isinstance(dev, dict):
            problems.append("device must be an object, e.g. "
                            "{\"port_path\": \"3.4\"}")
        elif isinstance(dev, dict) and "location" in dev:
            # The `location` selector took "Port_#0004.Hub_#0005" from the registry,
            # where `Hub_#NNNN` is an enumeration counter, not hardware. It jumped
            # with the plug untouched and the panel stopped finding itself. A silent
            # migration is out: it would take guessing which module was meant, and
            # that is exactly what is being got rid of here.
            problems.append(
                "device.location (\"%s\") is no longer supported — the Hub_# "
                "part is an enumeration counter that jumps without the plug "
                "being touched. Run `python -m panel --list` and enter the "
                "port_path it reports" % dev.get("location"))
        # An upper bound ONLY where something states one: 0..7 is the range of the
        # PROPERTY_BRIGHTNESS property in the AX206 firmware. The rest get the floor
        # alone, because a ceiling would have to be invented — and an invented
        # threshold that rejects a correct configuration is worse than no threshold.
        #
        # In the new shape brightness is per panel and the scales differ, so the
        # top-level key is checked only where it can still mean the AX206 range.
        if "panels" not in self._raw:
            self._number(problems, "brightness", int, 0, 7)
        self._number(problems, "tick_sec", float, 0.01)
        self._number(problems, "width", int, 1)
        self._number(problems, "height", int, 1)
        return problems

    def _check_panels(self, problems):
        """The `panels` list: shape, driver names, selector keys, brightness.

        Everything here APPENDS a problem and never raises, including the numeric
        checks - a hand-edited panel.json is the normal case, and a TypeError out
        of validate() would reach the excepthook under pythonw, where nobody sees
        it and the task restarts every minute.
        """
        from .drivers import REGISTRY, known

        if "panels" in self._raw and "device" in self._raw:
            problems.append(
                "panel.json has both `device` (the old shape) and `panels` (the "
                "new one) — leave one; merging them would mean guessing")
        if "panels" in self._raw and "brightness" in self._raw:
            problems.append(
                "brightness is now per panel, in each `panels` entry — driver "
                "scales differ, so a top-level `brightness` would be ambiguous")

        raw = self._raw.get("panels")
        if raw is not None and not isinstance(raw, list):
            problems.append("panels must be a list of objects")
            return
        entries = self._d.get("panels") or []
        if not entries:
            problems.append("no panel specified (`panels`)")
            return

        seen = {}
        for i, entry in enumerate(entries):
            where = "panels[%d]" % i
            if not isinstance(entry, dict):
                problems.append("%s must be an object" % where)
                continue
            backend = entry.get("backend")
            if backend not in REGISTRY:
                problems.append("%s: unknown backend %r (known: %s)"
                                % (where, backend, ", ".join(known())))
                continue
            mod = REGISTRY[backend]

            if "location" in entry:
                # The value itself is untrustworthy, so there is nothing to
                # migrate: `Hub_#NNNN` inside it is an enumeration counter that
                # jumped without anyone touching a plug.
                problems.append(
                    "%s.location (\"%s\") is no longer supported — the Hub_# "
                    "part is an enumeration counter that jumps without the "
                    "plug being touched. Run `python -m panel --list` and "
                    "enter the port_path it reports"
                    % (where, entry.get("location")))
                continue

            extra = [k for k in entry
                     if k not in mod.SELECTOR_KEYS and k not in PANEL_KEYS]
            if extra:
                # An unknown key used to match nothing and fall through to "the
                # only device there is" - a typo quietly aimed the client at
                # whatever happened to be plugged in.
                problems.append(
                    "%s: unknown keys %s; for %s the allowed ones are: %s"
                    % (where, ", ".join(sorted(extra)), backend,
                       ", ".join(mod.SELECTOR_KEYS)))

            key = (backend, tuple(sorted((k, str(v)) for k, v in entry.items()
                                         if k in mod.SELECTOR_KEYS)))
            if key in seen and key[1]:
                problems.append("%s points at the same device as %s"
                                % (where, seen[key]))
            seen.setdefault(key, where)

            if entry.get("brightness") is not None:
                scale = mod.caps_for(self._canvas()).brightness
                self._panel_number(problems, "%s.brightness" % where,
                                   entry["brightness"], scale)

            if entry.get("rotate") is not None:
                self._panel_rotate(problems, where, entry["rotate"])

    def _canvas(self):
        """(width, height) if they are usable, else None.

        This runs before the numeric checks below, and a hand-edited file can hold
        "480" or nonsense there; a driver asked for its capabilities must not be
        the place that discovers it.
        """
        try:
            return (int(self._d["width"]), int(self._d["height"]))
        except (TypeError, ValueError, OverflowError, KeyError):
            return None

    @staticmethod
    def _panel_rotate(problems, where, raw):
        """How the panel is mounted. Two separate messages on purpose.

        "180" as a string and 90 as a number are different mistakes: the first is
        a typing slip, the second is someone asking for a portrait screen. Telling
        them apart is the difference between a fix and a puzzle.
        """
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            problems.append("%s.rotate must be a number of degrees (got: %r)"
                            % (where, raw))
            return
        if value not in ROTATIONS:
            problems.append(
                "%s.rotate=%r — only %s allowed. A quarter turn would require a "
                "portrait layout (320x480), and only one 3:2 layout is drawn"
                % (where, raw, " or ".join(str(v) for v in ROTATIONS)))

    @staticmethod
    def _panel_number(problems, name, raw, scale):
        """Per-panel brightness against that driver's own scale.

        int() is the whole guard: it rejects strings, None, Infinity (OverflowError)
        and NaN (ValueError) alike, so nothing untyped reaches the comparison.
        """
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            problems.append("%s must be a number (got: %r)" % (name, raw))
            return
        if not (scale.lo <= value <= scale.hi):
            problems.append("%s is out of the range %s for this driver"
                            % (name, scale.describe()))

    def _number(self, problems, name, kind, low, high=None):
        """One numeric field: APPENDS a problem, never raises.

        A bare `int(self.brightness)` in the body of validate() was a pitfall. This
        function promises to return a list of problems, and with
        `"brightness": "jasno"` in panel.json a ValueError came out of it instead.
        The excepthook is installed by then, so instead of one sentence about what
        to fix there was a traceback and a task restart every minute — under
        pythonw, with no console to show it to anyone.
        """
        raw = self._d.get(name)
        try:
            value = kind(raw)
        except (TypeError, ValueError, OverflowError):
            # OverflowError, because json.load accepts bare `Infinity` and `NaN`,
            # and int(float("inf")) is neither a TypeError nor a ValueError.
            problems.append("%s must be a number (got: %r)" % (name, raw))
            return
        if isinstance(value, float) and not math.isfinite(value):
            # `Infinity` and `NaN` pass through float() and through EVERY range
            # comparison, so without this line they fall into the tick loop:
            # wait(inf) raises OverflowError there, and wait(nan) returns at once
            # and turns the timing into a busy loop.
            problems.append("%s must be a finite number (got: %r)" % (name, raw))
            return
        if value < low:
            problems.append("%s must be >= %s" % (name, low))
            return
        if high is not None and value > high:
            problems.append("%s is out of the range %s..%s" % (name, low, high))
            return
        # The value is stored AFTER conversion. Without this the check was only
        # apparent: "width": "480" passed validation, because int("480") succeeds —
        # and then Layout computed `"480" - 1` and broke with a TypeError AFTER
        # validate() had announced the configuration usable.
        self._d[name] = value


def load(path=CONFIG_PATH):
    """Loads the configuration. A missing file and bad JSON are TWO different
    errors — the first means 'not installed yet', the second 'broken while editing'."""
    if not os.path.exists(path):
        raise ConfigError("missing configuration file: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise ConfigError("panel.json is invalid JSON: %s" % e) from e
    except OSError as e:
        raise ConfigError("cannot read %s: %s" % (path, e)) from e
    if not isinstance(data, dict):
        raise ConfigError("panel.json must contain a JSON object")
    return Config(data, path)


def example():
    """A template to paste in — used by --list and the README."""
    return json.dumps({
        "stream_url": DEFAULTS["stream_url"],
        "stream_token": "<the STREAM_TOKENS entry labeled panel>",
        "account_1": {"uuid": "<account uuid>", "name": "you@example.org"},
        "account_2": {"uuid": "<account uuid>", "name": "billing@example.org"},
        "panels": [{"backend": "ax206", "port_path": "3.4", "brightness": 5}],
    }, indent=2, ensure_ascii=False)
