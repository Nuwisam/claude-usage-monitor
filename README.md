# Claude Usage Monitor

Podgląd **aktualnych limitów Claude** dla wielu kont (Max i Team), z historią w bazie.

Dane zbiera sonda uruchamiana z hooka Claude Code **na maszynie użytkownika** — tam, gdzie
leżą poświadczenia. Sonda odpytuje `GET /api/oauth/usage` bieżącym tokenem i wysyła do
monitora **sam wynik pomiaru**. Token nigdy nie opuszcza maszyny, a endpoint tokenowy nie
jest wołany nigdy — odświeżanie należy do Claude Code. Dzięki temu nie ma tu głównego
wektora utraty konta, czyli rotacji jednorazowego refresh tokenu.

## Stan: wdrożone

**<https://usage.example.org/claude-usage/>** — za SSO.

| Element | Stan |
|---|---|
| Sonda + hooki (`PostToolUse` async, `Stop`) | działa |
| Backend + MariaDB na `192.0.2.10` | działa |
| Apache `/claude-usage` + filtr brzegowy | działa |
| API odczytu za SSO, kontrakt v2 | działa |
| UI — **Live** i **Historia** | działa |

UI pokrywa dwa widoki z makiet. Diagnostyka (zdarzenia, batche, maszyny, surowe payloady)
została świadomie przy `curl` — patrz `docs/UI-HANDOUT.md` § 10.

## Architektura

```
maszyna z Claude Code                              192.0.2.10
┌──────────────────────────────┐
│ hook PostToolUse (async)     │
│ hook Stop                    │
│        ↓                     │      HTTPS + Bearer + X-Ingest-Key
│ client/usage-probe.py        │ ───────────────────────────────────►  Apache
│  · czyta token (read-only)   │                                         │
│  · GET /api/oauth/usage      │              /claude-usage/api/ingest ──► backend
│  · throttle 60 s             │              /claude-usage/api/*     ──► backend (SSO)
│  · spool przy awarii         │              /claude-usage/          ──► backend (statyki)
└──────────────────────────────┘                          ┌──────────────┴──────────────┐
                                                          │ backend  FastAPI :8000       │
                                                          │   + zbudowany frontend       │
                                                          │ mariadb  (sieć internal)     │
                                                          └──────────────────────────────┘
```

**Dwa kontenery, nie trzy.** Statyki UI serwuje backend (etap `node` w jego Dockerfile).
Host moze miec wyczerpane pule adresowe Dockera, a wariant z osobnym nginx-em i `auth_request` niesie
pułapkę, w której `$scheme` w kontenerze to `http`, a `$request_uri` nie zawiera prefiksu —
przez co powrót po zalogowaniu wyrzuca na korzeń serwisu. Tu bramą SSO zostaje backend,
budujący `redirect_url` z jawnych `PUBLIC_ORIGIN` + `APP_BASE_PATH`.

## Dlaczego tak, a nie inaczej

Trzy podejścia sprawdzone i odrzucone w kroku 0 (`docs/POC-FINDINGS.md`):

- **Statusline hook** — byłby darmowy (zero wywołań API), ale **nie działa w rozszerzeniu
  VS Code**; to funkcja wyłącznie CLI/TUI ([#55643](https://github.com/anthropics/claude-code/issues/55643)).
- **Poller na serwerze** — wymagałby trzymania tokenów i **rotowania refresh tokenu**, czyli
  dokładnie tego, co powoduje utratę konta.
- **Parsowanie lokalnych JSONL** — niedokładne; zgłoszenie CCUM #202 dokumentuje rozjazd
  1.4% vs 12% wobec serwera.

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

1. Skopiuj `client/usage-probe.py` na **dysk lokalny** (nie sieciowy — zmierzone +19 ms na
   wywołanie z dysku sieciowego).
2. Utwórz `%LOCALAPPDATA%\claude-usage-monitor\config.json`:
   ```json
   {"ingest_url": "https://usage.example.org/claude-usage/api/ingest",
    "ingest_token": "<token TEJ maszyny z INGEST_TOKENS>",
    "edge_key": "<INGEST_EDGE_KEY>",
    "throttle_sec": 60}
   ```
3. W `~/.claude/settings.json`:
   ```json
   "hooks": {
     "PostToolUse": [{"hooks": [{"type": "command", "async": true, "timeout": 10,
        "command": "python \"X:/Projekty/repozytorium skilli/tools/usage-probe.py\""}]}],
     "Stop": [{"hooks": [{"type": "command", "timeout": 10,
        "command": "python \"X:/Projekty/repozytorium skilli/tools/usage-probe.py\""}]}]
   }
   ```

Bez `config.json` sonda działa w trybie **tylko lokalnym** — loguje do
`%LOCALAPPDATA%\claude-usage-monitor\usage-samples.jsonl`, nic nie wysyła.
Analiza lokalna: `python client/analyze-samples.py`.

## Wdrożenie serwera

```bash
cd /var/lib/claude-usage-monitor
cp .env.example .env      # MARIADB_*, INGEST_TOKENS, INGEST_EDGE_KEY, ALLOWED_EMAILS
docker compose up -d --build

EDGE=$(grep -oP 'INGEST_EDGE_KEY=\K.*' .env)
sed "s|__INGEST_EDGE_KEY__|$EDGE|" deploy/apache/claude-usage-monitor-include.conf.example \
    > /etc/apache2/sites-available/claude-usage-monitor-include.conf
# dopisz Include do sites-available/example_org-ssl.conf
apachectl configtest && systemctl reload apache2
```

## Testy

```bash
cd backend
pip install -e ".[dev]"
DATABASE_URL="sqlite+aiosqlite:///:memory:" INGEST_TOKENS="t:m" ALLOWED_EMAILS="a@b.pl" pytest

cd ../frontend && npm run typecheck
```

107 testów, w tym normalizator i ścieżka zapisu uruchamiane na **realnym payloadzie** z konta
Max (`backend/tests/fixtures/usage_max.json`), nie na wymyślonym.

Kilka z nich pilnuje błędów, które raz już przeszły niezauważone na produkcję — dedup i guard
monotoniczności przy kołyszącym się `resets_at`, fallback SPA zjadający błędy API, `unknown`
renderowane jako zero. Przy zmianach w tych okolicach zacznij od przeczytania ich nazw.

## Licencja

MIT. Projekt nie jest powiązany z Anthropic. Endpoint `/api/oauth/usage` jest nieudokumentowany
i może się zmienić bez ostrzeżenia — stąd archiwum surowych odpowiedzi i tolerancyjny parser.
