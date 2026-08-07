# Klient — sonda limitów

Skrypt uruchamiany z hooków Claude Code. **Nie wysyła żadnego żądania do `api.anthropic.com`.**
Pomiar zleca samemu Claude Code (`claude -p "/usage"`), czyta wynik z dysku i wysyła do
monitora sam wynik.

## Dlaczego nie odpytujemy endpointu sami

Wersja 2 sondy wołała `GET /api/oauth/usage` własnym tokenem OAuth z `.credentials.json`,
podszywając się pod `User-Agent: claude-code/…` (bez tego trafia się w agresywny bucket 429).
Warunki Anthropic mówią bez wyjątków, że tokeny OAuth z kont Free/Pro/Max nie mogą być używane
*„in any other product, tool, or service"* — nie ma tam furtki dla użycia read-only.

Wersja 3 usuwa cały problem: żądanie wykonuje pierwszorzędny klient, własnym tokenem, który
sam sobie odświeża. My tylko czytamy to, co zostawił.

## Dwa źródła, bo świeżość i kompletność leżą gdzie indziej

Zmierzone, nie wydedukowane:

| Źródło | Świeżość | Co zawiera |
|---|---|---|
| stdout `claude -p "/usage"` | **świeże przy każdym wywołaniu** | procenty głównych okien, jako tekst |
| `~/.claude.json` → `cachedUsageUtilization` | ≤ 5 min | **pełne surowe ciało odpowiedzi** — `spend`, `extra_usage`, `limits[]`, wszystkie 17 bucketów |

Rozdział bierze się stąd, że pięciominutowy throttle w Claude Code dotyczy **zapisu na
dysk**, a nie pobrania — dlatego interaktywne `/usage` zawsze pokazuje aktualne dane, a plik
bywa o kilka minut starszy.

Sonda scala jedno z drugim: struktura z cache, świeże procenty nadpisane na wierzchu. Wynik ma
**dokładnie ten sam kształt** co dawna odpowiedź HTTP, więc `backend/app/parsing.py` nie
wymagał ani jednej zmiany. Utrata miejsc po przecinku przy `Math.floor` w stdout **nie jest
stratą** — API samo zwraca liczby całkowite (sprawdzone na surowym payloadzie).

## `/usage` nie zużywa limitu

`/usage` jest w binarce zarejestrowane dwukrotnie; wariant `supportsNonInteractive: true` jest
aktywny właśnie w trybie `-p` i zwraca `{type:"text"}`, co ustawia `shouldQuery=false`.
Zmierzone na 6 wywołaniach: `num_turns=0`, `duration_api_ms=0`, `total_cost_usd=0`.

**Ale to działa tylko wtedy, gdy argument trafi w komendę lokalną.** Jeśli nie trafi — leci
normalny, płatny turn modelu. Sonda wykrywa to po `num_turns>0` i taki zrzut odrzuca.

> **Pułapka:** Git Bash na Windows konwertuje argumenty zaczynające się od `/`, więc
> `claude -p "/usage"` staje się `claude -p "C:/Program Files/Git/usage"` i dostajesz płatny
> turn. Testuj z PowerShella albo ustaw `MSYS_NO_PATHCONV=1`.

## Pliki

| Plik | Rola |
|---|---|
| `usage-probe.py` | **Źródło prawdy.** Sonda **i** sygnalizator zablokowanej sesji — tu się je edytuje |
| `analyze-samples.py` | Analiza lokalnego logu — tempo zmian, błędy, konta |

**Dlaczego jeden plik, a nie dwa.** Sygnalizator był osobnym skryptem wpiętym w dziesięć
zdarzeń, z których **siedem** zajmowała już sonda — a że są to `PreToolUse` i `PostToolUse`,
czyli te odpalane przy każdym wywołaniu narzędzia, w **przytłaczającej większości przebiegów**
startowały dwa CPythony zamiast jednego. Zmierzone (100 przebiegów na wariant, przeplatane,
mediana): dołożenie kodu sygnalizatora do procesu sondy kosztuje **2,7 ms** (95% CI 1,9–3,2),
a osobny proces **41,9 ms** (41,7–42,3). Po faktycznym scaleniu kontrola dała **1,7 ms** —
mniej niż prognoza, bo scalenie usunęło duplikaty (+542 linie zamiast +648). W skali doby,
przy ~21 000 zdarzeń, licząc ze zmierzonych 1,7 ms: **+36 s** zamiast **+890 s**.

Poprzednie uzasadnienie rozdziału — *„+0,294 ms na każdą linię dopisaną do sondy"* — było
**zawyżone ~71-krotnie**; realnie 0,0041 ms/linię. Rozdział wymuszał przy tym duplikat:
z 13 nazw wspólnych obu plikom 9 było bajt w bajt identycznych, a `_extract_block` (~40 linii)
różnił się wyłącznie komentarzem.

Konsekwencja: **alerty wymagają sondy.** Nie ma już osobnego skryptu dla maszyny, która chciałaby
powiadomienia bez pomiaru limitów.

Tutaj jest **źródło**: ładuje je `backend/tests/test_probe_parsing.py` po sztywnej ścieżce
i tutaj trafiają zmiany. Każda kopia rozdana na maszyny jest **wydaniem** i **może być
starsza** od HEAD — to poprawne, bo publikacja ma być decyzją, nie skutkiem ubocznym pushu;
automat wypchnąłby na maszyny zdalne wersję roboczą.

Dlatego **każda zmiana zachowania wymaga podbicia `SCRIPT_VERSION`**: wersja jedzie w każdym
batchu, więc różnicę widać w `/api/machines` po `scriptVersion`. Bez podbicia dwie różne
sondy są nierozróżnialne.

## Instalacja

Instrukcja jest kompletna: od pustej maszyny do potwierdzonego pomiaru w monitorze.
Kolejność kroków ma znaczenie — sekrety sprawdzamy **zanim** ruszymy `settings.json`,
żeby nieudana instalacja zostawiła plik nietknięty.

### 0. Wymagania

| Czego trzeba | Jak sprawdzić | Gdy brak |
|---|---|---|
| `claude` w `PATH` | `claude --version` | Pełna ścieżka idzie do `config.json` jako `claude_bin`. Bez tego sonda nie ma czym zlecić pomiaru i loguje `brak-claude-w-path` |
| Działający Python | po kolei `python3 --version`, `python --version`, `py -3 --version` | Stop — hook nie ma czym się uruchomić |
| `~/.claude/settings.json` | plik istnieje i parsuje się jako JSON | Brak pliku: utwórz `{}`. **Jest, ale się nie parsuje: stop.** Nie nadpisuj — trzyma całą konfigurację Claude Code |

**Zapamiętaj nazwę interpretera, która zadziałała — dokładnie ta wchodzi do hooka.**
Nie wpisuj tam ścieżki bezwzględnej: aktualizacja Pythona ją przesuwa i wszystkie hooki
giną po cichu. Na Linuksie i macOS zwykle istnieje wyłącznie `python3`.

### 1. Token maszyny — ten krok dzieje się na serwerze

Ingest autoryzuje **każdą maszynę osobno**, tokenem z `INGEST_TOKENS`. Maszyna zdalna nie
ma dostępu do hosta monitora, więc tokenu nie wygeneruje sobie sama. Kandydata robi się tak:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

a na hoście monitora dopisuje się go do `INGEST_TOKENS` w `.env` — jako `,<token>:<nazwa-maszyny>`,
gdzie `<nazwa-maszyny>` to etykieta widoczna w panelu (`desktop`, `laptop`, `vps-1`). Potem,
w katalogu z `docker-compose.yml`:

```bash
docker compose up -d
```

**`docker compose up -d`, nigdy `restart`.** `INGEST_TOKENS` czytane jest przy *tworzeniu*
kontenera; `restart` zostawia stary zestaw zmiennych i świeży token dalej jest nieważny —
objaw nie do odróżnienia od źle przepisanego tokenu.

Drugi sekret, `INGEST_EDGE_KEY`, sprawdza Apache przed aplikacją, żeby skanery nie dobijały
się do Pythona. Jest **wspólny dla wszystkich maszyn** — weź istniejący z `.env` hosta, nie
wymyślaj nowego. Format obu w [`.env.example`](../.env.example), wdrożenie serwera w
[`README.md`](../README.md#wdrożenie-serwera).

### 2. Konfiguracja

`%LOCALAPPDATA%\claude-usage-monitor\config.json` (Windows)
albo `~/.local/state/claude-usage-monitor/config.json` (Linux, macOS):

```json
{
  "ingest_url": "https://usage.example.org/claude-usage/api/ingest",
  "ingest_token": "<token TEJ maszyny, z kroku 1>",
  "edge_key": "<INGEST_EDGE_KEY>",
  "throttle_sec": 60,
  "claude_bin": "<opcjonalnie, gdy `claude` nie jest w PATH>"
}
```

Plik jest **celowo poza repo** — token maszyny nie ma prawa trafić do gita.
Katalog danych sonda wyprowadza z `%LOCALAPPDATA%` / `~/.local/state` w czasie działania,
**nie** ze swojego położenia — więc nie ma znaczenia, gdzie leży sam skrypt.

Bez `config.json` sonda działa w **trybie tylko lokalnym**: mierzy i loguje, nic nie wysyła.
To jest legalny stan, nie awaria. Sygnalizator zablokowanej sesji (krok 7) jest wtedy
**włączony** i na Windows podnosi toasty — milknie tylko wysyłka. Wyłącza go
`"session_status": false`.

### 3. Handshake — sekrety sprawdzamy przed dotknięciem `settings.json`

Pusty POST rejestruje maszynę i wraca przed jakimkolwiek sprawdzaniem konta, więc testuje
dokładnie te dwa sekrety i nic więcej. `INGEST_URL`, `TOKEN` i `EDGE_KEY` to trzy wartości
wpisane przed chwilą do `config.json`:

```bash
curl -s -o /tmp/hs.txt -w '%{http_code}' -X POST "$INGEST_URL" \
  -H "Authorization: Bearer $TOKEN" -H "X-Ingest-Key: $EDGE_KEY" \
  -H 'Content-Type: application/json' -d '{}'
```

| Status | Ciało | Znaczenie |
|---|---|---|
| `200` | `{"ok":false,…,"batchId":N}` | Oba sekrety dobre. `ok:false` jest **poprawne** — pusty batch nie niesie próbek |
| `403` | **HTML od Apache** | Zły albo brakujący edge key. Nie parsuj ciała jako JSON przed sprawdzeniem statusu |
| `401` | `{"detail":{"reason":"invalid-token"}}` | Tokenu nie ma w `INGEST_TOKENS` — albo kontener został `restart`owany zamiast odtworzony (krok 1) |

**Dopóki nie ma 200, nie instalujemy hooków.**

### 4. Pod ścieżką z hooków stawiamy przekierowanie, nie kopię

Ścieżka
`%LOCALAPPDATA%\claude-usage-monitor\usage-probe.py` (Windows) albo
`~/.local/state/claude-usage-monitor/usage-probe.py` (Linux, macOS) jest **kontraktem** —
wskazuje na nią każdy wpis w `settings.json`. Leży tam kilkanaście linijek Pythona, które
wykonują prawdziwą sondę spod `SRC`. To jest **cała treść pliku**, nie fragment:

```python
#!/usr/bin/env python3
"""Przekierowanie, nie sonda. Prawdziwy kod leży pod SRC."""
import os, runpy, sys

SRC = r"C:\sciezka\do\repo\client\usage-probe.py"   # pelna sciezka do pliku ze zrodlem

if not os.path.isfile(SRC):        # zasada 5: brak źródła to cisza, nie traceback
    sys.exit(0)
try:
    runpy.run_path(SRC, run_name="__main__")
except Exception:                  # NIE OSError — patrz niżej
    sys.exit(0)
```

Łapka jest **szeroka celowo**. `except OSError` łapie zerwany udział sieciowy i uśpiony
dysk, ale nie łapie obciętego zapisu: niepełne źródło pod `SRC` daje `SyntaxError` z `compile()`
w `runpy`, a to nie jest `OSError` — traceback szedłby wtedy do hooka przy każdym
z dwunastu zdarzeń, aż ktoś zauważy. `SystemExit` jest `BaseException`, więc
`sys.exit(main())` z sondy nadal przechodzi tędy na wylot i kod wyjścia się nie zmienia.
Nic ponad to nie jest ukrywane: ciało sondy ma własne `except Exception`, więc ta łapka
odpowiada wyłącznie za awarie **ładowania**.

Dzięki temu **edycja w repo działa natychmiast**, bez kopiowania po każdej zmianie. Docelowo
miało tam stać dowiązanie symboliczne, ale Windows odmawia jego utworzenia bez trybu dewelopera
albo praw administratora (`Administrator privilege required`) — przekierowanie robi to samo bez
żadnych uprawnień i tak samo na maszynie zdalnej. Różni je **wyłącznie `SRC`**: tutaj repo
projektu, na maszynie zdalnej katalog, do którego rozdano kopię. Sondę odnajduje po
`%LOCALAPPDATA%`, nie po `__file__`, więc przekierowanie niczego jej nie przesuwa.

Koszt, rozbity na składniki, bo jedna liczba na cały przebieg zawsze kłamie (mediana z 20–25
przebiegów, `SRC` na dysku sieciowym):

| składnik | ile |
|---|---|
| goły start CPythona — podłoga, nie do zejścia | 31–36 ms |
| sonda uruchomiona wprost, do wartownika rekurencji | 35,7 ms |
| to samo przez przekierowanie | 46,7 ms |
| **narzut samego przekierowania** (dodatkowy odczyt pliku z dysku sieciowego + `runpy`) | **~11 ms** |
| pełny przebieg kończący się na throttlu | 46,9 ms |
| odłączony `claude -p "/usage"` | ~3,4 s |

Przekierowanie nie jest więc darmowe, ale i tak ginie przy koszcie, którego dotyczy cała
konstrukcja: sonda **nie czeka** na `claude`, więc te ~3,4 s nie wchodzą do przebiegu.
Maszyna zdalna czyta `SRC` z dysku lokalnego i nie płaci nawet tych 11 ms.

Do wersji 6 dochodziły tu jeszcze 23 ms na importy `socket`, `hashlib`, `http.client`
i `urllib.parse` przy każdym przebiegu; od wersji 7 są leniwe, za throttlem.

**Te liczby mierzyć u siebie, nie przepisywać.** Maszyna, na której powstały, miała rozrzut
rzędu 30 ms między min a max, a pomiar składników rozjeżdża się z pomiarem całości nawet
trzykrotnie. Poprzednia
wersja tego akapitu twierdziła, że przebieg kończy się „po ~30 ms" — czyli poniżej podłogi
startu samego interpretera.

### 5. Hooki w `~/.claude/settings.json`

Każdy wpis jest identyczny — różni je wyłącznie zdarzenie, pod które trafia:

```json
{"type": "command", "async": true, "timeout": 10,
 "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}
```

Na Linuksie i macOS ten sam wpis z `python3` i ścieżką
`/home/<user>/.local/state/claude-usage-monitor/usage-probe.py`.

Zdarzeń jest **dziewięć** dla samego pomiaru: `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStop`, `Stop` oraz `Notification`
z `"matcher": "idle_prompt"`. Sygnalizator zablokowanej sesji dokłada trzy — patrz krok 7.
W całości:

```json
"hooks": {
  "PostToolUse":        [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "Stop":               [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "SessionStart":       [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "UserPromptSubmit":   [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "PreToolUse":         [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "PostToolUseFailure": [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "PostToolBatch":      [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "SubagentStop":       [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "Notification":       [{"matcher": "idle_prompt", "hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}]
}
```

**Scalaj, nigdy nie nadpisuj.** `settings.json` trzyma model, motyw i cudze hooki —
jedyna dopuszczalna edycja to dopisanie się do istniejących tablic.

`PostToolUse` z `"async": true` to główny wyzwalacz — zmierzone 0,24 ms narzutu. `Stop` domyka
lukę dla tur bez wywołań narzędzi, reszta skraca lukę po wznowieniu sesji. Przy throttlu 60 s
gęstsze wyzwalanie nic nie kosztuje.

**Ścieżka musi być pełna, `%LOCALAPPDATA%` nie zadziała.** Hooki na Windows uruchamiane są
przez Git Bash (widać to w `claude --debug`: `Using bash path: C:\Program Files\Git\bin\bash.exe`),
a bash nie rozwija składni `%ZMIENNA%`.

**`UserPromptSubmit` wymaga `"async": true`.** Synchronicznie blokuje wysłanie promptu aż do
30-sekundowego timeoutu — to jest zdarzenie, w którym hook *może* dopisać kontekst do promptu,
więc Claude Code czeka na jego wynik. Z `async` nie ma na co czekać.

### 6. Weryfikacja — i co znaczy „działa"

**Pierwszy przebieg nie mierzy.** Sonda nigdy nie czeka na proces potomny: `claude -p "/usage"`
trwa ~3,4 s, a wynik konsumuje **następny** przebieg. Poprawna instalacja przez jeden cykl
wygląda więc na pustą — nie diagnozuj przed drugim uruchomieniem.

1. Uruchom komendę hooka ręcznie, odczekaj ~60 s (throttle) i uruchom drugi raz.
   Na Windows rób to **z PowerShella** albo z `MSYS_NO_PATHCONV=1` — patrz pułapka Git Basha
   wyżej; z Git Basha dostaniesz płatną turę modelu zamiast darmowej komendy.
2. `usage-samples.jsonl` w katalogu danych rośnie, a ostatnia linia ma blok `measurement`
   ze `source: cli_merged` albo `cli_usage_cache`.
3. `spool.jsonl` zostaje pusty. **Rosnący spool przy zdrowym logu to jedyny sygnał, że sama
   wysyłka pada** — wyniku POST-a sonda celowo nie loguje.
4. Maszyna widoczna w panelu; `scriptVersion` w `/api/machines` mówi, na jakim kodzie chodzi.

Wyłączenie albo odinstalowanie: [`docs/RUNBOOK.md`](../docs/RUNBOOK.md#wyłączenie-zbierania),
sekcja „Wyłączenie zbierania".

### 7. Sygnalizator zablokowanej sesji

Sonda wykrywa też moment, w którym Claude Code stanął i **czeka na Ciebie**: prośba o zgodę
na narzędzie, `AskUserQuestion`, `ExitPlanMode`. Podnosi wtedy toast na tej maszynie i wysyła
alert do monitora, żeby panel na biurku pokazał kartę i znacznik przy koncie.

Nie ma osobnego skryptu ani drugiego przekierowania — to ta sama sonda, ten sam proces.

**Co przy tym czyta** (poza `config.json` i własnym katalogiem stanu), zawsze tylko do odczytu
i wyłącznie w gałęzi zamiatania, czyli na `UserPromptSubmit`, `Stop`, `SessionEnd`
i `SessionStart` — i tylko wtedy, gdy katalog stanu **nie jest pusty**:

- **`~/.claude/sessions/`** (albo `$CLAUDE_CONFIG_DIR/sessions/`) — rejestr żywych sesji, który
  Claude Code prowadzi sam: plik `<pid>.json` z polem `sessionId`. Wpis blokady sesji, której
  w rejestrze już nie ma, jest wpisem po sesji, która nie żyje, i wtedy gaśnie. Sonda **nigdy**
  nie sprawdza pidów: `os.kill(pid, 0)` na Windows mapuje się na `TerminateProcess`, czyli
  ubijałaby sesje Claude Code. Liczy się sama obecność rekordu.
- **transkrypt sesji** (`~/.claude/projects/<slug>/<session_id>.jsonl`, dla subagenta
  `…/<session_id>/subagents/agent-<agent_id>.jsonl`) — ostatnie 32 KB, żeby sprawdzić, czy
  wywołanie ma już `tool_result`. Uchwyt trzymany na czas jednego odczytu i nic dłużej.

Gdy któregokolwiek z tych źródeł nie da się przeczytać w całości, sonda **nie kasuje niczego** —
„nie wiem" nigdy nie znaczy „pusto" — i zostawia jedną linię w `usage-samples.jsonl`
(`alert_skip`), żeby dało się odróżnić zepsuty mechanizm od braku pracy.

**Konfiguracja** — cztery klucze dopisane do istniejącego `config.json`. Token i edge key są
**te same**, bo autoryzuje się ta sama maszyna:

```json
{
  "session_status": true,
  "alert_url": "https://usage.example.org/claude-usage/api/session-alert",
  "toast": true,
  "blocked_ttl_sec": 86400
}
```

- **`session_status`** — wyłącznik całości, domyślnie włączony. `false` gasi pliki stanu,
  toast i wysyłkę; pomiar limitów działa dalej. Wyłączenie **gasi też to, co akurat wisi**:
  przy pierwszym zdarzeniu kasuje wpisy i wysyła jeden pusty zbiór, więc znacznik na panelu
  znika od razu, a nie po dobie.
- Bez **`alert_url`** sygnalizator pracuje **tylko lokalnie**: pisze pliki stanu i podnosi
  toast, nic nie wysyła. To legalny stan i przy okazji kanał awaryjny — toast dociera nawet
  wtedy, gdy serwer leży.
- **`"toast": false`** wyłącza samo powiadomienie; alerty na panel jadą dalej.

Po stronie panelu jest **osobna** flaga `session_alerts` w `panel.json` i tak ma być: sonda
stoi na maszynie z sesją, panel na biurku, a to bywają różne maszyny. `session_status` gasi
**źródło**, `session_alerts` **wyświetlanie**.

**Hooki** — do dziewięciu zdarzeń sondy dochodzą **trzy**, w tej samej postaci:

```json
"PermissionRequest":  [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
"PermissionDenied":   [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
"SessionEnd":         [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}]
```

Razem **dwanaście** zdarzeń. `PermissionRequest` jest hookiem **decyzyjnym** — kontrakt brzmi
„exit 0 bez JSON-a na stdout = oddaj decyzję człowiekowi", a sonda nigdy nic nie wypisuje
(pilnuje tego `test_sonda_milczy_na_stdout_przy_permission_request`).

**`Notification` NIE jest tu używane.** Zmierzone: w rozszerzeniu VS Code nie odpala się —
zero wystąpień na 2 395 próbkach, potwierdza to niezależnie zgłoszenie
[#29928](https://github.com/anthropics/claude-code/issues/29928). Payload i tak nie ma
`tool_name` ani `tool_use_id`, więc nie dałoby się po nim domknąć wpisu.
`PermissionRequest` pokrywa wszystkie trzy stany i odpala **wyłącznie** wtedy, gdy człowiek
jest naprawdę pytany.

**Weryfikacja:**

1. Wywołaj coś zza bramki uprawnień (np. `Bash` poza katalogiem projektu) i **nie
   odpowiadaj**. W `%LOCALAPPDATA%\claude-usage-monitor\session-status\` musi pojawić się
   plik `<session_id>__main__<klucz>.json`, a na ekranie toast.
2. Odpowiedz „tak" — plik znika.
3. Odpowiedz „nie" albo naciśnij Esc — plik **zostaje**, bo odmowa nie generuje żadnego
   zdarzenia hooka (zmierzone 5/5). Znika przy najbliższym zdarzeniu zamiatania, i to
   **dowolnej** sesji na tej maszynie, nie tylko tej zablokowanej: odmowa i Esc zapisują
   w transkrypcie `tool_result`, a sonda czyta ogon 32 KB transkryptu każdego wpisu
   (`closed_by_transcript`). To jedyna droga dla sesji, która po odmowie **zamilkła** —
   `Stop` nie odpala na przerwanej turze, więc bez tego wpis wisiałby do TTL 24 h.
   Nie domknie się w ten sposób wpis, którego rekord `tool_use` nie mieści się w ogonie
   (`Write` z dużą treścią, ~1,3% przypadków) — taki czeka na TTL.
4. Panel: karta na pełnym ekranie, po `alert_takeover_sec` (domyślnie 5 min) znacznik
   akcentu przy nazwie konta. Okno należy do **zbioru**: jeśli w międzyczasie zablokuje
   się druga sesja, karta zostanie — z obiema blokadami — dopóki i ta druga się nie wypali.

Furtka awaryjna, gdyby coś się zawiesiło: `del %LOCALAPPDATA%\claude-usage-monitor\session-status\*`.

## Zasady, których nie wolno złamać

**1. Żadnego żądania do `api.anthropic.com`.** Ani jednego. To jest cały sens wersji 3.

**2. `accessToken` nie służy do uwierzytelniania niczego.** `.credentials.json` czytamy
wyłącznie po metadane planu (`subscriptionType`, `rateLimitTier`, `expiresAt`), tylko do
odczytu. Linia podziału leży przy **użyciu** tokena, nie przy odczycie pliku.

**3. Nigdy nie wołamy endpointu tokenowego.** Odświeżanie należy do Claude Code.

**4. Zero ciężkich importów w ścieżce gorącej.** Tylko biblioteka standardowa. Na górze
pliku stoi wyłącznie `sys, os, json, time, re` — reszta jest lokalna, w gałęziach, których
ścieżka gorąca **nie dotyka**: `shutil` i `subprocess` w pomiarze przez CLI, `ssl` w budowie
kontekstu, `http.client` z `urllib.parse` w `post()`, `socket` z `hashlib` przy składaniu
rekordu, `hashlib` w `call_key`, `base64` z `subprocess` w `toast()`. Te cztery z `post()`
i `record` zeszły z góry w wersji 7 i były warte 23 ms na każdym przebiegu.

Uwaga po scaleniu: importy sekcji alertu **nie** są „za throttlem" — sygnalizator biegnie
przed nim. Są za czymś innego rodzaju: odpalają się wyłącznie przy WEJŚCIU w blokadę i przy
WYJŚCIU z niej, czyli parę razy na blokadę, a nie przy każdym wywołaniu narzędzia. Ścieżka
gorąca (`PostToolUse` przy pustym katalogu stanu) kończy się na jednym `scandir` i nie
importuje niczego.

**Nie przenosić ich w górę ani nie dodawać użyć przed linią importu** —
`NameError`/`UnboundLocalError` połknie tu zasada 5 i maszyna przestanie raportować
bez jednego objawu.

**5. Nigdy nie rzuca wyjątkiem.** `except: sys.exit(0)` na najwyższym poziomie.

**6. Sonda nigdy nie czeka na proces potomny.** `claude -p "/usage"` trwa ~3,4 s. Wynik
konsumuje **następny** przebieg — dzięki temu hook kosztuje ~47 ms, a nie 3,4 s.

**7. Zapora przed rekurencją jest obowiązkowa.** Proces potomny to normalna sesja Claude Code
i odpala hook `Stop`. Sonda ustawia potomkowi `CUM_PROBE_CHILD=1` i przy tej zmiennej kończy
się natychmiast. **Sam throttle tego nie zatrzyma** — każdy potomek ma własny zegar.

## Jak to się zachowuje

- **Throttle 60 s** — znacznik zapisywany **przed** wywołaniem, żeby równoległe hooki nie
  zrobiły stampede. Okno wyścigu jest niezerowe: sporadycznie przejdą dwa wywołania zamiast
  jednego. Nieszkodliwe przy tej skali.
- **Bootstrap** — przy pierwszym uruchomieniu na maszynie `cachedUsageUtilization` jeszcze nie
  istnieje. Sonda loguje `brak-cache`, odpala `/usage` i pomiar pojawia się w następnym cyklu.
- **Strażnik wygasłego okna** — `resets_at` mamy tylko z cache. Gdy okno zresetuje się między
  zapisem cache a odczytem, para (procent, `resets_at`) jest sprzeczna. Przy świeżym procencie
  zerujemy `resets_at`; bez świeżego wyrzucamy całą serię z cyklu — publikacja dawnych 95%
  jako bieżących byłaby grubym błędem, bo realnie jest ~0%.
- **Strażnik absurdalnych wartości** — procent > 101 jest odrzucany, nie obcinany do 100.
  Claude Code potrafi wyciec epoch z `resets_at` w pole procentu (#52326), a obcięcie
  zamieniłoby ewidentną awarię w wiarygodnie wyglądający fałszywy alarm.
- **Spool** — nieudany POST ląduje w `spool.jsonl` i jedzie jako `backlog[]` przy następnym
  udanym. Spool obcinany **dopiero po potwierdzeniu** liczby przyjętych wpisów, więc awaria
  w połowie nic nie gubi. Cap 5000 linii.
- **Log lokalny zawsze**, niezależnie od tego, czy POST się udał.

## Diagnostyka

```bash
python client/analyze-samples.py          # tempo zmian, błędy, konta, przełączenia
```

Pliki robocze w `%LOCALAPPDATA%\claude-usage-monitor\`:
`usage-samples.jsonl` (log), `spool.jsonl` (zaległości), `last-probe.txt` (throttle),
`usage-cli.json` (zrzut stdout z `/usage`), `config.json`, `usage-probe.py` (**przekierowanie**
do źródła, nie sonda — patrz Instalacja), `session-status\` (po jednym pliku na trwającą
blokadę) i `session-status-posted.txt` (odcisk ostatnio wysłanego zbioru — stąd wiadomo,
że nie trzeba wysyłać ponownie).

Pole `measurement` w każdym wpisie logu mówi, skąd wzięty jest pomiar: `source`
(`cli_merged` / `cli_usage_cache`), `cache_age_s`, `fresh_age_s`, `fresh_at` (czas zrzutu),
`fresh_covered` (które serie wzięły wartość ze zrzutu), `sent_at` (kotwica wieku — patrz
niżej), `fresh_skip` (czemu zrzut nie został użyty), `dropped` (co odrzuciły strażniki),
`spawn_error`.

### Datowanie: dwa źródła, dwa czasy

`captured_at` to **zawsze** czas cache'u (`fetchedAtMs`), bo z cache pochodzi wszystko —
w tym `spend` i `extra_usage`, których zrzut nie zawiera nigdy. Czas zrzutu jedzie osobno,
jako `fresh_at`, i dotyczy wyłącznie serii wymienionych w `fresh_covered`.

Sklejenie obu w jeden stempel (co robiła sonda v4) odmładzało `spend` i `extra_usage` o całą
różnicę wieków — do godziny. Backend rozstrzyga po tym stemplu, który odczyt jest bieżący,
więc maszyna ze starszym cache'em, ale świeższym zrzutem cofała stan właśnie tych dwóch serii.
Guard monotoniczności ich nie broni: wymaga znanej granicy okna, a te dwie serie `resets_at`
nie mają **nigdy**.

Sam moment pomiaru wylicza **serwer**, nie klient:

```
offset      = arrived_at − sent_at              # raz na żądanie
measured_at = min(ts + offset, arrived_at)      # per seria
```

czyli `received_at − wiek`. Zegar ścienny maszyny nie wchodzi do rachunku — liczy się tylko
różnica `sent_at − ts`, a ta jest w obrębie jednego zegara. Wpisy ze spoola przechodzą tą samą
formułą: ich `sent_at` pochodzi z chwili nieudanej próby, więc wiek liczy się sam.

Wpis, w którym pomiar jest **nowszy** niż `sent_at`, jest odrzucany w całości — zegar cofnął
się między zapisem a wysyłką, więc datowanie jest niepewne. Wpis jest przy tym liczony do
`backlogAccepted`, żeby spool dało się obciąć.

### Zrzut starszy od cache'u

Scalanie zakłada, że zrzut jest świeższy, ale zrzutowi wolno mieć do 900 s, a cache odświeża
w tym czasie zwykła praca w Claude Code — kolejność potrafi się odwrócić (zmierzone: 2 razy
na 1646 pomiarów, do −105 s). Wtedy zrzut jest **ignorowany w całości** (`fresh_skip:
zrzut-starszy-od-cache`).

Groźny jest reset okna między zrzutem a cache'em: procent poszedłby ze zrzutu, czyli sprzed
resetu (95%), a `resets_at` mamy **wyłącznie** z cache, czyli już z nowego okna. Strażnik
wygasłego okna tego nie łapie — granica jest ważna, więc nic nie wygląda na sprzeczne —
i publikowalibyśmy 95% przeciwko oknu, w którym realnie jest ~1%. Koszt odrzucenia jest zerowy:
zostaje wartość z cache, która jest nowsza i dokładniejsza (stdout obcina do liczb całkowitych).

**Certyfikaty na Windows:** magazyn CA używany przez Pythona odrzuca łańcuch Let's Encrypt
niektórych hostów z błędem `certificate has expired`, mimo że każde ogniwo jest ważne — `curl`
przechodzi, Python nie. Klient używa `certifi`, gdy jest dostępne. Gdyby go brakowało, wskaż
własny plik przez `"ca_bundle"` w `config.json`. **Nie wyłączaj weryfikacji.**
