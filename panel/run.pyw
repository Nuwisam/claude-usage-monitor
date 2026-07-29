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

    Selektor urzadzenia bierzemy z DOMYSLNYCH ustawien, bo zepsuta jest wlasnie
    konfiguracja — przy jednym module trafia w niego bez zadnej wskazowki.
    """
    from panel import config as C, render
    from panel.link import PanelLink

    # Z pliku bierzemy WYLACZNIE selektor modulu i sciezke do libusb, i tylko gdy
    # maja poprawny ksztalt. Powod na "bierzemy": walidacja odrzuca konfiguracje
    # najczesciej z powodu, ktory z wyborem urzadzenia nie ma nic wspolnego (brak
    # tokenu, zle uuid), a przy dwoch modulach domyslny brak selektora nie trafilby
    # w zaden. Powod na "wylacznie": to jest sciezka wyswietlania BLEDU KONFIGURACJI,
    # wiec nie wolno jej karmic niesprawdzonymi polami z tego samego pliku —
    # `"device": "cos"` rzucaloby AttributeError w select(), a `"brightness": "duzo"`
    # ValueError w set_brightness(). Blad w pokazywaniu bledu zostawia ciemne szklo.
    try:
        raw = C.load()._d
    except C.ConfigError:
        raw = {}
    safe = {k: raw[k] for k, kind in (("device", dict), ("libusb_dll", str))
            if isinstance(raw.get(k), kind)}
    cfg = C.Config(safe)
    link = PanelLink(cfg)
    try:
        link.send(render.Renderer(cfg.width, cfg.height).frame(
            render.ScreenState(message=lines)), force=True)
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
