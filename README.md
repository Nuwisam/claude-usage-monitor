# Claude Usage Monitor

Podgląd **aktualnych limitów Claude** dla wielu kont (Max i Team), z historią w bazie.

Dane zbiera sonda uruchamiana z hooka Claude Code **na maszynie użytkownika**. Sonda
**nie wysyła żadnego żądania do `api.anthropic.com`** — pomiar zleca samemu Claude Code
(`claude -p "/usage"`, zero zużycia limitu) i czyta wynik z dysku, po czym wysyła do monitora
sam wynik pomiaru.

Żaden token nie opuszcza maszyny i **żaden nie jest używany do uwierzytelniania czegokolwiek**
po naszej stronie: żądanie wykonuje pierwszorzędny klient własnym, samodzielnie odświeżanym
tokenem. Endpoint tokenowy nie jest wołany nigdy, więc nie ma tu głównego wektora utraty
konta — rotacji jednorazowego refresh tokenu.

## Stan

| Element | Stan |
|---|---|
| Sonda + hooki (`PostToolUse` async, `Stop`) | działa |
| Backend + MariaDB w Compose | działa |
| Brama autoryzacji: `none` / `header` / `verify` | działa |
| API odczytu, kontrakt v3 | działa |
| UI — **Live** i **Historia** | działa |
| Panel biurkowy AX206 (SSE) | działa |

Diagnostyka (zdarzenia, batche, maszyny, surowe payloady) została świadomie przy `curl` —
patrz [docs/API.md](docs/API.md) § 10.

Typowe wdrożenie stawia to za reverse proxy z SSO, ale to jedna z trzech możliwości, nie założenie
projektu. Instalacja na własnej maszynie nie wymaga niczego przed backendem.

## Architektura

```
maszyna z Claude Code                              serwer
┌──────────────────────────────┐
│ hook PostToolUse (async)     │
│ hook Stop                    │
│        ↓                     │      HTTPS + Bearer + X-Ingest-Key
│ client/usage-probe.py        │ ───────────────────────────────────►  Apache
│  · zleca `claude -p /usage`  │                                         │
│  · czyta cache + stdout      │              /claude-usage/api/ingest ──► backend
│  · throttle 60 s             │              /claude-usage/api/*     ──► backend (brama)
│  · spool przy awarii         │              /claude-usage/          ──► backend (statyki)
└──────────────────────────────┘                          ┌──────────────┴──────────────┐
                                                          │ backend  FastAPI :8000       │
                                                          │   + zbudowany frontend       │
                                                          │ mariadb  (sieć internal)     │
                                                          └──────────────────────────────┘
```

**Dwa kontenery, nie trzy.** Statyki UI serwuje backend (etap `node` w jego Dockerfile).
Osobny nginx z `auth_request` niesie pułapkę, w której `$scheme` w kontenerze to `http`,
a `$request_uri` nie zawiera prefiksu — przez co powrót po zalogowaniu wyrzuca na korzeń
serwisu. Tu bramą zostaje backend, budujący `redirect_url` z jawnych `PUBLIC_ORIGIN`
+ `APP_BASE_PATH`.

## Dlaczego tak, a nie inaczej

Podejścia sprawdzone i odrzucone:

- **Statusline hook** — byłby darmowy (zero wywołań API), ale **nie działa w rozszerzeniu
  VS Code**; to funkcja wyłącznie CLI/TUI ([#55643](https://github.com/anthropics/claude-code/issues/55643),
  zamknięte jako `not_planned`). Repo referencyjne stoi właśnie na tym mechanizmie i dlatego
  nie jest dla nas ścieżką. Daje też wyłącznie `five_hour` i `seven_day` — bez kaskady, kwot
  w USD, `is_active` i `severity`.
- **Poller na serwerze** — wymagałby trzymania tokenów i **rotowania refresh tokenu**, czyli
  dokładnie tego, co powoduje utratę konta.
- **Parsowanie lokalnych JSONL** — niedokładne; zgłoszenie CCUM #202 dokumentuje rozjazd
  1.4% vs 12% wobec serwera.
- **Własne wołanie `/api/oauth/usage`** — działało (wersja 2 sondy), ale wymagało użycia
  tokena OAuth i podszycia się pod `User-Agent: claude-code/…`. Warunki Anthropic zakazują
  używania tych tokenów *„in any other product, tool, or service"*, bez wyjątku dla odczytu.
- **`--debug api` jako źródło** — sprawdzone, **nie zrzuca ciała odpowiedzi** endpointu.
  Gdyby zrzucał, cała dwuźródłowość niżej byłaby zbędna.

### Co robimy zamiast tego

Pomiar zleca **sam Claude Code**: `claude -p "/usage"`. Komenda jest zarejestrowana dwukrotnie
i wariant `supportsNonInteractive` zwraca `{type:"text"}` → `shouldQuery=false`, więc **żaden
turn modelu się nie odbywa**. Zmierzone: `num_turns=0`, `duration_api_ms=0`,
`total_cost_usd=0`, ~3,4 s na wywołanie. Pomiar limitu nie zużywa limitu.

Sonda czyta wynik z dwóch miejsc, bo świeżość i kompletność leżą gdzie indziej:

| Źródło | Świeżość | Zawartość |
|---|---|---|
| stdout `/usage` | świeże przy każdym wywołaniu | procenty głównych okien |
| `~/.claude.json` → `cachedUsageUtilization` | ≤ 5 min | pełne surowe ciało odpowiedzi |

Pięciominutowy throttle w Claude Code dotyczy **zapisu na dysk**, nie pobrania — stąd ten
rozdział. Sonda scala jedno z drugim, a wynik ma dokładnie ten sam kształt co dawna odpowiedź
HTTP, więc parser backendu nie wymagał zmian.

## Kluczowe decyzje projektowe

**Otwarty zbiór serii.** Odpowiedź ma 17 kluczy najwyższego poziomu, z czego 5 nie było znanych
ani z walidatora w binarce Claude Code, ani z repo referencyjnego. Serie są wierszami w tabeli,
nie kolumnami — nowy bucket u Anthropic nie wymaga migracji.

**Limity są kaskadą.** Okno 5 h / tygodniowe → po wyczerpaniu kredyty (`extra_usage` / `spend`)
→ twardy blok. Dlatego `spend` jest serią pierwszej kategorii, a `limits[].is_active` mówi,
co realnie ogranicza *teraz*.

**Cztery stany świeżości, a `unknown` nigdy nie jest zerem.** Najgorszy tryb awarii to pokazanie
fałszywego, pewnie wyglądającego 0% — bo na tej podstawie odpalisz duże zadanie i trafisz
w ścianę. `ingest_batches` odróżnia „cisza klienta" (można wnioskować reset) od „klient działa,
ale brak próbek" (awaria — mówimy „nie wiem").

**Tożsamość konta z `oauthAccount.accountUuid`, nie z konfiguracji.** Na jednej maszynie
przełączasz konta przez `/login`, a `settings.json` jest wspólny — statyczny label przypisywałby
połowę próbek do złego konta i cicho zatruwał historię obu.

## Instalacja klienta na nowej maszynie

1. Pod ścieżką z hooków połóż **przekierowanie**, nie kopię sondy — wtedy zmiana u źródła
   działa od razu, bez kopiowania:
   ```python
   SRC = r"<pełna ścieżka do usage-probe.py>"
   if not os.path.isfile(SRC):
       sys.exit(0)
   runpy.run_path(SRC, run_name="__main__")
   ```
2. Utwórz `%LOCALAPPDATA%\claude-usage-monitor\config.json`:
   ```json
   {"ingest_url": "https://usage.example.org/claude-usage/api/ingest",
    "ingest_token": "<token TEJ maszyny z INGEST_TOKENS>",
    "edge_key": "<INGEST_EDGE_KEY>",
    "throttle_sec": 60}
   ```
3. W `~/.claude/settings.json` — **pełna ścieżka**, bo hooki idą przez Git Bash, który nie
   rozwija `%LOCALAPPDATA%`:
   ```json
   "hooks": {
     "PostToolUse": [{"hooks": [{"type": "command", "async": true, "timeout": 10,
        "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
     "Stop": [{"hooks": [{"type": "command", "timeout": 10,
        "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}]
   }
   ```
4. Wymagany `claude` w `PATH` (albo `claude_bin` w `config.json`).

Bez `config.json` sonda działa w trybie **tylko lokalnym** — loguje do
`%LOCALAPPDATA%\claude-usage-monitor\usage-samples.jsonl`, nic nie wysyła.
Analiza lokalna: `python client/analyze-samples.py`.

## Wdrożenie serwera

```bash
cp .env.example .env      # MARIADB_*, AUTH_MODE, INGEST_TOKENS
docker compose up -d --build
```

`AUTH_MODE` jest **wymagane i nie ma wartości domyślnej**. Bez niego kontener nie wstanie —
tak ma być, bo tylko Ty wiesz, czy przed backendem cokolwiek stoi.

**Lokalnie, bez niczego przed backendem.** `AUTH_MODE=none`. Port ląduje na
`127.0.0.1:8080`, więc UI działa pod <http://127.0.0.1:8080/claude-usage/> i nie jest
osiągalne z sieci. Nie zmieniaj `BACKEND_BIND` na `0.0.0.0`, dopóki trybem jest `none`.

**Za reverse proxy.** `AUTH_MODE=header` (proxy podaje adres w nagłówku — musi go *usuwać*
z żądań przychodzących) albo `AUTH_MODE=verify` (backend pyta usługę tożsamości).
W obu razach zdejmij publikowany port, bo proxy sięga kontenera po sieci dockerowej —
`BACKEND_BIND=` **nie wystarczy**, `:-` odpala się także na pustej wartości:

```yaml
# docker-compose.override.yml (nieśledzony)
services:
  claude_usage_monitor_backend:
    ports: !reset []
```

Przykładowy `Include` dla Apache leży w
[deploy/apache/](deploy/apache/claude-usage-monitor-include.conf.example);
`__INGEST_EDGE_KEY__` podstawiasz przy deployu. Krok po kroku:
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## Testy

```bash
cd backend
pip install -e ".[dev]"
pytest                    # zmienne środowiskowe ustawia tests/conftest.py

cd ../frontend && npm run typecheck
cd ../panel && pytest
```

271 testów backendu i 152 panelu, w tym normalizator i ścieżka zapisu uruchamiane na
**realnym payloadzie** z konta Max (`backend/tests/fixtures/usage_max.json`), nie na
wymyślonym. Kwoty rozliczeniowe w fixture'ach są przeskalowane; procenty zostały
oryginalne, więc `spend.percent` dalej zgadza się z `used/limit` obok.

Kilka z nich pilnuje błędów, które raz już przeszły niezauważone na produkcję — dedup i guard
monotoniczności przy kołyszącym się `resets_at`, fallback SPA zjadający błędy API, czas ze
strefą w parametrach `/history`, `unknown` renderowane jako zero, endpointy diagnostyczne
czytające kolumny usunięte migracją. Przy zmianach w tych okolicach zacznij od przeczytania ich nazw.

## Licencja

MIT. Projekt nie jest powiązany z Anthropic.

Sonda nie wysyła żadnego żądania do `api.anthropic.com` i nie używa tokena OAuth do
uwierzytelniania czegokolwiek — pomiar zleca Claude Code własnym kanałem. Kształt danych,
które czytamy z jego cache, pozostaje jednak nieudokumentowany i może się zmienić bez
ostrzeżenia — stąd archiwum surowych odpowiedzi i tolerancyjny parser.
