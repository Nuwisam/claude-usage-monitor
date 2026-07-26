# UI — brief dla makiet i kontrakt API

Dokument pełni dwie role: **brief dla projektu makiet** i **kontrakt dla implementacji**.
Wszystkie przykłady są **wygenerowane z działającego systemu**, nie wymyślone.

Stan na 2026-07-26: backend wdrożony i zbiera dane. Frontend nie istnieje.
Base URL: `https://usage.example.org/claude-usage/api`

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
| `GET /accounts` · `PATCH /accounts/{uuid}` | Lista kont / edycja `label`, `color`, `isEnabled` |
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

**Limity są kaskadą, nie jedną liczbą.** Okno 5 h / tygodniowe → po wyczerpaniu **kredyty**
(`extra:usage`, `spend:org`) → twardy blok. Na Max kredyty są wyłączone (`spend.enabled=false`),
więc wyczerpanie tygodniowego to od razu stop. Na Team kaskada jest pełna. UI powinno pokazywać,
**na którym szczeblu jesteś**, a nie tylko górny procent.

**Kwoty w `spend.extra` są w jednostkach mniejszych z wykładnikiem** —
`{"amount_minor": 0, "currency": "USD", "exponent": 2}` znaczy `0,00 USD`. Nie upraszczaj do float.

**`gaps[]` w `/history` ma dwa rodzaje** i wymaga dwóch różnych cieniowań: cisza klienta
(`client_silent`) to co innego niż brak zmian w danych. Przy tym źródle **brak danych jest
informacją**, nie usterką wykresu.

---

## 7. Realna odpowiedź `GET /status`

Wygenerowana z działającego systemu, konto Max, 2026-07-26 19:07 UTC. Skrócona do dwóch serii;
pełna ma sześć.

```json
{
  "contractVersion": 1,
  "serverNow": "2026-07-26T19:07:37.564772",
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
    "lastSampleAt": "2026-07-26T19:07:37.532549",
    "lastBatchAt": "2026-07-26T19:07:37.532549",
    "lastClientHost": "desktop",
    "series": [
      {
        "seriesId": 3,
        "seriesKey": "limit:session|session|-|-",
        "label": "Sesja",
        "source": "limit",
        "sortOrder": 15,
        "utilization": 31.0,
        "rawUtilization": 31.0,
        "resetsAt": "2026-07-26T20:00:00.848603",
        "secondsToReset": 3143,
        "capturedAt": "2026-07-26T19:07:21",
        "freshness": "live",
        "isActive": true,
        "severity": "normal",
        "deltaPct1h": 0.0,
        "primary": true,
        "duplicateOf": null,
        "extra": null
      },
      {
        "seriesId": 1,
        "seriesKey": "bucket:five_hour",
        "label": "Sesja (5 h)",
        "source": "bucket",
        "sortOrder": 10,
        "utilization": 31.0,
        "rawUtilization": 31.0,
        "resetsAt": "2026-07-26T20:00:00.848603",
        "secondsToReset": 3143,
        "capturedAt": "2026-07-26T19:07:21",
        "freshness": "live",
        "isActive": null,
        "severity": null,
        "deltaPct1h": 0.0,
        "primary": false,
        "duplicateOf": "limit:session|session|-|-",
        "extra": {"limit_dollars": null, "used_dollars": null, "remaining_dollars": null}
      }
    ]
  }]
}
```

Zwróć uwagę: te dwie serie to **ten sam limit**. Druga ma `primary: false` i wskazuje na
pierwszą — domyślnie pokazuj tylko pierwszą.

Seria `spend:org` w `extra` niesie:

```json
{"enabled": false, "cap": null, "limit": null, "balance": null,
 "used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
 "can_purchase_credits": false, "can_toggle": false,
 "disclaimer": "Usage credits cover you when you hit your plan limits. …"}
```

## 8. Realna odpowiedź `GET /history`

```json
{
  "bucket": "raw",
  "points": [{"t": "2026-07-26T18:59:09", "min": 29.0, "max": 29.0,
              "avg": 29.0, "last": 29.0, "n": 1}],
  "resets": [],
  "gaps": [{"from": "2026-07-26T17:55:02", "to": "2026-07-26T18:59:10",
            "kind": "client_silent"}]
}
```

`bucket=auto` dobiera agregację do szerokości zakresu: `raw` do 6 h, `5m` do 48 h, `1h` powyżej.
Przy agregacji `min`/`max` są po to, żeby **piki przeżyły** — rysuj pasmo min-max za linią `avg`,
inaczej wykres kłamie.

---

## 9. Co jest do zaprojektowania

Punkt wyjścia, nie wymóg co do liczby ekranów:

- **Live** — stan bieżący per konto i seria. **To jedyny widok, którego realnie potrzebuję.**
  Musi odpowiadać na „czy mogę teraz odpalić duże zadanie" w jednym spojrzeniu.
- **History** — przebieg w czasie, granice resetów, dziury.
- **Accounts + Maszyny** — kto raportował, kiedy, jaka wersja Claude Code, gotowy blok
  `settings.json` do skopiowania przy instalacji na nowej maszynie.
- **Diagnostics** — `events` (w tym oś czasu przełączeń konta), ostatni surowy payload,
  `stats`.

### Czego handout celowo NIE przesądza

Stacku frontendu, liczby i podziału ekranów, sposobu wizualizacji (gauge / pasek / sama liczba),
palety, biblioteki wykresów, układu. To wraca z makietami.

---

## 10. Zanim powstanie UI

System jest użyteczny przez `curl` i przeglądarkę:

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

`/status` zwraca `contractVersion` (obecnie `1`). Przy zmianie łamiącej zgodność liczba
rośnie — UI ma to sprawdzać i **głośno protestować**, zamiast po cichu renderować śmieci.

Kontrakt zamrożony po tym, jak przez system przeszły prawdziwe dane z konta Max. Konto Team
zostanie zweryfikowane, gdy wróci mu limit — jeśli ujawni pola, których tu nie ma, wersja
kontraktu wzrośnie, a ten dokument zostanie zaktualizowany **przed** projektowaniem makiet.
