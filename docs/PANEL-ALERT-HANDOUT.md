# Karta „Claude czeka na Ciebie" — materiał do przeprojektowania

Karta, którą panel biurkowy pokazuje, gdy sesja Claude Code stanęła i czeka na człowieka,
**działa i jest wersją roboczą**. Zbudowano ją wyłącznie z istniejącego słownika wizualnego,
żeby mechanika była sprawdzona, zanim ktoś się nią zajmie. Ten dokument jest wszystkim, czego
potrzeba, żeby ją zaprojektować od nowa — z pomiarami, które ograniczają, co wolno.

Punkt wyjścia to nie opis, tylko obrazy: `docs/handout/*.png`, wyrenderowane przez panel
i przepuszczone przez jego własną kwantyzację, czyli **dokładnie to, co pokaże szkło**.

| Plik | Co pokazuje |
|---|---|
| [`alert-permission.png`](handout/alert-permission.png) | prośba o zgodę na narzędzie — najczęstszy przypadek |
| [`alert-question.png`](handout/alert-question.png) | `AskUserQuestion` |
| [`alert-plan.png`](handout/alert-plan.png) | `ExitPlanMode` — najdłuższy nagłówek, 235 px z 480 |
| [`alert-long.png`](handout/alert-long.png) | nazwa projektu nie mieszcząca się w dużym stopniu |
| [`alert-multi.png`](handout/alert-multi.png) | trzy blokady naraz: nagłówek + wiersz „inne:" |
| [`pasy-trojkat.png`](handout/pasy-trojkat.png) | stan po zwinięciu — trójkąt obok nazwy konta |
| [`pasy-bez-alertu.png`](handout/pasy-bez-alertu.png) | ten sam ekran bez alertu, do porównania |

Wygenerować ponownie:

```
cd panel
python tools/render-png.py --alert multi --zoom 3 --rgb565 --out alert.png
python tools/render-png.py --triangle --zoom 3 --rgb565 --out trojkat.png
```

`--rgb565` jest **obowiązkowe** przy ocenie: bez niego oglądasz ładniejszą prawdę z pulpitu.

---

## Płótno i materiał

- **480 × 320 px, jeden układ poziomy.** Ćwierć obrotu nie istnieje — panel przyjmuje
  wyłącznie 0° albo 180°, bo obrót o 90° wymagałby drugiego układu (320 × 480).
- **RGB565 (5/6/5).** Każda para tło/pierwszy plan musi przeżyć kwantyzację; pilnują tego
  `panel/tests/test_render.py::test_kolory_przezywaja_kwantyzacje` i
  `::test_pary_kolorow_przezywaja_kwantyzacje`.
- **Bez przezroczystości.** W CSS pół-krycie robi `color-mix(…, transparent)`; panel miesza
  z góry (`theme.mix`), więc w czasie rysowania nie ma żadnej alfy.
- **Czcionka: Segoe UI Regular.** Pillow ma `raqm: False`, więc nie da się poprosić
  o `tabular-nums` — cyfry muszą być tabularne **same z siebie**. Segoe UI Light **nie
  jest** (jedynka węższa od zera), więc duża liczba drgałaby przy każdej zmianie.
- **Ikony rysowane wektorowo, nigdy fontem ikon.** Przy 11 px font zamienia się w plamę;
  `draw.clock_glyph` i `draw.warn_triangle` są tego precedensem.
- **Polskie znaki z ogonkami** — tekst widoczny na ekranie pisze się po polsku (zasada 10
  projektu). Wysokość mierzy się z faktycznego obrysu, nie z nominalnego rozmiaru, bo „Ń"
  sięga wyżej niż wielkie litery bez znaku diakrytycznego.

### Paleta (`panel/panel/theme.py`)

| Nazwa | RGB | Gdzie dziś |
|---|---|---|
| `BG` | `#1C1B19` | tło całego ekranu, także karty |
| `SURFACE` / `SUNKEN` | `#262523` / `#211F1D` | niewykorzystane na karcie |
| `TEXT` | `#F0EEE6` | nazwa projektu |
| `ACCENT` / `ACCENT_500` | `#D97757` | paski, kropka łącza |
| `ACCENT_800` | `#46281D` | **tło banera karty** |
| `ACCENT_700` / `_300` / `_200` / `_100` | `#8A4A33` / `#E89477` / `#F0AB90` / `#F7CBB8` | `_200` = „czeka N min", `_100` = tytuł banera |
| `NEUTRAL_900…100` | `#322F2B` … `#D6D2C6` | tory pasków |
| `DANGER` | `#E04B3A` | **trójkąt ostrzegawczy — jedyna czerwień w projekcie** |
| `TEXT_78…TEXT_10` | mieszanki `TEXT` z tłem | teksty wtórne |

`DANGER` nie ma odpowiednika w `frontend/src/styles/theme.css` — w całej palecie projektu nie
było czerwieni. Zielona składowa musi zostać nisko (≤ `0x50`): akcent panelu sam jest
pomarańczowo-czerwony, więc jaśniejszy odcień przeczyta się jako „trochę inny pomarańcz",
a nie jako inny kolor. **Odcień trzeba ocenić na szkle, nie na monitorze.**

Tło karty zostaje `BG`. Pełnoekranowe pole akcentu przy jasności 5 to blask w oczy
i widoczne pasmowanie RGB565.

---

## Co karta ma do pokazania

Pola jednego wpisu — pełny kontrakt w [`API.md` § 3.2](API.md):

| Pole | Przykład | Uwagi |
|---|---|---|
| `reason` | `permission` / `question` / `plan` | mapowane na nagłówek; **nieznana wartość jest legalna** (writer bywa nowszy niż panel) i daje „CLAUDE CZEKA" |
| `project` | `claude-usage-monitor` | nazwa **korzenia** projektu, nie `basename(cwd)` |
| `tool` | `Bash`, `AskUserQuestion` | |
| `machine` | `laptop`, `desktop` | sesja może chodzić zdalnie — to mówi, gdzie iść |
| `detail` | `git status` | obcinane do 120 znaków po stronie klienta; **dziś nierysowane** |
| `since` | `2026-08-05T21:00:00Z` | z niego liczy się i „czeka N", i wypalenie okna |
| `accountUuid` | | decyduje, przy którym pasie stanie trójkąt |
| `agentId` / `agentType` | `agent-a` / `general-purpose` | blokada w subagencie; **dziś nierysowane** |
| `permissionMode` | `default` | diagnostyka |

Blokad może być kilka naraz. Dziś: pierwsza jest nagłówkiem, reszta schodzi do wiersza
„inne: alfa, beta". Kolejność ustala `panel/panel/status.py`: najpierw `plan`, potem
`question`, potem `permission`, a w remisie najstarsze `since`.

`detail` i `agentType` są **dostępne i nieużyte** — to najbardziej oczywisty zapas
informacji dla nowego projektu.

---

## Ograniczenia, których nie da się obejść projektem

To jest właściwa treść tego dokumentu. Każdy punkt jest zmierzony.

**1. Zmiana sceny nie mieści się w ticku.** Przejście pasy → karta brudzi **62,5 %** klatki
w 45 prostokątach, czyli **1,17 s** na Turingu (wycinkami, bo to poniżej progu pełnej klatki)
plus 355 ms na AX206. `link.send` idzie w zwykłej pętli po ekranach, więc tick kosztuje
**sumę** obu: ≈ **1,5 s** przy `tick_sec = 1.0`. Gdyby to samo przejście poszło pełną klatką,
byłoby 1874 + 355 ms ≈ 2,23 s — i dokładnie tak było przy dawnym progu 0,60.

Przejście gubi więc jeden tick — to zaakceptowane, nie do ukrycia. Praktyczny wniosek:
**im mniej piksela zmienia się między pasami a kartą, tym taniej**, ale nie ma projektu,
który zejdzie poniżej kosztu przemalowania tła (samo tło to 1,27 s pełnej klatki).

**2. Próg pełnej klatki to 0,85** (`panel/panel/surface.py`). Zestaw wycinków nigdy nie jest
cięższy od pełnej klatki — `coalesce()` dowodnie nie pokrywa czystego piksela, a drut kosztuje
6,1 µs/bajt bez narzutu stałego. Realnym ograniczeniem jest `MAX_RECTS = 256`. Projekt
rozsypany na kilkadziesiąt drobnych, rozrzuconych elementów **jest** droższy od kilku
zwartych bloków — nie przez bajty, tylko przez liczbę prostokątów.
Pilnuje tego `panel/tests/test_alert.py::test_przejscie_do_karty_miesci_sie_pod_progiem_pelnej_klatki`.

**3. Czas MUSI być gruboziarnisty.** AX206 nie umie wycinków, więc każda zmiana napisu to
pełne 355 ms. Sekundy zamieniłyby ~2,5 % obciążenia USB w ~35 % **na cały czas trwania
karty**. Stąd `fmt.waited()`: „chwilę" / „4 min" / „1 h 05 min" / „2 d 3 h".
**Żywego zegara na karcie nie ma** — `20:57` w banerze to statyczny moment pojawienia się
promptu. Każdy element, który tyka szybciej niż raz na minutę, jest zakazany.

**4. Karta oddaje ekran po 5 minutach.** `alert_takeover_sec` (domyślnie 300 s, liczone od
`since` **z serwera**) zwija ją do czerwonego trójkąta obok nazwy konta. Projekt musi więc
obejmować **oba** stany, a nie tylko kartę — i trójkąt jest tym, co widać dłużej.

**5. Trójkąt mieści się w nagłówku pasa bez ruszania układu.** `render._header` używa kursora
prawo-do-lewa: znacznik łącza, zegar i plakietka planu dekrementują prawą krawędź, a nazwa
konsumuje resztę przez `ellipsize`. Zmierzony zapas przy najdłuższej realnej nazwie konta (adres e-mail, 144 px): 202 px
w pasie górnym, 259 px w dolnym. Trójkąt z odstępem to 19 px, czyli < 6 % budżetu tytułu,
i gryzie **wyłącznie** wtedy, gdy nazwa i tak jest skracana.

**6. Karta bije wszystko, także kartę „Niezgodny kontrakt".** Ta ostatnia schodzi wtedy do
stopki. Odwrotna kolejność zdegradowałaby alert do wiersza na dole ekranu błędu — a to jest
jedyna rzecz na tym panelu, która wymaga, żebyś wstał od biurka.

**7. Debounce 2 s przy wejściu, linger 10 s przy wyjściu**, karta w lingerze jest
**zamrożona**. To osądy, nie pomiary, i wszystkie trzy są kluczami w `panel.json`.

---

## Idiomy, których warto się trzymać

- `draw.ellipsize(text, font, max_w)` na **każdym** napisie o nieznanej długości. Nazwy kont
  i projektów bywają dłuższe, niż 480 px pozwala.
- Zejście o stopień zamiast obcięcia, gdy tekst nie wchodzi: `F_PROJECT` 34 px → 26 px.
  To powtórzenie istniejącego `F_SES_NUM` → `F_SES_NUM_TIGHT`, a nie nowy mechanizm.
- Kursor prawo-do-lewa w nagłówku — elementy odejmują sobie miejsce od prawej, a tekst
  bierze resztę.
- Trzy stopnie tekstu i nic pomiędzy: 10 px wyłącznie etykiety wersalikami, 11 px dane
  wtórne, od 12 px treść.
- Etykiety wersalikami mają `letter-spacing` (`draw.text_tracked`, tracking 1) — bez tego
  zlewają się w plamę.
- Współrzędne **wyliczane ze stałych** w `panel/panel/layout.py`, nigdy wpisane na sztywno
  w kod rysujący.

## Co zmienić, gdy projekt będzie gotowy

`panel/panel/layout.py` → klasa `Alert` (geometria i stopnie czcionek) oraz
`panel/panel/render.py` → `Renderer._alert` (rysowanie). Jeśli dojdą nowe pola,
także `render.AlertState` i `render.alert_state`. Cała reszta — transport, maszyna stanów,
debounce, wypalanie okna, trójkąt — stoi i jest otestowana.
