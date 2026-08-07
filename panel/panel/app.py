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
from . import fmt, model, render, status, stream
from .link import PanelLink
from .log import get as log


class AlreadyRunning(Exception):
    pass


def seconds(raw, default=0.0):
    """Prog czasu z panel.json -> liczba sekund. "infinity" daje inf.

    Wspolny dla `alert_flash_sec` i `alert_takeover_sec`, bo obie wartosci sa recznie
    edytowane i obie ida do POROWNANIA: goly string wywraca tick TypeError-em.
    Smieci znacza `default`, nie wyjatek.
    """
    if isinstance(raw, str) and raw.strip().lower() in ("infinity", "inf"):
        return float("inf")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value or value < 0:         # NaN albo ujemna
        return default
    return value


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
    def __init__(self, cfg, monotonic=time.monotonic):
        self.cfg = cfg
        # Zegar wstrzykiwalny, bo `holding()` i znaczniki debounce/linger czytaja go
        # wprost, a testy nie moga czekac 300 s na wypalenie karty. `fmt.ServerClock`
        # jest wstrzykiwany od poczatku — to ten sam idiom, nie nowy.
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

        # Zablokowane sesje. `alerts` to ostatni PELNY zbior z ramki — przyrostow tu
        # nie ma, wiec nie ma tez stanu do uzgadniania.
        self.alerts = []
        self._seen_at = {}          # klucz -> monotonic pierwszego zobaczenia (debounce)
        self._card = None           # (AlertState, mono_wygasniecia_lingera albo None)
        self._carded = set()        # klucze, ktore juz mialy swoj blysk
        self._flash_until = None
        # Latch: czy cokolwiek juz namalowalismy w tym biegu. Bez niego karta zgaszona
        # w 3. sekundzie oddawalaby sterowanie z powrotem do `holding()`.
        self.ever_painted = False

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
                self.first_data_at = self.first_data_at or self.monotonic()
            self.link_state = "live"
        elif event == "alert":
            # Ta galaz CELOWO nie ustawia ani `first_data_at`, ani `link_state`.
            # Pierwsze otworzyloby bramke `holding()` i wytarloby obraz z poprzedniego
            # biegu; drugie klamaloby, bo alert nie jest dowodem swiezosci danych
            # o zuzyciu — te przychodza wylacznie ramka `account`.
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
            # Kazda ramka niesie pelny stan, wiec zaleglosc naprawia sie sama.
            log().info("strumien: lag (%s)", payload.get("reason"))
        elif event == "bye":
            self.link_state = "reconnecting"

    # -- obraz -------------------------------------------------------------

    def alert_slots(self):
        """Pasy ze znacznikiem: indeks pasa -> powod jednym slowem.

        Nie sam zbior indeksow, bo pas pisze przy nazwie planu, NA CO czeka. Przy kilku
        blokadach na jednym koncie zostaje pierwsza — kolejnosc ustala `status.parse_frame`,
        wiec pierwsza jest tez najpilniejsza.

        Alert bez dopasowania do zadnego skonfigurowanego konta laduje na pasie GORNYM.
        Regula jest prosta i celowo taka zostaje: statyczne mapowanie maszyna -> pas
        rozjechaloby sie po pierwszym /login, a przelaczanie kont jest tu rutyna.
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
        """Blokady, ktore maja prawo ZAJAC EKRAN — po debounce i przed wypaleniem okna.

        Okno `alert_takeover_sec` liczy sie od `since` z serwera, nie od chwili, w
        ktorej panel zobaczyl wpis: inaczej restart panelu wskrzeszalby karte dla
        blokady sprzed godziny. Debounce liczy sie lokalnie, bo dotyczy migotania.
        """
        out = []
        for b in self.alerts:
            seen = self._seen_at.get(b.key)
            if seen is None or mono - seen < self.cfg.blocked_debounce_sec:
                continue
            if b.since is not None:
                age = (now_ms - fmt.ms(b.since)) / 1000.0
                if age >= seconds(self.cfg.alert_takeover_sec, 300.0):
                    continue        # stan przestal byc PRZEJMUJACY, nie przestal byc prawdziwy
            out.append(b)
        return out

    def screen(self):
        mono = self.monotonic()
        now_ms = self.clock.now_ms()

        live = self._card_alerts(mono, now_ms)
        if live:
            # Blysk per KLUCZ, nie per karta: druga blokada tez ma zwrocic uwage,
            # a tykniecie "czeka N min" niczym nie miga.
            swieze = {b.key for b in live} - self._carded
            okno = seconds(self.cfg.alert_flash_sec)
            if swieze and okno > 0:
                self._flash_until = mono + okno
            self._carded |= {b.key for b in live}
            # Faza migania z zegara, nie z licznika ticków: przy zgubionym ticku
            # (przejscie sceny kosztuje wiecej niz sekunde) licznik rozjechalby sie
            # z czasem i miganie zwalnialoby razem z panelem.
            flood = (self._flash_until is not None and mono < self._flash_until
                     and int(mono) % 2 == 0)
            # Niezgodny kontrakt schodzi do stopki zamiast przykrywac alert: to jedyna
            # rzecz na tym ekranie, ktora wymaga, zebys wstal od biurka.
            footer = ("panel: niezgodny kontrakt"
                      if self.contract_mismatch is not None else None)
            self._card = (render.alert_state(live, now_ms, footer, flood), None)
        elif self._card is not None:
            state, expiry = self._card
            if expiry is None:
                # Linger: karta zostaje jeszcze chwile i jest ZAMROZONA. Bez zamrozenia
                # "czeka N min" tykaloby dalej na prompcie, na ktory juz odpowiedziales,
                # a kazdy przeskok to pelna klatka na AX206.
                self._card = (state, mono + self.cfg.blocked_linger_sec)
            elif mono >= expiry:
                self._card = None
        if self._card is not None:
            if self._flash_until is not None and mono >= self._flash_until:
                self._flash_until = None
            return render.ScreenState(alert=self._card[0])

        if self.contract_mismatch is not None:
            return render.ScreenState(message=[
                "Niezgodny kontrakt",
                "serwer podaje v%s, panel zna v%s" % (self.contract_mismatch,
                                                      model.CONTRACT_VERSION),
                "zaktualizuj klienta panelu",
            ])

        # Bez wlasnej bramki czasu: `holding()` juz odpowiada za "nie dotykaj ekranu,
        # dopoki nic nie wiesz". Drugi, niezalezny prog w tym miejscu byl bledem —
        # po zgaszeniu karty alertu sterowanie szlo w pasy z pustymi kontami i malowalo
        # "brak danych z serwera" przez kilkanascie sekund, czyli dokladnie ten ekran,
        # ktory `holding()` ma tlumic.
        if self.first_data_at is None:
            host = self.cfg.stream_url.split("//")[-1].split("/")[0]
            reason = ("brak polaczenia" if self.link_state == "down"
                      else "czekam na pierwsze dane")
            return render.ScreenState(message=[
                "Monitor limitow Claude", host, reason,
            ])

        flagged = self.alert_slots()
        bands = []
        for index, slot in enumerate(self.cfg.accounts):
            account = self.accounts.get(slot.uuid)
            note = "nieznane konto" if slot.uuid in self.unknown_uuids else None
            bands.append(render.band_state(account, name=slot.name, now_ms=now_ms,
                                           show_clock=(index == 0), note=note,
                                           alert=flagged.get(index)))
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
        i ktora unieważnia obraz z poprzedniego biegu. Zablokowana sesja jest
        drugim — alert jest wiadomoscia, na ktora czekasz, wiec nie ma powodu,
        zeby czekal na uplyw progu.

        `ever_painted` jest LATCHEM: raz namalowany ekran nie moze wrocic do stanu
        "nie dotykamy". Bez niego karta alertu zgaszona przed uplywem progu oddawala
        sterowanie z powrotem tutaj i panel zastygal z polowa poprzedniej sceny.
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
        log().info("start: %d konto(a), panele: %s", len(uuids),
                   ", ".join(link.tag for link in self.panels) or "(brak)")
        deadline = self.monotonic()
        try:
            while not self.stop.is_set():
                self.tick()
                deadline += self.cfg.tick_sec
                delay = deadline - self.monotonic()
                if delay < 0:
                    # Po dlugim blicie albo resecie nie nadrabiamy zaleglosci —
                    # lepiej zgubic tick niz gonic wlasny ogon.
                    deadline = self.monotonic()
                    delay = 0
                self.stop.wait(delay)
        finally:
            self.stop.set()
            for link in self.panels:
                link.close()        # bez czyszczenia ekranu, celowo

    def run_once(self, wait_sec=30.0):
        """Polacz, poczekaj na pierwsza ramke, narysuj raz, wyjdz."""
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
