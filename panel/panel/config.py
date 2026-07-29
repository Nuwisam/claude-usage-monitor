"""Konfiguracja panelu — %LOCALAPPDATA%\\claude-usage-monitor\\panel.json.

Ten sam katalog co config.json sondy, ale OSOBNY plik. Token strumienia ma inny
zakres niz token ingestu (backend/app/auth.py:62-67 odrzuca ingestowy na /stream),
a `/usage-monitor-enrollment` przepisuje plik sondy — nie chcemy, zeby przy okazji
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
    "device": None,          # {"location": "Port_#0004.Hub_#0006"} albo {"index": 0}
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


class Config:
    def __init__(self, data, path=CONFIG_PATH):
        self.path = path
        self._d = dict(DEFAULTS)
        self._d.update(data or {})

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

        dev = self._d.get("device")
        if dev is not None and not isinstance(dev, dict):
            problems.append("device musi byc obiektem, np. "
                            "{\"location\": \"Port_#0004.Hub_#0006\"}")
        # Gorna granica TYLKO tam, gdzie ja cos podaje: 0..7 to zakres wlasciwosci
        # PROPERTY_BRIGHTNESS z firmware'u AX206. Reszta dostaje sama podloge,
        # bo sufit musialbym wymyslic — a wymyslony prog, ktory odrzuca poprawna
        # konfiguracje, jest gorszy niz brak progu.
        self._number(problems, "brightness", int, 0, 7)
        self._number(problems, "tick_sec", float, 0.01)
        self._number(problems, "width", int, 1)
        self._number(problems, "height", int, 1)
        return problems

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
        "device": {"location": "Port_#0004.Hub_#0006"},
        "brightness": 5,
    }, indent=2, ensure_ascii=False)
