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

from panel import fmt, render                              # noqa: E402
from tests import fixtures                                 # noqa: E402


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
    args = ap.parse_args()

    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    state = build(args.scene, now_ms, args.link)
    if args.message:
        state.message = args.message.split("|")

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
