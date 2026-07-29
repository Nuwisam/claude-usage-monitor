"""Petla klienta.

Rytm: tick co sekunde, ale klatka leci na panel TYLKO gdy obraz sie rozni
(link.send). Przy zegarze bez sekund i zaokraglonych odliczeniach obraz zmienia
sie mniej wiecej raz na minute plus przy kazdym zdarzeniu SSE — czyli ~2 % czasu
na USB zamiast 38 %.
"""
import os
import queue
import threading
import time

from . import config as C
from . import fmt, model, render, stream
from .link import PanelLink
from .log import get as log


class AlreadyRunning(Exception):
    pass


def single_instance(path=None):
    """Blokada na pliku. Zwraca uchwyt, ktory trzeba trzymac do konca procesu.

    Wylaczny uchwyt USB juz gwarantuje, ze rysuje tylko jeden proces — ale druga
    instancja krecilaby sie wtedy w petli "panel zajety przez inny proces",
    ktora czyta sie jak awaria sprzetu, a nie jak "juz dziala".
    """
    import msvcrt

    path = path or os.path.join(C.OUTDIR, "panel.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+b")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise AlreadyRunning("panel juz dziala (blokada %s)" % path)
    return handle


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.clock = fmt.ServerClock(time.monotonic)
        self.renderer = render.Renderer(cfg.width, cfg.height)
        self.panel = PanelLink(cfg)
        self.q = queue.Queue()
        self.stop = threading.Event()

        self.accounts = {}          # uuid -> model.AccountStatus
        self.unknown_uuids = set()
        self.link_state = "down"
        self.contract_mismatch = None
        self.first_data_at = None
        self.started = time.monotonic()

    # -- zdarzenia ---------------------------------------------------------

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
                log().error("kontrakt v%s, panel zna v%s", version,
                            model.CONTRACT_VERSION)
            else:
                self.contract_mismatch = None
            self.unknown_uuids = set(payload.get("unknown") or [])
            if self.unknown_uuids:
                log().warning("serwer nie zna kont: %s",
                              ", ".join(sorted(self.unknown_uuids)))
            self.link_state = "live"
            log().info("hello: subskrypcja %s, ping %ss, zycie %ss",
                       payload.get("subscribed"), payload.get("pingSec"),
                       payload.get("maxLifetimeSec"))
        elif event == "account":
            raw = payload.get("account") or {}
            account = model.AccountStatus(raw)
            if account.uuid:
                self.accounts[account.uuid] = account
                self.unknown_uuids.discard(account.uuid)
                self.first_data_at = self.first_data_at or time.monotonic()
            self.link_state = "live"
        elif event == "ping":
            self.link_state = "live"
        elif event == "lag":
            # Kazda ramka niesie pelny stan, wiec zaleglosc naprawia sie sama.
            log().info("strumien: lag (%s)", payload.get("reason"))
        elif event == "bye":
            self.link_state = "reconnecting"

    # -- obraz -------------------------------------------------------------

    def screen(self):
        if self.contract_mismatch is not None:
            return render.ScreenState(message=[
                "Niezgodny kontrakt",
                "serwer podaje v%s, panel zna v%s" % (self.contract_mismatch,
                                                      model.CONTRACT_VERSION),
                "zaktualizuj klienta panelu",
            ])

        waiting = (self.first_data_at is None
                   and time.monotonic() - self.started >= self.cfg.splash_after_sec)
        if waiting:
            host = self.cfg.stream_url.split("//")[-1].split("/")[0]
            reason = ("brak polaczenia" if self.link_state == "down"
                      else "czekam na pierwsze dane")
            return render.ScreenState(message=[
                "Monitor limitow Claude", host, reason,
            ])

        now_ms = self.clock.now_ms()
        bands = []
        for index, slot in enumerate(self.cfg.accounts):
            account = self.accounts.get(slot.uuid)
            note = "nieznane konto" if slot.uuid in self.unknown_uuids else None
            bands.append(render.band_state(account, name=slot.name, now_ms=now_ms,
                                           show_clock=(index == 0), note=note))
        while len(bands) < 2:
            bands.append(None)
        return render.ScreenState(clock=fmt.hm(self.clock.now()),
                                  link=self.link_state, bands=bands)

    # -- petla -------------------------------------------------------------

    def holding(self):
        """Czy trzymamy rece przy sobie, zamiast malowac.

        Panel trzyma ostatnia klatke bez podlaczonego hosta, wiec do czasu
        pierwszych danych na szkle stoi obraz z POPRZEDNIEGO biegu — i on jest
        lepszy niz cokolwiek, co umiemy narysowac, nie wiedzac jeszcze nic.
        Samo `splash_after_sec` tego nie zalatwialo: bramkowalo wylacznie karte
        stanu, a pasy z napisem "brak danych z serwera" szly na panel juz
        w pierwszym ticku — czyli kazdy restart i tak wycieral ekran.

        Niezgodny kontrakt jest wyjatkiem: to jedyna rzecz, ktora wiemy od razu
        i ktora unieważnia obraz z poprzedniego biegu.
        """
        return (self.first_data_at is None
                and self.contract_mismatch is None
                and time.monotonic() - self.started < self.cfg.splash_after_sec)

    def tick(self):
        stream.drain(self.q, self.on_event)
        if self.holding():
            return None
        frame = self.renderer.frame(self.screen())
        self.panel.send(frame)
        return frame

    def run(self):
        uuids = [a.uuid for a in self.cfg.accounts]
        client = stream.StreamClient(self.cfg, uuids, self.q, self.stop)
        client.start()
        log().info("start: %d konto(a), panel %s", len(uuids),
                   self.cfg.device or "(jedyny)")
        deadline = time.monotonic()
        try:
            while not self.stop.is_set():
                self.tick()
                deadline += self.cfg.tick_sec
                delay = deadline - time.monotonic()
                if delay < 0:
                    # Po dlugim blicie albo resecie nie nadrabiamy zaleglosci —
                    # lepiej zgubic tick niz gonic wlasny ogon.
                    deadline = time.monotonic()
                    delay = 0
                self.stop.wait(delay)
        finally:
            self.stop.set()
            self.panel.close()      # bez czyszczenia ekranu, celowo

    def run_once(self, wait_sec=30.0):
        """Polacz, poczekaj na pierwsza ramke, narysuj raz, wyjdz."""
        uuids = [a.uuid for a in self.cfg.accounts]
        client = stream.StreamClient(self.cfg, uuids, self.q, self.stop)
        client.start()
        deadline = time.monotonic() + wait_sec
        while time.monotonic() < deadline and self.first_data_at is None:
            stream.drain(self.q, self.on_event)
            time.sleep(0.2)
        self.stop.set()
        got = self.first_data_at is not None
        frame = self.renderer.frame(self.screen())
        self.panel.send(frame, force=True)
        self.panel.close()
        return got, frame


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
