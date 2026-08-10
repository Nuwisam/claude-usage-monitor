# AGENTS.md — zasady pracy w tym repo

Monitor limitów Claude dla wielu kont. Szczegóły: `README.md`.
Rozpoznanie, na którym stoi projekt — sekcja „Dlaczego tak, a nie inaczej" w `README.md`.

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
Na górze pliku wolno tylko: `sys`, `json`, `os`, `time`, `re`.
`socket`, `hashlib`, `http.client`, `urllib.parse`, `ssl`, `base64` i `subprocess` są
dozwolone, ale **wyłącznie leniwie, poza ścieżką gorącą** — w ciele `post()`, `main()`
oraz w gałęziach wejścia i wyjścia z blokady w sekcji alertu. Ta sekcja biegnie PRZED
throttlem, więc nie jest „za throttlem" — jest za czymś innym: odpala się parę razy na
blokadę, a nie przy każdym wywołaniu narzędzia. Ścieżka gorąca kończy się na jednym
`scandir` i nie importuje niczego. Na górze te moduły kosztowały
23 ms przy **każdym** odpaleniu hooka, także tym, który zaraz wychodzi na throttlu, czyli
na zdecydowanej większości. Zmierzone: 59,5 → 35,7 ms mediana, na dwóch identycznych
kopiach sondy różniących się tylko tą linią.
Sam `import httpx` to ~150 ms, a skrypt startuje przy **każdym** wywołaniu narzędzia.
Podłoga jest twarda: goły start CPythona to 31–36 ms (min–mediana pomiaru referencyjnego)
i nic jej nie zejdzie — cały budżet zależy od tej zasady.

Liczb wydajnościowych **nie przepisywać z tego pliku bez ponownego pomiaru**. Maszyna, na
której powstały, miała rozrzut rzędu 30 ms między min a max, a pomiar składników
(`python -c "import ..."`)
potrafi rozminąć się z pomiarem końcówka-do-końcówki nawet trzykrotnie. Wiążący jest
pomiar całego przebiegu sondy, nie suma kosztów modułów.

**3. Sonda nigdy nie rzuca wyjątkiem.**
`except: sys.exit(0)` na najwyższym poziomie. To jedyny kod w projekcie, który działa w ścieżce
Twojej pracy — błąd tutaj psuje sesję, nie tylko wykres.

**4. `unknown` nigdy nie jest zerem.**
`app/freshness.py` rozróżnia „okno się zresetowało i nikt nie pracował" (można wnioskować 0%)
od „klient działa, ale brak próbek" (awaria → `utilization: null`). Fałszywe, pewnie wyglądające
zero jest najgorszym trybem awarii tego narzędzia — użytkownik odpali duże zadanie i trafi
w ścianę. Test regresyjny: `test_dzialajacy_klient_bez_probek_daje_unknown`.

W pikselach niesie tę zasadę **etykieta wieku odczytu**, nie osobny rysunek toru. UI liczy
wartość jako `utilization ?? rawUtilization` — czyli przy `unknown` pokazuje **ostatni
ZMIERZONY** procent, nigdy zero i nigdy słowa zamiast znanej liczby — a o tym, ile ta liczba
jest warta, mówi stojące obok „potwierdzone w śr. o 11:58 · 3 d 4 h temu". `live`, `stale`
i `unknown` wyglądają więc **identycznie**; osobny rysunek zostaje dla `inferred_reset`
(wnioskowanie, nie pomiar) i dla serii, której nie zmierzono **nigdy** — tam pusty tor
czytałoby się jako zero. To ten sam model po obu stronach biurka: `frontend/src/lib/freshness.ts`
i `panel/panel/view.py` są jedną decyzją w dwóch językach, a panel miał ją pierwszy.

Wycofany miernik (`unavailableReason`) tej zasady **nie łamie, tylko ją stosuje**: gdy
organizacja odetnie kredyty, Anthropic podaje `percent: 0` — i to zero jest odrzucane, a na
ekranie zostaje ostatni ZMIERZONY procent razem z kwotami, ze stemplem tamtego pomiaru.
Fantomem jest zero z payloadu wycofania, nigdy wartość zmierzona wcześniej.

**Świadomy rozjazd WWW z panelem** jest w jednym miejscu i wynika z rozmiaru ekranu: WWW pisze
przy twardym bloku „kredyty wyłączone przez organizację", panel nie pisze o tym **nic** —
480×320 nie ma na to miejsca ani potrzeby, więc wiersz kredytów pokazuje same kwoty. To decyzja,
nie dług: powód jest wyjaśnieniem, a panel jest wskaźnikiem.

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
**i** aktualizacja `docs/API.md` **i** stałej `CONTRACT_VERSION` w
`frontend/src/api/types.ts` — UI porównuje je i protestuje w nagłówku przy rozjeździe.

Konsumentów tej stałej jest **dwóch**: `/api/status` i ramki `/api/stream`, bo ramka `account`
niesie ten sam model `AccountStatus`. Osadzaj go w ramkach **dosłownie** — wariant „lite"
byłby drugim kontraktem do utrzymania i dopiero on łamałby tę zasadę. Kartę konta składa
jedna funkcja (`build_account_status`), a `test_status_contract.py` porównuje wynik obu
ścieżek pole po polu.

Stałą trzyma u siebie **trzeci** odbiorca: `panel/panel/model.py` (`CONTRACT_VERSION`).
Panel czyta tylko strumień, więc rozjazd zobaczy dopiero w ramce `hello` — i wtedy przestaje
ufać danym, zamiast rysować je dalej.

**9. `resets_at` porównuj z tolerancją, nigdy na równość.**
Granica okna podawana przez Anthropic **kołysze się**: zmierzone 49 próbek w 3 h, jedno okno,
wartości od `00:59:59.014384` do `01:00:00.982268`. Porównanie doslowne było zawsze fałszywe
i po cichu wyłączyło **trzy** mechanizmy naraz — dedup, guard monotoniczności i wykrywanie
granic resetu (61 „resetów" na dobę zamiast pięciu). Jest na to `parsing.same_reset_window`
i testy regresyjne; prawdziwy reset przesuwa granicę o całe okno, więc rozróżnia się je
progiem, a nie równością.

**10. UI text is English (US), and so are comments.**
The deliberate inconsistency is gone: series labels, warnings and captions reach the screen in
English, and code comments are written the same way. Fixed terminology: window, pool, series,
sample, freshness, cascade, withdrawn meter, block, permission, credits, overage, reset, probe,
frame, banner, card. `display_label` is refreshed on every ingest, so a dictionary fix reaches
series registered earlier.

This rule states the target, not the current tree: the Polish→English migration is in flight and
runs over several waves. Polish strings still in the tree — `_LABELS` in `backend/app/parsing.py`
and the formatter strings in `frontend/src/lib/format.ts` are the two a reader hits first — are
work not yet done, not counter-examples to the rule; the dot decimal separator declared at the
top of `format.ts` is likewise ahead of its implementation.

**11. Czas wchodzi przez `NaiveUtcDt`, wychodzi przez `UtcDt`.**
Odkąd wyjście ma strefę, przeglądarka ją **odsyła** (`Date.toISOString()`), a baza,
`utcnow()` i próbki są naiwne w UTC. Zwykły `datetime` w parametrze zapytania wpuszcza czas
ze strefą do środka i wywraca się dopiero warstwę dalej — Historia zwracała 500 przy każdym
otwarciu. Gorszy wariant jest cichy: sterownik MySQL formatuje datetime `strftime`-em
i `tzinfo` **ignoruje**, więc `+02:00` przesunęłoby cały zakres o dwie godziny przy
HTTP 200 i poprawnie wyglądającej odpowiedzi. Oba typy leżą obok siebie w `app/schemas.py`.

## Glossary

| Polish | English |
|---|---|
| okno | window |
| pula | pool |
| sufit | ceiling |
| seria | series |
| próbka | sample |
| pomiar | measurement |
| odczyt | reading |
| świeżość | freshness |
| kaskada | cascade |
| szczebel | rung |
| miernik | meter |
| wycofany | withdrawn |
| licznik | meter (NOT "counter") |
| blokada | block |
| zgoda | permission (the concept, matching the `permission` reason key). The panel's `SHORT` display value is `ALLOW`: measured 39 px against `AlertList.REASON_W = 58`, where `PERMISSION` is 66 px and `APPROVAL` 59 px and both overflow. |
| kredyty | credits |
| nadwyżka | overage |
| reset | reset |
| granica resetu | reset boundary |
| sonda | probe |
| sygnalizator | signaller |
| ramka | frame (stream frame vs display frame — qualify when ambiguous) |
| pas / pasy | band / bands |
| pasmo | banner (deliberately NOT "band") |
| karta | card |
| pasek | bar |
| tor | track |
| klatka | frame (display) |
| zalana | flooded |
| znacznik | marker |
| tusz | ink |
| układ | layout |
| makieta | mockup |
| stempel | stamp |
| dziura | gap |
| brama / bramka | gate |
| zrzut | dump |
| ścieżka gorąca | hot path |
| zasada | rule |
| pułapka | pitfall |
| etykieta | label |
| stopka | footer |
| maszyna | machine |
| konto | account |
| awaria | failure |
| kontrakt | contract |
| wykres | chart |

## Locale

| Item | Rule |
|---|---|
| Numeric date | `26.07` — unchanged |
| Clock | 24 h — unchanged |
| Decimal separator | a **dot**, in prose and in the formatters alike |
| Time preposition | `at`, `yesterday at`, `on Wed. at`; the bare numeric-date form takes no preposition |
| `DAYS` | Sun. Mon. Tue. Wed. Thu. Fri. Sat. |
| HTML | `<html lang="en">` |
| Spelling | US (organization, utilization, color), to match the API payload |

## Układ

```
client/     sonda (stdlib-only) + narzędzia analizy; zasady 1-3
              usage-probe.py — ŹRÓDŁO PRAWDY, kopie na maszynach są wydaniem
                zawiera TAKŻE sygnalizator zablokowanej sesji (sekcja „alert"):
                zmierzone 1,7 ms za dołożenie go do tego procesu wobec 41,9 ms
                za osobny — patrz `client/README.md`
backend/    FastAPI + SQLAlchemy async + Alembic; MariaDB; serwuje też statyki UI
  app/parsing.py     czyste funkcje, cała logika normalizacji  <- tu zaczynaj przy zmianach API
  app/freshness.py   czyste funkcje, cztery stany świeżości
  app/services/      ingest (zapis), status (odczyt), cascade (szczeble limitu)
  app/services/events.py  broker SSE — W PROCESIE, patrz pulapka o --workers nizej
  app/sso.py         brama: AUTH_MODE none / header / verify + allowlista adresów
  app/main.py        mount /assets + fallback SPA; powłoka HTML celowo BEZ bramy
frontend/   React 18 + Vite + TypeScript, bez Tailwinda i bez biblioteki wykresów
  src/lib/freshness.ts   stan -> wygląd; JEDYNE miejsce tej decyzji (zasada 4 w pikselach)
  src/lib/time.ts        stamp/atStamp — JEDYNE miejsce decyzji „czy dopisać dzień"
  src/mocks/             VITE_MOCKS=1: stany, których w produkcji nie da się wywołać
docs/       API.md (kontrakt), RUNBOOK.md (obsługa i diagnostyka)
              PANEL-ALERT-HANDOUT.md + handout/ — zbudowany projekt karty alertu
deploy/     szablon vhosta Apache; sekret podstawiany przy deployu, NIE w repo
```

**Frontend budowany jest w Dockerfile backendu** (etap `node`), więc kontekst budowania to
katalog repo, nie `./backend`. Bez `.dockerignore` każdy build wysyłałby do daemona cały
katalog `data/` z bazą.

Iteracja nad wyglądem: `cd frontend && VITE_MOCKS=1 npm run dev`. Backend nie ma CORS, więc
dev przeciw zdalnemu wdrożeniu i tak nie zadziała — mocki dają też stany, których w produkcji
nie da się wywołać. Warianty przez `?mock=states`.

## Testy

```bash
cd backend && pytest                     # zmienne srodowiskowe ustawia tests/conftest.py
cd ../frontend && npm run typecheck      # kontrakt jest typowany, korzystaj z tego
cd ../panel && pytest
```

`tests/conftest.py` przypisuje `AUTH_MODE`, `DATABASE_URL`, `INGEST_TOKENS` i `ALLOWED_EMAILS`
**bezwarunkowo**, nie przez `setdefault` — inaczej przypadkowa zmienna w powłoce decydowałaby
o tym, co zestaw sprawdza, a wynik rozjeżdżałby się między maszynami bez śladu w repo.

Normalizator i ścieżka zapisu są testowane na **realnym payloadzie** z konta Max
(`tests/fixtures/usage_max.json`). Przy zmianach w `parsing.py` albo `services/ingest.py`
zaktualizuj fixture świeżą odpowiedzią, nie wymyślaj danych.

## Deploy

```bash
git pull && docker compose up -d --build
```

`AUTH_MODE` jest wymagane i nie ma wartości domyślnej — bez niego kontener nie wstanie.
Konfiguracja jednego wdrożenia (sieci, porty, adres usługi tożsamości) siedzi w nieśledzonym
`docker-compose.override.yml` i `.env`, nie w repo.

**Sondy nie trzeba kopiować.** Pod ścieżką z hooków
(`%LOCALAPPDATA%\claude-usage-monitor\usage-probe.py`) wystarczy kilkanaście linijek
przekierowania, które `runpy` wykonuje prawdziwy plik spod `SRC` — czyli
`client/usage-probe.py` w repo. Wtedy edycja działa od razu, bez `Copy-Item`. Miało tam stać
dowiązanie symboliczne; Windows odmawia jego utworzenia bez trybu dewelopera albo praw
administratora. Szczegóły w `client/README.md`.

**Gdzie kopia jednak leży, tam jest wydaniem.** `client/usage-probe.py` to źródło (ładuje je
`backend/tests/test_probe_parsing.py` po sztywnej ścieżce); kopia na maszynie zdalnej **może
być starsza** od HEAD i to jest poprawne — publikacja ma być decyzją, nie skutkiem ubocznym
pushu. Dlatego **każda zmiana zachowania sondy wymaga podbicia `SCRIPT_VERSION`**: wersja
jedzie w każdym batchu i jest jedynym sposobem, żeby z `/api/machines` odczytać, która
maszyna chodzi na którym kodzie. Bez podbicia dwie różne sondy są nierozróżnialne, a pytanie
„czemu tamta maszyna raportuje inaczej" zostaje bez odpowiedzi.

## Pułapki

- **`uvicorn --workers > 1` rozbija strumień SSE.** Broker (`app/services/events.py`) żyje
  w pamięci procesu, więc przy wielu workerach ingest trafia do innego procesu niż
  połączenie klienta i **część subskrybentów milknie bez jednego objawu w logach** — dane
  nadal płyną, tylko nie do wszystkich. `entrypoint.sh` startuje jeden proces świadomie.
  Skalowanie w poziom wymaga najpierw brokera poza procesem (Redis pub/sub albo
  `LISTEN/NOTIFY`), nie samej flagi.
- **Backend potrzebuje DWÓCH sieci.** `claude-usage-monitor_internal` ma `internal: true`,
  a to odcina także ruch **wychodzący** — bez drugiej sieci `AUTH_MODE=verify` nie miałby
  jak zapytać usługi tożsamości, a publikowany port nie miałby czego publikować. Jeśli
  `docker compose up` kończy się na *„all predefined address pools have been fully
  subnetted"*, host wyczerpał pule adresowe Dockera; poszukaj osieroconych sieci albo
  podłącz backend do sieci, która już istnieje (`networks: !override` w nadpisaniu).
- **`statusLine` nie działa w rozszerzeniu VS Code** — to funkcja CLI/TUI. Nie próbuj
  wracać do tego pomysłu. Zgłoszenie
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
- **Payload hooka to UTF-8, ale `sys.stdin` dekoduje go kodowaniem locale.** W procesie
  hooka zmierzone `sys.stdin.encoding = cp1250`, `errors = surrogateescape` — czyli
  `sys.stdin.read()` cicho psuje polskie znaki (dwa znaki na jeden: `ć` → `Ä‡`), a bajty
  bez odpowiednika w cp1250 (`0x81 0x83 0x88 0x90 0x98`, m.in. `Ł` i apostrof typograficzny)
  zamienia w samotne surogaty. Te wywracają się dopiero warstwę dalej, na `.encode("utf-8")`
  w `write_excl` — a że tam stoi `except: pass`, wpis blokady powstawał **pusty** i alert
  ginął bez śladu. Czytaj `sys.stdin.buffer` i dekoduj jawnie. Cała reszta ścieżki ma jawne
  utf-8, więc wiernie niosła to, co weszło: jedno miejsce psuło toast, panel i backend naraz.
  Objawem bywa **brak zdarzenia**, nie krzaki.
- **PowerShell 5.1 czyta `.ps1` bez BOM jako ANSI** — i wtedy myślnik `—` (U+2014, trzy bajty
  w UTF-8) rozpada się na trzy znaki, z których `0x94` to **cudzysłów zamykający** U+201D.
  Parser uznaje go za koniec stringa i przewraca się kilkadziesiąt linii dalej, w miejscu bez
  związku z przyczyną („The string is missing the terminator"). Skrypty `.ps1` trzymamy
  w **czystym ASCII** — to odporniejsze niż liczenie na to, że każda kolejna edycja zachowa BOM.
- **Klient weryfikuje TLS przez `certifi`, nie przez magazyn systemowy.** Magazyn CA Windows
  potrafi odrzucić poprawny łańcuch Let's Encrypt, w którym każde ogniwo jest ważne. Nie
  wyłączaj weryfikacji — podmień magazyn.
- **Windows odmawia tworzenia dowiązań symbolicznych** bez trybu dewelopera albo praw
  administratora (`Administrator privilege required`). Stąd pod ścieżką z hooków stoi
  kilkanaście linijek przekierowania w Pythonie, a nie symlink.
- **Sonda musi mieć konce linii LF, a skrypty powłoki dodatkowo bit `+x`.** Bit wykonywalny
  z indeksu gita nie zawsze przekłada się na katalog roboczy — jeśli repo jest widziane
  przez udział sieciowy, git po stronie Linuksa **po cichu pomija** skrypt bez `+x`. Końce
  linii pilnuje `.gitattributes`; przy pracy z takiego udziału ustaw też
  `core.filemode=false`, inaczej `git add -A` z Windows zdejmie bit wykonywalny bez słowa
  ostrzeżenia.
