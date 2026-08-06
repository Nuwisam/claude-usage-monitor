#!/usr/bin/env python3
"""Sonda limitow Claude + sygnalizator zablokowanej sesji. Oba z hookow Claude Code.

DWIE FUNKCJE, JEDEN PROCES — i to jest zmierzone, nie estetyczne. Sygnalizator byl
osobnym skryptem (`client/session-status.py`), przez co 9 z 10 jego zdarzen odpalalo
DRUGIEGO CPythona obok tego, ktory i tak startowal dla sondy. Pomiar (100 przebiegow
na wariant, przeplatane, mediana): dolozenie kodu sygnalizatora do tego procesu
kosztuje 2,7 ms (95% CI 1,9-3,2), a osobny proces 41,9 ms (41,7-42,3). W skali doby,
przy ~21 000 zdarzen: +57 s wobec +890 s. Dawne uzasadnienie rozdzialu ("+0,294 ms na
kazda dopisana linie") bylo zawyzone ~71-krotnie — realnie 0,0041 ms/linia.

Skutek uboczny, tez policzony: 9 z 13 wspolnych nazw bylo bajt w bajt identycznych,
a `_extract_block` (~40 linii) roznil sie tylko komentarzem. Rozdzial WYMUSZAL duplikat.

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

SYGNALIZATOR (sekcja "alert" nizej) wykrywa moment, w ktorym Claude Code stanal
i czeka na CZLOWIEKA: prompt o zgode, AskUserQuestion, ExitPlanMode. Kazda blokada to
jeden plik w katalogu stanu; zbior tych plikow jest CALA prawda, a POST tylko
powiadomieniem o zmianie, niosacym zbior w calosci. Odpala sie PRZED throttlem —
alert nie moze czekac 60 s, a throttle sondy to 60 s. Wylacza go "session_status": false.

Konfiguracja: %LOCALAPPDATA%\\claude-usage-monitor\\config.json (Windows)
              ~/.local/state/claude-usage-monitor/config.json (Linux)
    {"ingest_url": "https://usage.example.org/claude-usage/api/ingest",
     "ingest_token": "<token TEJ maszyny>",
     "edge_key": "<wspolny sekret brzegowy>",
     "throttle_sec": 60,
     "claude_bin": "<opcjonalnie pelna sciezka do claude>",
     "session_status": true,        # sygnalizator; false wylacza go w calosci
     "alert_url": "https://usage.example.org/claude-usage/api/session-alert",
     "toast": true,
     "blocked_ttl_sec": 86400}
Celowo plik lokalny, a nie repo — token maszyny nie ma prawa trafic do gita.
"""
import sys, os, json, time, re

SCRIPT_VERSION = 8

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
    ~/.claude.json REALNIE takie ma, json.load() na calosci sie na nim wywraca.

    Dwoch wolajacych: `read_claude_json` (tozsamosc konta i cache pomiaru) oraz
    `account_uuid` z sekcji alertu (do ktorego pasa panelu nalezy trojkat). Przed
    scaleniem obu skryptow ta funkcja istniala w DWOCH kopiach."""
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
    obniza o rzad wielkosci. Alias, nie ID z data — ID bywaja wycofywane.

    --strict-mcp-config --mcp-config {"mcpServers":{}} odcina boot MCP — sonda go nie uzywa,
    a to kilkanascie procesow node/npx na przebieg."""
    exe = find_claude(cfg)
    if not exe:
        return "brak-claude-w-path"
    import subprocess
    env = dict(os.environ)
    env[CHILD_ENV] = "1"                # zapora przed rekurencja hookow
    kw = {}
    if os.name == "nt":
        # CREATE_NO_WINDOW (ukryta konsola, dziedziczona przez wnuki) | NEW_PROCESS_GROUP
        # (brak Ctrl+C od rodzica). NIE dodawac DETACHED_PROCESS (0x8) — wygrywa z
        # CREATE_NO_WINDOW, wnuki alokuja wtedy wlasna, widoczna konsole.
        kw["creationflags"] = 0x08000000 | 0x00000200
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
             "/usage", "--output-format", "json",
             # tylko na koncu: --mcp-config jest wariadyczne, zjadloby prompt jako sciezke
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
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


def post(cfg, target, body):
    """Jeden POST. `target` jawnie, bo wolajacych jest dwoch: ingest pomiaru
    (`ingest_url`) i sygnalizator (`alert_url`). Oba uwierzytelnia ten sam token
    maszyny i ten sam sekret brzegowy.

    Importy lokalne — sciezka zimna. `http.client` wciaga socket i ssl; te cztery
    moduly na gorze pliku kosztowaly ~23 ms przy KAZDYM przebiegu, takze tym, ktory
    konczy sie na throttlu. NIE przenosic w gore ani nie dodawac uzyc przed ta linia.
    """
    import http.client, urllib.parse

    url = urllib.parse.urlsplit(target)
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


# =============================================================== alert: sygnalizator
# Wykrywa, ze Claude Code stanal i czeka na czlowieka. Ta sekcja NIE dotyka niczego
# z sondy poza wspolnymi pomocnikami (`_safe`, `load_config`, `_iso`, `_find`,
# `_extract_block`, `ssl_context`, `post`).
#
# CO ZMIERZONO, I DLACZEGO KOD WYGLADA WLASNIE TAK (Claude Code 2.1.221, VS Code,
# permission_mode=default, Windows; 224 zdarzenia filtrowane po session_id):
#   * `PermissionRequest` odpala WYLACZNIE przy realnym pytaniu do czlowieka —
#     auto-dopuszczone Read/Grep/Write/echo nie generuja go ani razu. Zero heurystyk.
#   * `PermissionRequest` NIE MA `tool_use_id`. Stad klucz hybrydowy.
#   * `tool_input` dla AskUserQuestion ZMIENIA sie miedzy wejsciem a wyjsciem (harness
#     domerza odpowiedzi, 1326 -> 1649 B), wiec hash z tool_input nie moze byc jedynym
#     kluczem. Te dwa narzedzia klucza sie po `tool_use_id` z PreToolUse.
#   * NIC, co konczy wywolanie inaczej niz normalnym wykonaniem, nie generuje zdarzenia:
#     odmowa przyciskiem, Esc na prompcie i Esc w trakcie dzialania — 5/5 przypadkow
#     konczy sie na PreToolUse + PermissionRequest i niczym wiecej. `PermissionDenied`
#     nie odpalil ani razu, `is_interrupt: true` okazalo sie nieosiagalne. Dlatego
#     zamiatanie po prefiksie session_id jest OBOWIAZKOWE, a nie ostroznosciowe.
#   * `Stop` NIE odpala na przerwanej turze, a odmowa konczy ture wlasnie jako
#     przerwanie. Dlatego w liscie zamiatania pierwszy jest `UserPromptSubmit`.
#   * `PostToolUse` nie jest gwarantowany (Edit na pliku planu: 0/6 domknietych, na
#     innych plikach 4/4). Domyka to `PostToolBatch.tool_calls[]`.

STATEDIR = os.path.join(OUTDIR, "session-status")
POSTED = os.path.join(OUTDIR, "session-status-posted.txt")

DEFAULT_TTL_S = 86400
DETAIL_MAX = 120
MAX_ENTRIES = 64            # sufit na wypadek awarii zamiatania; panel i tak pokazuje kilka

# Te dwa narzedzia ZAWSZE blokuja, wiec PreToolUse nie daje przy nich falszywek —
# a niesie `tool_use_id`, ktorego `PermissionRequest` nie ma.
ENTER_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}

CLOSING_EVENTS = ("PostToolUse", "PostToolUseFailure")
SWEEP_EVENTS = ("UserPromptSubmit", "Stop", "SessionEnd")

_ACCT_CACHE = {}


def account_uuid():
    """`oauthAccount.accountUuid` z ~/.claude.json, z cache na mtime.

    Czytane WYLACZNIE na sciezce wejscia (rzadkiej), wiec nie zastepuje
    `read_claude_json` — ta czyta ten sam plik po throttlu i po znacznie wiecej.
    Panel maluje trojkat obok konkretnej nazwy konta, wiec musi wiedziec, do ktorego
    pasa alert nalezy; zasada 7 mowi, ze tozsamosc bierze sie stad i tylko stad.
    """
    path = _find(".claude.json")
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _ACCT_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    block = _safe(_extract_block, text, "oauthAccount") or {}
    uuid = block.get("accountUuid")
    uuid = uuid if isinstance(uuid, str) else None
    _ACCT_CACHE[path] = (mtime, uuid)
    return uuid


# --------------------------------------------------------------- nazwa projektu
def _slug(path):
    """Sciezka -> slug katalogu transkryptow. Kazdy znak spoza [A-Za-z0-9] to '-'.

    Normalizacja obustronna, wiec ewentualna roznica w traktowaniu podkreslnika przez
    Claude Code nas nie rozjedzie: ten sam filtr kladziemy takze na nazwe katalogu."""
    return "".join(c if (c.isascii() and c.isalnum()) else "-"
                   for c in os.path.normcase(path))


def project_name(cwd, transcript_path):
    """Nazwa projektu — z katalogu transkryptu, NIE z `basename(cwd)`.

    Zmierzone: 38 z 73 sesji raportuje wiecej niz jedno `cwd` (w jednej sesji naraz
    ...\\claude-usage-monitor, ...\\backend, ...\\frontend, ...\\frontend\\src — naglowek
    pokazywalby "src"), a 27 z 51 roznych `cwd` to `...\\.claude\\worktrees\\agent-a<hex>`.

    "Idz w gore do .git" tez jest zle: korzen worktree ma `.git` jako PLIK, wiec walk-up
    staje na worktree i zwraca `agent-a00ce9ba287d12ab1`.

    Transkrypty leza pod ~/.claude/projects/<slug PIERWOTNEGO cwd sesji>/, a ten katalog
    zawiera zmierzone WYLACZNIE korzenie projektow. Odzyskujemy korzen, obcinajac segmenty
    `cwd`, az slug prefiksu zgodzi sie z nazwa katalogu. Zero I/O na sciezce glownej.
    """
    if not isinstance(cwd, str) or not cwd:
        return None
    want = None
    if isinstance(transcript_path, str) and transcript_path:
        want = _slug(os.path.basename(os.path.dirname(transcript_path)))
    path = os.path.normpath(cwd)
    if want:
        probe = path
        while True:
            if _slug(probe) == want:
                return os.path.basename(probe) or probe
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
    # Zapas: katalog z `.git` jako KATALOGIEM (plik = worktree, ten nas nie interesuje).
    probe = path
    while True:
        if _safe(os.path.isdir, os.path.join(probe, ".git")):
            return os.path.basename(probe) or probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return os.path.basename(path) or path
        probe = parent


# --------------------------------------------------------------- klucz wpisu
def call_key(tool_name, tool_input, prompt_id):
    """sha256(prompt_id | tool_name | json(tool_input)) [:16].

    Uzywane tam, gdzie `tool_use_id` nie istnieje — czyli dla `PermissionRequest`.
    Zmierzone jako stabilne dla Bash/Edit/Read/Grep/Write: 36 wywolan, zero kolizji,
    dokladnie jeden tool_use_id na klucz. Znany przypadek zdegenerowany: dwa IDENTYCZNE
    co do znaku wywolania w obrebie jednego prompt_id dziela klucz, wiec wyjscie
    pierwszego zdejmie wpis drugiego. Rzadkie i tanie.
    """
    import hashlib
    blob = "%s|%s|%s" % (prompt_id or "", tool_name or "",
                         json.dumps(tool_input, sort_keys=True, ensure_ascii=False,
                                    default=str))
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def _fname(session_id, agent_id, key):
    return "%s__%s__%s.json" % (session_id, agent_id or "main", key)


# --------------------------------------------------------------- katalog stanu
def entries():
    """Wszystkie wpisy: [(nazwa_pliku, mtime)]. `scandir`, NIGDY `os.stat(sciezka)`.

    Zmierzone przeciw czytelnikowi w petli: `os.stat(path)` koliduje w 35% przy
    maksymalnym obciazeniu, `scandir` + `DirEntry.stat()` w 0% — ten drugi jest
    obslugiwany z rekordu enumeracji katalogu i nie otwiera niczego.
    """
    out = []
    try:
        with os.scandir(STATEDIR) as it:
            for de in it:
                if not de.name.endswith(".json"):
                    continue
                try:
                    out.append((de.name, de.stat().st_mtime))
                except OSError:
                    continue
    except OSError:
        return []
    return out


def read_entry(name):
    try:
        with open(os.path.join(STATEDIR, name), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_excl(name, payload):
    """Zapis przez O_CREAT|O_EXCL. Zwraca True, gdy plik POWSTAL teraz.

    Zmierzone: 0 twardych porazek na 10 978 prob przeciw czytelnikowi w petli.
    Wariant "temp + os.replace" odpada, bo CPython otwiera bez FILE_SHARE_DELETE,
    wiec uchwyt czytelnika blokuje `replace` i `remove` (zreprodukowane: WinError 5 / 32).
    O_EXCL daje przy okazji zachowanie `since` za darmo i BEZ odczytu: FileExistsError
    znaczy "ta blokada juz jest", wiec stempel pierwszego wejscia zostaje nietkniety.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_BINARY", 0)
    try:
        os.makedirs(STATEDIR, exist_ok=True)
        fd = os.open(os.path.join(STATEDIR, name), flags, 0o600)
    except FileExistsError:
        return False
    except Exception:
        return False
    try:
        # ensure_ascii=False + utf-8 jawnie, jak log_local: bez tego cp1250 wywala sie
        # na polskiej sciezce w `detail`.
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass
    finally:
        _safe(os.close, fd)
    return True


def drop(name):
    """Kasowanie idempotentne. Jedyna krucha operacja w tej sekcji — uchwyt czytelnika
    potrafi ja zablokowac, wiec trzy podejscia. Porazka to zawieszony alert, nie
    zgubiony: kazde kolejne zdarzenie wyjscia probuje ponownie."""
    path = os.path.join(STATEDIR, name)
    for _ in range(3):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            time.sleep(0.01)
    return False


def sweep_ttl(ttl_s, now):
    """Granica smieci. Kasuje, NIGDY nie ukrywa — o tym, jak dlugo alert zajmuje ekran,
    decyduje panel (`alert_takeover_sec`), nie ten prog. Wartosc pochodzi z recznie
    edytowanego config.json, wiec smieci znacza domyslna, a nie wyjatek."""
    try:
        ttl_s = float(ttl_s)
    except (TypeError, ValueError):
        ttl_s = DEFAULT_TTL_S
    for name, mtime in entries():
        if now - mtime > ttl_s:
            drop(name)


# --------------------------------------------------------------- wysylka alertu
def snapshot():
    """Biezacy zbior wpisow maszyny, posortowany po `since`."""
    out = []
    for name, _mtime in sorted(entries()):
        data = read_entry(name)
        if data is None:
            continue
        data["key"] = name[:-5]
        out.append(data)
    out.sort(key=lambda e: e.get("since") or "")
    return out[:MAX_ENTRIES]


def _fingerprint(items):
    return json.dumps([[e.get("key"), e.get("reason")] for e in items], sort_keys=True)


def publish(cfg):
    """POST tylko wtedy, gdy ZBIOR sie zmienil.

    Znacznik zapisujemy DOPIERO po udanej wysylce, wiec nieudany POST powtorzy sie przy
    nastepnym zdarzeniu. Ograniczenie znane i wpisane w projekt: zablokowana sesja nie
    generuje kolejnych zdarzen, wiec POST zgubiony dokladnie na wejsciu czeka na
    najblizszy ruch w tej albo innej sesji na tej maszynie.
    """
    items = snapshot()
    finger = _fingerprint(items)
    try:
        with open(POSTED, "r", encoding="utf-8") as f:
            if f.read() == finger:
                return
    except Exception:
        pass
    url = cfg.get("alert_url")
    if not url or not cfg.get("ingest_token"):
        return                      # tryb tylko lokalny — pliki i toast, bez sieci
    body = {"entries": items, "sent_at": _iso(time.time()),
            "script_version": SCRIPT_VERSION}
    try:
        code, _resp = post(cfg, url, body)
    except Exception:
        return
    if code >= 300:
        return
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(POSTED, "w", encoding="utf-8") as f:
            f.write(finger)
    except Exception:
        pass


# --------------------------------------------------------------- toast lokalny
TOAST_TITLES = {"permission": "Claude czeka na zgodę",
                "question": "Claude ma pytanie",
                "plan": "Claude czeka na akceptację planu"}


def _xml(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("'", "&apos;").replace('"', "&quot;"))


def toast(reason, project, detail):
    """Powiadomienie Windows przez WinRT, bez zadnych modulow.

    BurntToast nie jest zainstalowany, a wariant z `[xml]` z poradnikow nie dziala
    (`Cannot find type [Windows.Data.Xml.Dom.XmlDocument]`). Ponizsze jest sprawdzone:
    PowerShell 5.1, zero modulow, 394 ms. AUMID zarejestrowanego PowerShella jest nosny
    — Windows po cichu odrzuca toasty z niezarejestrowanych AppID.

    -EncodedCommand, NIE -Command: PowerShell 5.1 dekoduje wiersz polecenia strona
    kodowa konsoli i polskie znaki wychodzily krzakami. Base64 z UTF-16LE tego nie
    dotyczy i przy okazji znosi problem cytowania w `detail`.
    """
    if os.name != "nt":
        return
    import base64, subprocess
    line1 = TOAST_TITLES.get(reason, "Claude czeka na Ciebie")
    line2 = project or ""
    if detail:
        line2 = ("%s — %s" % (line2, detail))[:90] if line2 else detail[:90]
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType=WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType=WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$x.LoadXml('<toast><visual><binding template=\"ToastText02\">"
        "<text id=\"1\">%s</text><text id=\"2\">%s</text>"
        "</binding></visual></toast>');"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
        ").Show((New-Object Windows.UI.Notifications.ToastNotification $x))"
    ) % (_xml(line1), _xml(line2))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # CREATE_NO_WINDOW | NEW_PROCESS_GROUP — jak w spawn_refresh. NIE dodawac
            # DETACHED_PROCESS: wygrywa z CREATE_NO_WINDOW i konsola miga.
            creationflags=0x08000000 | 0x00000200, close_fds=True)
    except Exception:
        pass


# --------------------------------------------------------------- maszyna stanow
_DETAIL_KEYS = ("command", "file_path", "path", "url", "pattern", "description",
                "prompt", "plan")


def detail_of(tool_name, tool_input):
    if tool_name == "AskUserQuestion":
        qs = (tool_input or {}).get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            for k in ("question", "header"):
                v = qs[0].get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:DETAIL_MAX]
        return ""
    if not isinstance(tool_input, dict):
        return ""
    for k in _DETAIL_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())[:DETAIL_MAX]
    return ""


def enter(cfg, hook, reason, key):
    session_id = hook.get("session_id")
    if not session_id or not key:
        return
    name = _fname(session_id, hook.get("agent_id"), key)
    tool_name = hook.get("tool_name")
    entry = {
        "session_id": session_id,
        "agent_id": hook.get("agent_id"),
        "agent_type": hook.get("agent_type"),
        "reason": reason,
        "tool": tool_name,
        "detail": detail_of(tool_name, hook.get("tool_input")),
        "project": project_name(hook.get("cwd"), hook.get("transcript_path")),
        "cwd": hook.get("cwd"),
        "since": _iso(time.time()),
        "account_uuid": account_uuid(),
        # Do diagnostyki: caly pomiar szedl w trybie `default`, a tryby
        # auto-zatwierdzajace rozstrzygaja wywolanie PRZED warstwa promptu.
        "permission_mode": hook.get("permission_mode"),
    }
    if not write_excl(name, entry):
        return                              # ta blokada juz jest — bez toasta, bez POST-u
    if cfg.get("toast", True):
        toast(reason, entry["project"], entry["detail"])
    publish(cfg)


def _close_keys(hook, call):
    """Oba kandydaty klucza dla jednego wywolania. Wyjscie nie musi wiedziec, ktorym
    trybem wpis powstal — kasowanie jest idempotentne, wiec chybienie nic nie kosztuje."""
    out = []
    tuid = call.get("tool_use_id")
    if tuid:
        out.append(tuid)
    out.append(call_key(call.get("tool_name"), call.get("tool_input"),
                        hook.get("prompt_id")))
    return out


def leave(cfg, hook):
    """Zdarzenie zamykajace. Zdarzenie z `agent_id` nigdy nie zamknie wpisu zapisanego
    bez niego — segment agenta jest czescia nazwy pliku, wiec regula wynika z konstrukcji."""
    if not entries():
        return                              # sciezka goraca konczy sie tutaj
    session_id = hook.get("session_id")
    if not session_id:
        return
    calls = [hook]
    if hook.get("hook_event_name") == "PostToolBatch":
        calls = [c for c in (hook.get("tool_calls") or []) if isinstance(c, dict)]
    hit = False
    for call in calls:
        for key in _close_keys(hook, call):
            if drop(_fname(session_id, hook.get("agent_id"), key)):
                hit = True
    if hit:
        publish(cfg)


def sweep_session(cfg, hook, gc_cwd=False):
    """Zamiatanie po prefiksie `<session_id>__`.

    OBOWIAZKOWE, nie ostroznosciowe: to jedyny mechanizm gaszacy alert po odmowie
    i po przerwaniu, bo zadne z nich nie generuje wlasnego zdarzenia. `UserPromptSubmit`
    jest w tej liscie najwazniejszy, bo `Stop` nie odpala na przerwanej turze.

    `SessionEnd` zamiata WYLACZNIE wlasny session_id: zmierzone, ze przychodzi ~raz na
    minute z identyfikatorem dziecka `claude -p` odpalanego przez ten sam skrypt, wiec
    zamiatanie globalne wycieraloby alerty co minute.
    """
    session_id = hook.get("session_id")
    all_entries = entries()
    if not all_entries:
        return
    prefix = ("%s__" % session_id) if session_id else None
    hit = False
    for name, _mtime in all_entries:
        if prefix and name.startswith(prefix):
            hit = drop(name) or hit
            continue
        if gc_cwd and hook.get("cwd"):
            data = read_entry(name)
            if data and data.get("cwd") == hook.get("cwd"):
                # Ten sam projekt, inna sesja: poprzednie okno padlo w blokadzie.
                hit = drop(name) or hit
    if hit:
        publish(cfg)


def alert_shutdown(cfg):
    """Wylaczone przez `session_status: false` — zgas to, co jeszcze wisi.

    Sam `return` by nie wystarczyl: blokada trwajaca w chwili wylaczenia zostalaby na
    panelu do serwerowego TTL (24 h), bo nikt juz nie wysle korekty. Po pierwszym takim
    przebiegu katalog jest pusty i kazdy kolejny konczy sie na samym `scandir`.
    """
    biezace = entries()
    if not biezace:
        return
    for name, _mtime in biezace:
        drop(name)
    publish(cfg)


def alert_dispatch(cfg, hook):
    """Wejscie sekcji alertu. Wolane PRZED throttlem, opakowane w `_safe`."""
    if not cfg.get("session_status", True):
        return alert_shutdown(cfg)

    event = hook.get("hook_event_name")

    if event == "PreToolUse":
        # Bramka nazwa narzedzia jako PIERWSZA instrukcja: dla ~90% wywolan galaz
        # konczy sie tutaj i nie dotyka dysku.
        reason = ENTER_TOOLS.get(hook.get("tool_name"))
        if reason is None:
            return
        return enter(cfg, hook, reason, hook.get("tool_use_id"))

    if event == "PermissionRequest":
        if hook.get("tool_name") in ENTER_TOOLS:
            # Te dwa maja wlasne wejscie po `tool_use_id`. Kolejnosc PreToolUse vs
            # PermissionRequest jest NIEGWARANTOWANA (zmierzone 20% inwersji), wiec
            # dwa zrodla wejscia dla jednego wywolania daly by wyscig o dwa pliki.
            return
        return enter(cfg, hook, "permission",
                     call_key(hook.get("tool_name"), hook.get("tool_input"),
                              hook.get("prompt_id")))

    if event in CLOSING_EVENTS or event == "PostToolBatch":
        return leave(cfg, hook)

    if event == "PermissionDenied":
        # Zarejestrowany przez caly pomiar i nie odpalil ani razu — nie moze na nim
        # stac zadna regula. Wpiety, bo nic nie kosztuje i zlapie odmowy klasyfikatora.
        return leave(cfg, hook)

    if event in SWEEP_EVENTS or event == "SessionStart":
        _safe(sweep_ttl, cfg.get("blocked_ttl_sec", DEFAULT_TTL_S), time.time())
        return sweep_session(cfg, hook, gc_cwd=(event == "SessionStart"))


# --------------------------------------------------------------- main
def main():
    t0 = time.perf_counter()

    # Zapora przed rekurencja. MUSI byc przed czymkolwiek innym: proces potomny
    # `claude -p "/usage"` odpala hook Stop, ktory uruchamia te sonde ponownie.
    if os.environ.get(CHILD_ENV):
        return 0

    hook = _safe(json.loads, sys.stdin.read() or "{}") or {}
    cfg = load_config()

    # PRZED throttlem. Alert ma dojsc natychmiast, a throttle sondy to 60 s — za tym
    # progiem blokada byla by widoczna dopiero po minucie albo wcale.
    # `_safe`, bo zasada 3: sonda nie ma prawa rzucic wyjatkiem, a sygnalizator nie ma
    # prawa zepsuc pomiaru limitow.
    _safe(alert_dispatch, cfg, hook)

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

    # Jak w post(): import lokalny, bo tu jestesmy juz za throttlem. NIE przenosic
    # w gore — uzycie przed ta linia to UnboundLocalError, ktory `except Exception`
    # w :764 polknie w cisze i maszyna przestanie raportowac bez jednego objawu.
    import socket, hashlib

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
        code, resp = post(cfg, cfg["ingest_url"], payload)
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
