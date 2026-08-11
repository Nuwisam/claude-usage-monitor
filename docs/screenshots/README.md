# Web UI screenshots

The images the top of [`README.md`](../../README.md) shows. They are rendered from the **mock
mode** of the frontend, never from a live deployment — a live one carries real account
addresses, and text baked into pixels passes every text-level check.

| File | What it shows | Source |
|---|---|---|
| [`live.png`](live.png) | Live: two accounts, the session hero, the cascade, the remaining windows | `/` (default mock) |
| [`live-credits.png`](live-credits.png) | The cascade after the weekly pool runs out: credits, and credits withdrawn by the organization | `/?mock=credits` |
| [`history.png`](history.png) | History: one series across all accounts, reset boundaries, and both kinds of gap | `/history` |

## Regenerate

`VITE_MOCKS=1` replaces the API layer with `frontend/src/mocks/`; no backend and no database are
needed. The mock variants are listed at the top of `frontend/src/mocks/status.ts`.

```bash
cd frontend
npm ci
VITE_MOCKS=1 npm run dev          # http://127.0.0.1:5173/claude-usage/
```

Then, from a second shell, with any Chrome or Chromium build (`chrome` below stands for its
executable, `$OUT` for an **absolute** path to this directory — Chrome resolves `--screenshot`
against its own working directory, and a relative path fails with *"Failed to write file"*):

```bash
FLAGS="--headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --virtual-time-budget=6000"
U=http://127.0.0.1:5173/claude-usage

chrome $FLAGS --window-size=1280,700 --screenshot="$OUT/live.png"          "$U/"
chrome $FLAGS --window-size=1280,640 --screenshot="$OUT/live-credits.png"  "$U/?mock=credits"
chrome $FLAGS --window-size=1280,850 --screenshot="$OUT/history.png"       "$U/history"
```

The window heights are chosen so the frame ends just under the last row — Chrome captures the
viewport, not the full page, so a taller window adds an empty strip and a shorter one cuts a row
in half. Re-check them after any layout change. At scale factor 2 the files come out
2560x1400, 2560x1280 and 2560x1700.

`--force-device-scale-factor=2` is what makes the text sharp on a high-DPI screen; without it the
images look soft at GitHub's rendering width.

## Before committing a new one

Look at every image with your own eyes. Grep reads PNG bytes, never the text drawn into them, so
an account address, a machine name or a local path can travel in a screenshot through any check
that only reads text. The mock data uses `example.org` addresses and machine names like `desktop`
and `laptop`; anything else in the frame does not belong in the repo.

The desk-panel images live next door in [`../handout/`](../handout/) and have their own recipe in
[`../PANEL-ALERT-HANDOUT.md`](../PANEL-ALERT-HANDOUT.md).
