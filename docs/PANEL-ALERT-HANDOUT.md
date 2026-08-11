# The "Claude is waiting for you" card — as built

The card the desk panel shows when a Claude Code session has stalled and is waiting on a
person. This document describes **what is built**: four layouts driven by the block count,
the motion layer, and the state after folding away. An earlier draft — cobbled together
from the band vocabulary, to test the mechanics — was not kept.

The reference is not a description, only images: `docs/handout/*.png`, rendered by the panel
and pushed through its own quantization, that is, **exactly what the glass will show**.

| File | What it shows |
|---|---|
| [`card-solo.png`](handout/card-solo.png) | one block — the project name as the hero, the `Detail` tile, the `Mode` strip |
| [`card-pair.png`](handout/card-pair.png) | two blocks — two equal halves of 140 px each |
| [`card-list.png`](handout/card-list.png) | three blocks — a list of 3 × 77 px rows, the newest one's detail in the footer |
| [`card-many.png`](handout/card-many.png) | five blocks — three rows and a counter for the rest |
| [`card-flooded.png`](handout/card-flooded.png) | a **full** frame of the motion layer: banner in the accent plus the 6 px rail |
| [`bands-marker-upper.png`](handout/bands-marker-upper.png) | state after folding away — a 4 px bar next to the upper account |
| [`bands-marker-lower.png`](handout/bands-marker-lower.png) | the same next to the lower account (with credits — the band is just as tall) |
| [`bands-no-alert.png`](handout/bands-no-alert.png) | the same screen without an alert, for comparison |

Regenerate:

```
cd panel
python tools/render-png.py --alert solo   --zoom 3 --rgb565 --out ../docs/handout/card-solo.png
python tools/render-png.py --alert pair   --zoom 3 --rgb565 --out ../docs/handout/card-pair.png
python tools/render-png.py --alert list   --zoom 3 --rgb565 --out ../docs/handout/card-list.png
python tools/render-png.py --alert many   --zoom 3 --rgb565 --out ../docs/handout/card-many.png
python tools/render-png.py --alert solo --flood --zoom 3 --rgb565 --out ../docs/handout/card-flooded.png
python tools/render-png.py --marker upper --zoom 3 --rgb565 --out ../docs/handout/bands-marker-upper.png
python tools/render-png.py --marker lower --zoom 3 --rgb565 --out ../docs/handout/bands-marker-lower.png
python tools/render-png.py                --zoom 3 --rgb565 --out ../docs/handout/bands-no-alert.png
```

`--rgb565` is **mandatory** for review: without it you are looking at how much nicer the
same thing happens to look on a desktop screen, not at what the panel draws.

---

## Canvas and material

- **480 × 320 px, one landscape layout.** There is no quarter turn — the panel accepts
  only 0° or 180°, because a 90° rotation would need a second layout (320 × 480).
- **RGB565 (5/6/5).** Every background/foreground pair has to survive quantization; that's
  guarded by `panel/tests/test_render.py::test_colors_survive_quantization` and
  `::test_color_pairs_survive_quantization`.
- **No transparency.** In CSS, half-opacity is done with `color-mix(…, transparent)`; the
  panel mixes it in up front (`theme.mix`), so there is no alpha at all at draw time.
- **Font: Segoe UI Regular.** Pillow's `raqm` is `False`, so there's no asking for
  `tabular-nums` — digits have to be tabular **on their own**. Segoe UI Light **is not** (the
  one is narrower than the zero), so a large number would jitter on every change.
- **Icons are drawn as vectors, never with an icon font.** At 11 px a font glyph turns into a
  blob; `draw.clock_glyph` is the precedent for that.
- **Height is measured from the actual outline, not from the nominal size.** The probe string
  that does the measuring (`PROBE` in `panel/panel/draw.py`) is a run of accented capitals and
  descenders, `ĄĘŚŹŻgjpqy` — accents that reach up, tails that reach down. Measured against the
  nominal size instead, `Ń` and `ą` clip at 10–12 px. Those glyphs are the measurement, not
  text, and none of them reaches the glass: swapping them for unaccented letters would
  silently change every line height on the panel.

### Palette (`panel/panel/theme.py`)

| Name | RGB | Where |
|---|---|---|
| `BG` | `#1C1B19` | background of the whole screen, also the card; **and text in the flooded banner** |
| `SURFACE` / `SUNKEN` | `#262523` / `#211F1D` | `Detail` tile / `Mode` strip and list footers |
| `TEXT` | `#F0EEE6` | project name |
| `ACCENT` / `ACCENT_500` | `#D97757` | bars, the link dot, **the flooded banner, the 6 px rail, the 4 px marker** |
| `ACCENT_800` | `#46281D` | banner background at rest |
| `ACCENT_700` / `_300` / `_200` / `_100` | `#8A4A33` / `#E89477` / `#F0AB90` / `#F7CBB8` | `_200` = waiting time and reasons, `_100` = banner title and the account name with a block |
| `NEUTRAL_900…100` | `#322F2B` … `#D6D2C6` | bar tracks; `_900` **also the rail at rest** |
| `TEXT_78…TEXT_10` | mixes of `TEXT` with the card background | secondary text on the `BG` background |
| `TEXT_*_SURFACE` / `TEXT_*_SUNKEN` | the same percentages, but on the tile / strip background | text in the `Detail` tile, in the `Mode` strip, and in list footers |

These last two rows are not duplicates. In CSS, `color-mix(…, transparent)` mixes with
whatever is **actually underneath** it, so the same percentage over the tile and over the
card background is two different colors. The panel has no alpha — it mixes up front — so
every (percentage, background) pair has to be its own constant. The backgrounds differ by
(5, 4, 4), which after 5/6/5 quantization shows up on the green channel; the fidelity
audit caught it on the label in the tile and in both footers.

**There is no red in the design.** `DANGER` and `draw.warn_triangle` were removed together
with the warning triangle — every signal now runs on the accent ramp. That leaves the panel
with the same palette as `frontend/src/styles/theme.css`, without a single exception.

---

## Four layouts

Chosen by the **number of blocks**, not a flag (`render.Renderer._alert`). The threshold
sits at three: up to two, the project name stays the hero, because there's a specific window
to go back to and you need to know which one; from three on, names drop into a list, because
three names don't fit in 34 px.

What counts is the number of blocks **waiting**, not those from the last five minutes: the
takeover window belongs to the **set**, so as long as any one of them is inside the window,
the card lists all of them. Back when the window was counted separately per entry, three of
these four layouts had no way onto the screen — two blocks would have had to start within
the same five-minute window.

| Blocks | Layout | What you see |
|---|---|---|
| 1 | `AlertSolo` | name at 34 px (26 px when it doesn't fit), tool and machine, "waiting N", `Detail` tile up to two lines, `Mode` strip |
| 2 | `AlertPair` | two equal halves, name at 30 px, detail cut to one line, no strip |
| 3 | `AlertList` | reason in a fixed 58 px column, name at 19 px, time to the right, the newest one's detail in the footer |
| 4+ | `AlertMany` | the three newest with a 17 px name and just the machine, the rest counted in the footer ("+2 MORE" and names) |

Order always comes from `panel/panel/status.py`: **youngest first**, an entry without
`since` goes last. The reason has no say in it. Rank (`plan`, `question`, `permission`) used
to govern the order, and that was harmless only as long as everything on the card was under
five minutes old; once the window came to belong to the set, rank pushed out of the rows
exactly the block that had just taken over the screen. Rationale for the new order: **every
block was already shown solo when it arrived** — once cut down to three rows, the ones worth
showing are the ones you haven't seen yet.
**The hour in the banner is the start of the oldest wait on screen**, not the header's
`since` — rows go youngest first, so the first of them is by definition the newest, and the
banner says how long all of this has already been going on.

With many blocks the banner reads `WAITING · 3`, not "3 waiting". The banner keeps a fixed
vocabulary: one word, then a separate counter after the dot. Nothing about the wording depends
on the number, so there is no phrase to build around it and no second form to render — the
word stays put, only the digit changes, and the banner reads identically at one block and at
fifty. It also keeps the caption's width predictable, which is what a 480 px banner needs.

### Entry fields

Full contract in [`API.md` § 3.2](API.md). All of them are used:

| Field | Where on the card |
|---|---|
| `reason` | banner heading (1 block) and the reason word in the list; **an unknown value is legal** and gives "CLAUDE IS WAITING" / "waiting" |
| `project` | the project **root** name — hero in layouts 1 and 2, column in the list |
| `tool` | row under the name (not in layout 4+, where only the machine remains) |
| `machine` | same row; a session can run remotely, this says where to go |
| `detail` | `Detail` tile (2 lines), one line in layout 2, footer in layout 3 |
| `since` | "waiting N", the hour in the banner, row order, and opening the window for the set |
| `accountUuid` | which band the marker sits at once folded away |
| `agentType`, `permissionMode` | `Mode` strip — the answer to "why is it even asking" |

---

## Motion layer: two frames, not an animation

The panel repaints itself **line by line from the top**, so a stepped animation would tear
over the sweep and read as a glitch. What movement remains is the swap of **two full
frames**:

- **empty** — banner `ACCENT_800`, 6 px rail in `NEUTRAL_900`,
- **full** — banner flooded with `ACCENT` and rail in `ACCENT`, and the text in the banner
  drops to `BG` (5.51:1 instead of 2.69:1).

**The rail is present in both frames** — the flood only repaints it. A bar appearing out of
nothing is a stronger movement than a change of color, and outside the `alert_flash_sec`
window the card would be left without a left edge. The rail runs the full height under the
banner, through the `Mode` strip and the list footers too — in the mockup it's `position:
absolute`, so it paints above the blocks in the flow. Guarded by
`test_rail_is_present_in_both_frames_of_every_layout`, separately for each of the four layouts and
both frames.

The flood dirties ~13% of the frame, about 0.24 s on the Turing — well inside a tick. A
full-screen flash would be a full frame (1.87 s), that is, a slow repaint instead of a
flash. Guarded by `panel/tests/test_alert.py::test_full_frame_fits_in_the_tick`.

**Nothing outside the banner and the rail moves between frames** — text jumping by a pixel
would read as a glitch. The mockup draws the text in the flooded banner 2 px higher than at
rest (a separate layer, different sub-pixel rounding); the panel keeps one baseline for both.

The motion layer draws **last**, over the content: otherwise the `Mode` strip would paint
over the rail.

**A deliberate departure: the hour in the flooded banner.** The mockup disagrees with itself
here — `1a-alert` paints it in solid `BG`, while the three `-p` frames give `color-mix(BG
76%, transparent)`, that is, `#493128`. The panel keeps solid `BG` in **all four** layouts:
that's 5.51:1 on the accent, while the 76% version gives 3.84:1 — below AA for 15 px text.
The hour is the only number on this card, and the card exists so that someone gets up from
their desk.

---

## Limits this design can't work around

Every point here is measured.

**1. A scene change doesn't fit in a tick.** The bands → card transition dirties about
**62%** of the frame across several dozen rectangles, that is, ~**1.17 s** on the Turing
plus 355 ms on the AX206. `link.send` runs its usual loop over the screens, so a tick costs
the **sum** of both: ≈ **1.5 s** at `tick_sec = 1.0`. The transition drops one tick —
accepted, not something to hide.

**2. The full-frame threshold is 0.85** (`panel/panel/surface.py`). The set of crops is
never heavier than a full frame, so the real constraint is `MAX_RECTS = 256`. A layout
scattered across several dozen small elements **is** more expensive than a few compact
blocks — not in bytes, only in the number of rectangles. Guarded by
`test_transition_to_card_fits_under_threshold_for_every_layout`, separately for all four
layouts.

**3. Time MUST be coarse.** The AX206 has no cropping, so any change to a string is a full
355 ms. Seconds would turn ~2.5% of USB load into ~35% **for the card's whole life on
screen**. Hence `fmt.waited()`: "a moment" / "4 min" / "1 h 05 min" / "2 d 3 h". **There is
no live clock on the card** — the hour in the banner is a static moment. Any element that
ticks faster than once a minute is forbidden.

**4. The card gives the screen back 5 minutes after the LAST block.** `alert_takeover_sec`
(default 300 s, counted from the server's `since`) collapses it to a 4 px bar next to the
account name. The window belongs to the set, so it's timed by the youngest block, not each
one separately — the card's time on screen ends up the same as counting per entry, only its
contents change. This state is visible longer than the card itself.

**5. The marker fits in the band without moving the layout.** The 4 px bar sits inside the
margin field (`PAD_X` 14) and has the band's **full height**, regardless of how many rows
are inside — an account with credits has four rows instead of three, and the band is just as
tall all the same. The reason takes up 10 px of caps on the line with the plan name, laid out
inward from the right edge and out of the title's own budget, and the account name switches
to `ACCENT_100`.

**6. The card beats everything, including the "Contract mismatch" card.** The latter then
drops down into the `Mode` strip. The reverse order would demote the alert to a row at the
bottom of an error screen — and this is the one thing on this panel that requires you to get
up from your desk.

**7. Debounce 2 s on entry, linger 10 s on exit**, the card is **frozen** during linger.
These are judgment calls, not measurements, and all three are keys in `panel.json`.

---

## Idioms this code holds to

- **Coordinates computed from constants** in `panel/panel/layout.py`, never hard-coded in
  the drawing code.
- **Baselines measured on the rendered mockup**, not computed from the CSS boxes: the
  panel's typeface has different vertical metrics than the mockup font, so the same box
  model puts the ink 1–3 px somewhere else. The gap between boxes is invisible, the position
  of the ink is visible. **The measurement measures the BOTTOM OF THE INK**, not the middle
  of the box — and Pillow's `anchor="ls"` puts it at `base − 1`. `BANNER_BASE` and
  `MODE_BASE` sat two pixels too low for it, until the fidelity audit measured them again
  (24.33 px and 306.33 px on the mockup rendered at 3×). Guarded by
  `test_banner_and_strip_ink_sits_where_the_mockup_says`.
- **Rectangles in the half-open convention** (`draw.fill_rect`, `draw.rounded`): Pillow
  counts bounds inclusively, so a banner `(0, 0, 480, 38)` comes out 39 px.
- `draw.ellipsize` on **every** string of unknown length; `draw.ellipsize_tracked` wherever a
  string goes through `text_tracked` (letter spacing counts toward the width).
- A step down in size instead of truncation, when text doesn't fit: `F_PROJECT` 34 px →
  26 px.
- Right-to-left cursor in the banner header — elements are laid out inward from the right
  edge, and the title takes what's left.
- Three text sizes and nothing in between: 10 px caps-only labels, 11 px secondary data,
  12 px and up for content. Caps labels carry `letter-spacing` (`draw.text_tracked`).
- The `Detail` tile wraps at font 12 across 420 px, two lines at most (`AlertSolo`,
  `draw.wrap_lines`); the solo demo detail — "Dump scope: session only, session and week, or
  all the limit windows" — fits in one.
  A longer one wraps to both: the plan-approval demo text (`panel/tools/render-png.py:39-40`) with one
  clause appended, so the second line actually fills. Both cases are pinned by the wrap
  parametrize in `panel/tests/test_render.py`.

## Where things live

`panel/panel/layout.py` → `AlertSolo`, `AlertPair`, `AlertList`, `AlertMany` (geometry and
font sizes) and `panel/panel/render.py` → `Renderer._alert*` (drawing). A new field from the
frame goes into `status.Blocked`, `render.AlertRow` and `render.alert_state`. Transport, the
state machine, debounce, burning down the window and the marker are separate concerns, and
each is tested.
