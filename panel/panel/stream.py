"""Klient SSE /api/stream — osobny watek, tylko stdlib.

Dlaczego SSE, a nie polling: `/api/status` jest wylacznie za SSO, a `/api/stream`
to JEDYNY endpoint przyjmujacy bearer (backend/app/auth.py:54-89). Ramka `account`
niesie pelna karte `AccountStatus`, wiec zgubiona ramka jest nieszkodliwa —
nastepna i tak przyniesie caly stan.

Watek robi WYLACZNIE siec. Nie dotyka PIL ani USB: zawieszone gniazdo nie moze
zatrzymac panelu, a 376 ms blitu nie moze zatrzymac odbioru.
"""
import http.client
import json
import queue
import ssl
import threading
import time
import urllib.parse

from .log import get as log

# Stala kontraktu NIE jest tutaj, tylko w model.py — jedna sztuka na klienta.
# Stala jest to, co porownuje app.on_event(); druga kopia w tym pliku wygladalaby
# na wlascicielke (to on trzyma drut), a nie robilaby nic. Bump w zlej kopii
# zostawialby panel na karcie "Niezgodny kontrakt" po rzekomej naprawie.

_ssl_ctx = None


def ssl_context(cfg):
    """Magazyn CA Windows uzywany przez Pythona odrzuca niektore lancuchy Let's Encrypt z bledem 'certificate has expired', mimo ze KAZDE ogniwo jest wazne.
    curl przechodzi, Python nie — czyli wina magazynu, nie serwera. certifi
    dziala, wiec uzywamy go, gdy jest dostepny.

    Swiadomie NIE wylaczamy weryfikacji. `ca_bundle` w panel.json pozwala wskazac
    wlasny plik, gdyby certifi nie bylo zainstalowane. Skopiowane z sondy
    (client/usage-probe.py:467) — ta sama pulapka, to samo lekarstwo.
    """
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    cafile = cfg.ca_bundle
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except Exception:
            cafile = None
    _ssl_ctx = (ssl.create_default_context(cafile=cafile) if cafile
                else ssl.create_default_context())
    return _ssl_ctx


def parse_events(chunk_iter):
    """Strumien bajtow -> (event, data). Ramka konczy sie pusta linia.

    `data:` moze wystapic wielokrotnie w jednej ramce — events.py:47-56 rozbija
    tak wieloliniowy JSON, wiec sklejanie jest wymagane, nie kosmetyczne.
    """
    buf = b""
    event = None
    data = []
    for chunk in chunk_iter:
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.rstrip(b"\r").decode("utf-8", "replace")
            if line == "":
                if event is not None:
                    payload = None
                    if data:
                        try:
                            payload = json.loads("\n".join(data))
                        except ValueError:
                            payload = None
                    yield event, payload
                event, data = None, []
                continue
            # Pole SSE to `nazwa:wartosc` z JEDNA opcjonalna spacja po dwukropku.
            # Dopasowanie po "data: " ze spacja wbita w prefiks gubilo milczaco
            # ramki zapisane jako "data:{...}" — a stojace obok `retry:` bylo juz
            # dopasowywane bez spacji, wiec plik przeczyl sam sobie. Nasz backend
            # spacje wysyla zawsze (services/events.py:55), ale replay.py dostaje
            # nagranie, ktore nie musi z niego pochodzic.
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
            # `retry:` i komentarz (pusta nazwa pola, linia zaczeta od ":") pomijamy.


class StreamClient(threading.Thread):
    """Utrzymuje polaczenie i wrzuca zdarzenia na kolejke.

    Zdarzenia na kolejce: ("hello"|"account"|"ping"|"lag"|"bye", payload)
    oraz wlasne ("up", None) / ("down", powod) / ("contract", wersja).
    """

    def __init__(self, cfg, uuids, out_queue, stop_event=None):
        super().__init__(name="sse", daemon=True)
        self.cfg = cfg
        self.uuids = list(uuids)
        self.q = out_queue
        self.stop = stop_event or threading.Event()
        self.recorder = None

    # -- polaczenie --------------------------------------------------------

    def _request(self):
        url = urllib.parse.urlsplit(self.cfg.stream_url)
        query = urllib.parse.urlencode([("account", u) for u in self.uuids])
        path = (url.path or "/") + "?" + query
        # Timeout gniazda ponad DWA odstepy ping (serwer bije co 15 s). Bez tego
        # polotwarte TCP przez Apache wisialoby w nieskonczonosc, a panel
        # pokazywalby stare liczby z pelnym przekonaniem.
        timeout = 35.0
        if url.scheme == "https":
            conn = http.client.HTTPSConnection(url.netloc, timeout=timeout,
                                               context=ssl_context(self.cfg))
        else:
            conn = http.client.HTTPConnection(url.netloc, timeout=timeout)
        conn.request("GET", path, headers={
            "Authorization": "Bearer %s" % (self.cfg.stream_token or ""),
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        return conn, conn.getresponse()

    def _chunks(self, resp):
        # read1(), NIE read(). `read(n)` czeka az uzbiera sie CALE n bajtow albo
        # skonczy sie strumien — a SSE to male ramki wpadajace nieregularnie, wiec
        # kolejne karty i pingi zostawaly w buforze i panel stal z pierwsza ramka
        # wygladajac na zywego. read1() oddaje to, co juz przyszlo.
        reader = getattr(resp, "read1", None) or resp.read
        while not self.stop.is_set():
            chunk = reader(4096)
            if not chunk:
                return
            if self.recorder:
                try:
                    self.recorder.write(chunk)
                    self.recorder.flush()
                except OSError:
                    self.recorder = None
            yield chunk

    def _once(self):
        """Jedno polaczenie. Zwraca (czas_zycia_s, powod_zakonczenia)."""
        started = time.monotonic()
        conn = resp = None
        try:
            conn, resp = self._request()
            if resp.status == 401:
                resp.read()
                return time.monotonic() - started, "invalid-token"
            if resp.status == 503:
                resp.read()
                return time.monotonic() - started, "too-many-streams"
            if resp.status != 200:
                body = resp.read(400).decode("utf-8", "replace")
                return time.monotonic() - started, "http-%d %s" % (resp.status, body)

            self.q.put(("up", None))
            for event, payload in parse_events(self._chunks(resp)):
                if self.stop.is_set():
                    break
                self.q.put((event, payload))
                if event == "bye":
                    # 900 s to normalne zycie polaczenia, nie awaria.
                    return time.monotonic() - started, "bye"
            return time.monotonic() - started, "eof"
        except (OSError, http.client.HTTPException) as e:
            return time.monotonic() - started, "%s: %s" % (type(e).__name__, e)
        finally:
            for closable in (resp, conn):
                try:
                    if closable is not None:
                        closable.close()
                except Exception:
                    pass

    # -- petla -------------------------------------------------------------

    def run(self):
        if self.cfg.record_sse:
            try:
                self.recorder = open(self.cfg.log_file + ".sse", "ab")
            except OSError:
                self.recorder = None

        backoff = 3.0        # baza z pola `retry: 3000`, ktore serwer sam podaje
        while not self.stop.is_set():
            lived, reason = self._once()
            if self.stop.is_set():
                break

            if reason == "bye":
                # Wznawiamy natychmiast i BEZ migania "brak lacza" — to jest
                # zaplanowane zamkniecie po STREAM_MAX_LIFETIME_SEC.
                log().info("strumien: bye po %.0f s, wznawiam", lived)
                backoff = 3.0
                continue

            self.q.put(("down", reason))
            if reason in ("invalid-token", "too-many-streams"):
                # Zly token powtarzany co 3 s przez tydzien to sam szum w logu.
                log().error("strumien: %s — czekam 60 s", reason)
                delay = 60.0
            else:
                # Polaczenie krotsze niz 5 s liczy sie jako awaria, nawet jesli
                # cos przyslalo: inaczej petla bledu wyczerpalaby STREAM_MAX_CLIENTS.
                if lived >= 5.0:
                    backoff = 3.0
                log().warning("strumien: %s (zylo %.0f s), ponawiam za %.0f s",
                              reason, lived, backoff)
                delay = backoff
                backoff = min(backoff * 2, 30.0)
            self.stop.wait(delay)

        if self.recorder:
            try:
                self.recorder.close()
            except OSError:
                pass


def drain(q, handler):
    """Zdejmuje wszystko, co czeka. Model podmieniamy RAZ na tick, wiec panel
    nigdy nie pokazuje polowy jednej ramki obok polowy drugiej."""
    count = 0
    while True:
        try:
            event, payload = q.get_nowait()
        except queue.Empty:
            return count
        handler(event, payload)
        count += 1
