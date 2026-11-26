"""The client loop.

Rhythm: a tick every second, but a frame goes to the panel ONLY when the image
differs (link.send). Under `clock_seconds: false` — a clock without seconds, and
rounded countdowns — the image changes roughly once a minute plus on every SSE
event, which is ~2 % of the time on USB instead of 38 %.

`clock_seconds` is off by DEFAULT, so that saving is what a fresh install gets.
Turn it on and it goes away: the clock face then moves every tick, on the bands and
on the alert card alike, so the image differs every second and a frame goes out
every second.
"""
import os
import queue
import threading
import time

from . import config as C
from . import fmt, keep, model, render, status, stream
from .link import PanelLink
# Re-exported: run.pyw and __main__.py import both from here, and they have since
# before the lock had a module of its own (lock.py says why it moved).
from .lock import AlreadyRunning, single_instance  # noqa: F401
from .lock import is_free
from .log import get as log

# How often the main loop proves it is alive to the supervisor (tray.py), which
# calls it hung after supervisor.HANG_SEC of silence.
HEARTBEAT_EVERY_SEC = 5.0
# How often the wait between ticks looks for the supervisor's stop request
# (keep.STOP_REQUEST). The supervisor kills a client that has not drawn its stopped
# frame within supervisor.STOP_GRACE_SEC, and the request must not wait for a long
# `tick_sec` to run out: 1 s is the default tick, so every tick answers as fast.
STOP_POLL_SEC = 1.0


class StreamDied(RuntimeError):
    """The SSE thread is gone. Raised by the main loop, so the process ends and its
    supervisor (tray.py) starts a fresh one — see `App.run`."""


def seconds(raw, default=0.0):
    """A time threshold from panel.json -> a number of seconds. "infinity" gives inf.

    Shared by `alert_flash_sec` and `alert_takeover_sec`, because both values are edited
    by hand and both go into a COMPARISON: a bare string knocks the tick over with a
    TypeError. Garbage means `default`, not an exception.
    """
    if isinstance(raw, str) and raw.strip().lower() in ("infinity", "inf"):
        return float("inf")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value or value < 0:         # NaN or negative
        return default
    return value


# How old the supervisor's own heartbeat (keep.TRAY_HEARTBEAT, beaten every loop step)
# may be for it to still count as running: the silence its guardian calls a hang
# (guard.HANG_SEC).
SUPERVISOR_SILENCE_SEC = 120.0


def supervised():
    """Whether a supervisor would start a new client if this one exited.

    The guardian's epoch (keep.EPOCH_ENV, inherited down the chain) says so for an
    installed chain. A tray supervisor started by hand has no epoch, but it beats
    keep.TRAY_HEARTBEAT like any other, so a fresh one says so too. The old scheduled
    task, or a client run by hand, has neither.

    A fresh heartbeat is not the whole answer for a client without an epoch, though.
    A supervisor that quit leaves it fresh for SUPERVISOR_SILENCE_SEC after it is gone,
    so tray.lock must be held as well: a supervisor holds it until it exits
    (tray.main). A stopped supervisor beats and holds the lock, but starts nothing
    until the owner says Start, so keep.TRAY_STATE saying "stopped" or "quit" means
    no. That file is written only under a guardian's epoch (keep.StateStore), and a
    new guardian leaves the previous one's in place, so it is read only while a
    guardian holds guard.lock: a supervisor started by hand, with no guardian above
    it, still counts however an earlier chain left the file.

    Under a guardian the file's epoch is not compared: this client has none to
    compare it with. A new guardian's supervisor that has not yet written the file
    can therefore be read through the previous chain's "stopped". It is accepted: a
    client run by hand can start next to a live chain only while that chain has no
    client of its own (panel.lock), the answer errs towards "not supervised", and
    such a client keeps ticking, so the age on the glass keeps growing.
    """
    if os.environ.get(keep.EPOCH_ENV):
        return True
    at = keep.mtime_ns(os.path.join(C.OUTDIR, keep.TRAY_HEARTBEAT))
    if at is None or time.time_ns() - at >= SUPERVISOR_SILENCE_SEC * 1e9:
        return False
    if is_free(name="tray"):
        return False
    if is_free(name="guard"):
        return True
    _, want, _ = keep.read_state(os.path.join(C.OUTDIR, keep.TRAY_STATE))
    return want not in ("stopped", "quit")


class App:
    def __init__(self, cfg, monotonic=time.monotonic, heartbeat_path=None,
                 stop_path=None):
        self.cfg = cfg
        # The supervisor's "stop" (keep.STOP_REQUEST). None — the default — means no
        # one can ask, for the same reason as the heartbeat: a test App never reads the
        # live file. Only `main()` passes the real path.
        self.stop_path = stop_path
        # Where `run()` proves it is alive. None — the default — writes nothing, so a
        # test that builds an App never touches the file the live supervisor watches;
        # only `main()` passes the real path.
        self.heartbeat = (keep.Toucher(heartbeat_path, HEARTBEAT_EVERY_SEC, monotonic)
                          if heartbeat_path else None)
        # An injectable clock, because `holding()` and the debounce stamps read
        # it directly, and the tests cannot wait 300 s for the card to burn out.
        # `fmt.ServerClock` has been injected from the start — the same idiom, not a new one.
        self.monotonic = monotonic
        self.clock = fmt.ServerClock(monotonic)
        # One link per configured screen. Constructing them must not enumerate or
        # open anything: App(cfg) has to stay cheap and hardware-free.
        self.panels = [PanelLink(spec, cfg) for spec in cfg.panels]
        # One renderer per DISTINCT canvas among the screens, built up front so a
        # canvas nothing can draw fails here and not at the first tick. A dict rather
        # than a list because the set is read again at every tick: the panels can be
        # replaced after construction, and the renderer for a canvas is worth keeping.
        self.renderers = {}
        for canvas in self._canvases():
            self._renderer(canvas)
        self.q = queue.Queue()
        self.stop = threading.Event()

        self.accounts = {}          # account key -> model.AccountStatus
        self.unknown_keys = set()
        self.link_state = "down"
        self.contract_mismatch = None
        self.first_data_at = None
        self.started = monotonic()

        # Blocked sessions. `alerts` is the last FULL set from a frame — there are no
        # increments here, so there is no state to reconcile either.
        self.alerts = []
        self._seen_at = {}          # key -> monotonic of the first sighting (debounce)
        self._carded = set()        # keys that already had their flash
        self._flash_until = None
        # A latch: whether anything has been painted in this run. Without it a card put
        # out in the 3rd second would hand control back to `holding()`.
        self.ever_painted = False

    # -- events ------------------------------------------------------------

    def on_event(self, event, payload):
        if event == "up":
            self.link_state = "reconnecting"
            return
        if event == "down":
            self.link_state = "down"
            return
        if not isinstance(payload, dict):
            return

        if payload.get("serverNow"):
            self.clock.anchor(payload["serverNow"])

        if event == "hello":
            version = payload.get("contractVersion")
            if version != model.CONTRACT_VERSION:
                self.contract_mismatch = version
                log().error("contract v%s, panel has v%s", version,
                            model.CONTRACT_VERSION)
            else:
                self.contract_mismatch = None
            self.unknown_keys = set(payload.get("unknown") or [])
            if self.unknown_keys:
                log().warning("server does not know these accounts: %s",
                              ", ".join(sorted(self.unknown_keys)))
            self.link_state = "live"
            log().info("hello: subscription %s, ping %ss, lifetime %ss",
                       payload.get("subscribed"), payload.get("pingSec"),
                       payload.get("maxLifetimeSec"))
        elif event == "account":
            raw = payload.get("account") or {}
            account = model.AccountStatus(raw)
            if account.key:
                self.accounts[account.key] = account
                self.unknown_keys.discard(account.key)
                self.first_data_at = self.first_data_at or self.monotonic()
            self.link_state = "live"
        elif event == "alert":
            # This branch sets neither `first_data_at` nor `link_state`, DELIBERATELY.
            # The first would open the `holding()` gate and wipe the image from the
            # previous run; the second would lie, because an alert is no proof that the
            # usage data is fresh — that arrives in the `account` frame and nowhere else.
            if not self.cfg.session_alerts:
                return
            self.alerts = status.parse_frame(payload)
            now = self.monotonic()
            keys = {b.key for b in self.alerts}
            for key in keys:
                self._seen_at.setdefault(key, now)
            for key in list(self._seen_at):
                if key not in keys:
                    del self._seen_at[key]
            self._carded &= keys
        elif event == "ping":
            self.link_state = "live"
        elif event == "lag":
            # Every frame carries the full state, so a backlog repairs itself.
            log().info("stream: lag (%s)", payload.get("reason"))
        elif event == "bye":
            self.link_state = "reconnecting"

    # -- the image ---------------------------------------------------------

    def alert_slots(self):
        """Bands carrying a mark: band index -> the reason in one word.

        Not just the set of indices, because the band writes next to the plan name WHAT
        it is waiting for. With several blocks on one account the first one stays — the
        order is set by `status.parse_frame`, so the first is also the newest.

        Matching goes by the account KEY when the alert carries the organization (probe
        v17 on) — one user in two organizations has one uuid, and both bands may be that
        user's. Such an alert matches the pair or NOTHING: falling back to the uuid would
        put it on the band of the user's OTHER organization, which is the wrong account.
        An older probe sends the uuid alone; only then does the first band of that user win.

        An alert matching no configured account lands on the TOP band. The rule is
        simple and deliberately stays that way: a static machine -> band mapping would
        drift apart after the first /login, and account switching is routine here.
        """
        slots = self.cfg.accounts
        out = {}
        for b in self.alerts:
            if b.org_uuid is not None:
                index = next((i for i, a in enumerate(slots)
                              if (a.account_uuid, a.org_uuid) == (b.account_uuid, b.org_uuid)),
                             0)
            else:
                index = next((i for i, a in enumerate(slots)
                              if a.account_uuid == b.account_uuid), 0)
            out.setdefault(index, b.short)
        return out

    def _card_alerts(self, mono, now_ms):
        """Blocks entitled to TAKE THE SCREEN — after debounce, when ANY ONE is in window.

        The `alert_takeover_sec` window belongs to the SET, not to the entry: as long as
        any block fits inside the window, the card lists ALL the waiting ones, including
        those whose own counter has burnt out. With per-entry filtering three layouts out
        of four were dead — two blocks would have had to start inside the same five-minute
        window, and with sequential work that does not happen.

        The card's time on screen is NOT extended by this: the predicate "the card
        stays" is still "there exists a post-debounce entry younger than the window".
        Only the CONTENT changes.

        The window counts from the server's `since`, not from the moment the panel saw
        the entry: otherwise a panel restart would resurrect the card for an hour-old
        block. Debounce is counted locally, because it is about flicker, and it stays
        PER ENTRY — an entry in debounce does not get onto the card and does not open
        the window.
        """
        takeover_sec = seconds(self.cfg.alert_takeover_sec, 300.0)
        debounced, any_fresh = [], False
        for b in self.alerts:
            seen = self._seen_at.get(b.key)
            if seen is None or mono - seen < self.cfg.blocked_debounce_sec:
                continue
            debounced.append(b)
            # `takeover_sec > 0` on the outside, because an entry without `since` skips
            # the age comparison: without this, `alert_takeover_sec: 0` would give it a
            # card against its own documentation.
            if takeover_sec > 0 and (b.since is None
                                     or (now_ms - fmt.ms(b.since)) / 1000.0 < takeover_sec):
                any_fresh = True
        # the state stopped being TAKEOVER-WORTHY; it did not stop being true
        return debounced if any_fresh else []

    def screen(self):
        mono = self.monotonic()
        now_ms = self.clock.now_ms()

        live = self._card_alerts(mono, now_ms)
        if live:
            # The flash is per KEY, not per card: a second block has to draw attention
            # too, and the tick of "waiting N min" flashes nothing.
            new_keys = {b.key for b in live} - self._carded
            flash_sec = seconds(self.cfg.alert_flash_sec)
            if new_keys and flash_sec > 0:
                self._flash_until = mono + flash_sec
            self._carded |= {b.key for b in live}
            # The blink phase comes from the clock, not from a tick counter: on a lost
            # tick (a scene change costs more than a second) the counter would drift
            # away from the time and the blinking would slow down along with the panel.
            flood = (self._flash_until is not None and mono < self._flash_until
                     and int(mono) % 2 == 0)
            # A contract mismatch drops into the footer instead of covering the alert:
            # the alert is the one thing on this screen that calls for getting up.
            footer = ("panel: contract mismatch"
                      if self.contract_mismatch is not None else None)
            if self._flash_until is not None and mono >= self._flash_until:
                self._flash_until = None
            # `link` is the same value the bands get, and for the same reason: the card
            # covers the header, so this is the only place its mark can come from. The
            # banner clock ticks off the monotonic anchor whether the stream is alive or
            # not, and `self.alerts` is never cleared by "down" — so without this the
            # card is a moving face over a block that may already have been answered.
            return render.ScreenState(
                alert=render.alert_state(live, now_ms, footer, flood,
                                         link=self.link_state,
                                         **self._clock_switches()))
        # The card is a pure function of `live`: nothing takeover-worthy, nothing drawn.
        # No state to reconcile and no threshold on the way out — answering hands the
        # screen back on this tick.
        self._flash_until = None

        if self.contract_mismatch is not None:
            return render.ScreenState(message=[
                "Contract mismatch",
                "server reports v%s, panel has v%s" % (self.contract_mismatch,
                                                       model.CONTRACT_VERSION),
                "update the panel client",
            ])

        # With no time gate of its own: `holding()` already answers for "do not touch
        # the screen while you know nothing". A second, independent threshold here was
        # a bug — once the alert card went out, control went into the bands with empty
        # accounts and painted "no data from server" for a dozen or so seconds, that is
        # exactly the screen `holding()` is there to suppress.
        if self.first_data_at is None:
            host = self.cfg.stream_url.split("//")[-1].split("/")[0]
            reason = ("no connection" if self.link_state == "down"
                      else "waiting for first data")
            return render.ScreenState(message=[
                "Claude Usage Monitor", host, reason,
            ])

        flagged = self.alert_slots()
        bands = []
        for index, slot in enumerate(self.cfg.accounts):
            account = self.accounts.get(slot.key)
            note = "unknown account" if slot.key in self.unknown_keys else None
            bands.append(render.band_state(account, name=slot.name, now_ms=now_ms,
                                           show_clock=(index == 0), note=note,
                                           alert=flagged.get(index)))
        while len(bands) < 2:
            bands.append(None)
        # `now_ms` and NOT a second `self.clock.now()`: one frame must be one instant.
        # Sampling the ServerClock twice let the header land on a different second than
        # the rest of the frame, because the two reads straddle a boundary whenever the
        # monotonic source advances between them — which it does, every time.
        return render.ScreenState(clock=fmt.panel_clock(fmt.from_ms(now_ms),
                                                        **self._clock_switches()),
                                  link=self.link_state, bands=bands)

    def stopped_screen(self):
        """The LAST frame, when the owner stops the panel from the tray — or None when
        there is nothing worth keeping on the glass.

        The bands as they are now, with every relative word taken out, because nothing
        will redraw them: the age becomes STOPPED (in the accent) with the data's own
        time when it is older than the stop, the resets become full
        dates, the link mark goes to "down", and the header clock stays at this instant
        — which is exactly when the panel stopped. No alert card: it is about a block
        that may be answered a minute from now.

        None before the first data: there are no numbers of ours to keep, and the
        supervisor draws its own card instead (keep.EXIT_STOPPED_BLANK).
        """
        if self.first_data_at is None or self.contract_mismatch is not None:
            return None
        now_ms = self.clock.now_ms()
        bands = []
        for index, slot in enumerate(self.cfg.accounts):
            account = self.accounts.get(slot.key)
            note = "unknown account" if slot.key in self.unknown_keys else None
            bands.append(render.band_state(account, name=slot.name, now_ms=now_ms,
                                           show_clock=(index == 0), note=note,
                                           stopped=True))
        while len(bands) < 2:
            bands.append(None)
        # The date is forced, whatever `clock_date` says: this face is frozen, and a
        # bare "21:07" still on the glass tomorrow would read as today's.
        switches = dict(self._clock_switches(), date=True)
        return render.ScreenState(clock=fmt.panel_clock(fmt.from_ms(now_ms), **switches),
                                  link="down", bands=bands)

    def _stop_requested(self):
        if not self.stop_path or not os.path.exists(self.stop_path):
            return False
        try:
            os.remove(self.stop_path)
        except OSError:
            pass
        return True

    def paint_stopped(self):
        """Puts `stopped_screen()` on every screen. True when every screen took it.

        Drawn whole (`force`), because it is the frame the glass keeps: a partial update
        over a screen we may have misjudged would leave the misjudged part there for good.

        False also when only SOME screens took it: the one that refused still carries
        the last LIVE frame, frozen numbers that look current, and only the supervisor's
        card (keep.EXIT_STOPPED_BLANK) goes to every screen and so reaches it too.
        """
        state = self.stopped_screen()
        if state is None:
            log().info("stop requested: no data yet, nothing to keep on the glass")
            return False
        frames = dict((canvas, self._renderer(canvas).frame(state))
                      for canvas in self._canvases())
        drew = [link.send(frames[link.canvas], force=True) for link in self.panels]
        log().info("stop requested: stopped frame on %d of %d screen(s)",
                   sum(1 for d in drew if d), len(drew))
        return all(drew)

    def _clock_switches(self):
        """The two config reads behind BOTH clock faces, in one place.

        The invariant is that the header and the alert banner never wear different clock
        shapes. It used to rest on two call sites remembering to pass the same pair, which
        is not an invariant but a habit; here there is one source and the habit cannot
        rot. Returned as kwargs because its two consumers take them by keyword.
        """
        return dict(seconds=self.cfg.clock_seconds, date=self.cfg.clock_date)

    # -- the loop ----------------------------------------------------------

    def holding(self):
        """Whether we keep our hands off instead of painting.

        The panel holds its last frame with no host attached, so until the first data
        arrives the glass carries the image from the PREVIOUS run — and that one is
        better than anything we can draw while knowing nothing yet. `splash_after_sec`
        alone did not settle this: it gated the status card only, while the bands with
        "no data from server" went to the panel on the very first tick — that is, every
        restart wiped the screen anyway.

        A contract mismatch is the exception: it is the one thing known right away that
        invalidates the image from the previous run. A blocked session is the second —
        an alert is the message being waited for, so there is no reason for it to wait
        out a threshold.

        `ever_painted` is a LATCH: a screen once painted must not return to the "hands
        off" state. Without it an alert card put out before the threshold elapsed handed
        control back here and the panel froze with half of the previous scene.
        """
        return (self.first_data_at is None
                and self.contract_mismatch is None
                and not self.alerts
                and not self.ever_painted
                and self.monotonic() - self.started < self.cfg.splash_after_sec)

    def _canvases(self):
        """The distinct canvases the configured screens have to be drawn on, in the
        order they are configured.

        With no screens at all there is still one canvas: the configured one. `tick`
        keeps returning a frame in that case, which is what `--out` and the tests read,
        and what keeps `ever_painted` meaning anything on a desk with nothing plugged in.
        """
        out = []
        for link in self.panels:
            if link.canvas not in out:
                out.append(link.canvas)
        return out or [(self.cfg.width, self.cfg.height)]

    def _renderer(self, canvas):
        r = self.renderers.get(canvas)
        if r is None:
            r = self.renderers[canvas] = render.Renderer(
                *canvas, glue_pair=self.cfg.glue_aligned_weekly_pair)
        return r

    def _frames(self):
        """One frame per distinct canvas, all from ONE screen state.

        Screens that share a canvas share the frame OBJECT, not a copy of it: the
        payload and the rotations are memoised on the frame, so two AX206s side by side
        pack the pixels once between them.
        """
        state = self.screen()
        return dict((canvas, self._renderer(canvas).frame(state))
                    for canvas in self._canvases())

    def tick(self):
        stream.drain(self.q, self.on_event)
        if self.holding():
            return None
        frames = self._frames()
        self.ever_painted = True
        # Every panel gets the frame for ITS canvas and handles its own failures: one
        # screen held by another program must not stop the one next to it from drawing.
        for link in self.panels:
            link.send(frames[link.canvas])
        return frames[self._canvases()[0]]

    def run(self):
        keys = [a.key for a in self.cfg.accounts]
        client = stream.StreamClient(self.cfg, keys, self.q, self.stop)
        client.start()
        log().info("start: %d account(s), panels: %s", len(keys),
                   ", ".join(link.tag for link in self.panels) or "(none)")
        deadline = self.monotonic()
        orphaned = False
        # Read by `_wait` too: with no ceiling on `tick_sec`, a thread that dies early
        # in a long wait must not wait the whole tick to be noticed.
        self._stream = client
        try:
            while not self.stop.is_set():
                # BEFORE the tick: an ordinary frame painted after the request would
                # only be overwritten, and on a slow screen that is a whole blit.
                if self._stop_requested():
                    # The events queued since the last tick go in first, so the frame
                    # kept on the glass is the newest we have, not one second older.
                    stream.drain(self.q, self.on_event)
                    return self.paint_stopped()
                self.tick()
                # The stream thread catches only network errors (StreamClient._once);
                # anything else — a MemoryError under system memory pressure — ends the
                # thread while this loop keeps ticking. The screen then shows numbers
                # that stopped moving, with a heartbeat that says all is well. Ending
                # the process instead hands it to the supervisor, which starts a whole
                # new one; the thread's own traceback is already in the log
                # (threading.excepthook, log.py).
                #
                # Only when a supervisor is there to start that new process, though
                # (`supervised`). A client run by the old scheduled task or by hand has
                # nobody to restart it: exiting would leave the glass on a frame whose
                # "N min ago" never moves again. There the loop goes on as it did
                # before, so the age on the glass keeps growing and shows the data is
                # stale; the error goes to the log once.
                if not client.is_alive() and not self.stop.is_set():
                    if supervised():
                        raise StreamDied("stream thread is gone; exiting for a restart")
                    if not orphaned:
                        orphaned = True
                        self._stream = None     # nothing left for `_wait` to watch
                        log().error("stream thread is gone and no supervisor would "
                                    "restart this process; the data on the screen "
                                    "stays stale until the panel is restarted")
                # AFTER the tick, so a tick that hangs stops the heartbeat with it.
                if self.heartbeat is not None:
                    self.heartbeat.beat()
                deadline += self.cfg.tick_sec
                delay = deadline - self.monotonic()
                if delay < 0:
                    # After a long blit or a reset we do not make up the backlog —
                    # better to lose a tick than to chase our own tail.
                    deadline = self.monotonic()
                    delay = 0
                self._wait(delay)
        finally:
            self.stop.set()
            for link in self.panels:
                link.close()        # without clearing the screen, deliberately

    def _wait(self, delay):
        """The pause between ticks, cut into slices of at most STOP_POLL_SEC.

        The heartbeat goes on between slices, so a long `tick_sec` does not read as a
        hang (supervisor.HANG_SEC), and a stop request ends the pause at the next
        slice, so it is not killed for missing supervisor.STOP_GRACE_SEC: panel.json
        needs no ceiling on it. The request is only seen here, not consumed — the top
        of the loop in `run` does that and draws the stopped frame. The slices are
        counted down, not timed against the clock: a stopped clock cannot make this
        loop spin.

        A dead stream thread ends the pause too, so `run` reaches its dead-stream
        check at once: otherwise the heartbeat beaten here would say "alive" for a
        whole long tick over numbers that stopped moving.
        """
        while delay > 0 and not self.stop.is_set():
            if self.stop_path and os.path.exists(self.stop_path):
                return
            stream_thread = getattr(self, "_stream", None)
            if stream_thread is not None and not stream_thread.is_alive():
                return
            step = min(delay, HEARTBEAT_EVERY_SEC, STOP_POLL_SEC)
            if self.stop.wait(step):
                return
            delay -= step
            if self.heartbeat is not None:
                self.heartbeat.beat()

    def run_once(self, wait_sec=30.0):
        """Connect, wait for the first frame, draw once, exit."""
        keys = [a.key for a in self.cfg.accounts]
        client = stream.StreamClient(self.cfg, keys, self.q, self.stop)
        client.start()
        deadline = self.monotonic() + wait_sec
        while self.monotonic() < deadline and self.first_data_at is None:
            stream.drain(self.q, self.on_event)
            time.sleep(0.2)
        self.stop.set()
        got = self.first_data_at is not None
        # The second rendering site in this file, and it has to group by canvas for the
        # same reason `tick` does: `force=True` skips the comparison against what is on
        # the glass, so a frame of the wrong size would go out unchallenged and every
        # panel would report True.
        frames = self._frames()
        # Per-panel result, not just the stream's: a run where no screen drew
        # anything used to exit 0 because send()'s answer was thrown away.
        drew = [(link.tag, link.send(frames[link.canvas], force=True))
                for link in self.panels]
        for link in self.panels:
            link.close()
        # One frame comes back, for the first screen's canvas — that is the one `--out`
        # writes. With two sizes on the desk a single file cannot show both.
        return got, frames[self._canvases()[0]], drew


def main(argv=None):
    from . import log as logging_setup
    cfg = C.load()
    logging_setup.setup(cfg.log_file, cfg.log_level)
    problems = cfg.validate()
    if problems:
        raise C.ConfigError("; ".join(problems))
    lock = single_instance()
    # Under a supervisor no panel.stop is removed here. A request found at start was
    # written while this client was starting (the supervisor's Stop), and removing it
    # left the supervisor waiting STOP_GRACE_SEC to kill us instead of drawing our
    # stopped frame. A leftover from an earlier client is the supervisor's to clear,
    # before it starts this one (Supervisor._maybe_start).
    # With no supervisor nobody clears it and nobody can have just written it: a
    # leftover from a supervised client killed before it read its request would end
    # this run on its first tick, so it is removed.
    stop_path = os.path.join(C.OUTDIR, keep.STOP_REQUEST)
    if not supervised():
        try:
            os.remove(stop_path)
        except OSError:
            pass
    try:
        # True / False only after a stop request: whether a stopped frame was drawn.
        # None is the ordinary end (the loop ended without one), and never happens in
        # practice — App.run returns only on a request or an exception.
        return App(cfg, heartbeat_path=os.path.join(C.OUTDIR, keep.CLIENT_HEARTBEAT),
                   stop_path=stop_path).run()
    finally:
        lock.close()
