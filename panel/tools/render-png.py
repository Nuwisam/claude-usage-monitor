"""Render sceny do PNG — bez panelu i bez sieci.

    python tools/render-png.py --scene base --out out.png --zoom 3

`--rgb565` przepuszcza obraz przez pakowanie panelu i z powrotem, wiec PNG
pokazuje faktyczna kwantyzacje 5/6/5, a nie ladniejsza prawde z pulpitu.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image                                      # noqa: E402

from panel import fmt, render, status                      # noqa: E402
from tests import fixtures                                 # noqa: E402


def blocked(key, reason, project, tool, machine, ago_s, **kw):
    return status.Blocked(key=key, reason=reason, project=project, tool=tool,
                          machine=machine, since=_shift(fixtures.NOW_ISO, ago_s),
                          account_uuid=None, **kw)


# Dane demonstracyjne w ksztalcie makiety: te same powody, narzedzia, dlugosci nazw
# i stemple, zeby PNG dalo sie przylozyc do projektu. Nazwy projektow sa WYMYSLONE —
# tak samo jak adresy kont w fixtures.py sa z example.org. Kotwica czasu: fixtures.NOW_ISO.
def alert_scene(kind, now_ms, flood=False):
    """Warianty karty alertu — po jednym na uklad."""
    kb = dict(key="a", reason="question", project="panel-raportow",
              tool="AskUserQuestion", machine="desktop", ago_s=245,
              detail="Zakres zrzutu: tylko sesja, sesja i tydzień, czy wszystkie "
                     "okna limitów",
              permission_mode="default")
    cum = dict(key="b", reason="plan", project="claude-usage-monitor",
               tool="ExitPlanMode", machine="laptop", ago_s=610,
               detail="Plan na 6 kroków: layout.Alert, render._alert, AlertState, "
                      "testy geometrii i kwantyzacji",
               agent_type="general-purpose", permission_mode="plan")
    gps = dict(key="c", reason="permission", project="synchronizator-zdjec-worktree",
               tool="Bash", machine="desktop", ago_s=20, detail="git status")

    if kind == "solo":
        items = [blocked(**kb)]
    elif kind == "pair":
        # W makiecie ta blokada czeka "chwilę" — te same napisy po obu stronach
        # pozwalaja porownac render z projektem litera w litere.
        items = [blocked(**cum), blocked(**dict(kb, ago_s=28))]
    elif kind == "list":
        items = [blocked(**cum), blocked(**kb), blocked(**gps)]
    else:
        items = [blocked(**cum), blocked(**kb), blocked(**gps),
                 blocked("d", "permission", "cms-migracja", "Edit", "laptop", 150),
                 blocked("e", "question", "notes-sync", "AskUserQuestion", "desktop", 98)]
    return render.alert_state(items, now_ms, flood=flood)


def _shift(iso, seconds):
    from datetime import timedelta
    return fmt.parse_utc(iso) - timedelta(seconds=seconds)


def unpack_rgb565(payload, size):
    """Odwrotnosc image_to_rgb565 — pokazuje, co panel naprawde wyswietli."""
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for i in range(w * h):
        hi, lo = payload[2 * i], payload[2 * i + 1]
        r = hi & 0xF8
        g = ((hi & 0x07) << 5) | ((lo & 0xE0) >> 3)
        b = (lo & 0x1F) << 3
        px[i % w, i // w] = (r | (r >> 5), g | (g >> 6), b | (b >> 5))
    return img


def build(scene, now_ms, link="live"):
    accounts = fixtures.SCENES[scene]()
    bands = []
    for i, acc in enumerate(accounts):
        if acc is None:
            bands.append(None)
            continue
        bands.append(render.band_state(acc, now_ms=now_ms, show_clock=(i == 0)))
    clock = fmt.hm(fmt.parse_utc(fixtures.NOW_ISO))
    return render.ScreenState(clock=clock, link=link, bands=bands)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="base", choices=sorted(fixtures.SCENES))
    ap.add_argument("--out", default="panel.png")
    ap.add_argument("--zoom", type=int, default=1)
    ap.add_argument("--rgb565", action="store_true",
                    help="pokaz obraz po kwantyzacji panelu")
    ap.add_argument("--link", default="live",
                    choices=("live", "reconnecting", "down"))
    ap.add_argument("--message", help="zamiast pasow: pelnoekranowa karta stanu")
    ap.add_argument("--alert", choices=("solo", "pair", "list", "many"),
                    help="zamiast pasow: karta zablokowanej sesji")
    ap.add_argument("--flood", action="store_true",
                    help="klatka PELNA: pasmo zalane akcentem plus rail")
    ap.add_argument("--marker", choices=("upper", "lower", "both"),
                    help="pasy ze znacznikiem alertu na krawedzi wskazanego pasa")
    args = ap.parse_args()

    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    state = build(args.scene, now_ms, args.link)
    if args.marker:
        # Ten sam slownik co w panelu: `status.SHORT`, nie napis wpisany tutaj.
        which = {"upper": (0,), "lower": (1,), "both": (0, 1)}[args.marker]
        for i in which:
            if state.bands[i] is not None:
                state.bands[i].alert = status.SHORT["permission" if i == 0 else "question"]
    if args.message:
        state.message = args.message.split("|")
    if args.alert:
        state.alert = alert_scene(args.alert, now_ms, flood=args.flood)

    frame = render.Renderer().frame(state)
    img = frame.image
    if args.rgb565:
        img = unpack_rgb565(frame.rgb565("be"), img.size)
    if args.zoom > 1:
        img = img.resize((img.width * args.zoom, img.height * args.zoom),
                         Image.NEAREST)
    img.save(args.out)
    print("%s  %dx%d  ladunek %d B" % (args.out, frame.image.width,
                                       frame.image.height, len(frame.rgb565("be"))))


if __name__ == "__main__":
    main()
