"""Cel zadania harmonogramu. Rozszerzenie .pyw + pythonw.exe = brak okna konsoli.

Ten plik jest w repo, ale zadanie NIE wskazuje na niego wprost. Pod sciezka
%LOCALAPPDATA%\\claude-usage-monitor\\panel-run.pyw lezy kilkanascie linijek
przekierowania, ktore uruchamiaja TEN plik spod repo — ta sama konwencja co
przy sondzie (client/README.md). Dzieki temu edycja w repo dziala od razu,
bez kopiowania, a Windows nie wymaga praw administratora na dowiazanie.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _card(lines):
    """Pelnoekranowa karta na panelu.

    ConfigError obiecuje w swoim docstringu, ze blad konfiguracji jest WIDOCZNY
    NA PANELU, a Renderer._message powtarza to samo zdanie. Zadne z nich nie
    bylo prawda: app.main() rzuca, zanim powstanie App, wiec do urzadzenia nikt
    nie siegal. Na biurku zostawal ostatni dobry obraz — zamrozone, wiarygodnie
    wygladajace liczby, czyli dokladnie ten tryb awarii, przed ktorym broni
    zasada 4 z AGENTS.md. Log tego nie zastepuje: nikt go nie otwiera, dopoki
    nie zobaczy, ze cos jest nie tak.

    Karta idzie na WSZYSTKIE skonfigurowane ekrany, kazdy osobno: pol biurka
    z bledem, a pol z zamrozonymi, wiarygodnie wygladajacymi liczbami byloby
    gorsze niz stan sprzed tej funkcji.
    """
    from panel import config as C, render
    from panel.drivers import REGISTRY
    from panel.link import PanelLink

    # Z pliku bierzemy WYLACZNIE wskazanie ekranu i sciezke do libusb, i tylko gdy
    # maja poprawny ksztalt. Powod na "bierzemy": walidacja odrzuca konfiguracje
    # najczesciej z powodu, ktory z wyborem urzadzenia nie ma nic wspolnego (brak
    # tokenu, zle uuid), a przy dwoch ekranach domyslny brak selektora nie trafilby
    # w zaden. Powod na "wylacznie": to jest sciezka wyswietlania BLEDU KONFIGURACJI,
    # wiec nie wolno jej karmic niesprawdzonymi polami z tego samego pliku —
    # `"device": "cos"` rzucaloby AttributeError w select(), a `"brightness": "duzo"`
    # ValueError w set_brightness(). Blad w pokazywaniu bledu zostawia ciemne szklo.
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
        # Sanityzacja per wpis: zostaje backend i dobrze otypowany selektor.
        # `brightness` ODPADA celowo — wartosc z zepsutego pliku poszlaby prosto
        # do set_brightness(), a to jest jedyna rzecz, ktora ta funkcja musi
        # przezyc.
        clean = {"backend": entry["backend"]}
        for key in mod.SELECTOR_KEYS:
            value = entry.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                clean[key] = value
        # `rotate` ZOSTAJE, w przeciwienstwie do brightness: ekran powieszony do
        # gory nogami dostalby karte bledu do gory nogami, czyli nieczytelna —
        # a to jest dokladnie ta jedna rzecz, ktora ta funkcja ma pokazac.
        # Bezpiecznie, bo przepuszczamy tylko dwie znane wartosci; cokolwiek
        # innego znaczy 0 i widoczna karte, nie ciemne szklo.
        if entry.get("rotate") in C.ROTATIONS:
            clean["rotate"] = entry["rotate"]
        entries.append(clean)
    if not entries:
        # Nic nie przetrwalo — wracamy do dzisiejszej semantyki: bez selektora,
        # czyli "dokladnie jeden ekran albo DeviceNotFound". Braniem wszystkiego,
        # co widac, zlamalibysmy zasade z naglowka device.py wlasnie tam, gdzie
        # niczego nie da sie sprawdzic.
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
            # Jeden ekran zajety nie moze zabrac karty pozostalym.
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
        # Bez konsoli komunikat musi trafic do logu — inaczej petla restartow
        # co minute nie zostawia po sobie nic.
        try:
            logmod.setup(C.DEFAULT_LOG, "INFO", console=False).error(
                "konfiguracja: %s", e)
        except Exception:
            pass
        try:
            _card(["Błąd konfiguracji", str(e), C.CONFIG_PATH])
        except Exception:
            # Panel bywa zajety albo wypiety. Komunikat w logu juz poszedl,
            # a wywrocenie sie tutaj zamienialoby zly config w twarda awarie.
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
