# panel — Claude limits on USB displays

A headless client that subscribes to `/api/stream` and draws the state of the
limits on screens sitting on a desk. Layout **4a** from the mockup: two accounts
in bands, the percentage next to the block, credits at the bottom of the band.
There is one renderer and it draws a single logical 480×320 canvas; the screens
differ only in what they do with that canvas.

```
python -m panel                          loop (same as what the scheduled task runs)
python -m panel --list                   which screens are visible and on what ports
python -m panel --identify ax206#0       paint a big number on the given screen
python -m panel --probe [--backend ax206]  test card: colors, bars, descenders, rectangles
python -m panel --once                   one frame of real data, then exit
```

One rendered frame goes to **every** configured screen; each has its own
connection, its own backoff and its own memory of what it is showing. The
drivers live in [`panel/drivers/`](panel/drivers) — one file per screen type,
and the registry is an explicit dictionary in `__init__.py`.

Without hardware:

```
python tools/render-png.py --scene states --zoom 3 --rgb565 --out out.png
python tools/replay.py %LOCALAPPDATA%\claude-usage-monitor\panel.log.sse
python -m pytest tests -q
```

## Hardware — what you need to know before something breaks

Module: `USB\VID_1908&PID_0102`, GEMBIRD/QDtech "USB-Display", **Appotech
AX206** chip. The protocol is a proprietary SCSI command `0xCD` wrapped in a
CBW/CSW pair of the USB Bulk-Only Mass Storage transport; the starting point is
[`dpf-ax`](https://github.com/dreamlayers/dpf-ax), but **this firmware differs
from the one described there** and those differences are the biggest source of
surprises here.

| | |
|---|---|
| resolution | 480 × 320, RGB565 **high byte first**, no rotation |
| full frame | 307 200 B → **~355 ms** for a dark layout (≈2.8 fps) |
| pixel packing | 1.55 ms (`bytes.translate` + one OR on big integers; numpy unnecessary) |
| brightness | `SETPROPERTY`/`BRIGHTNESS`, 0–7 |
| handle | **exclusive** — either AIDA64 or us |
| device driver | libusb-win32 (`libusb0.sys`) — the same one AIDA64 uses |
| client library | **libusb-1.0** (the `libusb` package from `requirements.txt`) |

**The driver and the library are two different things**, and confusing them is
an easy trap to fall into here. The device stays under libusb-win32; only the
library we talk to it through has changed. The libusb-1.0 Windows backend
supports devices bound to `libusb0.sys` (measured: 1200 frames,
`missed_csw = 0`), so switching the driver to WinUSB is unnecessary and would
break AIDA64.

There is exactly one reason for changing the library: `libusb_get_port_numbers()`
gives the **port chain** from the same handle we open. The 0.1 API from
libusb-win32 gave no topology at all (`bus-0`, `devnum=0`) — the module had to
be looked up in the Windows registry and matched to the handle by ordering.

**Frame time depends on content**, despite a constant byte count on the wire.
Measured on this module, same 307 200 B blit: test card (dark) 354 ms, full
black 356 ms, bands of saturated colors 514 ms. The target layout is dark, so
~355 ms is what applies. Synthetic tests with saturated bands measure the worst
case — and they are exactly what made us briefly think there was a regression
that never existed, after the migration (libusb-win32 gave 503–533 ms on the
same bands).

**Draws full frames only.** The rectangle in the blit command is not an area to
redraw — it sets a *drawing window* that the firmware pours the whole stream
into, wrapping it. A smaller window therefore shows the **tail** of the
payload, not its start (a zero-padded payload gives a black window — that is
what the first, misleading results looked like). The transfer always has to be
307 200 B, so a blit into a 480×60 window costs exactly as much as the full
screen. A rectangle equal to exactly one window's worth is not confirmed
either. `blit()` therefore rejects partial rectangles instead of silently doing
nothing.

**A missing CSW is our bug, not the panel's whim.** The identical command
confirms normally, until a blit with the wrong byte count goes out before it;
after that the pipe stays silent until `reset()`. Read the `missed_csw` counter
as "we sent something wrong."

**Of the `dpf-ax` command set only blit and brightness work.** `FILLRECT`
(0x11), `COPYRECT` (0x13) and the `FGCOLOR` property are not confirmed even
after a reset. The geometry-query command is **unreliable**: sometimes it
returns the correct 480×320, sometimes it stays silent, and a failed attempt
corrupts the next transaction — which is why geometry is configuration, and
`probe_geometry()` is called at the very end of `--probe`.

**The screen holds its last frame** with no host attached. The client
deliberately does not clear it on exit: once the desk machine is shut down,
the last known state stays on.

The existing [`pyax206`](https://github.com/sayajinpt/pyax206) library
implements the identical protocol (the blit command matches byte for byte),
but it does not work on this unit: its `init()` treats a missing CSW as a
critical error and blows up on every attempt, even after a clean reset.

## The second screen: Turing rev A over a serial port

A different device in every respect except the pixel format. Measured on the unit
on this desk; the driver is [`panel/drivers/turing_rev_a.py`](panel/drivers/turing_rev_a.py).

| | |
|---|---|
| identity | `USB\VID_1A86&PID_5722\USB35INCHIPSV2`, CompatibleIds `USB\Class_02&SubClass_02&Prot_01` |
| driver | in-box **usbser.sys** (CDC-ACM), no CH34x install on a fresh machine |
| BusReportedDeviceDesc | `UsbMonitor`; Device Manager shows it as "USB Serial Device (COMn)", localized on a non-English Windows |
| resolution | 320 × 480 **portrait**; our 480 × 320 layout is rotated 90° host-side |
| pixels | RGB565 **low byte first** (the AX206 wants the other order) |
| wire | 115200 8N1 with rtscts — **the configured baud is ignored**, 1 M and 2 M measure the same |
| throughput | **6.1 µs/byte, zero fixed overhead**; full frame 307 200 B = 1.87 s |
| partial updates | **real** — a rect updates exactly that rect |
| brightness | percent, 0..100, inverted on the wire |
| acknowledgement | **none, ever** |

**It is rev A, and that was measured, not assumed.** It ignores the rev B
handshake (`0xCA 'HELLO' … 0xCA`) at 115200 and at 1 Mbaud, and it draws
correctly for every rev A command we send. Rev B is a different command set
(`0xCA`–`0xCE`) and will get its own file next to this one, never a flag inside it.

**Nothing is acknowledged, so nothing detects a wrong frame.** There is no CSW
analogue and no read-back: an unplugged, upside-down or desynchronised screen
accepts bytes exactly like a healthy one. Two consequences the code is built
around:

- A **torn write is not recoverable by reopening the port.** The firmware counts
  pixel bytes; closing the COM port does not touch the MCU, so the next command's
  6 bytes get eaten as pixels and the stream desynchronises for good — with an
  odd offset that means permanently byte-swapped colour. The driver pads the exact
  remainder (the count survives in pyserial's `_overlapped_write.InternalHigh`
  even though the exception drops it) and forces a full repaint. **If the padding
  fails too, only unplugging the screen fixes it.**
- **Every write is time-bounded.** pyserial's default `write_timeout=None` waits
  forever on Windows, and with rtscts a screen that deasserts CTS would freeze the
  tick loop — and with it *the other screen*, silently, with the scheduled task
  seeing a live process and never restarting it.

`RESET` (101) is never sent (unmeasured, and firmware in the middle of a payload
would eat it as pixels). `SET_ORIENTATION` (121) is never sent either: rotation
is host-side and measured, and without acknowledgement a coordinate-space
mistake would show up as a scrambled screen rather than an error.

**Identity is the port chain, same as the AX206.** pyserial reports `1-8.4`; we
keep `8.4` and drop the bus for exactly the reason `Hub_#` was dropped before.
`USB35INCHIPSV2` is a firmware constant, so it filters (other WCH serial devices
share the VID/PID and must never receive bitmap commands) but never selects.
Verified on **one** unit — a second identical screen has not been tested.

### Partial updates: what actually gets written

A full frame every second is impossible here (1.87 s), and unnecessary. The
client diffs the packed 565 buffer in device space on an 8 × 8 tile grid,
coalesces tiles into rectangles without ever covering a clean pixel, and cuts each
rectangle straight out of the full-frame payload. Measured on real frames
(cost = bytes × 6.1 µs):

| change | rectangles | bytes | scan | on the wire |
|---|---|---|---|---|
| one tick (seconds counter) | 3 | 1 536 | 2.3 ms | 9 ms |
| clock minute rollover | 3 | 1 792 | 2.0 ms | 11 ms |
| `base` → `states` | 63 | 87 680 | 4.5 ms | 535 ms |
| `base` → `edges` | 69 | 129 920 | 5.8 ms | 793 ms |

So the steady state is ~1 % of a tick and a **scene change costs 0.5–1.2 s** — the
top of that range is the bands → alert card transition (62.5 % dirty, 45 crops,
1.17 s), which is the most expensive one this client draws. Rare, always cheaper
than a full frame, and the loop drops ticks rather than catching up. Above 256
rectangles or 85 % of the frame the client sends the whole frame instead: past that
many rectangles the Python loop stops being worth it.

**The threshold is 85 %, not 60 %, and the old wording here was wrong.** It said a
bounding box on a scene change *is* the whole frame — true for a bounding-box
coalescer, and this one is not that: `coalesce()` provably never covers a clean
pixel, and the wire is linear at 6.1 µs/byte with only a 6 B rectangle header, so
a set of crops is **never** heavier than the full frame. The measurement that
forced the change: the bands → alert card transition dirties 62.5 % of the frame
in 45 rectangles, landing just above the old threshold and turning 1.16 s of crops
into a 1.87 s full frame for nothing. `tests/test_alert.py` pins the number.

**The periodic full repaint is timed from the last FULL write**, not from the last
write. With partial updates the clock writes something every minute, so timing it
off "last write" would mean it never fires — and on a link that confirms nothing,
that repaint is the only way back from a silent desync.

## Where the data comes from

`GET /api/stream?account=<uuid>&account=<uuid>` with `Authorization: Bearer`.
This is the **only read** endpoint that accepts a token — the other ten are SSO-only,
`/api/status` among them (`backend/app/routers/read.py:37-39`), so the panel has
nowhere to poll from and does
not need to: every `account` frame carries the full `AccountStatus` card, so a
lost frame is harmless.

- A `bye` after 900 s is **normal** — we reconnect right away, with no "link
  down" flicker.
- A 35 s socket timeout (over two ping intervals). Without it a half-open TCP
  connection through Apache would hang forever, and the panel would keep
  showing stale numbers with full confidence.
- Reading the stream goes through `read1()`, not `read()`. `read(n)` waits for
  the full `n` bytes, so later cards and pings would sit stuck in the buffer,
  and the panel would be stuck on the first frame **looking alive**.

## A blocked Claude Code session

The panel shows not only usage but also the fact that **Claude is waiting on
you**: a tool permission request, `AskUserQuestion` or `ExitPlanMode`. The
signal is collected by `client/usage-probe.py` on the machine running the
session, sent to the backend, which fans it out as an `alert` frame
(`docs/API.md` § 3.2). **The session can be running on a remote machine** —
the panel sees only what arrived over the stream.

The presentation is two-stage, and that is a decision, not a phase:

1. The card **takes over the whole screen** for `alert_takeover_sec` — a number
   of seconds (300 by default), `0` (the marker right away, no card) or
   `"infinity"` (the card stays up until you answer, and usage is invisible for
   that whole time). The layout is chosen by the **number of waiting blocks**:
   one — the project name as the hero, a `Detail` tile and a `Mode` strip; two —
   two equal halves; three — a list with the reason in a fixed column; four and
   more — the three newest plus a counter for the rest.

   For `alert_flash_sec` the card **blinks**, that is, it swaps the blank frame
   for one **flooded with the accent**: the banner and the 6 px rail on the left
   edge turn `ACCENT`, and the text in the banner turns to the background color.
   The rail is there **in both frames** — in the resting one it is
   `NEUTRAL_900` — so the flood changes the color, not the layout, and the card
   keeps a fixed left edge even after the window expires. Without blinking, the
   card comes in quietly, because it is as dark as the rest of the screen. The
   value is a number of seconds (20 by default), `0` disables it, and
   `"infinity"` blinks for the card's whole life. It lights up once per block
   **key**, so a "waiting N min" tick does not blink anything.

   **Two frames blink, not a stepped animation.** The panel repaints line by
   line, so intermediate frames would tear over the sweep. The flood is ~13 % of
   the frame (the banner plus the repainted rail), i.e. about 0.24 s on the
   Turing and 355 ms on the AX206 — both make it within a tick.
   A full-screen flash is structurally impossible here: that is by definition a
   full frame, and the Turing paints it progressively over 1.87 s (verified on
   the hardware).
2. It then collapses to a **4 px accent bar on the left edge of the account
   band** that reported the block; the account name turns `ACCENT_100`, and the
   reason appears in capitals on the line with the plan name. The bar sits in
   the margin area, so the band's layout does not shift by a pixel, and it has
   the band's full height. The state stops being *takeover*; it does not stop
   being *true* — usage comes back on screen, and the fact that something is
   waiting is still visible.
   **There is no red in the project**: all the signalling runs on the accent
   ramp.

The window is counted from the server's `since`, not from the moment the panel
saw the entry: otherwise restarting the panel would resurrect the card for a
block from an hour ago.

**The window belongs to the set, not to the entry.** As long as any waiting
block still fits in the window, the card lists **all** of them — including the
ones that already burned out on their own counter. A new block therefore opens
the window for the whole set, not just for itself. Without this, three of the
four layouts were dead: two blocks would have to start within the same
five-minute window, and with sequential work that does not happen. It does not
extend the card's time on screen — the predicate "the card stays" is the
same, only its content changes.

Rows go **newest first**; the reason has no effect on this, and an entry
without `since` lands at the end. Every block was already shown solo when it
came in, so when truncating to three rows the ones worth showing are the ones
you have not seen yet. After a panel restart the same rule applies in reverse:
a restart alone does not resurrect the card, but the first fresh block will
pull in entries from hours earlier too.

- **`blocked_debounce_sec` (2 s)** — how long a block must last before the card
  comes in. Without it, permission granted right away would give a full-screen
  flash. **There is no threshold on the way out** — the card is a pure function
  of what is still waiting, so answering hands the screen back on the next tick.
- **`session_alerts: false`** turns the whole feature off — the frame is then
  ignored and the panel behaves exactly as before. This is a **display** flag;
  the source is switched off separately, with the `session_status` key in
  `config.json` on the machine running the session. Two flags, because those
  can be two different machines.

An alert **with no match** to any configured account lands on the **top**
band. The rule is deliberately simple: a static machine → band mapping would
drift out of sync after the first `/login`, and switching accounts is routine
here. The account comes from `oauthAccount.accountUuid` read on the machine
running the session — rule 7 in `AGENTS.md`.

The waiting time is **coarse-grained** ("a moment" / "4 min" / "1 h 05 min" /
"2 d 3 h"), and that is not a matter of taste: the AX206 cannot do partial
updates, so every change to the text is a full 355 ms, and seconds would turn
~2.5 % USB load into ~35 % for the card's whole time on screen. There is no
live clock on the card — the hour in the banner is the static moment the
prompt appeared, more precisely the start of the **oldest wait on screen**.
Since rows go newest first, that is usually not the moment of the block that
just came in: the banner says how long all of this has already been going on,
and the first row says what arrived most recently.

The card's design and all four layouts are described in
`docs/PANEL-ALERT-HANDOUT.md`, with images pushed through the panel's
quantization. A preview without hardware:

```
python tools/render-png.py --alert solo   --zoom 3 --rgb565 --out ../docs/handout/card-solo.png
python tools/render-png.py --alert pair   --zoom 3 --rgb565 --out ../docs/handout/card-pair.png
python tools/render-png.py --alert list   --zoom 3 --rgb565 --out ../docs/handout/card-list.png
python tools/render-png.py --alert many   --zoom 3 --rgb565 --out ../docs/handout/card-many.png
python tools/render-png.py --alert solo --flood --zoom 3 --rgb565 --out ../docs/handout/card-flooded.png
python tools/render-png.py --marker upper --zoom 3 --rgb565 --out ../docs/handout/bands-marker-upper.png
python tools/render-png.py --marker lower --zoom 3 --rgb565 --out ../docs/handout/bands-marker-lower.png
python tools/render-png.py                --zoom 3 --rgb565 --out ../docs/handout/bands-no-alert.png
```

If an alert got stuck, the escape hatch is on the machine running the session,
not here: `del %LOCALAPPDATA%\claude-usage-monitor\session-status\*`.

## Refreshing

A tick every second, but **only what differs** goes to the screen. How much
that buys depends on the hardware: the AX206 draws full frames only, so the
saving is in not sending an identical one (measured at rest: 3 frames per
45 s, ~2.5 % of USB time); the serial screen accepts rectangles, so a typical
tick is ~1.5 kB instead of 307 kB.

Seconds stay where they are in the mockup: in the countdown below an hour, and
in the reading age below a minute. They come in exactly when you are working —
which is when they are most needed. Outside work the values roll into minutes
and hours on their own, and the panel goes quiet. The exception is the clock
in the header: it ticks independently of work, so it shows HH:MM.

## Drawing rules that must not be simplified

- **The panel has no separate freshness states in the drawing.** Currency is
  carried entirely by the reading-age label next to the account. When the
  backend does not know the current value (`utilization: null`, the account
  has been silent longer than `CLIENT_SILENT_SEC`), the panel shows the **last
  measurement** (`rawUtilization`) like any other number, and the "12 h ago"
  next to it says what it is worth.
- **Never zero instead of not knowing** (rule 4 in `AGENTS.md`) — the above
  reinforces that rule, it does not weaken it: an account silent for 12 h with
  a week at 100 % shows 100 %, not a reassuring zero. The hatched track with a
  diagonal and the words `unknown` are reserved for a series that **never**
  had a measurement — there really is nothing to draw, and the `%` sign
  then disappears ("unknown %" is a real pitfall).
- **The plan is always visible.** "40 %" means something different on Max 20×
  than on a Team seat. An unknown tier is shown raw, rather than disappearing
  (rule 5).
- **Age is counted from `confirmedAt`, never from `capturedAt`** — dedup does
  not store a sample when the value is unchanged, so `capturedAt` can be hours
  older.
- **The countdown anchors on `serverNow`**, and only ticks locally. The anchor
  sits on `time.monotonic()`, so an NTP jump will not shift the countdowns.
- **The link marker differs by shape, not color** (dot / ring / crossed out).
  When the stream drops, the reading age grows on both accounts at once, and
  it looks identical to "you stopped working".

## Installation

```powershell
.\deploy\install-task.ps1          # venv outside the repo + logon scheduled task
.\deploy\install-task.ps1 -Uninstall
```

Configuration: `%LOCALAPPDATA%\claude-usage-monitor\panel.json` — the same
directory as the probe's `config.json`, but a **separate file**: the stream
token has a different scope than the ingest token, and the probe's file gets
overwritten when it is updated.

```json
{
  "stream_url": "https://usage.example.org/claude-usage/api/stream",
  "stream_token": "<the STREAM_TOKENS entry labeled panel>",
  "account_1": {"uuid": "...", "name": "you@example.org"},
  "account_2": {"uuid": "...", "name": "billing@example.org"},
  "panels": [
    {"backend": "ax206",        "port_path": "3.4", "brightness": 5},
    {"backend": "turing-rev-a", "port_path": "8.4", "brightness": 40, "name": "right",
     "rotate": 180}
  ]
}
```

**`rotate` says how the screen is mounted**, in degrees counter-clockwise,
**added on top of the rotation the driver applies anyway** (`turing-rev-a` has
its own 90°, so `180` gives 270°). Only **`0` or `180`** are allowed — a quarter
turn would need a portrait 320×480 layout, and only one 3:2 layout is drawn;
changing the angle alone would give either scaling (this layout is hairline
strokes, it would not survive that) or a payload whose length does not fit the
rectangle. Omitted means `0`. To check without changing the file:
`python -m panel --identify turing-rev-a#0 --rotate 180`.

**Brightness is per screen, because the scales are not comparable**: `ax206` is
0..7 (a firmware property), `turing-rev-a` is 0..100 %. Omitted means "this
driver's default". A top-level `brightness` next to `panels` is a
**configuration error**, not a compromise — `5` would mean mid-range on one
screen and nearly off on the other.

**The old shape (`"device": {...}` plus a top-level `brightness`) still
works** and turns into a one-entry `ax206` list. Migrating it automatically is safe,
because it had exactly one possible meaning — there was one driver. That is
the difference from `location`, where the value itself was untrustworthy.
`device` and `panels` together is an error: merging them would mean guessing.
An unknown key in the selector is also an error — it used to match nothing
and silently fall through to "the only thing visible".

**Accounts are two named fields, not a list** — the shape of the
configuration is the shape of the screen here, so a third account cannot be
added by accident. After `/login` to a new account you have to point at it
here and restart the client: the subscription is fixed when the connection is
made, and SSE has no back-channel.

**The screen is pointed at in the configuration, and the client never reaches
for another one.** Eventually there will be two identical modules on the bus
(one under a different program), and both carry the same `WCH32` serial
number — that is a firmware constant, not a per-unit number. Windows derives
both the instance ID (`USB\VID_1908&PID_0102\WCH32`) and the `ContainerID`
from that serial, so **both of those values will be identical for the two
modules** and, as selectors, do not tell them apart at all.

The key is therefore `port_path` — a port chain, e.g. `"3.4"`: port 3 of the
controller, port 4 of the hub on that port. Three things to know about it:

- **It contains no enumeration counter.** The previous version took
  `LocationInformation` from the registry (`"Port_#0004.Hub_#0005"`), where
  `Hub_#NNNN` is a hub instance index assigned at detection. That index jumped
  with the plug untouched, and the client stopped seeing its module. The old
  `"location"` in `panel.json` now ends in a **readable configuration error**
  with instructions, not a silent migration.
- **The bus number does not enter the key** — it is a synthetic index of the
  controller, the same nature as `Hub_#`. `--list` shows it as diagnostics.
  Should two modules give the same chain (possible only with two USB
  controllers sharing the same port number), the selection ends in an error —
  never a shot in the dark.
- **Moving the plug changes the key.** That is unavoidable when identifying by
  topology, which is why `--list` prints a line ready to paste in.

Measured: `ports=(3,4)` matches the count from `DEVPKEY_Device_LocationPaths`
(`...#USB(3)#USB(4)`) and survives a device reset despite the USB address
jumping. Verified for **one** module at a time. The difference from the
previous version is qualitative, though: there is no longer anything to pair
up, because the chain comes from the same enumeration as the handle being
opened. Which module on the desk is which is, either way, settled only by
`--identify` — no reading resolves that.

The installer accepts a screen count: `.\deploy\install-task.ps1 -Panels 2`.
Without it, "OK" after the first line from the log would mean "drawing one out
of two". The script deliberately does not read `panel.json` — on a first
install that file does not exist yet.

The scheduled task must have "run **only when the user is logged on**" (the
network drive is mapped per session, and USB requires an interactive session)
and "stop after 3 days" turned off — enabled by default, it would kill the
panel every week.

## Diagnostics

| symptom | where to look |
|---|---|
| panel black / stale content | `panel.log`; is the task running; is another program holding the module |
| "panel held by another process" | another program or a second instance of the client |
| `missed_csw` rising | we sent the wrong byte count — a bug in the code, not in the hardware (AX206 only; the serial screen confirms nothing) |
| serial screen: colors swapped, image "shifted" | byte drift after a torn write. A full repaint does **not** fix this — unplug and replug the screen |
| one screen draws, the other does not | each panel has its own backoff; look in the log for the line with its tag (`turing-rev-a 8.4: …`) |
| numbers stand still, age keeps rising | the stream is alive, but the probe is silent — that is the correct picture |
| "unknown" on both accounts | no frames; check the token and the UUIDs in `panel.json` |
| a hard failure with no trace | `panel.log.fault` (faulthandler — there is no console under `pythonw`) |
