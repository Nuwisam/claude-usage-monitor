"""The client loop.

Rhythm: a tick every second, but a frame goes to the panel ONLY when the image
differs (link.send). With a clock without seconds and rounded countdowns the image
changes roughly once a minute plus on every SSE event — that is ~2 % of the time
on USB instead of 38 %.
"""
import os
import queue
import threading
import time

from . import config as C
from . import fmt, model, render, status, stream
from .link import PanelLink
from .log import get as log


class AlreadyRunning(Exception):
    pass


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


def single_instance(path=None):
    """A file lock. Returns a handle that has to be held until the process ends.

    The exclusive USB handle already guarantees that only one process draws — but the
    second instance would then spin in a loop of "panel held by another process",
    which reads like a hardware failure rather than like "it is already running".
    """
    import msvcrt

    path = path or os.path.join(C.OUTDIR, "panel.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+b")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise AlreadyRunning("panel already running (lock %s)" % path)
    return handle


class App:
    def __init__(self, cfg, monotonic=time.monotonic):
        self.cfg = cfg
        # An injectable clock, because `holding()` and the debounce/linger stamps read
        # it directly, and the tests cannot wait 300 s for the card to burn out.
        # `fmt.ServerClock` has been injected from the start — the same idiom, not a new one.
        self.monotonic = monotonic
        self.clock = fmt.ServerClock(monotonic)
        self.renderer = render.Renderer(cfg.width, cfg.height)
        # One link per configured screen. Constructing them must not enumerate or
        # open anything: App(cfg) has to stay cheap and hardware-free.
        self.panels = [PanelLink(spec, cfg) for spec in cfg.panels]
        self.q = queue.Queue()
        self.stop = threading.Event()

        self.accounts = {}          # uuid -> model.AccountStatus
        self.unknown_uuids = set()
        self.link_state = "down"
        self.contract_mismatch = None
        self.first_data_at = None
        self.started = monotonic()

        # Blocked sessions. `alerts` is the last FULL set from a frame — there are no
        # increments here, so there is no state to reconcile either.
        self.alerts = []
        self._seen_at = {}          # key -> monotonic of the first sighting (debounce)
        self._card = None           # (AlertState, linger expiry mono, or None)
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
            self.unknown_uuids = set(payload.get("unknown") or [])
            if self.unknown_uuids:
                log().warning("server does not know these accounts: %s",
                              ", ".join(sorted(self.unknown_uuids)))
            self.link_state = "live"
            log().info("hello: subscription %s, ping %ss, lifetime %ss",
                       payload.get("subscribed"), payload.get("pingSec"),
                       payload.get("maxLifetimeSec"))
        elif event == "account":
            raw = payload.get("account") or {}
            account = model.AccountStatus(raw)
            if account.uuid:
                self.accounts[account.uuid] = account
                self.unknown_uuids.discard(account.uuid)
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

        An alert matching no configured account lands on the TOP band. The rule is
        simple and deliberately stays that way: a static machine -> band mapping would
        drift apart after the first /login, and account switching is routine here.
        """
        slots = [a.uuid for a in self.cfg.accounts]
        out = {}
        for b in self.alerts:
            try:
                index = slots.index(b.account_uuid)
            except ValueError:
                index = 0
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
            self._card = (render.alert_state(live, now_ms, footer, flood), None)
        elif self._card is not None:
            state, expiry = self._card
            if expiry is None:
                # Linger: the card stays a moment longer and is FROZEN. Without freezing,
                # "waiting N min" would keep ticking on a prompt already answered, and
                # every step of it is a full frame on the AX206.
                self._card = (state, mono + self.cfg.blocked_linger_sec)
            elif mono >= expiry:
                self._card = None
        if self._card is not None:
            if self._flash_until is not None and mono >= self._flash_until:
                self._flash_until = None
            return render.ScreenState(alert=self._card[0])

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
        # accounts and painted "no data from server" for a dozen-odd seconds, that is
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
            account = self.accounts.get(slot.uuid)
            note = "unknown account" if slot.uuid in self.unknown_uuids else None
            bands.append(render.band_state(account, name=slot.name, now_ms=now_ms,
                                           show_clock=(index == 0), note=note,
                                           alert=flagged.get(index)))
        while len(bands) < 2:
            bands.append(None)
        return render.ScreenState(clock=fmt.hm(self.clock.now()),
                                  link=self.link_state, bands=bands)

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

    def tick(self):
        stream.drain(self.q, self.on_event)
        if self.holding():
            return None
        frame = self.renderer.frame(self.screen())
        self.ever_painted = True
        # Every panel gets the same frame and handles its own failures: one screen
        # held by another program must not stop the one next to it from drawing.
        for link in self.panels:
            link.send(frame)
        return frame

    def run(self):
        uuids = [a.uuid for a in self.cfg.accounts]
        client = stream.StreamClient(self.cfg, uuids, self.q, self.stop)
        client.start()
        log().info("start: %d account(s), panels: %s", len(uuids),
                   ", ".join(link.tag for link in self.panels) or "(none)")
        deadline = self.monotonic()
        try:
            while not self.stop.is_set():
                self.tick()
                deadline += self.cfg.tick_sec
                delay = deadline - self.monotonic()
                if delay < 0:
                    # After a long blit or a reset we do not make up the backlog —
                    # better to lose a tick than to chase our own tail.
                    deadline = self.monotonic()
                    delay = 0
                self.stop.wait(delay)
        finally:
            self.stop.set()
            for link in self.panels:
                link.close()        # without clearing the screen, deliberately

    def run_once(self, wait_sec=30.0):
        """Connect, wait for the first frame, draw once, exit."""
        uuids = [a.uuid for a in self.cfg.accounts]
        client = stream.StreamClient(self.cfg, uuids, self.q, self.stop)
        client.start()
        deadline = self.monotonic() + wait_sec
        while self.monotonic() < deadline and self.first_data_at is None:
            stream.drain(self.q, self.on_event)
            time.sleep(0.2)
        self.stop.set()
        got = self.first_data_at is not None
        frame = self.renderer.frame(self.screen())
        # Per-panel result, not just the stream's: a run where no screen drew
        # anything used to exit 0 because send()'s answer was thrown away.
        drew = [(link.tag, link.send(frame, force=True)) for link in self.panels]
        for link in self.panels:
            link.close()
        return got, frame, drew


def main(argv=None):
    from . import log as logging_setup
    cfg = C.load()
    logging_setup.setup(cfg.log_file, cfg.log_level)
    problems = cfg.validate()
    if problems:
        raise C.ConfigError("; ".join(problems))
    lock = single_instance()
    try:
        App(cfg).run()
    finally:
        lock.close()
