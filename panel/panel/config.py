"""Konfiguracja panelu — %LOCALAPPDATA%\\claude-usage-monitor\\panel.json.

Ten sam katalog co config.json sondy, ale OSOBNY plik. Token strumienia ma inny
zakres niz token ingestu (backend/app/auth.py:62-67 odrzuca ingestowy na /stream),
a plik sondy bywa nadpisywany przy jej aktualizacji — nie chcemy, zeby przy okazji
gubil ustawienia panelu.

Konta sa dwoma NAZWANYMI polami, nie lista. Uklad 4a ma dokladnie dwa pasy, wiec
ksztalt konfiguracji jest tu ksztaltem ekranu: trzeciego konta nie da sie dopisac
przez nieuwage, trzeba swiadomie zdecydowac, ktore dwa ogladasz.
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
    "device": None,          # {"port_path": "3.4"} albo {"index": 0}
    "width": 480,
    "height": 320,
    "brightness": 5,
    "ca_bundle": None,
    "libusb_dll": None,
    "log_path": None,
    "log_level": "INFO",
    "tick_sec": 1.0,
    # Panel dostaje klatke tylko wtedy, gdy obraz sie rozni. Ten prog wymusza
    # wyslanie mimo braku roznicy, zeby ewentualne przeklamanie na ekranie nie
    # zostalo tam na zawsze — panel trzyma ostatnia klatke w nieskonczonosc.
    "heal_repaint_sec": 300,
    # Ile czekac na pierwsze dane, zanim zamalujemy ekran karta stanu. Do tego
    # czasu na panelu zostaje obraz z poprzedniego biegu — to celowe.
    "splash_after_sec": 20,
    "record_sse": False,
}


class ConfigError(Exception):
    """Blad konfiguracji. Widoczny na panelu, nie tylko w logu."""


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
        """Konta w kolejnosci pasow: gorny, dolny. Puste sloty pomijane."""
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
        """Zwraca liste problemow. Pusta lista = konfiguracja zdatna do uzycia."""
        problems = []
        if not self.stream_token:
            problems.append("brak stream_token")
        if not self.stream_url:
            problems.append("brak stream_url")

        seen = set()
        for slot in ("account_1", "account_2"):
            raw = self._d.get(slot)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                problems.append("%s musi byc obiektem {\"uuid\": ...}" % slot)
                continue
            uuid = (raw.get("uuid") or "").strip()
            if not uuid:
                problems.append("%s bez uuid" % slot)
            elif uuid in seen:
                problems.append("%s powtarza uuid z poprzedniego slotu" % slot)
            else:
                seen.add(uuid)
        if not seen:
            problems.append("nie wskazano zadnego konta (account_1 / account_2)")

        self._check_panels(problems)

        dev = self._d.get("device")
        if dev is not None and not isinstance(dev, dict):
            problems.append("device musi byc obiektem, np. "
                            "{\"port_path\": \"3.4\"}")
        elif isinstance(dev, dict) and "location" in dev:
            # Selektor `location` bral z rejestru "Port_#0004.Hub_#0005", gdzie
            # `Hub_#NNNN` jest licznikiem enumeracji, nie sprzetem. Przeskoczyl
            # przy nieruszonej wtyczce i panel przestal sie odnajdywac. Cicha
            # migracja odpada: wymagalaby zgadywania, ktore modul ma na mysli,
            # a to jest dokladnie to, czego sie tu pozbywamy.
            problems.append(
                "device.location (\"%s\") nie jest juz obslugiwane — czlon "
                "Hub_# to licznik enumeracji, ktory przeskakuje bez ruszania "
                "wtyczki. Uruchom `python -m panel --list` i wpisz podany "
                "port_path" % dev.get("location"))
        # Gorna granica TYLKO tam, gdzie ja cos podaje: 0..7 to zakres wlasciwosci
        # PROPERTY_BRIGHTNESS z firmware'u AX206. Reszta dostaje sama podloge,
        # bo sufit musialbym wymyslic — a wymyslony prog, ktory odrzuca poprawna
        # konfiguracje, jest gorszy niz brak progu.
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
                "panel.json ma naraz `device` (stary ksztalt) i `panels` (nowy) "
                "— zostaw jedno; scalanie ich znaczyloby zgadywanie")
        if "panels" in self._raw and "brightness" in self._raw:
            problems.append(
                "jasnosc jest teraz per panel, w kazdym wpisie `panels` — skale "
                "sterownikow sa rozne, wiec gorne `brightness` byloby dwuznaczne")

        raw = self._raw.get("panels")
        if raw is not None and not isinstance(raw, list):
            problems.append("panels musi byc lista obiektow")
            return
        entries = self._d.get("panels") or []
        if not entries:
            problems.append("nie wskazano zadnego panelu (`panels`)")
            return

        seen = {}
        for i, entry in enumerate(entries):
            where = "panels[%d]" % i
            if not isinstance(entry, dict):
                problems.append("%s musi byc obiektem" % where)
                continue
            backend = entry.get("backend")
            if backend not in REGISTRY:
                problems.append("%s: nieznany backend %r (znam: %s)"
                                % (where, backend, ", ".join(known())))
                continue
            mod = REGISTRY[backend]

            if "location" in entry:
                # The value itself is untrustworthy, so there is nothing to
                # migrate: `Hub_#NNNN` inside it is an enumeration counter that
                # jumped without anyone touching a plug.
                problems.append(
                    "%s.location (\"%s\") nie jest juz obslugiwane — czlon Hub_# "
                    "to licznik enumeracji, ktory przeskakuje bez ruszania "
                    "wtyczki. Uruchom `python -m panel --list` i wpisz podany "
                    "port_path" % (where, entry.get("location")))
                continue

            extra = [k for k in entry
                     if k not in mod.SELECTOR_KEYS and k not in PANEL_KEYS]
            if extra:
                # An unknown key used to match nothing and fall through to "the
                # only device there is" - a typo quietly aimed the client at
                # whatever happened to be plugged in.
                problems.append(
                    "%s: nieznane klucze %s; dla %s wolno: %s"
                    % (where, ", ".join(sorted(extra)), backend,
                       ", ".join(mod.SELECTOR_KEYS)))

            key = (backend, tuple(sorted((k, str(v)) for k, v in entry.items()
                                         if k in mod.SELECTOR_KEYS)))
            if key in seen and key[1]:
                problems.append("%s wskazuje to samo urzadzenie co %s"
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
            problems.append("%s.rotate musi byc liczba stopni (jest: %r)"
                            % (where, raw))
            return
        if value not in ROTATIONS:
            problems.append(
                "%s.rotate=%r — wolno tylko %s. Cwierc obrotu wymagalaby ukladu "
                "pionowego (320x480), a rysowany jest jeden uklad 3:2"
                % (where, raw, " albo ".join(str(v) for v in ROTATIONS)))

    @staticmethod
    def _panel_number(problems, name, raw, scale):
        """Per-panel brightness against that driver's own scale.

        int() is the whole guard: it rejects strings, None, Infinity (OverflowError)
        and NaN (ValueError) alike, so nothing untyped reaches the comparison.
        """
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            problems.append("%s musi byc liczba (jest: %r)" % (name, raw))
            return
        if not (scale.lo <= value <= scale.hi):
            problems.append("%s poza zakresem %s dla tego sterownika"
                            % (name, scale.describe()))

    def _number(self, problems, name, kind, low, high=None):
        """Jedno pole liczbowe: DOPISUJE problem, nigdy nie rzuca.

        Golo `int(self.brightness)` w tresci validate() bylo pulapka. Ta funkcja
        obiecuje zwrocic liste problemow, a przy `"brightness": "jasno"`
        w panel.json wychodzil z niej ValueError. Excepthook jest wtedy juz
        ustawiony, wiec zamiast jednego zdania o tym, co poprawic, zostawal
        traceback i restart zadania co minute — pod pythonw, bez konsoli,
        ktora by go komukolwiek pokazala.
        """
        raw = self._d.get(name)
        try:
            value = kind(raw)
        except (TypeError, ValueError, OverflowError):
            # OverflowError, bo json.load przyjmuje gole `Infinity` i `NaN`,
            # a int(float("inf")) nie jest ani TypeError, ani ValueError.
            problems.append("%s musi byc liczba (jest: %r)" % (name, raw))
            return
        if isinstance(value, float) and not math.isfinite(value):
            # `Infinity` i `NaN` przechodza przez float() i przez KAZDE porownanie
            # zakresu, wiec bez tej linii wpadaja do petli ticku: wait(inf) rzuca
            # tam OverflowError, a wait(nan) wraca natychmiast i zamienia pomiar
            # czasu w busy-loop.
            problems.append("%s musi byc liczba skonczona (jest: %r)" % (name, raw))
            return
        if value < low:
            problems.append("%s musi byc >= %s" % (name, low))
            return
        if high is not None and value > high:
            problems.append("%s poza zakresem %s..%s" % (name, low, high))
            return
        # Zapisujemy wartosc PO konwersji. Bez tego sprawdzenie bylo pozorne:
        # "width": "480" przechodzilo walidacje, bo int("480") sie udaje — a potem
        # Layout liczyl `"480" - 1` i pekal TypeError-em juz PO tym, jak validate()
        # obwiescilo, ze konfiguracja jest zdatna do uzycia.
        self._d[name] = value


def load(path=CONFIG_PATH):
    """Wczytuje konfiguracje. Brak pliku i zly JSON to DWA rozne bledy —
    pierwszy znaczy 'jeszcze nie zainstalowane', drugi 'zepsute przy edycji'."""
    if not os.path.exists(path):
        raise ConfigError("brak pliku konfiguracji: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise ConfigError("panel.json jest niepoprawnym JSON-em: %s" % e) from e
    except OSError as e:
        raise ConfigError("nie moge odczytac %s: %s" % (path, e)) from e
    if not isinstance(data, dict):
        raise ConfigError("panel.json musi zawierac obiekt JSON")
    return Config(data, path)


def example():
    """Wzor do wklejenia — uzywany przez --list i README."""
    return json.dumps({
        "stream_url": DEFAULTS["stream_url"],
        "stream_token": "<wpis z STREAM_TOKENS o etykiecie panel>",
        "account_1": {"uuid": "<uuid konta>", "name": "you@example.org"},
        "account_2": {"uuid": "<uuid konta>", "name": "billing@example.org"},
        "panels": [{"backend": "ax206", "port_path": "3.4", "brightness": 5}],
    }, indent=2, ensure_ascii=False)
