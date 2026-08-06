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
| Sonda + hooki (12 zdarzeń, `async`) | działa |
| Backend + MariaDB w Compose | działa |
| Brama autoryzacji: `none` / `header` / `verify` | działa |
| API odczytu, kontrakt v3 | działa |
| UI — **Live** i **Historia** | działa |
| Panele biurkowe przez SSE — AX206 i Turing rev A | działa |
| Alert „Claude czeka na Ciebie" — toast + karta i trójkąt na panelu | działa; karta czeka na przeprojektowanie |

Diagnostyka (zdarzenia, batche, maszyny, surowe payloady) została świadomie przy `curl` —
patrz [docs/API.md](docs/API.md) § 10.

Panele: instalacja, `panel.json` i **wskazanie modułu po łańcuchu portów USB** (`port_path`,
`--list`, `--identify`) — [`panel/README.md` → Instalacja](panel/README.md#instalacja).

Typowe wdrożenie stawia to za reverse proxy z SSO, ale to jedna z trzech możliwości, nie założenie
projektu. Instalacja na własnej maszynie nie wymaga niczego przed backendem.

## Architektura

```
maszyna z Claude Code                              serwer
┌──────────────────────────────┐
│ 12 hooków (async), m.in.     │
│ PostToolUse, PermissionRequest│
│        ↓                     │      HTTPS + Bearer + X-Ingest-Key
│ client/usage-probe.py        │ ───────────────────────────────────►  Apache
│  · zleca `claude -p /usage`  │                                         │
│  · czyta cache + stdout      │       /claude-usage/api/ingest        ──► backend
│  · throttle 60 s             │       /claude-usage/api/session-alert ──► backend
│  · spool przy awarii         │       /claude-usage/api/*             ──► backend (brama)
│  · alert zablokowanej sesji  │       /claude-usage/                  ──► backend (statyki)
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

**Zablokowana sesja to sygnał, nie dana.** Gdy Claude czeka na Twoją zgodę, odpowiedź albo
akceptację planu, tura po prostu stoi — i nic Ci tego nie powie, jeśli odszedłeś od biurka.
Sonda podnosi wtedy toast lokalnie i wysyła alert na panel. Idzie on
przez backend, bo **sesja może chodzić na maszynie zdalnej, a panel stoi lokalnie**, ale
**nie trafia do bazy**: blokada gaśnie, gdy klikniesz „tak", więc tabela oznaczałaby migrację
i cykl życia wierszy dla stanu, po którym nie ma zostać żaden ślad. Szczegóły:
[docs/API.md § 3.2](docs/API.md), instalacja: [`client/README.md`](client/README.md#instalacja).

## Instalacja klienta na nowej maszynie

**[`client/README.md` → Instalacja](client/README.md#instalacja).** Tam jest cała
procedura: wymagania, wydanie tokenu maszyny po stronie serwera, konfiguracja, handshake
sprawdzający sekrety przed dotknięciem `settings.json`, przekierowanie, hooki i weryfikacja.
Streszczenia tutaj celowo nie ma — rozjechałoby się z oryginałem.

Sonda bez konfiguracji **nie wysyła nic na zewnątrz**: mierzy i pisze do lokalnego
`usage-samples.jsonl`, i nie potrzebuje serwera. Do obejrzenia tego logu wystarczy
`python client/analyze-samples.py` — monitor nie jest do tego konieczny.

Jedno zastrzeżenie, bo bywa niespodzianką: **sygnalizator zablokowanej sesji działa też bez
konfiguracji** i na Windows podnosi wtedy toasty. Wysyłka milczy, powiadomienia nie. Gasi je
`"session_status": false` w `config.json`, a sam toast `"toast": false`.

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

324 testy backendu i 319 panelu, w tym normalizator i ścieżka zapisu uruchamiane na
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
