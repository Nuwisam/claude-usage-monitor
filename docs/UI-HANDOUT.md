# UI — kontrakt API (v2)

Dokument powstał jako **brief dla makiet**; makiety przyszły i UI jest zbudowane, więc dziś
jest to przede wszystkim **kontrakt**. Wszystkie przykłady są **wygenerowane z działającego
systemu**, nie wymyślone.

Stan na 2026-07-27: backend i frontend wdrożone pod `https://usage.example.org/claude-usage/`.
Base URL API: `https://usage.example.org/claude-usage/api`

**`contractVersion` = 3.** Co doszło w v3:

- **`confirmedAt`** — kiedy ostatnio POTWIERDZONO tę wartość. **Świeżość liczy się z tego
  pola, nie z `capturedAt`.** Dedup celowo nie zapisuje próbki, gdy wartość się nie zmieniła,
  więc `capturedAt` bywa o minuty starsze niż ostatni realny pomiar — a wtedy stabilny odczyt
  wygląda w UI jak zerwana łączność. To dwie przeciwne informacje dla kogoś, kto właśnie
  decyduje, czy odpalić duże zadanie.
- **`valueSince`** — odkąd wartość jest niezmienna. Stąd podpis „bez zmian od 12:05".
- `capturedAt` zostaje i znaczy dalej to samo: czas ostatniej zapisanej PRÓBKI.
- **`deltaFrom`** — od której PRÓBKI liczy się `deltaPct1h`. Baseline jest przycięty do
  **bieżącego okna**, więc rozpiętość bywa krótsza niż godzina i UI musi to napisać
  („+3 pp od 14:03", nie „+3 pp w ciągu godziny"). Wcześniej po resecie sesji punkt
  odniesienia pochodził z poprzedniego okna i przez godzinę wisiało „−46 pp w ciągu godziny".
  `null` zawsze i dokładnie wtedy, gdy `deltaPct1h` jest `null`. Nazwa `deltaPct1h` zostaje —
  jej zmiana łamałaby zgodność, a dodanie pola nie łamie (patrz § 11).

Co doszło w v2 względem v1 i dlaczego:

| Zmiana | Powód |
|---|---|
| **Czas na drucie z offsetem** (`…Z`) — **jedyna zmiana łamiąca zgodność** | v1 wysyłało naiwny UTC bez strefy, a `new Date("2026-07-26T19:07:37")` w JS to czas **lokalny**. Countdown był cicho przesunięty o strefę |
| `kind`, `group`, `bucketKey` w seriach | Bez nich UI musiało zgadywać, która seria jest oknem 5 h — po `sortOrder` albo po prefiksie klucza, czyli dokładnie tak, jak zabrania zasada 5 z `AGENTS.md` |
| `cascade[]` przy koncie | „Na którym szczeblu limitu jesteś" to wiedza dziedzinowa i wymaga sięgnięcia do nietypowanych `spend`/`extra_usage`. Liczy ją backend, ma testy |
| `gaps[].kind = no_samples` faktycznie emitowane | Pole istniało w v1, ale **żadna linia kodu go nie ustawiała** — awaria serii wyglądała identycznie jak bezczynność |
| `resets[]` niezależne od koszyka | W v1 wypełniane tylko przy `bucket=raw`, więc domyślny widok 24 h wracał bez ani jednej granicy resetu |

---

## 1. Co ten system pokazuje

Ile **aktualnie zostało limitu Claude** dla kilku kont naraz. Dziś dwa: jedno **Max**,
jedno **Team**. Historia zbiera się przy okazji, ale nie jest celem.

Dane pochodzą z sondy uruchamianej na maszynach użytkownika przy pracy z Claude Code.
**Nie płyną, gdy nie pracujesz** — i to nie usterka, tylko właściwość źródła, którą UI
musi uczciwie komunikować (patrz § 4).

---

## 2. Uwierzytelnienie

Backend sam jest bramą SSO — nie ma przed nim nginx-a z `auth_request`, więc **nikt nie
zwróci przeglądarce 302**. Zamiast tego:

| Sytuacja | Odpowiedź |
|---|---|
| Brak sesji | `401 {"detail": {"reason": "not-authenticated", "redirect_url": "…"}}` |
| Zalogowany, ale spoza allowlisty | `403 {"detail": {"reason": "email-not-allowed"}}` |
| SSO niedostępne | `503 {"detail": {"reason": "sso-unreachable"}}` |

**UI musi obsłużyć 401 przekierowaniem** na `detail.redirect_url`. To jest część kontraktu,
nie szczegół implementacji.

```json
{"detail": {"reason": "not-authenticated",
  "redirect_url": "https://usage.example.org/oauth2/start?rd=https%3A%2F%2Fusage.example.org%2Fclaude-usage%2F"}}
```

Wszystkie odpowiedzi mają `Cache-Control: no-store`. Nieaktualny procent limitu jest gorszy
niż brak odpowiedzi.

---

## 3. Endpointy

| Metoda i ścieżka | Zwraca |
|---|---|
| `GET /status` | **Główny endpoint.** Stan bieżący wszystkich kont i serii. Odpytywać co **15 s**, a przy podłączonym strumieniu (§ 3.1) co **3 min** |
| `GET /stream?account=<uuid>` | **SSE.** Karta konta wypychana natychmiast po pomiarze. Zob. § 3.1 |
| `GET /history?account=&seriesId=&from=&to=&bucket=auto` | Przebieg w czasie + dziury + granice resetów |
| `GET /accounts` | Lista kont. **`PATCH /accounts/{uuid}` NIE ISTNIEJE** — kolumny `label`, `color`, `isEnabled` są w bazie, ale nie ma ścieżki zapisu. Nie buduj na nim edycji |
| `GET /machines` | Które maszyny raportowały które konta, z wersją **sondy** (`scriptVersion`) |
| `GET /series` | Rejestr serii (zob. § 6 — lista jest otwarta) |
| `GET /events` | Log operacyjny: przełączenia konta, drift schematu, błędy klienta |
| `GET /batches` · `GET /batches/{id}/raw` | Log przyjęć + surowa odpowiedź Anthropic |

**W `/batches` nie ma kodów HTTP i nie będzie** — od v3 sonda nie wysyła żadnego żądania do
Anthropic, więc nie ma odpowiedzi, którą miałyby opisywać. W ich miejscu jest proweniencja
pomiaru: `measurementSource` (`cli_merged` = świeże procenty ze stdout `/usage`,
`cli_usage_cache` = sam cache Claude Code, do 5 min stary), `cacheAgeS`, `freshAgeS`.
Przewaga `cli_usage_cache` to cicha awaria — dane płyną dalej, tylko rozdzielczość spadła
z minuty do pięciu, i **to jedyne miejsce, w którym widać to wprost**.
| `GET /stats` | Liczniki, współczynnik dedupu, skuteczność ingestu 24 h |

**Czas działa w obie strony.** `from` i `to` w `/history` przyjmują ISO-8601 ze strefą
(`…Z`, `…+02:00`) albo bez niej — bez strefy zakłada się UTC, ze strefą wartość jest
**przeliczana**, a nie obcinana. Wysyłaj po prostu `Date.toISOString()`.

To nie jest uprzejmość dla klienta, tylko domknięcie granicy: gdy kontrakt v2 dopiął
strefę do czasu wychodzącego, przeglądarka zaczęła ją odsyłać — i widok Historia zwracał
**500** przy każdym otwarciu, bo reszta backendu liczy na naiwnym UTC. Pilnuje tego
`backend/tests/test_history_endpoint.py`.

---

## 3.1 Strumień zdarzeń (SSE)

```
GET /api/stream?account=<uuid>&account=<uuid>[&snapshot=0]
Accept: text/event-stream
```

Serwer wypycha ramkę, gdy przez `/api/ingest` przyjdzie pomiar dla **zapisanego** konta.

**Subskrypcja jest wyłącznie po `account_uuid`.** Adresu e-mail w tym kontrakcie nie ma —
ani jako parametru, ani jako klucza dopasowania. Jeden adres wskazuje realnie kilka kont
(konto Pro i miejsce w Teamie pod tym samym adresem), a `email` jest nadpisywany przy każdym
pomiarze; adresowanie po nim znaczyłoby, że zbiór kont pod subskrypcją zmienia się bez
wiedzy subskrybenta — po cichu. Co najmniej jeden `account` jest **wymagany**: brak
parametru to `400 {"reason": "no-subscription"}`, nigdy niejawne „wszystko".

**Autoryzacja: Bearer albo ciasteczko SSO.** Obecność nagłówka `Authorization` wybiera
ścieżkę tokenową (`STREAM_TOKENS`); bez niego działa zwykła sesja SSO, więc `EventSource`
z przeglądarki nie wymaga niczego dodatkowego. `STREAM_TOKENS` to **osobny sekret** od
`INGEST_TOKENS` — token sondy jest poświadczeniem wyłącznie do zapisu i nie otwiera odczytu.

| event | kiedy | treść |
|---|---|---|
| `hello` | raz, na starcie | `{contractVersion, serverNow, subscribed[], unknown[], pingSec, maxLifetimeSec}` |
| `account` | snapshot na starcie + po każdym przyjętym pomiarze | `{contractVersion, serverNow, account, warnings[]}` |
| `ping` | co `pingSec` (15 s) | `{serverNow}` |
| `lag` | odbiorca nie nadążył | `{reason:"queue-overflow", dropped}` |
| `bye` | po `maxLifetimeSec` (900 s) | `{reason:"lifetime"}`, potem czyste zamknięcie |

**`account` niesie dokładnie ten sam obiekt, co element `accounts[]` w `/status`** — ten sam
model, te same pola, ta sama funkcja składająca po stronie serwera. Nie ma wariantu „lite"
i nie będzie: drugi kształt tych samych danych to drugi kontrakt do utrzymania.

Cztery rzeczy, które trzeba wiedzieć, zanim się to podepnie:

1. **`unknown[]` nie jest błędem.** To UUID-y, których nie ma w bazie — literówka albo konto,
   które dopiero powstanie. Połączenie zostaje otwarte i subskrypcja obejmuje je dalej, więc
   konto założone w trakcie strumienia dojdzie samo. Zgłaszamy je, bo pomyłka w konfiguracji
   musi wyglądać inaczej niż bezczynność.
2. **Nie ma replayu** ani `Last-Event-ID`. Po ponownym połączeniu dostajesz świeży snapshot,
   co jest ściśle lepsze od odtwarzania historii. Każda ramka jest **pełnym** stanem konta,
   nie przyrostem — dlatego zgubienie ramek jest nieszkodliwe i dlatego `lag` wystarczy jako
   jedyny sygnał przerwy.
3. **`bye` po 900 s jest normalne.** To jedyny moment, w którym długie połączenie ponownie
   weryfikuje sesję SSO. `EventSource` wznawia sam; klient headless musi wznowić sam.
4. **Poll zostaje.** Strumień nie przelicza świeżości przy ciszy klienta (bo cisza nie
   generuje zdarzeń), nie niesie `warnings[]` liczonych ponad kontami i nie pokaże konta,
   o którego UUID nikt nie prosił. `/status` co **3 min** domyka wszystkie trzy.

   Dlaczego 3 min, a nie minuta: sonda ma throttle 60 s, więc minutowy poll nie mógłby
   pokazać niczego, czego nie przyniósł już strumień. To, po co poll naprawdę jest, zmienia
   się **z upływem czasu**, nie z nowymi danymi — a najszybsze z tych przejść to
   `live → stale` po `FRESH_WINDOW_SEC` (300 s). Najgorszy przypadek: seria pokazuje `live`
   przez 8 minut zamiast 5. Błąd jest ograniczony i idzie w nieszkodliwą stronę — `stale`
   jest jawnie niealarmowe, a w trwającym oknie utilization tylko rośnie, więc widoczna
   liczba pozostaje ograniczeniem **dolnym**.

`snapshot=0` pomija karty startowe — używa tego przeglądarka, która przed otwarciem
strumienia i tak pobrała `/status`.

**Filtr to routing, nie autoryzacja.** Zalogowany użytkownik widzi wszystkie konta
w `/status`; strumień niczego przed nikim nie zamyka.

---

## 4. Cztery stany świeżości — to jest kontrakt, nie sugestia stylistyczna

Każda seria w `/status` ma pole `freshness`. **Nie wolno ich zlewać w jeden.**

| `freshness` | Znaczenie | `utilization` | Jak pokazać |
|---|---|---|---|
| `live` | **Potwierdzenie** świeższe niż 5 min (od v3 — nie próbka, patrz niżej) | liczba | normalnie |
| `stale` | Brak potwierdzenia od 5 min, ale okno wciąż trwa | liczba | czas potwierdzenia + wartość może być już wyższa |
| `inferred_reset` | Okno się zresetowało i klient milczał — **wnioskujemy** ~0% | `0.0` | wyraźnie inaczej niż pomiar |
| `unknown` | Klient raportuje, ale brak danych dla tej serii — **awaria** | **`null`** | „nie wiem", **nigdy 0%** |

**Świeżość liczy się z `confirmedAt`, nie z `capturedAt`** — to jest ta zmiana z v3.
Pod v2 stabilna wartość wpadała w `stale` przez sam dedup, więc „nic się nie zmienia"
wyglądało identycznie jak „straciliśmy łączność". Odwrotnie też trzeba uważać przy
podpisywaniu `stale`: hooki odpalają się **tylko przy pracy**, więc kwadrans przerwy daje
ten sam stan co martwy klient. Awarię rozpoznaje osobno `unknown` (tam wchodzi w grę
`lastBatchAt`), i dlatego przy `stale` nie wolno pisać niczego alarmowego.

Przy `stale` wartość jest **dolnym ograniczeniem**: w trwającym oknie zużycie tylko rośnie.
„Może być już wyższa" jest prawdziwe, „wciąż aktualna" — nie. Przy decyzji „czy odpalić duże
zadanie" mylenie się w tę stronę jest bezpieczne, w drugą nie.

### Najważniejsze zdanie w tym dokumencie

**`unknown` nie może zostać wyrenderowane jako 0%.**

Najgorszy tryb awarii tego narzędzia to pokazanie fałszywego, pewnie wyglądającego zera —
bo na tej podstawie użytkownik odpali duże zadanie i trafi w ścianę. Właśnie po to backend
zwraca w tym stanie `utilization: null`, a obok `rawUtilization` z **ostatnią zmierzoną**
wartością. UI może pokazać „ostatnio widziane 42%, ale nie wiemy co teraz" — nie może udawać
pomiaru.

`inferred_reset` też jest wnioskowaniem, nie pomiarem, i musi wyglądać inaczej. Zastrzeżenie
do tooltipa: wnioskowanie jest prawdziwe, **chyba że** konto było w tym czasie używane
z claude.ai, mobile albo Cowork — one czerpią z tego samego limitu, ale nie wysyłają próbek.

### Świeżość próbki ≠ świeżość liczby

`capturedAt` to moment, w którym **my** zaobserwowaliśmy wartość. Etykieta brzmi
„zaobserwowane 14:23", **nie** „stan na 14:23".

---

## 5. Trzy twarde reguły prezentacji

Wynikają z danych, nie z gustu.

**1. Przy każdym koncie widoczny plan.** `orgType`, `seatTier`, `rateLimitTier`, `email`.
Bez tego dwa razy „40%" wygląda tak samo, a znaczy **różne ilości bezwzględne** — inne na
Max 20x, inne na miejscu Team Standard.

**2. Nigdy nie sumować ani nie średniować procentów między kontami.** To liczba bez znaczenia.

**3. Porównania w czasie domyślnie facetowane per konto**, nie nakładane na jedną oś.
Overlay tylko świadomie i wtedy legenda niesie plan.

---

## 6. Reguły dotyczące danych

**Zero zahardkodowanych nazw bucketów.** Odpowiedź Anthropic ma 17 kluczy najwyższego poziomu,
z czego 5 nie było znanych z żadnego źródła (`amber_ladder`, `iguana_necktie`, `nimbus_quill`,
`tangelo`, `omelette_promotional`). Renderuj to, co zwróci `/status` i `/series`, sortując po
`sortOrder`. Nowy bucket ma pojawić się **bez zmiany kodu UI**.

**`primary` i `duplicateOf`.** API raportuje ten sam limit dwukrotnie — raz jako bucket
najwyższego poziomu, raz jako wpis w `limits[]`. Backend paruje je po danych i oznacza
duplikaty. **Pokazuj tylko `primary: true`.** Wpis z `limits[]` wygrywa, bo niesie
`isActive` i `severity`. Gdy wartości się rozjadą, pary nie powstaną i obie serie będą
widoczne — to celowe, wolimy pokazać rozjazd niż go ukryć.

**`isActive` mówi, co realnie ogranicza *teraz*.** To najcenniejsze pole w całej odpowiedzi
i zasługuje na eksponowanie. Zaobserwowane: wiążący limit **przeskakuje w czasie** — rano
`weekly_all`, po intensywnej sesji `session`.

**`severity`** to gotowa klasyfikacja od Anthropic (`normal`, …). Używaj jej zamiast
wymyślać własne progi.

**Countdowny licz od `serverNow`** z odpowiedzi, nie od zegara przeglądarki. `secondsToReset`
jest policzone po stronie serwera.

**`resetsAt: null` ma dwa różne powody i żaden nie znaczy „ta seria się nie resetuje".**
Anthropic nie podaje granicy dla okna z **0% zużycia** (widać to w `limits[]`:
`weekly_scoped percent 0 → resets_at null`) — okno 5 h przed pierwszym użyciem po prostu nie
ma instancji. Osobno sonda **zeruje przedawnioną granicę z cache**, gdy okno przeturlało się
między zapisem cache a odczytem (do ~5 min). Podpis musi je rozróżniać:

| stan serii | podpis |
|---|---|
| `resetsAt` w przyszłości | `reset za 2 h 05 min · o 20:00` |
| `resetsAt` w przeszłości | `reset minął · o 20:00` — **nigdy** „reset za …" |
| `resetsAt: null`, `utilization: 0` | `okno nie wystartowało` |
| `resetsAt: null`, `utilization > 0` | `czas resetu nieznany` |
| seria bez okna (`spend`, `extra_usage`) | `bez resetu` |

**Drut jest w UTC, ekran w strefie użytkownika.** Konwersję robi wyłącznie warstwa
prezentacji (`lib/time.ts`) — żadna wartość nie jest przeliczana przed wysłaniem ani przed
porównaniem. Tam, gdzie widać surowe godziny bez kontekstu „teraz" (zakres historii), strefa
jest **podpisana** (`UTC+2`), bo dwie strefy na jednym ekranie bez etykiety to najkrótsza
droga do błędnej interpretacji.

**Limity są kaskadą, nie jedną liczbą** — i od v2 liczy ją backend, w `cascade[]` przy koncie.
Cztery szczeble w kolejności: `session` → `weekly` → `credits` → `hard_block`, każdy ze
`state` (`on` / `off` / `unknown`) i jeden z `isCurrent: true`.

`isCurrent` startuje na szczeblu, którego seria ma `isActive`, i **zsuwa się w dół, gdy ten
szczebel jest wyczerpany** — zaobserwowany przypadek Team: tygodniowy jest `isActive` i ma
100%, ale praca realnie leci z kredytów. Pokazanie tygodniowego jako bieżącego byłoby myleniem
„to mnie ogranicza" z „tu się skończyło".

**`state: "off"` i `state: "unknown"` to dwie różne rzeczy.** „Kredyty wyłączone" jest
informacją, „nie wiem, czy masz kredyty" jest jej brakiem. Zlanie ich pokazywałoby ścieżkę
wyjścia z limitu, której może nie być. Gdy zsuwanie kończy się na szczeblu `unknown`, on
dostaje `isCurrent` — a UI pisze tam „nie wiem", nie zgaduje.

**Kwoty w kaskadzie są w jednostkach mniejszych z wykładnikiem** — `usedMinor: 3820`,
`exponent: 2`, `currency: "USD"` znaczy `38,20 USD`. Backend **nie formatuje**; to robi UI.
Ta sama zasada w `spend.extra`: `{"amount_minor": 0, "currency": "USD", "exponent": 2}`.

**`gaps[]` w `/history` ma dwa rodzaje** i wymaga dwóch różnych cieniowań:
- `client_silent` — nie było batchy. **Nie pracowałeś**, więc nie ma czego mierzyć.
- `no_samples` — batche przychodziły, ale dla tej serii nie było ani jednej próbki. **Awaria**,
  ta sama, którą w `/status` widać jako `unknown`.

Wykres malujący oba jednakowo kłamie dokładnie tam, gdzie to narzędzie kłamać nie może.
Przy tym źródle **brak danych jest informacją**, nie usterką wykresu.

**`resets_at` KOŁYSZE SIĘ — nie porównuj go na równość.** Zmierzone: 49 próbek w 3 h, jedno
okno sesji, wartości od `00:59:59.014384` do `01:00:00.982268`. Anthropic dolicza tam
mikrosekundy swojej odpowiedzi i drobny dryf sekundowy. Prawdziwy reset przesuwa granicę
o **całe okno** (5 h albo 7 dni), więc rozróżnia się je tolerancją, nie równością. Backend
robi to za UI (`same_reset_window`), ale gdybyś liczył cokolwiek z `resetsAt` po stronie
przeglądarki — pamiętaj o tym.

---

## 7. Realna odpowiedź `GET /status`

Wygenerowana z działającego systemu, konto Max, 2026-07-26 21:57 UTC. Skrócona do dwóch serii
(pełna ma sześć) — obie `primary`, bo duplikaty bucketów są tu pominięte dla czytelności.

```json
{
  "contractVersion": 3,
  "serverNow": "2026-07-26T21:57:27.446632Z",
  "warnings": [],
  "accounts": [{
    "uuid": "00000000-0000-4000-8000-000000000003",
    "label": "you@example.org",
    "email": "you@example.org",
    "displayName": "Tomasz",
    "color": null,
    "orgType": "claude_max",
    "seatTier": null,
    "rateLimitTier": "default_claude_max_5x",
    "subscriptionType": "max",
    "isEnabled": true,
    "lastSampleAt": "2026-07-26T21:57:06.343335Z",
    "lastBatchAt": "2026-07-26T21:57:06.343335Z",
    "lastClientHost": "desktop",
    "cascade": [
      {"key": "session", "state": "on", "isCurrent": true, "utilization": 91.0,
       "seriesKey": "limit:session|session|-|-",
       "usedMinor": null, "limitMinor": null, "currency": null, "exponent": null},
      {"key": "weekly", "state": "on", "isCurrent": false, "utilization": 43.0,
       "seriesKey": "limit:weekly_all|weekly|-|-",
       "usedMinor": null, "limitMinor": null, "currency": null, "exponent": null},
      {"key": "credits", "state": "off", "isCurrent": false, "utilization": null,
       "seriesKey": "spend:org",
       "usedMinor": 0, "limitMinor": null, "currency": "USD", "exponent": 2},
      {"key": "hard_block", "state": "on", "isCurrent": false, "utilization": null,
       "seriesKey": null,
       "usedMinor": null, "limitMinor": null, "currency": null, "exponent": null}
    ],
    "series": [
      {
        "seriesId": 3,
        "seriesKey": "limit:session|session|-|-",
        "label": "Sesja",
        "source": "limit",
        "sortOrder": 15,
        "kind": "session",
        "group": "session",
        "bucketKey": null,
        "utilization": 91.0,
        "rawUtilization": 91.0,
        "resetsAt": "2026-07-27T00:59:59.056340Z",
        "secondsToReset": 10951,
        "capturedAt": "2026-07-26T21:57:05Z",
        "confirmedAt": "2026-07-26T21:57:05Z",
        "valueSince": "2026-07-26T21:41:07Z",
        "freshness": "live",
        "isActive": true,
        "severity": "critical",
        "deltaPct1h": 28.0,
        "deltaFrom": "2026-07-26T20:58:03Z",
        "primary": true,
        "duplicateOf": null,
        "extra": null
      },
      {
        "seriesId": 4,
        "seriesKey": "limit:weekly_all|weekly|-|-",
        "label": "Tydzień (wszystkie modele)",
        "source": "limit",
        "sortOrder": 25,
        "kind": "weekly_all",
        "group": "weekly",
        "bucketKey": null,
        "utilization": 43.0,
        "rawUtilization": 43.0,
        "resetsAt": "2026-08-01T15:59:59.056361Z",
        "secondsToReset": 496951,
        "capturedAt": "2026-07-26T21:57:05Z",
        "confirmedAt": "2026-07-26T21:57:05Z",
        "valueSince": "2026-07-26T21:10:44Z",
        "freshness": "live",
        "isActive": false,
        "severity": "normal",
        "deltaPct1h": 3.0,
        "deltaFrom": "2026-07-26T20:58:03Z",
        "primary": true,
        "duplicateOf": null,
        "extra": null
      }
    ]
  }]
}
```

Trzy rzeczy warte zauważenia w tej jednej odpowiedzi:

- **`isActive` faktycznie przeskakuje.** W przykładzie z v1 (19:07) wiązał `weekly_all`;
  tutaj, po intensywnej sesji, wiąże `session` przy 91% i `severity: "critical"`.
- **`cascade` mówi więcej niż górny procent** — `session` jest bieżącym szczeblem, kredyty są
  `off`, więc twardy blok stoi zaraz za tygodniowym.
- **`resetsAt` obu serii kończy się na `.056340` i `.056361`** — 21 µs różnicy, bo to
  mikrosekundy odpowiedzi, nie granicy okna. Patrz ostrzeżenie w § 6.

Serie `bucket:five_hour` i `bucket:seven_day` w tej samej odpowiedzi mają `primary: false`
i `duplicateOf` wskazujące na powyższe — domyślnie ich nie pokazuj.

Seria `spend:org` w `extra` niesie:

```json
{"enabled": false, "cap": null, "limit": null, "balance": null,
 "used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
 "can_purchase_credits": false, "can_toggle": false,
 "disclaimer": "Usage credits cover you when you hit your plan limits. …"}
```

## 8. Realna odpowiedź `GET /history`

Zakres 24 h, seria sesji, konto Max. `points` skrócone do trzech.

```json
{
  "bucket": "5m",
  "points": [
    {"t": "2026-07-26T17:45:00Z", "min": 23.0, "max": 23.0, "avg": 23.0, "last": 23.0, "n": 1},
    {"t": "2026-07-26T17:50:00Z", "min": 25.0, "max": 25.0, "avg": 25.0, "last": 25.0, "n": 2},
    {"t": "2026-07-26T17:55:00Z", "min": 25.0, "max": 25.0, "avg": 25.0, "last": 25.0, "n": 1}
  ],
  "resets": ["2026-07-26T17:55:01Z", "2026-07-26T20:16:03Z"],
  "gaps": [
    {"from": "2026-07-25T21:57:27.476580Z", "to": "2026-07-26T17:49:02.415203Z",
     "kind": "client_silent"},
    {"from": "2026-07-26T19:17:20.357794Z", "to": "2026-07-26T20:16:03.677026Z",
     "kind": "client_silent"}
  ]
}
```

`bucket=auto` dobiera agregację do szerokości zakresu: `raw` do 6 h, `5m` do 48 h, `1h` powyżej.
Przy agregacji `min`/`max` są po to, żeby **piki przeżyły**; wdrożone UI rysuje samą `avg`,
ale dane na pasmo min-max są dostępne.

Zwróć uwagę na pierwszą dziurę: prawie **20 godzin ciszy klienta** w dobowym zakresie. To nie
usterka — dane przyrastają tylko wtedy, gdy pracujesz, i wykres ma to pokazywać wprost.

---

## 9. Co makiety rozstrzygnęły

Ta sekcja była listą pytań do projektanta. Makiety (runda 2, `Claude Usage Monitor v2.dc.html`)
odpowiedziały na nie tak:

| Pytanie | Rozstrzygnięcie |
|---|---|
| Liczba ekranów | **Dwa: Live i Historia.** Konta/Maszyny i Diagnostyka zostają przy `curl` |
| Hierarchia widoku Live | **Na pierwszym planie zawsze Sesja 5 h.** `isActive` NIE przestawia hierarchii — jest cienką kreską i słowem `wiąże` przy serii, która ogranicza |
| Wizualizacja | Poziomy tor + liczba. Cztery stany świeżości mają **cztery różne rysunki toru**, nie tylko inny kolor |
| Paleta | Nocturne (struktura) + **ciepła paleta Claude** (`--color-accent: #d97757` na `#1c1b19`) |
| Biblioteka wykresów | Żadna — wykres to własne SVG, `viewBox 0 0 1000 200` |
| Szerokości | Jeden układ w dwóch: pełne okno (konta jako kolumny) i wąska kolumna |

**Dlaczego stały hero, a nie ruchomy.** Gdyby pierwszy plan przeskakiwał za `isActive`, ten sam
ekran znaczyłby co innego w zależności od pory tygodnia. Stały hero plus ruchomy znacznik daje
jedno i drugie: „ile zostało w oknie, w którym pracuję" **oraz** „co mnie realnie ogranicza".

---

## 10. Bez UI, przez `curl`

UI pokrywa Live i Historię; reszta danych (zdarzenia, batche, maszyny, surowe payloady)
jest dostępna wyłącznie tędy:

```bash
# w przegladarce zalogowanej do SSO — najprostsza droga
https://usage.example.org/claude-usage/api/status

# z terminala (wymaga ciasteczka sesji SSO)
curl -s -b "$COOKIE" https://usage.example.org/claude-usage/api/status | jq \
  '.accounts[] | {email, orgType,
    serie: [.series[] | select(.primary) | {label, utilization, freshness, isActive}]}'

# co mnie teraz ogranicza
curl -s -b "$COOKIE" .../api/status | jq '.accounts[].series[] | select(.isActive)'

# log operacyjny — przelaczenia konta, drift schematu
curl -s -b "$COOKIE" .../api/events | jq '.[] | {ts, type, message}'
```

Lokalnie, bez SSO i bez serwera:
`python client/analyze-samples.py`

---

## 11. Wersjonowanie kontraktu

`/status` zwraca `contractVersion` (obecnie **`3`**). Przy zmianie łamiącej zgodność liczba
rośnie — UI sprawdza to i **głośno protestuje** w nagłówku, zamiast po cichu renderować śmieci
(`frontend/src/components/Nav.tsx`, stała `CONTRACT_VERSION` w `api/types.ts`).

**Ta sama liczba jedzie w kopercie każdej ramki SSE** i oznacza dokładnie to samo, bo ramka
`account` niesie ten sam model. Konsumentów jest więc dwóch: `/status` i `/stream`. Dodanie
strumienia **nie** podbiło wersji — `/status` nie zmienił się ani o pole.

Dodanie pola jest zmianą **nie**łamiącą i nie wymaga podbicia. Wersja poszła z 1 na 2 wyłącznie
przez zmianę serializacji czasu — reszta v2 to dodatki. Tak samo doszło `deltaFrom`: wersja
została na 3, a UI bez tego pola dostaje `undefined` i wraca do brzmienia godzinowego.

Konto Team nie zostało jeszcze zweryfikowane na żywo (ma wyczerpany limit tygodniowy). To ono
włącza szczeble `credits` i `hard_block` z kwotami; do tego czasu stoją one na fixture
zbudowanym z `docs/POC-FINDINGS.md` i makiety. Jeśli prawdziwa odpowiedź ujawni pola, których
tu nie ma, ten dokument idzie do aktualizacji razem z kodem.
