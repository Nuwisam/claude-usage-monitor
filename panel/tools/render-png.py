"""Render a scene to PNG — no panel and no network.

    python tools/render-png.py --scene base --out out.png --zoom 3

`--rgb565` pushes the image through the panel's packing and back, so the PNG
shows the real 5/6/5 quantization, not the prettier truth of a desktop screen.
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


# Demo data shaped like the mockup: the same reasons, tools, name lengths
# and stamps, so a PNG can be laid against the design. The project names are MADE UP —
# just as the account addresses in fixtures.py come from example.org. Time anchor: fixtures.NOW_ISO.
def alert_scene(kind, now_ms, flood=False):
    """Alert card variants — one per layout."""
    kb = dict(key="a", reason="question", project="reporting-panel",
              tool="AskUserQuestion", machine="desktop", ago_s=245,
              detail="Dump scope: session only, session and week, or all the "
                     "limit windows",
              permission_mode="default")
    cum = dict(key="b", reason="plan", project="claude-usage-monitor",
               tool="ExitPlanMode", machine="laptop", ago_s=610,
               detail="A 6-step plan: layout.Alert, render._alert, AlertState, "
                      "geometry and quantization tests",
               agent_type="general-purpose", permission_mode="plan")
    gps = dict(key="c", reason="permission", project="photo-synchronizer-worktree",
               tool="Bash", machine="desktop", ago_s=20, detail="git status")

    if kind == "solo":
        items = [blocked(**kb)]
    elif kind == "pair":
        # In the mockup this block waits "a moment" — the same strings on both sides
        # let the render be compared to the design letter by letter.
        items = [blocked(**cum), blocked(**dict(kb, ago_s=28))]
    elif kind == "list":
        items = [blocked(**cum), blocked(**kb), blocked(**gps)]
    else:
        items = [blocked(**cum), blocked(**kb), blocked(**gps),
                 blocked("d", "permission", "cms-migration", "Edit", "laptop", 150),
                 blocked("e", "question", "notes-sync", "AskUserQuestion", "desktop", 98)]
    # The same order the running client uses (`status.parse_frame`): newest first. The
    # scene skips the parser, so without this the PNGs would teach an order the panel never draws.
    items.sort(key=lambda b: (b.since is None,
                              -b.since.timestamp() if b.since else 0.0,
                              b.key))
    return render.alert_state(items, now_ms, flood=flood)


def _shift(iso, seconds):
    from datetime import timedelta
    return fmt.parse_utc(iso) - timedelta(seconds=seconds)


def unpack_rgb565(payload, size):
    """The inverse of image_to_rgb565 — shows what the panel will really display."""
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
                    help="show the image after the panel's quantization")
    ap.add_argument("--link", default="live",
                    choices=("live", "reconnecting", "down"))
    ap.add_argument("--message", help="instead of the bands: a full-screen status card")
    ap.add_argument("--alert", choices=("solo", "pair", "list", "many"),
                    help="instead of the bands: a blocked-session card")
    ap.add_argument("--flood", action="store_true",
                    help="FULL frame: banner flooded with the accent plus the rail")
    ap.add_argument("--marker", choices=("upper", "lower", "both"),
                    help="bands with the alert marker on the edge of the chosen band")
    args = ap.parse_args()

    now_ms = fmt.ms(fmt.parse_utc(fixtures.NOW_ISO))
    state = build(args.scene, now_ms, args.link)
    if args.marker:
        # The same dictionary as in the panel: `status.SHORT`, not a string typed in here.
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
    print("%s  %dx%d  payload %d B" % (args.out, frame.image.width,
                                       frame.image.height, len(frame.rgb565("be"))))


if __name__ == "__main__":
    main()
