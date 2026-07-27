# AGENTS.md — zasady pracy w tym repo

Monitor limitów Claude dla wielu kont. Szczegóły: `README.md`.
Wyniki rozpoznania, na których stoi cały projekt: `docs/POC-FINDINGS.md`.

## Zasady, których nie wolno złamać

Każda z nich powstała po realnym problemie, nie z ostrożności.

**1. Sonda nie wysyła żadnego żądania do `api.anthropic.com`.**
Pomiar zleca sam Claude Code (`claude -p "/usage"`), a `client/usage-probe.py` czyta tylko
wynik z dysku. Nie ma w niej klienta HTTP do Anthropic, nie ma nagłówka `Authorization`
z tokenem OAuth i nie ma podszywania się pod `User-Agent: claude-code/…`.

Powód jest prosty: warunki Anthropic mówią bez wyjątków, że tokeny OAuth z kont Free/Pro/Max
nie mogą być używane *„in any other product, tool, or service"*. Nie ma tam furtki dla użycia
read-only. Wersja 2 sondy wołała `/api/oauth/usage` sama i była w szarej strefie; wersja 3
usuwa cały ten problem, bo żądanie wykonuje pierwszorzędny klient własnym, odświeżanym
tokenem.

`.credentials.json` czytamy dalej — ale **wyłącznie po metadane planu** (`subscriptionType`,
`rateLimitTier`, `expiresAt`), tylko do odczytu, i `accessToken` nie jest z niego używany do
niczego. Linia podziału leży przy **użyciu tokena**, nie przy odczycie pliku: odczyt jest
operacją czysto lokalną, wysłanie nim żądania — nie.

Endpointu tokenowego (`grant_type=refresh_token`) nie wołamy i nigdy nie zawołamy. Rotacja
jednorazowego refresh tokenu to główny udokumentowany wektor utraty konta
([#38248](https://github.com/anthropics/claude-code/issues/38248), #47754, #53063).

**2. Zero ciężkich importów w kliencie.**
Wolno: `sys`, `json`, `os`, `time`, `socket`, `hashlib`, `http.client`, `urllib.parse`, `ssl`.
Sam `import httpx` to ~150 ms, a skrypt startuje przy **każdym** wywołaniu narzędzia. Zmierzony
start CPythona: 27 ms — cały budżet zależy od tej zasady.

**3. Sonda nigdy nie rzuca wyjątkiem.**
`except: sys.exit(0)` na najwyższym poziomie. To jedyny kod w projekcie, który działa w ścieżce
Twojej pracy — błąd tutaj psuje sesję, nie tylko wykres.

**4. `unknown` nigdy nie jest zerem.**
`app/freshness.py` rozróżnia „okno się zresetowało i nikt nie pracował" (można wnioskować 0%)
od „klient działa, ale brak próbek" (awaria → `utilization: null`). Fałszywe, pewnie wyglądające
zero jest najgorszym trybem awarii tego narzędzia — użytkownik odpali duże zadanie i trafi
w ścianę. Test regresyjny: `test_dzialajacy_klient_bez_probek_daje_unknown`.

**5. Żadnych zahardkodowanych nazw bucketów.**
Odpowiedź ma 17 kluczy najwyższego poziomu, z czego 5 nie było znanych ani z walidatora
w binarce Claude Code, ani z repo referencyjnego. Serie są **wierszami w tabeli**. Nowy bucket
u Anthropic ma zadziałać bez migracji i bez deployu.

**6. Surowa odpowiedź trafia do bazy zawsze.**
`raw_payloads` (adresowane treścią). To jedyny sposób odtworzenia historii po zmianie schematu
przez Anthropic. Repo referencyjne straciło użytkownikom dane, bo miało strict decoder (PR #271).

**7. Tożsamość konta wyłącznie z `oauthAccount.accountUuid`.**
Nigdy ze statycznej konfiguracji. Na jednej maszynie konta przełącza się przez `/login`,
a `settings.json` jest wspólny — label przypisywałby połowę próbek do złego konta i cicho
zatruwał historię obu, bez żadnego widocznego objawu.

**8. Kontrakt API jest zamrożony.**
`/api/status` zwraca `contractVersion` (dziś **3**). Zmiana łamiąca zgodność = podbicie wersji
**i** aktualizacja `docs/UI-HANDOUT.md` **i** stałej `CONTRACT_VERSION` w
`frontend/src/api/types.ts` — UI porównuje je i protestuje w nagłówku przy rozjeździe.

**9. `resets_at` porównuj z tolerancją, nigdy na równość.**
Granica okna podawana przez Anthropic **kołysze się**: zmierzone 49 próbek w 3 h, jedno okno,
wartości od `00:59:59.014384` do `01:00:00.982268`. Porównanie doslowne było zawsze fałszywe
i po cichu wyłączyło **trzy** mechanizmy naraz — dedup, guard monotoniczności i wykrywanie
granic resetu (61 „resetów" na dobę zamiast pięciu). Jest na to `parsing.same_reset_window`
i testy regresyjne; prawdziwy reset przesuwa granicę o całe okno, więc rozróżnia się je
progiem, a nie równością.

**10. Tekst widoczny w UI pisze się po polsku, z ogonkami.**
Komentarze w kodzie zostają bez — to świadoma niespójność (kodowanie na Windows), ale etykiety
serii, ostrzeżenia i podpisy trafiają na ekran. `display_label` jest odświeżane przy każdym
ingest, więc poprawka słownika dochodzi do serii zarejestrowanych wcześniej.

**11. Czas wchodzi przez `NaiveUtcDt`, wychodzi przez `UtcDt`.**
Odkąd wyjście ma strefę, przeglądarka ją **odsyła** (`Date.toISOString()`), a baza,
`utcnow()` i próbki są naiwne w UTC. Zwykły `datetime` w parametrze zapytania wpuszcza czas
ze strefą do środka i wywraca się dopiero warstwę dalej — Historia zwracała 500 przy każdym
otwarciu. Gorszy wariant jest cichy: sterownik MySQL formatuje datetime `strftime`-em
i `tzinfo` **ignoruje**, więc `+02:00` przesunęłoby cały zakres o dwie godziny przy
HTTP 200 i poprawnie wyglądającej odpowiedzi. Oba typy leżą obok siebie w `app/schemas.py`.

## Układ

```
client/     sonda (stdlib-only) + narzędzia analizy; zasady 1-3
backend/    FastAPI + SQLAlchemy async + Alembic; MariaDB; serwuje też statyki UI
  app/parsing.py     czyste funkcje, cała logika normalizacji  <- tu zaczynaj przy zmianach API
  app/freshness.py   czyste funkcje, cztery stany świeżości
  app/services/      ingest (zapis), status (odczyt), cascade (szczeble limitu)
  app/main.py        mount /assets + fallback SPA; powłoka HTML celowo BEZ SSO
frontend/   React 18 + Vite + TypeScript, bez Tailwinda i bez biblioteki wykresów
  src/lib/freshness.ts   stan -> wygląd; JEDYNE miejsce tej decyzji (zasada 4 w pikselach)
  src/mocks/             VITE_MOCKS=1: stany, których w produkcji nie da się wywołać
docs/       POC-FINDINGS (dlaczego tak), UI-HANDOUT (kontrakt), POLLING-HANDOUT (odrzucone)
deploy/     szablon vhosta Apache; sekret podstawiany przy deployu, NIE w repo
```

**Frontend budowany jest w Dockerfile backendu** (etap `node`), więc kontekst budowania to
katalog repo, nie `./backend`. Bez `.dockerignore` każdy build wysyłałby do daemona cały
katalog `data/` z bazą.

Iteracja nad wyglądem: `cd frontend && VITE_MOCKS=1 npm run dev` — backend nie ma CORS ani
portu na hoście, więc dev przeciw produkcji i tak nie zadziała. Warianty przez `?mock=states`.

## Testy

```bash
cd backend
DATABASE_URL="sqlite+aiosqlite:///:memory:" INGEST_TOKENS="t:m" ALLOWED_EMAILS="a@b.pl" pytest

cd ../frontend && npm run typecheck      # kontrakt jest typowany, korzystaj z tego
```

Normalizator i ścieżka zapisu są testowane na **realnym payloadzie** z konta Max
(`tests/fixtures/usage_max.json`). Przy zmianach w `parsing.py` albo `services/ingest.py`
zaktualizuj fixture świeżą odpowiedzią, nie wymyślaj danych.

## Deploy

Kopia robocza **jest** wdrożeniem: `/var/lib/claude-usage-monitor` == `Z:/projects/claude-usage-monitor`.

```bash
cd /var/lib/claude-usage-monitor && git pull && docker compose up -d --build
```

Zmiana w kliencie wymaga skopiowania na dysk lokalny maszyny — patrz `client/README.md`.

## Pułapki tego środowiska

- **Docker ma wyczerpane pule adresowe** (~31 sieci to limit, host jest przy granicy).
  Nie dodawaj nowych sieci bez potrzeby.
- **`statusLine` nie działa w rozszerzeniu VS Code** — to funkcja CLI/TUI. Nie próbuj
  wracać do tego pomysłu, jest zamknięty w `docs/POC-FINDINGS.md`. Zgłoszenie
  [#55643](https://github.com/anthropics/claude-code/issues/55643) zamknięto jako
  `not_planned` (bot od nieaktywności), więc to się samo nie naprawi.
- **`claude -p "/usage"` NIE zużywa limitu, ale `claude -p "cokolwiek innego"` zużywa.**
  `/usage` jest zarejestrowane dwukrotnie i wariant `supportsNonInteractive` zwraca
  `{type:"text"}`, co ustawia `shouldQuery=false` — zmierzone `num_turns=0`,
  `duration_api_ms=0`, `total_cost_usd=0`. Jeśli argument nie trafi w komendę lokalną,
  leci **normalny turn modelu**. Sonda wykrywa to po `num_turns>0` i odrzuca taki zrzut.
- **Git Bash zjada argumenty zaczynające się od `/`.** `claude -p "/usage"` w Git Bashu
  staje się `claude -p "C:/Program Files/Git/usage"` (konwersja ścieżek MSYS) i zamiast
  komendy lokalnej dostajesz płatny turn modelu. Testuj z PowerShella albo ustaw
  `MSYS_NO_PATHCONV=1`. Kosztowało to dwa przypadkowe wywołania po ~$0,10.
- **Magazyn CA Windows** odrzuca łańcuch Let's Encrypt niektorych hostow, choć każde ogniwo jest ważne.
  Klient używa `certifi`; nie wyłączaj weryfikacji.
- **Skrypt uruchamiany z dysku sieciowego kosztuje +19 ms** na wywołanie. Wykonywana kopia
  zawsze na dysku lokalnym.
