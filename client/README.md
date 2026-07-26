# Klient — sonda limitów

Skrypt uruchamiany z hooków Claude Code. Czyta bieżący token **lokalnie**, odpytuje
`GET /api/oauth/usage` i wysyła do monitora **sam wynik pomiaru**. Token nigdy nie opuszcza
maszyny.

## Pliki

| Plik | Rola |
|---|---|
| `usage-probe.py` | **Produkcyjny.** Sonda wpinana w hooki |
| `analyze-samples.py` | Analiza lokalnego logu — tempo zmian, błędy, konta |
| `probe-usage.py` | Diagnostyczny. Jedno wywołanie, drukuje całą odpowiedź |
| `poc-dump.py` | Statusline z kroku 0. Działa **tylko w trybie terminalowym** CLI |

## Instalacja

**1. Skopiuj na dysk LOKALNY.** Uruchomienie z dysku sieciowego kosztuje +19 ms przy każdym
wywołaniu (zmierzone: 27 ms lokalnie vs 46 ms z dysku sieciowego), a skrypt startuje przy każdym użyciu
narzędzia przez Claude Code.

```powershell
Copy-Item client\usage-probe.py X:\Projekty\repozytorium skilli\tools\usage-probe.py
```

**2. Konfiguracja** — `%LOCALAPPDATA%\claude-usage-monitor\config.json` (Windows)
albo `~/.local/state/claude-usage-monitor/config.json` (Linux):

```json
{
  "ingest_url": "https://usage.example.org/claude-usage/api/ingest",
  "ingest_token": "<token TEJ maszyny z INGEST_TOKENS>",
  "edge_key": "<INGEST_EDGE_KEY>",
  "throttle_sec": 60
}
```

Plik jest **celowo poza repo** — token maszyny nie ma prawa trafić do gita.
Bez `config.json` sonda działa w **trybie tylko lokalnym**: mierzy i loguje, nic nie wysyła.

**3. Hooki** w `~/.claude/settings.json`:

```json
"hooks": {
  "PostToolUse": [{"hooks": [{"type": "command", "async": true, "timeout": 10,
     "command": "python \"X:/Projekty/repozytorium skilli/tools/usage-probe.py\""}]}],
  "Stop": [{"hooks": [{"type": "command", "timeout": 10,
     "command": "python \"X:/Projekty/repozytorium skilli/tools/usage-probe.py\""}]}]
}
```

`PostToolUse` z `"async": true` to główny wyzwalacz — jedyne zdarzenie z prawdziwym
fire-and-forget, zmierzone 0,24 ms narzutu. `Stop` domyka lukę dla tur bez wywołań narzędzi.
**`UserPromptSubmit` odpada** — blokuje wysłanie promptu z 30-sekundowym timeoutem.

## Zasady, których nie wolno złamać

**1. `.credentials.json` wyłącznie do odczytu.** Nigdy nie zapisujemy.

**2. Nigdy nie wołamy endpointu tokenowego.** Odświeżanie należy do Claude Code. Wygasły
token → pomijamy pomiar, logujemy `token-wygasl`, Claude Code odświeży go sam przy normalnej
pracy. To jest cały mechanizm, dzięki któremu nie ma tu ryzyka utraty konta.

**3. Zero ciężkich importów.** Tylko biblioteka standardowa. `import httpx` to ~150 ms —
przy starcie interpretera 27 ms to pięciokrotność całego budżetu.

**4. Nigdy nie rzuca wyjątkiem.** `except: sys.exit(0)` na najwyższym poziomie.

**5. Throttle jest obowiązkowy.** `PostToolUse` odpala się przy każdym narzędziu, a wywołanie
sieciowe kosztuje ~500 ms.

## Jak to się zachowuje

- **Throttle 60 s** — znacznik zapisywany **przed** wywołaniem, żeby równoległe hooki nie
  zrobiły stampede. Okno wyścigu jest niezerowe: sporadycznie przejdą dwa wywołania zamiast
  jednego. Nieszkodliwe przy tej skali.
- **Spool** — nieudany POST ląduje w `spool.jsonl` i jedzie jako `backlog[]` przy następnym
  udanym. Spool obcinany **dopiero po potwierdzeniu** liczby przyjętych wpisów, więc awaria
  w połowie nic nie gubi. Cap 5000 linii.
- **Cache konta po mtime** — `/login` przepisuje `.claude.json`, więc unieważnienie po mtime
  **jest** mechanizmem wykrywania przełączenia konta.
- **Log lokalny zawsze**, niezależnie od tego, czy POST się udał.

## Diagnostyka

```bash
python client/analyze-samples.py          # tempo zmian, 429, konta, przełączenia
python client/probe-usage.py              # jedno wywołanie, cała odpowiedź na ekran
```

Pliki robocze w `%LOCALAPPDATA%\claude-usage-monitor\`:
`usage-samples.jsonl` (log), `spool.jsonl` (zaległości), `last-probe.txt` (throttle),
`oauth-account.cache.json` (cache tożsamości), `config.json`.

**Certyfikaty na Windows:** magazyn CA używany przez Pythona odrzuca łańcuch Let's Encrypt
niektórych hostów z błędem `certificate has expired`, mimo że każde ogniwo jest ważne — `curl`
przechodzi, Python nie. Klient używa `certifi`, gdy jest dostępne. Gdyby go brakowało, wskaż
własny plik przez `"ca_bundle"` w `config.json`. **Nie wyłączaj weryfikacji.**
