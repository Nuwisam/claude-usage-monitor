"""SSE client for /api/stream — a separate thread, stdlib only.

Why SSE and not polling: `/api/status` is behind SSO exclusively, and `/api/stream`
is the ONLY endpoint that accepts a bearer (backend/app/auth.py:54-89). The
`account` frame carries a full `AccountStatus` card, so a lost frame is harmless —
the next one brings the whole state anyway.

The thread does network work EXCLUSIVELY. It touches neither PIL nor USB: a hung
socket must not be able to stop the panel, and 376 ms of blit must not be able to
stop reception.
"""
import http.client
import json
import queue
import ssl
import threading
import time
import urllib.parse

from .log import get as log

# The contract constant is NOT here but in model.py — one per client. The constant
# is what app.on_event() compares; a second copy in this file would look like the
# owner (this is the module that holds the wire) while doing nothing at all. A bump
# in the wrong copy would leave the panel on the "Contract mismatch" card after a
# supposed fix.

_ssl_ctx = None


def ssl_context(cfg):
    """The Windows CA store Python uses rejects some Let's Encrypt chains with
    'certificate has expired', even though EVERY link in them is valid.
    curl gets through, Python does not — so the fault is the store's, not the
    server's. certifi works, so it is used whenever it is available.

    Verification is deliberately NOT disabled. `ca_bundle` in panel.json allows
    pointing at a file of your own, in case certifi is not installed. Copied from
    the probe (client/usage-probe.py:467) — the same pitfall, the same remedy.
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
    """A byte stream -> (event, data). A frame ends with an empty line.

    `data:` can appear several times in one frame — events.py:47-56 breaks
    multi-line JSON up that way, so joining is required, not cosmetic.
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
            # An SSE field is `name:value` with ONE optional space after the colon.
            # Matching on "data: " with the space baked into the prefix silently
            # lost frames written as "data:{...}" — while the `retry:` beside it
            # was already matched without a space, so the file contradicted itself.
            # This backend always sends the space (services/events.py:55), but
            # replay.py is handed a recording that need not come from it.
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
            # `retry:` and a comment (empty field name, a line starting with ":")
            # are skipped.


class StreamClient(threading.Thread):
    """Keeps the connection up and puts events on the queue.

    Events on the queue: ("hello"|"account"|"ping"|"lag"|"bye", payload)
    plus its own ("up", None) / ("down", reason) / ("contract", version).
    """

    def __init__(self, cfg, uuids, out_queue, stop_event=None):
        super().__init__(name="sse", daemon=True)
        self.cfg = cfg
        self.uuids = list(uuids)
        self.q = out_queue
        self.stop = stop_event or threading.Event()
        self.recorder = None

    # -- connection --------------------------------------------------------

    def _request(self):
        url = urllib.parse.urlsplit(self.cfg.stream_url)
        query = urllib.parse.urlencode([("account", u) for u in self.uuids])
        path = (url.path or "/") + "?" + query
        # A socket timeout over TWO ping intervals (the server beats every 15 s).
        # Without it a half-open TCP through Apache would hang forever, and the
        # panel would show stale numbers with full conviction.
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
        # read1(), NOT read(). `read(n)` waits until the WHOLE of n bytes has
        # gathered or the stream ends — and SSE is small frames arriving
        # irregularly, so later cards and pings stayed in the buffer and the panel
        # was stuck on the first frame, looking alive. read1() hands back what has
        # already arrived.
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
        """One connection. Returns (lifetime_s, end_reason)."""
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
                    # 900 s is a normal connection lifetime, not a failure.
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

    # -- the loop ----------------------------------------------------------

    def run(self):
        if self.cfg.record_sse:
            try:
                self.recorder = open(self.cfg.log_file + ".sse", "ab")
            except OSError:
                self.recorder = None

        backoff = 3.0        # the base from the server's own `retry: 3000` field
        while not self.stop.is_set():
            lived, reason = self._once()
            if self.stop.is_set():
                break

            if reason == "bye":
                # Resume at once and WITHOUT flashing the "no link" mark — this is
                # a planned close after STREAM_MAX_LIFETIME_SEC.
                log().info("stream: bye after %.0f s, resuming", lived)
                backoff = 3.0
                continue

            self.q.put(("down", reason))
            if reason in ("invalid-token", "too-many-streams"):
                # A bad token retried every 3 s for a week is pure noise in the log.
                log().error("stream: %s — waiting 60 s", reason)
                delay = 60.0
            else:
                # A connection shorter than 5 s counts as a failure even if it sent
                # something: otherwise an error loop would exhaust STREAM_MAX_CLIENTS.
                if lived >= 5.0:
                    backoff = 3.0
                log().warning("stream: %s (lived %.0f s), retrying in %.0f s",
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
    """Drains everything that is waiting. The model is swapped ONCE per tick,
    so the panel never shows half of one frame next to half of another."""
    count = 0
    while True:
        try:
            event, payload = q.get_nowait()
        except queue.Empty:
            return count
        handler(event, payload)
        count += 1
