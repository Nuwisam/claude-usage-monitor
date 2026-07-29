"""Odtworzenie nagranego strumienia SSE do PNG.

Nagrywanie wlacza `"record_sse": true` w panel.json — surowe bajty ladują obok
logu, jako panel.log.sse.

    python tools/replay.py %LOCALAPPDATA%\\claude-usage-monitor\\panel.log.sse --outdir klatki

Po co: odtworzenie zlej ramki nie moze wymagac czekania, az sytuacja powtorzy sie
w dzialajacym systemie. Zwlaszcza ze ramki plyna tylko wtedy, gdy ktos pracuje.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from panel import fmt, model, render, stream                # noqa: E402


def chunks(path, size=4096):
    with open(path, "rb") as f:
        while True:
            data = f.read(size)
            if not data:
                return
            yield data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--outdir", default="replay")
    ap.add_argument("--zoom", type=int, default=1)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    renderer = render.Renderer()
    clock = fmt.ServerClock(lambda: 0.0)
    accounts = {}
    order = []
    frames = 0

    for event, payload in stream.parse_events(chunks(args.path)):
        if not isinstance(payload, dict):
            continue
        if payload.get("serverNow"):
            clock.anchor(payload["serverNow"])
        if event != "account":
            continue
        account = model.AccountStatus(payload.get("account") or {})
        if not account.uuid:
            continue
        if account.uuid not in accounts:
            order.append(account.uuid)
        accounts[account.uuid] = account

        now_ms = clock.now_ms()
        bands = [render.band_state(accounts[u], now_ms=now_ms, show_clock=(i == 0))
                 for i, u in enumerate(order[:2])]
        while len(bands) < 2:
            bands.append(None)
        state = render.ScreenState(clock=fmt.hm(clock.now()), link="live", bands=bands)
        img = renderer.frame(state).image
        if args.zoom > 1:
            from PIL import Image
            img = img.resize((img.width * args.zoom, img.height * args.zoom),
                             Image.NEAREST)
        frames += 1
        img.save(os.path.join(args.outdir, "klatka-%04d.png" % frames))

    print("odtworzono %d klatek do %s/" % (frames, args.outdir))
    if not frames:
        print("(brak ramek `account` w pliku — czy record_sse bylo wlaczone?)")


if __name__ == "__main__":
    main()
