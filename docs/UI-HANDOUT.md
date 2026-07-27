# UI — kontrakt API (v2)

Dokument powstał jako **brief dla makiet**; makiety przyszły i UI jest zbudowane, więc dziś
jest to przede wszystkim **kontrakt**. Wszystkie przykłady są **wygenerowane z działającego
systemu**, nie wymyślone.

Stan na 2026-07-27: backend i frontend wdrożone pod `https://usage.example.org/claude-usage/`.
Base URL API: `https://usage.example.org/claude-usage/api`

**`contractVersion` = 2.** Co doszło względem v1 i dlaczego:

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
| `GET /status` | **Główny endpoint.** Stan bieżący wszystkich kont i serii. Odpytywać co **15 s** |
| `GET /history?account=&seriesId=&from=&to=&bucket=auto` | Przebieg w czasie + dziury + granice resetów |
| `GET /accounts` | Lista kont. **`PATCH /accounts/{uuid}` NIE ISTNIEJE** — kolumny `label`, `color`, `isEnabled` są w bazie, ale nie ma ścieżki zapisu. Nie buduj na nim edycji |
| `GET /machines` | Które maszyny raportowały które konta, z wersją Claude Code |
| `GET /series` | Rejestr serii (zob. § 6 — lista jest otwarta) |
| `GET /events` | Log operacyjny: przełączenia konta, drift schematu, błędy klienta |
| `GET /batches` · `GET /batches/{id}/raw` | Log przyjęć + surowa odpowiedź Anthropic |
| `GET /stats` | Liczniki, współczynnik dedupu, skuteczność ingestu 24 h |

---

## 4. Cztery stany świeżości — to jest kontrakt, nie sugestia stylistyczna

Każda seria w `/status` ma pole `freshness`. **Nie wolno ich zlewać w jeden.**

| `freshness` | Znaczenie | `utilization` | Jak pokazać |
|---|---|---|---|
| `live` | Próbka świeższa niż 5 min | liczba | normalnie |
| `stale` | Starsza, ale okno wciąż trwa — wartość nadal prawdziwa, bo zużycie nie rośnie samo | liczba | z etykietą czasu obserwacji |
| `inferred_reset` | Okno się zresetowało i klient milczał — **wnioskujemy** ~0% | `0.0` | wyraźnie inaczej niż pomiar |
| `unknown` | Klient raportuje, ale brak danych dla tej serii — **awaria** | **`null`** | „nie wiem", **nigdy 0%** |

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
duplikaty. **Domyślnie pokazuj tylko `primary: true`.** Wpis z `limits[]` wygrywa, bo niesie
`isActive` i `severity`. Gdy wartości się rozjadą, pary nie powstaną i obie serie będą
widoczne — to celowe, wolimy pokazać rozjazd niż go ukryć.

**`isActive` mówi, co realnie ogranicza *teraz*.** To najcenniejsze pole w całej odpowiedzi
i zasługuje na eksponowanie. Zaobserwowane: wiążący limit **przeskakuje w czasie** — rano
`weekly_all`, po intensywnej sesji `session`.

**`severity`** to gotowa klasyfikacja od Anthropic (`normal`, …). Używaj jej zamiast
wymyślać własne progi.

**Countdowny licz od `serverNow`** z odpowiedzi, nie od zegara przeglądarki. `secondsToReset`
jest policzone po stronie serwera.

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
  "contractVersion": 2,
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
        "freshness": "live",
        "isActive": true,
        "severity": "critical",
        "deltaPct1h": 28.0,
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
        "freshness": "live",
        "isActive": false,
        "severity": "normal",
        "deltaPct1h": 3.0,
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

`/status` zwraca `contractVersion` (obecnie **`2`**). Przy zmianie łamiącej zgodność liczba
rośnie — UI sprawdza to i **głośno protestuje** w nagłówku, zamiast po cichu renderować śmieci
(`frontend/src/components/Nav.tsx`, stała `CONTRACT_VERSION` w `api/types.ts`).

Dodanie pola jest zmianą **nie**łamiącą i nie wymaga podbicia. Wersja poszła z 1 na 2 wyłącznie
przez zmianę serializacji czasu — reszta v2 to dodatki.

Konto Team nie zostało jeszcze zweryfikowane na żywo (ma wyczerpany limit tygodniowy). To ono
włącza szczeble `credits` i `hard_block` z kwotami; do tego czasu stoją one na fixture
zbudowanym z `docs/POC-FINDINGS.md` i makiety. Jeśli prawdziwa odpowiedź ujawni pola, których
tu nie ma, ten dokument idzie do aktualizacji razem z kodem.
