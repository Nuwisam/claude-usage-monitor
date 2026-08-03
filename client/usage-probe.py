#!/usr/bin/env python3
"""Sonda limitow Claude, uruchamiana z hooka Claude Code (PostToolUse async + Stop).

NIE wola api.anthropic.com. Zamiast tego zleca pomiar samemu Claude Code
(`claude -p "/usage"`) i czyta wynik z dwoch miejsc, ktore ten zostawia na dysku.

ZASADY BEZPIECZENSTWA — nie lamac przy rozwijaniu:
  1. Sonda NIE wykonuje zadnego zapytania do api.anthropic.com. Jedynym podmiotem
     wolajacym /api/oauth/usage jest Claude Code, wlasnym kanalem, wlasnym tokenem,
     ktory sam sobie odswieza. To usuwa naraz: uzycie tokena OAuth przez obce
     narzedzie (zakazane w ToS), impersonacje User-Agenta i ryzyko bana.
  2. .credentials.json czytamy TYLKO do odczytu i TYLKO po metadane planu.
     accessToken nie jest z niego pobierany ani uzywany do niczego.
  3. NIGDY nie wolamy endpointu tokenowego (grant_type=refresh_token).
  4. Throttle obowiazkowy — PostToolUse odpala sie przy kazdym narzedziu.
  5. Zero ciezkich importow w sciezce goracej. Tylko stdlib.
  6. Nigdy nie rzuca wyjatkiem i nigdy nie blokuje sesji.
  7. Sonda NIGDY nie czeka na proces potomny. `claude -p "/usage"` trwa ~3,4 s;
     wynik konsumuje DOPIERO nastepny przebieg sondy.

DWA ZRODLA, bo swiezosc i kompletnosc leza w roznych miejscach (zmierzone):
  * stdout `claude -p "/usage"` — SWIEZE przy kazdym wywolaniu, ale tylko procenty
    glownych okien, jako tekst. Wartosci sa calkowite — i to nie jest strata, bo
    API samo zwraca liczby calkowite (zweryfikowane na surowym payloadzie).
  * ~/.claude.json -> cachedUsageUtilization.utilization — PELNE surowe cialo
    odpowiedzi (spend, extra_usage, limits[], wszystkie buckety), ale Claude Code
    przepisuje je najwyzej raz na 5 minut (twardy throttle zapisu po jego stronie).

Scalamy: struktura z cache + swieze procenty ze stdout nadpisane na wierzchu.
Wynik ma DOKLADNIE ten sam ksztalt co dawna odpowiedz HTTP, wiec parser backendu
nie wymaga zmian.

Konfiguracja: %LOCALAPPDATA%\\claude-usage-monitor\\config.json (Windows)
              ~/.local/state/claude-usage-monitor/config.json (Linux)
    {"ingest_url": "https://usage.example.org/claude-usage/api/ingest",
     "ingest_token": "<token TEJ maszyny>",
     "edge_key": "<wspolny sekret brzegowy>",
     "throttle_sec": 60,
     "claude_bin": "<opcjonalnie pelna sciezka do claude>"}
Celowo plik lokalny, a nie repo — token maszyny nie ma prawa trafic do gita.
"""
import sys, os, json, time, re, socket, hashlib, http.client, urllib.parse

SCRIPT_VERSION = 5

# Znacznik dziedziczony przez proces potomny. `claude -p "/usage"` to normalna sesja
# Claude Code — odpali hook Stop, ktory odpali sonde, ktora odpalilaby kolejnego
# `claude`... Sam throttle tego NIE zatrzyma, bo kazdy potomek ma wlasny zegar.
CHILD_ENV = "CUM_PROBE_CHILD"

_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
OUTDIR = os.path.join(_base, "claude-usage-monitor")
CONFIG = os.path.join(OUTDIR, "config.json")
LOG = os.path.join(OUTDIR, "usage-samples.jsonl")
SPOOL = os.path.join(OUTDIR, "spool.jsonl")
THROTTLE_FILE = os.path.join(OUTDIR, "last-probe.txt")
CLI_OUT = os.path.join(OUTDIR, "usage-cli.json")

MAX_SPOOL_LINES = 5000
MAX_BACKLOG_PER_REQUEST = 200
CACHE_MAX_AGE_S = 3600          # tyle samo, ile TTL odczytu po stronie Claude Code
CLI_MAX_AGE_S = 900             # starszy zrzut stdout ignorujemy — lepiej same dane z cache
MAX_SANE_PCT = 101              # patrz strazniki w parse_usage_text / sanitize


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:
        return None


def log_local(rec):
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def throttled(seconds):
    """Znacznik zapisujemy PRZED wywolaniem, zeby rownolegle hooki nie zrobily stampede."""
    try:
        if time.time() - os.path.getmtime(THROTTLE_FILE) < seconds:
            return True
    except Exception:
        pass
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(THROTTLE_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass
    return False


# --------------------------------------------------------------- tozsamosc + cache
def _find(name, in_claude_dir=False):
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    cands = []
    if cfg:
        cands.append(os.path.join(cfg, name))
    home = os.path.expanduser("~")
    cands.append(os.path.join(home, ".claude", name) if in_claude_dir
                 else os.path.join(home, name))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _extract_block(text, key):
    """Wycina zbalansowany blok {...} po kluczu. Odporne na duplikaty kluczy roznjace sie
    wielkoscia liter (z:/... i Z:/...), na ktorych parsery calego pliku padaja —
    ~/.claude.json REALNIE takie ma, json.load() na calosci sie na nim wywraca."""
    i = text.find('"%s"' % key)
    if i < 0:
        return None
    start = text.find("{", i)
    if start < 0:
        return None
    depth, j, n, in_str, esc = 0, start, len(text), False, False
    while j < n:
        c = text[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:j + 1])
        j += 1
    return None


def _extract_scalar(text, key):
    """Wycina wartosc SKALARNA po kluczu — string, liczbe, bool albo null.

    Rodzenstwo `_extract_block`, ktore umie tylko `{...}`. `cachedExtraUsageDisabledReason`
    lezy na najwyzszym poziomie jako goly string albo null, wiec tamta funkcja go nie widzi.

    `raw_decode` od pozycji za dwukropkiem zamiast szukania konca recznie: sam json wie,
    gdzie konczy sie wartosc, i radzi sobie z apostrofami oraz sekwencjami ucieczki w srodku.
    """
    i = text.find('"%s"' % key)
    if i < 0:
        return None
    j = text.find(":", i + len(key) + 2)
    if j < 0:
        return None
    j += 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1          # raw_decode NIE toleruje bialych znakow przed wartoscia
    return json.JSONDecoder().raw_decode(text, j)[0]


_ACCT_FIELDS = ("accountUuid", "emailAddress", "organizationUuid", "organizationName",
                "organizationType", "organizationRateLimitTier", "userRateLimitTier",
                "seatTier", "hasExtraUsageEnabled", "displayName")


def read_claude_json():
    """Jeden odczyt pliku, trzy wyciagi: tozsamosc konta, cache pomiaru i powod
    wylaczenia kredytow.

    Dawna wersja cache'owala tozsamosc po mtime. Teraz i tak musimy czytac ten plik za
    kazdym razem (cachedUsageUtilization sie zmienia), wiec osobny cache byl juz tylko
    dodatkowym I/O. Przelaczenie konta przez /login przepisuje ten sam plik, wiec
    wykrywanie zmiany konta nadal dziala — po prostu bez posrednika."""
    path = _find(".claude.json")
    if not path:
        return None, None, None, None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None, None, None, None

    acct = None
    raw = _safe(_extract_block, text, "oauthAccount")
    if raw:
        acct = {k: raw.get(k) for k in _ACCT_FIELDS}

    cached = _safe(_extract_block, text, "cachedUsageUtilization")
    # Powod wylaczenia kredytow z cache KLIENTA. Rozroznia sam trzy stany, ktorych dane
    # w pasmie nie rozrozniaja: null / `org_spend_cap_reached` (wyczerpana WLASNA pula,
    # gdzie `spend.disabled_reason` jest null) / `org_level_disabled_until` (sufit
    # organizacji). Zbieramy go DO WGLADU — werdykt zostaje na danych w pasmie.
    reason = _safe(_extract_scalar, text, "cachedExtraUsageDisabledReason")
    return acct, cached, os.path.dirname(path), reason


# --------------------------------------------------------------- metadane planu
def load_token_meta():
    """TYLKO metadane planu. accessToken celowo nie jest zwracany — od wersji 3 sonda
    nie uwierzytelnia niczego. Brak pliku (macOS trzyma credentiale w Keychain) nie
    jest juz bledem krytycznym: pomiar dziala dalej, znikaja tylko tagi planu."""
    path = _find(".credentials.json", in_claude_dir=True)
    if not path:
        return {"reason": "brak-credentials"}
    try:
        with open(path, "r", encoding="utf-8") as f:      # TYLKO odczyt
            oa = (json.load(f).get("claudeAiOauth") or {})
    except Exception as e:
        return {"reason": "odczyt-%s" % type(e).__name__}
    exp = oa.get("expiresAt")
    return {"subscription_type": oa.get("subscriptionType"),
            "rate_limit_tier": oa.get("rateLimitTier"),
            "expires_in_s": int(exp / 1000.0 - time.time()) if exp else None}


# --------------------------------------------------------------- pomiar przez CLI
def find_claude(cfg):
    b = cfg.get("claude_bin")
    if b and os.path.isfile(b):
        return b
    import shutil                       # import lokalny — sciezka zimna, raz na 60 s
    return shutil.which("claude")


def spawn_refresh(cfg):
    """Odpala `claude -p "/usage"` i NATYCHMIAST wraca. Wynik przeczyta nastepny przebieg.

    /usage jest zarejestrowane dwukrotnie; wariant z supportsNonInteractive jest aktywny
    wlasnie w trybie -p. Zwraca {type:"text"}, co ustawia shouldQuery=false — czyli
    zaden turn modelu sie nie odbywa. Zmierzone: num_turns=0, duration_api_ms=0,
    total_cost_usd=0. Pomiar limitu nie zuzywa limitu.

    --no-session-persistence wylacza zapis transkryptu. Bez niej kazde wywolanie zostawia
    ~4 KB plik w ~/.claude/projects/<cwd> — zmierzone 102 pliki w 2h35m pracy. Flaga dziala
    wylacznie z -p. Zweryfikowane A/B: z flaga 0 plikow, bez niej 1 plik, przy czym cache
    cachedUsageUtilization nadal sie odswieza (a od niego zalezy merge).

    --model haiku to pas bezpieczenstwa, bezczynny na sciezce szczesliwej: /usage zwraca
    shouldQuery=false, wiec zaden model nie rusza (zmierzone: num_turns=0, koszt 0, czas bez
    zmian). Znaczenie ma tylko wtedy, gdy argument nie trafi w komende lokalna — wtedy leci
    platna tura, ktora bez tej flagi poszlaby na modelu z settings.json.
    Sonda i tak odrzuci taki zrzut po num_turns>0, ale koszt jest juz poniesiony; flaga go
    obniza o rzad wielkosci. Alias, nie ID z data — ID bywaja wycofywane."""
    exe = find_claude(cfg)
    if not exe:
        return "brak-claude-w-path"
    import subprocess
    env = dict(os.environ)
    env[CHILD_ENV] = "1"                # zapora przed rekurencja hookow
    kw = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW — bez konsoli i bez wiazania z sesja rodzica
        kw["creationflags"] = 0x00000008 | 0x08000000
    else:
        kw["start_new_session"] = True
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        out = open(CLI_OUT, "wb")
    except Exception:
        return "brak-pliku-wyjscia"
    try:
        subprocess.Popen(
            [exe, "-p", "--no-session-persistence", "--model", "haiku",
             "/usage", "--output-format", "json"],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
            cwd=OUTDIR,                 # neutralny katalog: bez CLAUDE.md i hookow projektu
            env=env, close_fds=True, **kw)
    except Exception as e:
        return "spawn-%s" % type(e).__name__
    finally:
        _safe(out.close)
    return None


# [^:\n] a nie [^:] — klasa negatywna lapie takze znak nowej linii, wiec bez wykluczenia
# \n tytul przezera naglowek i puste linie az do dwukropka w NASTEPNEJ linii. Efekt jest
# podstepny: krotkie wejscia parsuja sie dobrze, realne wyjscie gubi pierwszy odczyt.
_PCT_RE = re.compile(r"^(?P<title>\S[^:\n]*):\s+(?P<pct>\d+)%\s+used", re.M)


def parse_usage_text(text):
    """Tolerancyjny parser wyjscia /usage. Nierozpoznana linia jest ignorowana, nie jest
    bledem. Tytuly sa lokalizowalne i moga sie zmienic miedzy wersjami — dlatego surowy
    tekst i tak trafia do payloadu, obok sparsowanych wartosci.

        Current session: 47% used - resets Jul 27, 12:30pm (UTC)
        Current week (all models): 48% used - resets Aug 1, 6pm (UTC)
        Current week (Fable): 0% used

    Sekcja atrybucji ("100% of your usage came from...") nie ma dwukropka przed
    procentem, wiec nie lapie sie w regex."""
    out = {"session": None, "weekly_all": None, "scoped": {}}
    for m in _PCT_RE.finditer(text or ""):
        title, pct = m.group("title").strip(), int(m.group("pct"))
        if pct > MAX_SANE_PCT:
            # Claude Code potrafi wyciec epoch z resets_at w pole procentu (blad #52326).
            # Odrzucamy zamiast obcinac do 100: obciecie zamienia ewidentna awarie w
            # wiarygodnie wygladajace "limit na maksie", czyli w falszywy alarm.
            continue
        low = title.lower()
        if low == "current session":
            out["session"] = pct
        elif low == "current week (all models)":
            out["weekly_all"] = pct
        elif low.startswith("current week (") and title.endswith(")"):
            out["scoped"][title[len("Current week ("):-1]] = pct
    return out


def read_fresh():
    """Czyta zrzut stdout zostawiony przez POPRZEDNI przebieg.

    Plik pisze proces potomny, wiec mozemy trafic na zapis w toku — dlatego walidacja
    jest przez json.loads(): urwany plik po prostu nie sparsuje i cykl leci na samym
    cache. Zadnego blokowania, zadnego pliku-znacznika."""
    try:
        age = time.time() - os.path.getmtime(CLI_OUT)
        with open(CLI_OUT, "r", encoding="utf-8", errors="replace") as f:
            d = json.loads(f.read())
    except Exception:
        return None, None, None
    if d.get("num_turns"):
        # num_turns>0 znaczy, ze "/usage" nie trafilo w komende lokalna i poszlo do modelu.
        # Taki wynik jest bezwartosciowy i kosztowny — nie uzywamy go i sygnalizujemy.
        return None, None, "nie-komenda-lokalna"
    if age > CLI_MAX_AGE_S:
        return None, None, "stary-zrzut"
    parsed = parse_usage_text(d.get("result") or "")
    if parsed["session"] is None and parsed["weekly_all"] is None:
        return None, None, "brak-procentow"
    return parsed, os.path.getmtime(CLI_OUT), None


def dump_outdated(fresh_at, cache_at):
    """Czy zrzut jest STARSZY od cache — wtedy nie ma czego nakladac.

    Cale scalanie zaklada, ze zrzut jest swiezszy. Ale zrzutowi wolno miec do
    CLI_MAX_AGE_S (900 s), a cache w tym czasie odswieza zwykla praca w Claude Code, wiec
    kolejnosc potrafi sie odwrocic (zmierzone: 2 przypadki na 1646 pomiarow, do -105 s).

    Grozny jest przypadek z RESETEM OKNA miedzy zrzutem a cache. Procent szedlby wtedy ze
    zrzutu, czyli sprzed resetu (np. 95%), a `resets_at` mamy WYLACZNIE z cache, czyli juz
    z nowego okna. `sanitize` tego nie zlapie — granica jest wazna, wiec nic nie wyglada na
    sprzeczne — i publikujemy 95% przeciwko oknu, w ktorym realnie jest ~1%. Pewnie
    wygladajaca nieprawda, dokladnie w chwili, gdy okno jest wolne. W historii zostaje
    dodatkowo spadek wewnatrz jednego okna, ktory `window_start_index` czyta jako reset.

    W normalnym kierunku ten sam reset konczy sie dobrze: zrzut daje ~1%, granica z cache
    jest przeterminowana, `sanitize` ja zeruje i zglasza `reset-w-toku`.

    Koszt odrzucenia jest ZEROWY: zostaje wartosc z cache, ktora jest nowsza — i przy okazji
    dokladniejsza, bo stdout obcina procenty do liczb calkowitych."""
    return bool(fresh_at) and bool(cache_at) and fresh_at < cache_at


def _limit_model(lim):
    """display_name modelu z wpisu limits[] — ze STRAZNIKAMI TYPU na kazdym poziomie.

    Skrot `(lim.get("scope") or {}).get("model")` dziala tylko dopoki `scope` jest slownikiem
    albo brakiem. Ta funkcja biegnie dla KAZDEGO limitu (bo klucz pokrycia zawiera model
    niezaleznie od `kind`), wiec `scope: "global"` dalby AttributeError w `merge()` — czyli
    przed `log_local`, przed spoolem i przed POST-em. Przebieg znikalby bez sladu, przy kazdym
    kolejnym cyklu, dopoki cache ma ten ksztalt. Backend ma tu ten sam straznik
    (backend/app/parsing.py:386)."""
    scope = lim.get("scope")
    if not isinstance(scope, dict):
        return None
    model = scope.get("model")
    if not isinstance(model, dict):
        return None
    name = model.get("display_name")
    return name if isinstance(name, str) else None


def _limit_key(lim):
    """Identyfikator pokrycia dla wpisu limits[]. Musi byc IDENTYCZNY z tym, co liczy
    backend (`parsing.probe_key`) — bez slugowania, surowy `display_name`. Rozjazd jest
    CICHY: zbior po prostu nigdy sie nie dopasuje i zachowanie cofa sie do stanu sprzed
    tej zmiany.

    `surface` do klucza NIE wchodzi, bo `merge` dopasowuje po `kind`+`model` i powierzchni
    nie rozroznia — dwa limity roznjace sie tylko nia naprawde sa pokryte oba."""
    return "limit:%s:%s" % (lim.get("kind") or "?", _limit_model(lim) or "-")


def merge(cached_usage, fresh):
    """Struktura z cache + swieze procenty na wierzchu.

    Zwraca payload w ksztalcie identycznym z dawna odpowiedzia HTTP oraz liste serii
    POKRYTYCH przez zrzut. Pokrycie to nie to samo co zmiana: swiezy odczyt rowny wartosci
    z cache JEST potwierdzeniem i musi sie liczyc, bo od tej listy zalezy datowanie po
    stronie backendu (`measurement.fresh_covered`) i decyzja `reset-w-toku` w `sanitize`."""
    if not fresh:
        return cached_usage, []
    usage = json.loads(json.dumps(cached_usage))      # kopia — nie mutujemy zrodla
    covered = []

    def put(bucket, val):
        b = usage.get(bucket)
        if not isinstance(b, dict) or val is None:
            return
        b["utilization"] = val
        covered.append("bucket:%s" % bucket)

    put("five_hour", fresh["session"])
    put("seven_day", fresh["weekly_all"])

    for lim in (usage.get("limits") or []):
        if not isinstance(lim, dict):
            continue
        kind, val = lim.get("kind"), None
        if kind == "session":
            val = fresh["session"]
        elif kind == "weekly_all":
            val = fresh["weekly_all"]
        elif kind == "weekly_scoped":
            val = fresh["scoped"].get(_limit_model(lim))
        if val is None:
            continue
        lim["percent"] = val
        covered.append(_limit_key(lim))
    return usage, covered


def _epoch(iso):
    """resets_at przychodzi jako ISO-8601 z offsetem: 2026-07-27T10:29:59.761469+00:00."""
    if not iso:
        return None
    try:
        from datetime import datetime          # modul C, import znikomy
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


def sanitize(usage, covered, now):
    """Odrzuca dane z okna, ktore juz sie zresetowalo, oraz absurdalne procenty.

    Sedno problemu: `resets_at` mamy WYLACZNIE z cache, ktory ma do 5 minut (a w trybie
    awaryjnym do godziny). Jesli okno zresetowalo sie w miedzyczasie, para
    (procent, resets_at) jest wewnetrznie sprzeczna. Dwa przypadki, dwie reakcje:

    Pytanie brzmi "czy seria dostala SWIEZY procent", wiec czytamy `covered`, nie liste
    zmian. Wczesniej szla tu lista zmienionych wartosci, przez co seria o swiezym procencie
    ROWNYM cache'owemu i wygaslym oknie byla wyrzucana z pomiaru zamiast dostac
    `reset-w-toku` — czyli tracilismy jedyny prawdziwy odczyt.

      * seria dostala swiezy procent  -> procent jest prawdziwy, nieaktualny jest tylko
        czas resetu. Zerujemy resets_at; nastepny zapis cache (<=5 min) poda nowy.
      * seria NIE dostala swiezego    -> procent tez pochodzi z wygaslego okna. Publikacja
        dawnych 95% jako biezacych bylaby grubym bledem (realnie jest ~0%), wiec
        wyrzucamy cala serie z tego cyklu.

    Zwraca liste zdarzen do diagnostyki — cisza przy odrzucaniu danych jest gorsza niz
    brak danych, bo wyglada jak poprawny pomiar."""
    events = []
    covered_set = set(covered)

    for key, bucket in list(usage.items()):
        if not isinstance(bucket, dict) or "utilization" not in bucket:
            continue
        util = bucket.get("utilization")
        if isinstance(util, (int, float)) and util > MAX_SANE_PCT:
            usage[key] = None
            events.append("%s:absurd(%s)" % (key, util))
            continue
        exp = _epoch(bucket.get("resets_at"))
        if exp and exp <= now:
            if ("bucket:%s" % key) in covered_set:
                bucket["resets_at"] = None
                events.append("%s:reset-w-toku" % key)
            else:
                usage[key] = None
                events.append("%s:okno-wygaslo" % key)

    kept = []
    for lim in (usage.get("limits") or []):
        if not isinstance(lim, dict):
            continue
        pct = lim.get("percent")
        kind = lim.get("kind") or "?"
        if isinstance(pct, (int, float)) and pct > MAX_SANE_PCT:
            events.append("limit:%s:absurd(%s)" % (kind, pct))
            continue
        exp = _epoch(lim.get("resets_at"))
        if exp and exp <= now:
            if _limit_key(lim) in covered_set:
                lim["resets_at"] = None
                events.append("limit:%s:reset-w-toku" % kind)
            else:
                events.append("limit:%s:okno-wygaslo" % kind)
                continue
        kept.append(lim)
    if "limits" in usage:
        usage["limits"] = kept
    return usage, events


# --------------------------------------------------------------- spool
def read_spool(limit):
    try:
        with open(SPOOL, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except Exception:
        return [], 0
    out = []
    for l in lines[:limit]:
        rec = _safe(json.loads, l)
        if rec is not None:
            out.append(rec)
    return out, len(lines)


def append_spool(rec):
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(SPOOL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(SPOOL, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        if len(lines) > MAX_SPOOL_LINES:
            with open(SPOOL, "w", encoding="utf-8") as f:   # wyrzucamy najstarsze
                f.write("\n".join(lines[-MAX_SPOOL_LINES:]) + "\n")
    except Exception:
        pass


def trim_spool(accepted):
    """Obcinamy DOPIERO po potwierdzeniu, ile wpisow przyjeto — awaria w polowie nie gubi danych."""
    if accepted <= 0:
        return
    try:
        with open(SPOOL, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        with open(SPOOL, "w", encoding="utf-8") as f:
            rest = lines[accepted:]
            f.write(("\n".join(rest) + "\n") if rest else "")
    except Exception:
        pass


_ssl_ctx = None


def ssl_context(cfg):
    """Magazyn CA Windows uzywany przez Pythona odrzuca lancuch Let's Encrypt niektorych hostow
    z bledem 'certificate has expired', mimo ze KAZDE ogniwo jest wazne (zweryfikowane
    openssl-em: 2026/2028/2032/2032). curl przechodzi, Python nie — czyli wina magazynu,
    nie serwera. certifi dziala, wiec uzywamy go, gdy jest dostepny.

    Swiadomie NIE wylaczamy weryfikacji. `ca_bundle` w config.json pozwala wskazac wlasny
    plik, gdyby certifi nie bylo zainstalowane."""
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    import ssl
    cafile = cfg.get("ca_bundle")
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except Exception:
            cafile = None
    _ssl_ctx = ssl.create_default_context(cafile=cafile) if cafile \
        else ssl.create_default_context()
    return _ssl_ctx


def post(cfg, body):
    url = urllib.parse.urlsplit(cfg["ingest_url"])
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if url.scheme == "https":
        conn = http.client.HTTPSConnection(url.netloc, timeout=5.0,
                                           context=ssl_context(cfg))
    else:
        conn = http.client.HTTPConnection(url.netloc, timeout=5.0)
    try:
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer %s" % cfg.get("ingest_token", ""),
                   "Content-Length": str(len(data))}
        if cfg.get("edge_key"):
            headers["X-Ingest-Key"] = cfg["edge_key"]
        conn.request("POST", url.path or "/", body=data, headers=headers)
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        _safe(conn.close)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + "Z"


# --------------------------------------------------------------- main
def main():
    t0 = time.perf_counter()

    # Zapora przed rekurencja. MUSI byc przed czymkolwiek innym: proces potomny
    # `claude -p "/usage"` odpala hook Stop, ktory uruchamia te sonde ponownie.
    if os.environ.get(CHILD_ENV):
        return 0

    hook = _safe(json.loads, sys.stdin.read() or "{}") or {}
    cfg = load_config()

    if throttled(int(cfg.get("throttle_sec", 60))):
        return 0

    fresh, fresh_at, fresh_skip = read_fresh()
    acct, cached, cfg_dir, eu_reason = read_claude_json()

    # Zlecamy pomiar na NASTEPNY cykl. Zawsze, takze gdy teraz nie mamy czego wyslac —
    # to jest wlasnie mechanizm bootstrapu na swiezej maszynie.
    spawn_err = spawn_refresh(cfg)

    if not cached or not isinstance(cached.get("utilization"), dict):
        # Pierwszy przebieg na maszynie: Claude Code jeszcze nigdy nie zapisal cache.
        # Spawn wyzej to naprawi, pomiar pojawi sie w nastepnym cyklu.
        log_local({"t": round(time.time(), 3), "ok": False, "skip": "brak-cache",
                   "spawn": spawn_err, "event": hook.get("hook_event_name")})
        return 0

    cache_at = (cached.get("fetchedAtMs") or 0) / 1000.0
    cache_age = time.time() - cache_at
    if cache_age > CACHE_MAX_AGE_S:
        log_local({"t": round(time.time(), 3), "ok": False, "skip": "cache-przeterminowany",
                   "cache_age_s": round(cache_age), "spawn": spawn_err})
        return 0

    # Claude Code czysci cache przy zmianie konta, ale nie ufamy temu na slowo.
    if acct and cached.get("accountUuid") and cached["accountUuid"] != acct.get("accountUuid"):
        log_local({"t": round(time.time(), 3), "ok": False, "skip": "cache-innego-konta"})
        return 0

    if fresh and dump_outdated(fresh_at, cache_at):
        fresh, fresh_skip = None, "zrzut-starszy-od-cache"

    usage, covered = merge(cached["utilization"], fresh)
    usage, dropped = sanitize(usage, covered, time.time())

    # captured_at to moment POMIARU, nigdy now(). Uruchomienie sondy niczego nie
    # potwierdza — odczyt 4-minutowego cache znaczy tylko tyle, ze wartosc byla taka
    # 4 minuty temu. Podstawienie tu now() zawyzaloby swiezosc w UI.
    #
    # ZAWSZE `cache_at`, nigdy blend z `fresh_at`. Pomiar sklada sie z DWOCH zrodel o
    # roznym wieku, a `spend` i `extra_usage` pochodza WYLACZNIE z cache — jeden stempel
    # wziety ze zrzutu odmladzal je o cala roznice wiekow (do godziny). Backend rozstrzyga
    # po nim "ktory odczyt jest biezacy", wiec maszyna ze starszym cache, ale swiezszym
    # zrzutem cofala stan `spend:org` i `extra:usage` — jedynych dwoch serii, ktore granicy
    # okna nie maja nigdy, wiec guard monotonicznosci ich nie broni.
    #
    # Wiek zrzutu jedzie osobno (`fresh_at` + `fresh_covered`); to backend sklada z tego
    # date per seria, bo tylko on wie, ktora seria czym jest.
    #
    # Rozdzialu "pierwsza obserwacja tej wartosci" (wykres) od "ostatnie potwierdzenie"
    # (swiezosc) NIE robimy tutaj: sonda nie zna poprzedniej wartosci serii. Robi to
    # backend, ktory ma series_state — patrz confirmed_at w backend/app/services/ingest.py.
    captured = cache_at

    record = {
        "account": {
            "uuid": (acct or {}).get("accountUuid"),
            "email": (acct or {}).get("emailAddress"),
            "display_name": (acct or {}).get("displayName"),
            "org_uuid": (acct or {}).get("organizationUuid"),
            "org_name": (acct or {}).get("organizationName"),
            "org_type": (acct or {}).get("organizationType"),
            "seat_tier": (acct or {}).get("seatTier"),
            "org_rate_limit_tier": (acct or {}).get("organizationRateLimitTier"),
            "user_rate_limit_tier": (acct or {}).get("userRateLimitTier"),
            "extra_usage_enabled": (acct or {}).get("hasExtraUsageEnabled"),
        },
        "token_meta": load_token_meta(),
        "captured_at": _iso(captured),
        "client": {
            "host": _safe(socket.gethostname),
            "config_dir_hash": (hashlib.sha256(cfg_dir.encode()).hexdigest()[:16]
                                if cfg_dir else None),
            "script_version": SCRIPT_VERSION,
            "exec_ms": round((time.perf_counter() - t0) * 1000),
        },
        "hook": {"event": hook.get("hook_event_name"),
                 "session_id": hook.get("session_id"), "cwd": hook.get("cwd")},
        "measurement": {
            "source": "cli_merged" if fresh else "cli_usage_cache",
            "probe_at": _iso(time.time()),  # kiedy sonda sie uruchomila — DIAGNOSTYKA,
                                            # nie mylic z captured_at; roznica tych dwoch
                                            # to wlasnie opoznienie pomiaru
            "cache_age_s": round(cache_age),
            # Powod z cache KLIENTA. DIAGNOSTYKA: rozroznia wyczerpana wlasna pule od
            # sufitu organizacji, ale werdykt backendu stoi na `usage.spend`, bo tylko
            # ono jest spojne z reszta tej samej odpowiedzi. Nie idzie do `usage` —
            # parse_usage iteruje tam klucze najwyzszego poziomu i wartosc nie-slownikowa
            # zglosilaby drift schematu przy kazdym pomiarze.
            "extra_usage_disabled_reason": eu_reason,
            "fresh_age_s": round(time.time() - fresh_at) if fresh_at else None,
            # Czas zrzutu i lista serii, ktore z niego wzialy wartosc. Backend datuje po
            # nich TE serie, a reszte po `captured_at` (czyli po cache). Identyfikatory
            # musza sie zgadzac z `parsing.probe_key` — patrz `_limit_key`.
            "fresh_at": _iso(fresh_at) if (fresh and fresh_at) else None,
            "fresh_covered": covered,
            "fresh_skip": fresh_skip,       # czemu zrzut stdout nie zostal uzyty
            "dropped": dropped,             # co odrzucil sanitize i dlaczego
            "spawn_error": spawn_err,
        },
        "usage": usage,
    }

    log_local(dict(record, t=round(time.time(), 3), ok=True))   # lokalny log niezaleznie od POST

    if not cfg.get("ingest_url") or not cfg.get("ingest_token"):
        return 0                                   # tryb "tylko lokalnie" — brak konfiguracji

    backlog, spool_total = read_spool(MAX_BACKLOG_PER_REQUEST)

    # KOTWICA WIEKU. Backend liczy z niej `offset = arrived_at - sent_at` i datuje pomiar
    # jako `min(ts + offset, arrived_at)`, czyli `received_at - wiek`. Zegar scienny tej
    # maszyny nie wchodzi do rachunku — liczy sie tylko roznica `sent_at - ts`, a ta jest
    # w obrebie JEDNEGO zegara i dlatego wiarygodna.
    #
    # Ustawiane tuz przed wysylka i zapisywane TAKZE w `record`, ktory idzie do spoola:
    # dla wpisu ze spoola `sent_at` jest z chwili nieudanej proby, wiec wiek liczy sie sam
    # i we wlasciwym momencie, bez przeliczania czegokolwiek po stronie klienta.
    record["measurement"]["sent_at"] = _iso(time.time())

    payload = dict(record)
    if backlog:
        payload["backlog"] = backlog

    try:
        code, resp = post(cfg, payload)
    except Exception:
        append_spool(record)
        return 0

    if code >= 300:
        append_spool(record)
        return 0

    parsed = _safe(json.loads, resp) or {}
    trim_spool(int(parsed.get("backlogAccepted") or parsed.get("backlog_accepted") or 0))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)          # sonda nie ma prawa zepsuc sesji
