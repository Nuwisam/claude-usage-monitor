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
| `usage-probe.py` | **Źródło prawdy.** Sonda wpinana w hooki — tu się ją edytuje |
| `analyze-samples.py` | Analiza lokalnego logu — tempo zmian, błędy, konta |

Tutaj jest **źródło**: ładuje je `backend/tests/test_probe_parsing.py` po sztywnej ścieżce
i tutaj trafiają zmiany. Każda kopia rozdana na maszyny jest **wydaniem** i **może być
starsza** od HEAD — to poprawne, bo publikacja ma być decyzją, nie skutkiem ubocznym pushu;
automat wypchnąłby na maszyny zdalne wersję roboczą.

Dlatego **każda zmiana zachowania wymaga podbicia `SCRIPT_VERSION`**: wersja jedzie w każdym
batchu, więc różnicę widać w `/api/machines` po `scriptVersion`. Bez podbicia dwie różne
sondy są nierozróżnialne.

## Instalacja

**1. Pod ścieżką z hooków stawiamy przekierowanie, nie kopię.** Ścieżka
`%LOCALAPPDATA%\claude-usage-monitor\usage-probe.py` jest **kontraktem** — wskazuje na nią każdy
wpis w `settings.json`. Leży tam kilkanaście linijek Pythona, które wykonują prawdziwą sondę
spod `SRC`:

```python
SRC = r"C:\sciezka\do\repo\client\usage-probe.py"   # pelna sciezka do pliku w repo
if not os.path.isfile(SRC):        # zasada 5: brak źródła to cisza, nie traceback
    sys.exit(0)
runpy.run_path(SRC, run_name="__main__")
```

Dzięki temu **edycja w repo działa natychmiast**, bez kopiowania po każdej zmianie. Docelowo
miało tam stać dowiązanie symboliczne, ale Windows odmawia jego utworzenia bez trybu dewelopera
albo praw administratora (`Administrator privilege required`) — przekierowanie robi to samo bez
żadnych uprawnień i tak samo na maszynie zdalnej. Różni je **wyłącznie `SRC`**: tutaj repo
projektu, na maszynie zdalnej katalog, do którego rozdano kopię. Sondę odnajduje po
`%LOCALAPPDATA%`, nie po `__file__`, więc przekierowanie niczego jej nie przesuwa.

Koszt: jeden odczyt pliku więcej, a przy `SRC` na dysku sieciowym +19 ms na wywołanie (zmierzone:
27 ms lokalnie vs 46 ms z dysku sieciowego). W wersji 3 to margines — dominującym kosztem jest odpalenie
`claude` (~3,4 s, odłączone), a większość przebiegów kończy się na throttlu po ~30 ms. Maszyna
zdalna czyta z dysku lokalnego i nie płaci nawet tego.

**2. Konfiguracja** — `%LOCALAPPDATA%\claude-usage-monitor\config.json` (Windows)
albo `~/.local/state/claude-usage-monitor/config.json` (Linux):

```json
{
  "ingest_url": "https://usage.example.org/claude-usage/api/ingest",
  "ingest_token": "<token TEJ maszyny z INGEST_TOKENS>",
  "edge_key": "<INGEST_EDGE_KEY>",
  "throttle_sec": 60,
  "claude_bin": "<opcjonalnie, gdy `claude` nie jest w PATH>"
}
```

Plik jest **celowo poza repo** — token maszyny nie ma prawa trafić do gita.
Bez `config.json` sonda działa w **trybie tylko lokalnym**: mierzy i loguje, nic nie wysyła.

**3. Hooki** w `~/.claude/settings.json`:

```json
"hooks": {
  "PostToolUse": [{"hooks": [{"type": "command", "async": true, "timeout": 10,
     "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "Stop": [{"hooks": [{"type": "command", "timeout": 10,
     "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}]
}
```

**Ścieżka musi być pełna, `%LOCALAPPDATA%` nie zadziała.** Hooki na Windows uruchamiane są
przez Git Bash (widać to w `claude --debug`: `Using bash path: C:\Program Files\Git\bin\bash.exe`),
a bash nie rozwija składni `%ZMIENNA%`.

`PostToolUse` z `"async": true` to główny wyzwalacz — zmierzone 0,24 ms narzutu. `Stop` domyka
lukę dla tur bez wywołań narzędzi. Zarejestrowanych jest dziewięć zdarzeń
(dochodzą `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUseFailure`, `PostToolBatch`,
`SubagentStop`, `Notification`/`idle_prompt`) — przy throttlu 60 s gęstsze wyzwalanie nic nie
kosztuje, a skraca lukę po wznowieniu sesji.

**`UserPromptSubmit` wymaga `"async": true`.** Synchronicznie blokuje wysłanie promptu aż do
30-sekundowego timeoutu — to jest zdarzenie, w którym hook *może* dopisać kontekst do promptu,
więc Claude Code czeka na jego wynik. Z `async` nie ma na co czekać.

**4. Wymagane:** `claude` w `PATH` (albo `claude_bin` w konfiguracji). Bez tego sonda nie ma
czym zlecić pomiaru i loguje `brak-claude-w-path`.

## Zasady, których nie wolno złamać

**1. Żadnego żądania do `api.anthropic.com`.** Ani jednego. To jest cały sens wersji 3.

**2. `accessToken` nie służy do uwierzytelniania niczego.** `.credentials.json` czytamy
wyłącznie po metadane planu (`subscriptionType`, `rateLimitTier`, `expiresAt`), tylko do
odczytu. Linia podziału leży przy **użyciu** tokena, nie przy odczycie pliku.

**3. Nigdy nie wołamy endpointu tokenowego.** Odświeżanie należy do Claude Code.

**4. Zero ciężkich importów w ścieżce gorącej.** Tylko biblioteka standardowa.
`subprocess` i `shutil` importowane lokalnie, w gałęzi wykonywanej raz na 60 s.

**5. Nigdy nie rzuca wyjątkiem.** `except: sys.exit(0)` na najwyższym poziomie.

**6. Sonda nigdy nie czeka na proces potomny.** `claude -p "/usage"` trwa ~3,4 s. Wynik
konsumuje **następny** przebieg — dzięki temu hook kosztuje ~36 ms, a nie 3,4 s.

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
do źródła, nie sonda — patrz Instalacja).

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
