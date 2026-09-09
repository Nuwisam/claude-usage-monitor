"""A blocked session on the screen: the card, the fold into a marker, the cost of a scene change.

The clock is injected everywhere, because otherwise the window burn-out test would take 300 s.
"""
import pytest

from panel import app as app_mod, config as C, fmt, render, status, surface, theme

#: The widest clock face the panel draws. DERIVED from the formatter, never hand-typed:
#: a literal is a second definition of the face, and when the format moves the tests keep
#: measuring the old width while still passing. The anchor is all-double-digit so the
#: string stays the widest one `panel_clock` can produce.
CLOCK = fmt.panel_clock(fmt.parse_utc("2026-08-30T19:07:33Z"))


class Clock:
    """Monotonic under the test's control."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def cfg(**kw):
    # The flash is off by default: the card tests check the card, not what comes
    # before it. The flash tests switch it on explicitly.
    d = {"stream_token": "t", "account_1": {"uuid": "account-a"},
         "account_2": {"uuid": "account-b"}, "alert_flash_sec": 0}
    d.update(kw)
    return C.Config(d)


def after_flash(a, z):
    """Renders until the banner stops blinking. The window arms itself on the FIRST render
    after the key appears, so moving the clock alone is not enough."""
    screen = a.screen()
    z.advance(a.cfg.alert_flash_sec + 1)
    return a.screen()


NOW = "2026-08-05T21:07:00Z"


def app(clock, **kw):
    a = app_mod.App(cfg(**kw), monotonic=clock)
    a.clock.anchor(NOW)
    return a


def stream_frame(*entries):
    return {"contractVersion": 3, "serverNow": NOW, "alerts": list(entries)}


def entry(**kw):
    base = {"key": "s__main__k", "reason": "permission", "project": "proj",
            "machine": "laptop", "tool": "Bash", "since": NOW}
    base.update(kw)
    return base


# --- card entry and exit ----------------------------------------------------

def test_card_enters_after_debounce():
    z = Clock()
    a = app(z)
    a.on_event("alert", stream_frame(entry()))
    assert a.screen().alert is None, "debounce should suppress the card on an immediate approval"
    z.advance(a.cfg.blocked_debounce_sec)
    assert a.screen().alert is not None


def test_card_carries_project_tool_and_machine():
    z = Clock()
    a = app(z)
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    card = a.screen().alert
    row = card.rows[0]
    assert (row.project, row.tool, row.machine) == ("proj", "Bash", "laptop")
    assert card.title == "NEEDS PERMISSION"


def test_empty_set_extinguishes_card_at_once():
    """The card is a pure function of what is still waiting, so an answered prompt hands
    the screen back on the SAME tick — there is no threshold on the way out."""
    z = Clock()
    a = app(z)
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    assert a.screen().alert is not None
    a.on_event("alert", stream_frame())
    assert a.screen().alert is None, "an answered prompt must not outlive its own tick"


def test_window_burnout_collapses_card_to_marker():
    z = Clock()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", stream_frame(entry(since="2026-08-05T21:06:50Z")))   # 10 s ago
    z.advance(5)
    assert a.screen().alert is not None
    # The window counts from the SERVER's `since`, so we move the server clock, not just
    # the monotonic one: otherwise the test would check something other than the real run.
    a.clock.anchor("2026-08-05T21:08:00Z")
    a.first_data_at = z.t
    screen = a.screen()             # window burnt out: the card hands the screen back
    assert screen.alert is None, "after alert_takeover_sec the card must hand back the screen"
    assert screen.bands[0].alert, "but the entry lives on and must be visible as a marker"


def test_new_block_gets_a_new_window():
    z = Clock()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", stream_frame(entry(key="old", since="2026-08-05T21:00:00Z")))
    z.advance(5)
    assert a.screen().alert is None, "the old block already has a burnt-out window"
    a.on_event("alert", stream_frame(entry(key="old", since="2026-08-05T21:00:00Z"),
                                     entry(key="new", since=NOW)))
    z.advance(5)
    card = a.screen().alert
    assert card is not None
    assert len(card.rows) == 2, "a fresh block pulls the burnt one onto the same card"


def test_fresh_block_pulls_burnt_one_onto_same_card():
    """Regression on three dead layouts out of four.

    The window belongs to the SET: as long as anything is fresh, the card lists everything
    waiting. With per-entry filtering two blocks would have to start inside the same
    five-minute window — with sequential work that never happens, so `AlertPair`,
    `AlertList` and `AlertMany` had no way of reaching the screen.
    """
    z = Clock()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", stream_frame(entry(key="burnt", project="old",
                                            since="2026-08-05T21:00:00Z"),
                                     entry(key="fresh", project="new", since=NOW)))
    z.advance(5)
    card = a.screen().alert
    assert len(card.rows) == 2 and card.count == 2
    assert "2" in card.title, "the banner counts both, not just the fresh one"
    assert card.rows[0].project == "new", "the newest one is in the first row"


def test_burnt_out_set_extinguishes_entirely():
    """When the LAST fresh block burns out, the card hands the screen back along with the rest.

    To be fair: this is a guard AGAINST AN OVERREACHING FIX, not proof of one. It passes the
    same way on the old code and that is as it should be — it watches that a window belonging
    to the set has not started holding the card forever. The test that settles the change
    itself is `test_fresh_block_pulls_burnt_one_onto_same_card`.

    Both blocks have to come in fresh: a set that is old from the start builds no card at all,
    so there would be nothing to put out.
    """
    z = Clock()
    a = app(z, alert_takeover_sec=30)
    a.on_event("alert", stream_frame(
        entry(key="a", since="2026-08-05T21:06:50Z", accountUuid="account-a"),
        entry(key="b", since="2026-08-05T21:06:40Z", accountUuid="account-b")))
    z.advance(5)
    a.first_data_at = z.t
    assert len(a.screen().alert.rows) == 2
    # The SERVER clock, because the window counts from `since`, not from the monotonic one.
    a.clock.anchor("2026-08-05T21:09:00Z")
    screen = a.screen()             # window burnt out for both: the card goes at once
    assert screen.alert is None, "burnt-out set is extinguished entirely"
    assert screen.bands[0].alert and screen.bands[1].alert, "both entries live on as markers"


def test_zero_disables_card_even_without_since():
    """`alert_takeover_sec: 0` means "a marker right away, no card" — including for an entry
    with no stamp, which used to skip the age comparison and get a card after all."""
    z = Clock()
    a = app(z, alert_takeover_sec=0)
    a.on_event("alert", stream_frame(entry(since=None)))
    z.advance(5)
    a.first_data_at = z.t
    screen = a.screen()
    assert screen.alert is None
    assert screen.bands[0].alert, "the entry lives on as a marker"


# --- flash ------------------------------------------------------------------

def phases(a, z, seconds):
    """The `flood` values in consecutive seconds."""
    out = []
    for _ in range(seconds):
        out.append(a.screen().alert.flood)
        z.advance(1)
    return out


def test_full_frame_enters_then_fades():
    z = Clock(1000.0)
    a = app(z, alert_flash_sec=6)
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    readings = phases(a, z, 10)
    assert True in readings[:6] and False in readings[:6], "should flicker inside the window"
    assert not any(readings[7:]), "after the window the banner holds steady"


def test_flashing_does_not_return_while_card_ticks():
    # `alert_takeover_sec` explicitly infinite: the loop below walks 5 minutes, which is
    # exactly where the default window burns out — and a card that handed the screen
    # back would make this test pass for the wrong reason.
    z = Clock()
    a = app(z, alert_flash_sec=6, alert_takeover_sec="infinity")
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    after_flash(a, z)
    for _ in range(5):
        z.advance(60)
        assert not a.screen().alert.flood


def test_second_block_reignites_flashing():
    z = Clock()
    a = app(z, alert_flash_sec=6)
    a.on_event("alert", stream_frame(entry(key="a")))
    z.advance(5)
    after_flash(a, z)
    a.on_event("alert", stream_frame(entry(key="a"), entry(key="b")))
    z.advance(5)
    assert any(phases(a, z, 4))


def test_flashing_can_be_disabled():
    z = Clock()
    a = app(z, alert_flash_sec=0)
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    assert not any(phases(a, z, 4))


def test_infinity_flashes_for_the_cards_whole_life():
    z = Clock(1000.0)
    a = app(z, alert_flash_sec="infinity")
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    readings = phases(a, z, 60)
    assert any(readings) and not all(readings), "should flicker, not stay lit"
    assert any(readings[-6:]), "still flickers after a minute"


@pytest.mark.parametrize("raw,expected", [
    (20, 20.0), ("infinity", float("inf")), ("INF", float("inf")),
    (0, 0.0), (-5, 0.0), ("garbage", 0.0), (None, 0.0), (float("nan"), 0.0),
])
def test_seconds(raw, expected):
    """The values come from a hand-edited panel.json and go into a COMPARISON —
    a bare string crashes the tick with a TypeError. Junk must fall back to the default."""
    assert app_mod.seconds(raw) == expected


def test_takeover_infinity_does_not_collapse_card():
    z = Clock()
    a = app(z, alert_takeover_sec="infinity")
    a.on_event("alert", stream_frame(entry(since="2020-01-01T00:00:00Z")))
    z.advance(5)
    assert a.screen().alert is not None, "the card must stay until you respond"


def test_takeover_zero_gives_marker_immediately():
    z = Clock()
    a = app(z, alert_takeover_sec=0)
    a.first_data_at = z.t
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    screen = a.screen()
    assert screen.alert is None and screen.bands[0].alert


def test_garbage_in_thresholds_does_not_crash_the_tick():
    """Regression: `age >= "infinity"` is a TypeError, that is a dead tick under pythonw,
    with no console to show it."""
    z = Clock()
    a = app(z, alert_takeover_sec="garbage", alert_flash_sec="garbage")
    a.first_data_at = z.t
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    assert a.screen() is not None


def test_banner_flash_fits_in_the_tick():
    """The heart of the fix: a full-screen flash is a full frame (1.87 s on the Turing),
    that is a slow repaint instead of a flash. The banner alone has to be cheap enough
    to make it inside a second."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    blocks_ = status.parse_frame(stream_frame(entry()))
    R = render.Renderer()
    a = R.frame(render.ScreenState(alert=render.alert_state(blocks_, now_ms))).rgb565("be")
    b = R.frame(render.ScreenState(
        alert=render.alert_state(blocks_, now_ms, flood=True))).rgb565("be")
    rects = surface.coalesce(surface.dirty_tiles(a, b, 480, 320), surface.TILE)
    nbytes = sum((x1 - x0) * (y1 - y0) * 2 for x0, y0, x1, y1 in rects)
    assert nbytes / len(a) < 0.25, "the flash dirties %.1f%% of the frame" % (nbytes / len(a) * 100)
    assert nbytes * 6.1e-6 < 0.5, "%.0f ms on the wire — won't make it inside a tick" % (nbytes * 6.1e-3)


# --- precedence -------------------------------------------------------------

def test_alert_beats_holding():
    """An alert is the message being waited for — there is no reason for it to wait out
    the splash threshold."""
    z = Clock()
    a = app(z)
    assert a.holding()
    a.on_event("alert", stream_frame(entry()))
    assert not a.holding()


def test_alert_beats_mismatched_contract():
    z = Clock()
    a = app(z)
    a.contract_mismatch = 4
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    screen = a.screen()
    assert screen.alert is not None
    assert "contract" in screen.alert.footer, "the contract notice drops into the footer"


def test_after_card_we_do_not_return_to_holding():
    """Regression: the `ever_painted` latch alone was not enough, because `screen()` had its
    OWN, independent time gate — once the card went out it painted 'no data from server'."""
    z = Clock()
    a = app(z)
    a.on_event("alert", stream_frame(entry()))
    z.advance(5)
    assert a.tick() is not None
    a.on_event("alert", stream_frame())
    assert not a.holding()
    screen = a.screen()
    assert screen.message, "with no usage data we say so plainly, instead of painting empty bands"


def test_alert_does_not_pretend_to_be_fresh_data():
    """An `alert` frame may neither open the `first_data_at` gate nor set `link_state`
    to `live`: it is no proof that the usage data is fresh."""
    z = Clock()
    a = app(z)
    a.on_event("alert", stream_frame(entry()))
    assert a.first_data_at is None
    assert a.link_state == "down"


def test_config_switch_turns_off_alerts():
    z = Clock()
    a = app(z, session_alerts=False)
    a.on_event("alert", stream_frame(entry()))
    z.advance(60)
    assert a.alerts == []
    assert a.screen().alert is None


# --- the marker next to the account -----------------------------------------

def test_marker_lands_on_the_correct_accounts_band():
    z = Clock()
    a = app(z)
    a.first_data_at = z.t
    a.on_event("alert", stream_frame(entry(accountUuid="account-b")))
    bands = a.screen().bands
    assert not bands[0].alert and bands[1].alert


def test_unmatched_alert_lands_on_top_band():
    z = Clock()
    a = app(z)
    a.first_data_at = z.t
    a.on_event("alert", stream_frame(entry(accountUuid="account-we-dont-know")))
    bands = a.screen().bands
    assert bands[0].alert and not bands[1].alert


def test_reason_does_not_push_title_past_band():
    """The reason takes its room out of the TITLE's budget instead of being tacked on outside
    the band.

    Checked on the worst case: a long name, the clock, the link marker and the plan badge
    all at once — that is, everything competing for the same width.
    """
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    b = lay.bands[0]
    long_name = ("very.long.account.name.that.nobody.saw.coming"
                 "@subdomain.example.example.org")
    f = draw.font(L.F_NAME)
    lengths = []
    for alert in (None, "question"):
        img, d = draw.new_canvas((480, 320))
        state = render.BandState(title=long_name, plan="MAX 5×", show_clock=True,
                                 alert=alert)
        render.Renderer()._header(d, b, state,
                                  render.ScreenState(clock=CLOCK, link="live"))
        px = img.load()
        # The columns right of the band must stay background — nothing spilled outside the margin.
        for x in range(b.x1 + 1, 480):
            for y in range(b.header[1], b.header[3]):
                assert px[x, y] == theme.BG, "something spilled outside the band in column %d" % x
        # The end of the TITLE, not the end of the header: the title is set left, and the
        # reason, the badge and the clock right, so there is a gap between them. We look for
        # the first gap wider than the spacing between letters.
        ink = [any(px[x, y] != theme.BG for y in range(b.header[1], b.header[3]))
               for x in range(b.x0, b.x1)]
        end, gap = 0, 0
        for i, inked in enumerate(ink):
            if inked:
                end, gap = i, 0
            else:
                gap += 1
                if gap >= 6:
                    break
        lengths.append(end)
    assert lengths[1] < lengths[0], "the reason should shorten the title, not overlap it"


@pytest.mark.parametrize("canvas", [(480, 320), (1280, 720)])
def test_the_clock_face_stays_inside_the_band(canvas):
    """The clock is anchored right, so it can never spill past `b.x1` — but nothing stops it
    growing LEFT out of the band.

    `_header` shrinks `right` by the clock's width and then clamps the TITLE with
    `max(20, right - b.x0)`. That clamp protects the title, not the clock: the floor of 20 px
    is handed out even when there is no room left, and the clock itself is measured but never
    bounded. It fits today with a wide margin (128 px of a 452 px header) — this test is here
    so that a bigger `F_CLOCK`, a wider face or a tighter layout fails loudly instead of
    printing a date over the left margin.
    """
    from panel import draw, render as R

    renderer = R.Renderer(*canvas)
    b = renderer.layout.bands[0]
    long_name = ("very.long.account.name.that.nobody.saw.coming"
                 "@subdomain.example.example.org")
    img, d = draw.new_canvas(canvas)
    renderer._header(d, b, render.BandState(title=long_name, plan="MAX 5×",
                                            show_clock=True, alert="question"),
                     render.ScreenState(clock=CLOCK, link="live"))
    px = img.load()
    rows = range(b.header[1], b.header[3])
    for x in range(0, b.x0):
        for y in rows:
            assert px[x, y] == theme.BG, "header ink in the left margin, column %d" % x
    for x in range(b.x1 + 1, canvas[0]):
        for y in rows:
            assert px[x, y] == theme.BG, "header ink past the right edge, column %d" % x
    # And the face is whole: right-anchored at `b.x1 - CLOCK_MARK_W`, so its left end has to
    # land inside the band, not merely fail to paint outside it.
    face = draw.text_width(CLOCK, draw.font(renderer.L.F_CLOCK))
    assert b.x1 - renderer.L.CLOCK_MARK_W - face > b.x0, (
        "the clock face alone does not fit the header on %dx%d" % canvas)


def test_band_marker_has_full_height_and_sits_in_the_margin():
    """The 4 px marker sits in the margin (PAD_X 14), so the band's layout does not
    shift by a pixel — and it has the band's full height whatever the number of rows inside."""
    from panel import draw, layout as L

    lay = L.Layout(480, 320)
    for b in lay.bands:
        img, d = draw.new_canvas((480, 320))
        render.Renderer()._band(d, b, render.BandState(title="account",
                                                       alert="allow"),
                                render.ScreenState())
        px = img.load()
        assert all(px[x, y] == theme.ACCENT
                   for x in range(L.MARK_W) for y in range(b.top, b.bottom))
        assert px[L.MARK_W, b.top] != theme.ACCENT, "the marker is wider than 4 px"
        assert L.MARK_W < L.PAD_X, "the marker would have to take room from the band's content"


# --- cost on the wire -------------------------------------------------------

def scene_bands(now_ms):
    from tests import fixtures
    bands = [render.band_state(acc, now_ms=now_ms, show_clock=(i == 0))
             for i, acc in enumerate(fixtures.SCENES["base"]())]
    return render.ScreenState(clock=CLOCK, link="live", bands=bands)


def _fraction(a, b):
    R = render.Renderer()
    pa, pb = R.frame(a).rgb565("be"), R.frame(b).rgb565("be")
    tiles = surface.dirty_tiles(pa, pb, 480, 320)
    rects = surface.coalesce(tiles, surface.TILE)
    nbytes = sum((x1 - x0) * (y1 - y0) * 2 for x0, y0, x1, y1 in rects)
    return nbytes / len(pa), len(rects)


def test_transition_to_card_fits_under_full_frame_threshold():
    """Pins the number that `FULL_AT = 0.85` rests on.

    At the old threshold of 0.60 this transition landed ABOVE it and turned a set of
    crops into a full frame — 1.87 s instead of about 1.1 s, for no gain at all.

    The exact figures move with the scene: the header clock's width is part of the bands
    frame, so widening the face to a full date re-measured this at 59.0 % in 51 rectangles
    (it was 62.5 % in 45). The assertion deliberately pins the BAND, not the number —
    which is why it survived that change and this docstring did not.

    Dropping the clock glyph in front of the reset captions took the bands frame's ink
    down again, to 54.7 %, so the floor moves 0.55 -> 0.50. The floor is only a tripwire
    against the scene collapsing to nothing; the ceiling is the number that matters.
    """
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    bands = scene_bands(now_ms)
    card = scene_bands(now_ms)
    card.alert = render.alert_state(
        status.parse_frame(stream_frame(entry(since="2026-08-05T21:00:00Z"))), now_ms)
    fraction, rects = _fraction(bands, card)
    assert 0.50 < fraction < surface.FULL_AT, \
        "transition to the card changes %.1f%% of the frame — the FULL_AT threshold needs revisiting" % (
            fraction * 100)
    assert rects <= surface.MAX_RECTS


def test_marker_alone_is_cheap():
    """The marker is allowed to light up and go out often — it has to cost next to nothing.

    The threshold is looser than for the triangle (2%), because the bar runs through the
    band's WHOLE height and the account name changes color along with it: the left column of
    tiles and two headers get dirty. 6% is ~0.11 s on the Turing, still a fraction of a tick.
    """
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    bands = scene_bands(now_ms)
    with_marker = scene_bands(now_ms)
    for band in with_marker.bands:
        if band is not None:
            band.alert = "allow"
    fraction, rects = _fraction(bands, with_marker)
    assert fraction < 0.08, "the marker changes %.2f%% of the frame" % (fraction * 100)
    assert rects <= 12


# --- time format ------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "a moment"), (59, "a moment"), (60, "1 min"), (245, "4 min"),
    (3600, "1 h 00 min"), (3900, "1 h 05 min"), (86400, "1 d 0 h"),
    (183600, "2 d 3 h"),
])
def test_waited(seconds, expected):
    assert fmt.waited(0.0, seconds * 1000.0) == expected


def test_waited_does_not_go_below_zero():
    """Machine clocks drift apart, so a `since` from the future is real."""
    assert fmt.waited(10_000.0, 0.0) == "a moment"
    assert fmt.waited(None, 0.0) == "—"


def test_the_solo_time_line_fits_without_an_ellipsis():
    """"waiting: N" is the one string on the card drawn WITHOUT `draw.ellipsize`, so a
    longer prefix or a wider value would run off it in silence."""
    from panel import draw, layout as L

    L_ = L.AlertSolo(480, 320)
    f = draw.font(L_.F_WAITED)
    room = L_.x1 - L_.x0
    for value in ("a moment", "59 min", "23 h 59 min", "365 d 23 h"):
        line = "waiting: %s" % value
        assert draw.text_width(line, f) <= room, line


# --- card layouts -----------------------------------------------------------

def blocks(n):
    """n blocks with different reasons and stamps, in the parser's order."""
    return status.parse_frame(stream_frame(*[
        entry(key="k%d" % i, reason=("plan", "question", "permission")[i % 3],
              project="project-%d" % i, detail="detail %d" % i,
              since="2026-08-05T21:0%d:00Z" % i)
        for i in range(n)]))


@pytest.mark.parametrize("count,method", [
    (1, "_alert_solo"), (2, "_alert_pair"), (3, "_alert_list"),
    (4, "_alert_many"), (5, "_alert_many"),
])
def test_layout_picks_method_by_block_count(count, method, monkeypatch):
    """The threshold is at three: up to two the project name stays the hero, from three on
    it drops into a list, because three names at 34 px do not exist."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    state = render.alert_state(blocks(count), now_ms)
    called = []
    R = render.Renderer()
    for name in ("_alert_solo", "_alert_pair", "_alert_list", "_alert_many"):
        monkeypatch.setattr(R, name,
                            lambda d, a, n=name: called.append(n))
    R.frame(render.ScreenState(alert=state))
    assert called == [method]


def test_list_shows_three_but_counts_all():
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    state = render.alert_state(blocks(5), now_ms)
    assert len(state.rows) == render.ALERT_ROWS_MAX
    assert state.count == 5
    assert len(state.rest) == 2, "the remainder goes to the footer by name, it does not disappear"
    assert "5" in state.title


def test_banner_shows_the_time_now_and_the_rows_carry_the_ages():
    """The banner is a LIVE clock — the card takes the whole screen, so without it a blocked
    session leaves the desk with no clock at all.

    It used to show the start of the oldest wait. That reading did not go anywhere: `waited`
    on each row says how long that block has been going, per entry rather than as one
    aggregate. This pins both halves of the swap, so a revert cannot pass quietly."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    oldest = "2026-08-05T21:01:00Z"
    state = render.alert_state(status.parse_frame(stream_frame(
        entry(key="plan", reason="plan", since="2026-08-05T21:06:00Z"),
        entry(key="consent", reason="permission", since=oldest),
    )), now_ms)
    assert state.rows[0].short == "plan", "the newest goes first"
    assert state.at == fmt.panel_clock(fmt.parse_utc(NOW))
    assert state.at != fmt.panel_clock(fmt.parse_utc(oldest)), "the banner is not the oldest wait"
    # The ages are still on the screen, one per row: 1 min for the newest, 6 for the oldest.
    assert [r.waited for r in state.rows] == [fmt.waited(fmt.ms(fmt.parse_utc("2026-08-05T21:06:00Z")), now_ms),
                                              fmt.waited(fmt.ms(fmt.parse_utc(oldest)), now_ms)]


def test_the_banner_clock_advances_between_ticks():
    """A live clock has to MOVE. With the time frozen at the oldest wait, the card's only
    changing text was the `waited` label, so a banner stuck at one value would have looked
    correct — this is the assertion that would have caught it."""
    blocked = status.parse_frame(stream_frame(
        entry(key="consent", reason="permission", since="2026-08-05T21:01:00Z")))
    early = render.alert_state(blocked, fmt.ms(fmt.parse_utc("2026-08-05T21:07:00Z")))
    later = render.alert_state(blocked, fmt.ms(fmt.parse_utc("2026-08-05T21:07:01Z")))
    assert early.at != later.at, "one second apart, the banner clock did not move"


@pytest.mark.parametrize("kw", [{}, {"seconds": False}, {"date": False},
                                {"seconds": False, "date": False}])
def test_banner_time_follows_both_clock_switches(kw):
    """The card and the header must never wear two different clock shapes, so both switches
    have to reach the banner: a header without seconds beside a banner with them would read
    as a bug, and so would a bare time in one place against a full date in the other."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    blocked = status.parse_frame(stream_frame(
        entry(key="consent", reason="permission", since="2026-08-05T21:01:00Z")))
    assert render.alert_state(blocked, now_ms, **kw).at == \
        fmt.panel_clock(fmt.parse_utc(NOW), **kw)


@pytest.mark.parametrize("seconds,date", [(True, True), (False, True),
                                          (True, False), (False, False)])
def test_the_config_switches_reach_both_faces_through_the_app(seconds, date):
    """End to end, panel.json -> glass, through App.screen() and not around it.

    Everything either switch does happens at two call sites in `app.py`, and every
    other test here enters BELOW them - `fmt.panel_clock` directly, or `alert_state`
    with the keywords already supplied. So the whole config-to-face wiring could be
    deleted and the suite would stay green; that is the gap this closes, on both
    branches of `screen()`, which is also the only place the two faces meet.

    The card needs the debounce to elapse, and advancing the monotonic clock moves the
    ServerClock with it - so the banner is checked against NOW plus that same debounce,
    computed here rather than read back from the app, which would be circular.
    """
    z = Clock()
    a = app(z, clock_seconds=seconds, clock_date=date)
    a.first_data_at = z.t
    face = dict(seconds=seconds, date=date)

    assert a.screen().clock == fmt.panel_clock(fmt.parse_utc(NOW), **face), (
        "the band header did not take the switches")

    a.on_event("alert", stream_frame(entry(key="k", reason="permission",
                                           since="2026-08-05T21:06:00Z")))
    z.advance(a.cfg.blocked_debounce_sec)
    later = fmt.from_ms(fmt.ms(fmt.parse_utc(NOW))
                        + a.cfg.blocked_debounce_sec * 1000.0)
    card = a.screen().alert
    assert card is not None, "the card branch was not reached"
    assert card.at == fmt.panel_clock(later, **face), (
        "the alert banner did not take the switches")


def test_the_card_carries_the_link_state_the_bands_carry():
    """A live clock over a dead stream is the one thing this panel refuses to draw.

    The card is a TAKEOVER, so `_header` never runs and the only link mark on the glass is
    off it; the banner clock free-runs off the monotonic anchor and needs no stream to keep
    ticking; and `self.alerts` is not cleared by "down". Nothing here is wrong on its own —
    together they show a moving face over a block that may have been answered ten minutes
    ago. So the card reads the same value the bands read."""
    z = Clock()
    a = app(z, alert_takeover_sec="infinity")
    a.on_event("ping", {})
    a.on_event("alert", stream_frame(entry()))
    z.advance(a.cfg.blocked_debounce_sec + 1)
    assert a.screen().alert.link == "live"

    a.on_event("down", "socket timeout")
    z.advance(600)
    dead = a.screen()
    assert dead.alert is not None, "the card does not come down when the stream does"
    assert dead.alert.link == "down"


def test_a_dead_link_marks_the_banner_and_a_live_one_costs_nothing():
    """The mark is the exception, not furniture: a live link leaves the card exactly as it
    was, and only a broken one spends banner width. Asserted on the RENDER, because what is
    at stake is what reaches the glass, and confined to the banner, because the rows below
    are the same blocks either way."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    blocked = status.parse_frame(stream_frame(entry()))
    R = render.Renderer()

    def card(link):
        return R.frame(render.ScreenState(
            alert=render.alert_state(blocked, now_ms, link=link))).image

    unstated, live, down = card(None), card("live"), card("down")
    assert live.tobytes() == unstated.tobytes(), \
        "a live link must cost the ordinary card nothing"
    assert down.tobytes() != live.tobytes(), "a dead link leaves no mark on the glass"
    below = (0, render.layout_for(480, 320).BANNER_H, 480, 320)
    assert down.crop(below).tobytes() == live.crop(below).tobytes(), \
        "the mark belongs to the banner — it must not disturb the rows"


def test_fresh_block_is_visible_despite_three_older_ones():
    """Regression: with the window belonging to the SET, the reason's rank pushed out of the
    rows the very block that had taken the screen over — only its name stayed in the footer."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    state = render.alert_state(status.parse_frame(stream_frame(
        entry(key="s1", reason="plan", project="old-1", since="2026-08-05T19:07:00Z"),
        entry(key="s2", reason="plan", project="old-2", since="2026-08-05T19:10:00Z"),
        entry(key="s3", reason="question", project="old-3", since="2026-08-05T19:13:00Z"),
        entry(key="f", reason="permission", project="FRESH", since="2026-08-05T21:06:50Z"),
    )), now_ms)
    assert state.rows[0].project == "FRESH", "the block that took over the screen must be visible"
    assert state.rest == ["old-1"], "the oldest one goes down to the footer, not the newest"


def test_detail_tile_does_not_run_into_the_mode_strip():
    from panel import layout as L
    lay = L.Layout(480, 320)
    for lines in (1, 2):
        assert lay.alert_solo.fits(lines), "the tile for %d lines runs into the mode strip" % lines


@pytest.mark.parametrize("with_footer", [True, False])
def test_list_rows_fill_the_screen_without_gaps(with_footer):
    """The remainder of the division goes where the mockup's browser puts it. A gap of background
    at the footer would read as a screen cut short."""
    from panel import layout as L
    lay = L.Layout(480, 320)
    for layout_variant in (lay.alert_list, lay.alert_many):
        rects = layout_variant.rows(footer=with_footer)
        assert rects[0][0] == L.BANNER_H
        for (_, bottom), (top, _) in zip(rects, rects[1:]):
            assert top == bottom + L.DIVIDER_H, "rows do not meet at the divider"
        end = layout_variant.footer[1] if with_footer else layout_variant.height
        assert rects[-1][1] == end, "the last row does not reach the footer"


def _ink_bottom(px, x0, x1, y0, y1, bg, threshold=25):
    """The last row in which there is ink inside the given rectangle. For a caption with
    no descenders below the baseline that is exactly the baseline minus one."""
    last = None
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = px[x, y]
            if max(abs(p[i] - bg[i]) for i in range(3)) > threshold:
                last = y
                break
    return last


def test_banner_and_strip_ink_sits_where_the_mockup_says():
    """The baselines are MEASURED on the rendered mockup, so the test guards a measurement,
    not a formula.

    Measured on the mockup (`1a-alert`, rendered 3x, the bottom of the clock digits and the
    bottom of the caption in the `MODE` strip): banner 24.33 px, strip 306.33 px. Pillow with
    anchor="ls" puts the bottom of the ink at `base - 1`, so with the right constants the last
    written row is 23 and 305. It was 26 and 308 before — 1.67 px too low, in each of the four
    layouts at once.
    """
    from panel import draw, layout as L

    now_ms = fmt.ms(fmt.parse_utc(NOW))
    # With `permissionMode`, otherwise the `MODE` strip is not drawn at all and we would be
    # measuring an empty band.
    state = render.alert_state(
        status.parse_frame(stream_frame(entry(permissionMode="default"))), now_ms)
    px = render.Renderer().frame(render.ScreenState(alert=state)).image.load()

    assert L.BANNER_BASE == 24, "mockup measurement: bottom of the clock digits at 24.33 px"
    assert L.AlertSolo.MODE_BASE == 306, "mockup measurement: bottom of the `MODE` caption at 306.33 px"

    # The clock in the banner: the digits do not go below the baseline, so the bottom of the
    # ink gives it. The window is COMPUTED from the string that was drawn, not hard-coded:
    # the face grew from "21:01" to the full date and a fixed window would keep passing on
    # the tail of a longer string while measuring something else.
    at_x1 = 480 - L.ALERT_PAD_X
    at_x0 = at_x1 - draw.text_width(state.at, draw.font(L.F_BANNER_AT))
    assert _ink_bottom(px, at_x0, at_x1 + 1, 0, L.BANNER_H,
                       theme.ACCENT_800) == L.BANNER_BASE - 1
    # The `MODE` strip: neither "MODE" nor "default" has a descender.
    assert _ink_bottom(px, 18, 300, 320 - L.AlertSolo.MODE_H, 320,
                       theme.SUNKEN) == L.AlertSolo.MODE_BASE - 1


@pytest.mark.parametrize("count", [1, 2, 3, 5])
@pytest.mark.parametrize("flooded,color", [(False, "NEUTRAL_900"), (True, "ACCENT")])
def test_rail_is_present_in_both_frames_of_every_layout(count, flooded, color):
    """The rail is not a property of the full frame: it always sits below the banner, and
    flooding only repaints it. A bar appearing out of nowhere would be a stronger movement
    than a change of color, and outside the `alert_flash_sec` window the card would be left
    with no left edge.

    It runs through the WHOLE height below the banner, through the `MODE` strip in layout 1a
    and through the footers in 1c/1d too — which is why the banner is drawn last, over the
    content.
    """
    from panel import layout as L

    expected = getattr(theme, color)
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    state = render.alert_state(blocks(count), now_ms, flood=flooded)
    px = render.Renderer().frame(render.ScreenState(alert=state)).image.load()

    for y in range(L.BANNER_H, 320):
        for x in range(L.RAIL_W):
            assert px[x, y] == expected, \
                "rail has a hole at (%d, %d) with %d blocks" % (x, y, count)
        assert px[L.RAIL_W, y] != expected, \
            "rail wider than %d px in row %d" % (L.RAIL_W, y)
    assert px[0, L.BANNER_H - 1] != expected or flooded, \
        "rail bleeds into the banner"


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_transition_to_card_fits_under_threshold_for_every_layout(count):
    """`FULL_AT = 0.85` has to hold for EVERY layout, not only the one with a single block:
    above the threshold the set of crops turns into a full frame, that is 1.87 s on the
    Turing instead of ~1.2 s."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    bands = scene_bands(now_ms)
    card = scene_bands(now_ms)
    card.alert = render.alert_state(blocks(count), now_ms)
    fraction, rects = _fraction(bands, card)
    assert fraction < surface.FULL_AT, \
        "layout for %d blocks dirties %.1f%% of the frame — the FULL_AT threshold needs revisiting" % (
            count, fraction * 100)
    assert rects <= surface.MAX_RECTS


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_full_frame_fits_in_the_tick(count):
    """The heart of the movement layer: swapping the empty frame for the full one is the
    banner plus the rail, not the whole screen. A full-screen flash would be a full frame,
    that is 1.87 s of slow repainting instead of a flash.

    One pass per layout, because the cost of the flood must not depend on how many blocks
    happen to be waiting — the banner and the rail are shared, so the number has to come
    out the same."""
    now_ms = fmt.ms(fmt.parse_utc(NOW))
    empty = render.ScreenState(alert=render.alert_state(blocks(count), now_ms))
    full = render.ScreenState(
        alert=render.alert_state(blocks(count), now_ms, flood=True))
    fraction, _ = _fraction(empty, full)
    assert fraction < 0.25, "the full frame dirties %.1f%% of the frame" % (fraction * 100)
    assert fraction * len(render.Renderer().frame(empty).rgb565("be")) * 6.1e-6 < 0.5, \
        "won't make it inside a tick"
