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


def alert_scene(kind, now_ms):
    """Warianty karty alertu — to jest punkt wyjscia handoutu do UI.

    Dobrane tak, zeby pokazac wszystkie trzy rzeczy, ktore moga rozsadzic uklad:
    najdluzszy naglowek (`plan`), nazwe projektu nie miesczaca sie w duzym stopniu
    (`long`) i wiersz "inne:" (`multi`).
    """
    def blocked(key, reason, project, tool, machine, ago_s):
        return status.Blocked(
            key=key, reason=reason, project=project, tool=tool, machine=machine,
            since=fmt.parse_utc(fixtures.NOW_ISO), account_uuid=None,
        ) if ago_s is None else status.Blocked(
            key=key, reason=reason, project=project, tool=tool, machine=machine,
            since=_shift(fixtures.NOW_ISO, ago_s), account_uuid=None,
        )

    if kind == "permission":
        items = [blocked("a", "permission", "claude-usage-monitor", "Bash", "laptop", 245)]
    elif kind == "question":
        items = [blocked("a", "question", "panel-raportow", "AskUserQuestion", "desktop", 40)]
    elif kind == "plan":
        items = [blocked("a", "plan", "synchronizator-zdjec", "ExitPlanMode",
                         "laptop", 3900)]
    elif kind == "long":
        items = [blocked("a", "permission", "synchronizator-zdjec-worktree",
                         "PowerShell", "desktop", 90)]
    else:
        items = [blocked("a", "plan", "claude-usage-monitor", "ExitPlanMode", "laptop", 610),
                 blocked("b", "permission", "backend-api", "Bash", "desktop", 120),
                 blocked("c", "question", "frontend", "AskUserQuestion", "laptop", 30)]
    return render.alert_state(items, now_ms)


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
    ap.add_argument("--alert",
                    choices=("permission", "question", "plan", "long", "multi"),
                    help="zamiast pasow: karta zablokowanej sesji")
    ap.add_argument("--triangle", action="store_true",
                    help="pasy z trojkatem ostrzegawczym przy nazwie konta")
    args = ap.parse_args()

    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    state = build(args.scene, now_ms, args.link)
    if args.triangle:
        for band in state.bands:
            if band is not None:
                band.alert = True
    if args.message:
        state.message = args.message.split("|")
    if args.alert:
        state.alert = alert_scene(args.alert, now_ms)

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
