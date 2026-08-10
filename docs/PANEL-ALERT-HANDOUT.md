# Karta „Claude czeka na Ciebie" — zbudowany projekt

Karta, którą panel biurkowy pokazuje, gdy sesja Claude Code stanęła i czeka na człowieka.
Ten dokument opisuje **to, co jest zbudowane**: cztery układy zależne od liczby blokad,
warstwę ruchu i stan po zwinięciu. Wcześniejsza wersja robocza — zlepiona ze słownika pasów,
żeby sprawdzić mechanikę — nie istnieje.

Punkt odniesienia to nie opis, tylko obrazy: `docs/handout/*.png`, wyrenderowane przez panel
i przepuszczone przez jego własną kwantyzację, czyli **dokładnie to, co pokaże szkło**.

| Plik | Co pokazuje |
|---|---|
| [`karta-solo.png`](handout/karta-solo.png) | jedna blokada — nazwa projektu bohaterem, kafel `Szczegół`, listwa `Tryb` |
| [`karta-pair.png`](handout/karta-pair.png) | dwie blokady — dwie równe połowy po 140 px |
| [`karta-list.png`](handout/karta-list.png) | trzy blokady — lista 3 × 77 px, szczegół najnowszej w stopce |
| [`karta-many.png`](handout/karta-many.png) | pięć blokad — trzy wiersze i licznik reszty |
| [`karta-zalana.png`](handout/karta-zalana.png) | klatka **pełna** warstwy ruchu: pasmo w akcencie plus rail 6 px |
| [`pasy-znacznik-gorny.png`](handout/pasy-znacznik-gorny.png) | stan po zwinięciu — pasek 4 px przy koncie górnym |
| [`pasy-znacznik-dolny.png`](handout/pasy-znacznik-dolny.png) | to samo przy koncie dolnym (z kredytami — pas i tak równy) |
| [`pasy-bez-alertu.png`](handout/pasy-bez-alertu.png) | ten sam ekran bez alertu, do porównania |

Wygenerować ponownie:

```
cd panel
python tools/render-png.py --alert list --zoom 3 --rgb565 --out karta.png
python tools/render-png.py --alert solo --flood --zoom 3 --rgb565 --out zalana.png
python tools/render-png.py --marker upper --zoom 3 --rgb565 --out znacznik.png
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
  `draw.clock_glyph` jest tego precedensem.
- **English on screen** — on-screen text is written in English (project rule 10; the migration
  is still running, so the screenshots below show Polish until they are re-rendered). Height is
  measured from the actual outline, not from the nominal size.

### Paleta (`panel/panel/theme.py`)

| Nazwa | RGB | Gdzie |
|---|---|---|
| `BG` | `#1C1B19` | tło całego ekranu, także karty; **oraz napisy w zalanym pasmie** |
| `SURFACE` / `SUNKEN` | `#262523` / `#211F1D` | kafel `Szczegół` / listwa `Tryb` i stopki list |
| `TEXT` | `#F0EEE6` | nazwa projektu |
| `ACCENT` / `ACCENT_500` | `#D97757` | paski, kropka łącza, **zalane pasmo, rail 6 px, znacznik 4 px** |
| `ACCENT_800` | `#46281D` | tło pasma w klatce spoczynkowej |
| `ACCENT_700` / `_300` / `_200` / `_100` | `#8A4A33` / `#E89477` / `#F0AB90` / `#F7CBB8` | `_200` = czas czekania i powody, `_100` = tytuł pasma i nazwa konta z blokadą |
| `NEUTRAL_900…100` | `#322F2B` … `#D6D2C6` | tory pasków; `_900` **także rail w klatce spoczynkowej** |
| `TEXT_78…TEXT_10` | mieszanki `TEXT` z tłem karty | teksty wtórne na tle `BG` |
| `TEXT_*_SURFACE` / `TEXT_*_SUNKEN` | te same procenty, ale na tle kafla / listwy | napisy w kaflu `Szczegół`, w listwie `Tryb` i w stopkach list |

Te dwa ostatnie wiersze to nie duplikaty. W CSS `color-mix(…, transparent)` miesza się
z tym, co **akurat jest pod spodem**, więc ten sam procent nad kaflem i nad tłem karty
to dwa różne kolory. Panel nie ma alfy — miesza z góry — więc każda para (procent, tło)
musi być osobną stałą. Różnica teł to (5, 4, 4), czyli po kwantyzacji 5/6/5 słychać ją
na kanale zielonym; audyt zgodności złapał ją na etykiecie w kaflu i w obu stopkach.

**Czerwieni nie ma w projekcie.** `DANGER` i `draw.warn_triangle` zostały usunięte razem
z trójkątem ostrzegawczym — cała sygnalizacja stoi na rampie akcentu. Panel ma dzięki temu
tę samą paletę co `frontend/src/styles/theme.css`, bez ani jednego wyjątku.

---

## Cztery układy

Wybiera je **liczba blokad**, nie flaga (`render.Renderer._alert`). Próg jest przy trzech:
do dwóch nazwa projektu zostaje bohaterem, bo do konkretnego okna trzeba wrócić i trzeba
wiedzieć, do którego; od trzech nazwy schodzą do listy, bo trzy nazwy w 34 px nie istnieją.

Liczy się liczba blokad **czekających**, nie tych z ostatnich pięciu minut: okno przejęcia
należy do **zbioru**, więc dopóki którakolwiek jest w oknie, karta wypisuje wszystkie.
Dopóki okno było liczone osobno dla każdego wpisu, trzy z tych czterech układów nie miały
jak wejść na ekran — dwie blokady musiałyby zacząć się w tym samym pięciominutowym oknie.

| Blokad | Układ | Co widać |
|---|---|---|
| 1 | `AlertSolo` | nazwa 34 px (26 px, gdy nie wchodzi), narzędzie i maszyna, „czeka N", kafel `Szczegół` do dwóch linii, listwa `Tryb` |
| 2 | `AlertPair` | dwie równe połowy, nazwa 30 px, szczegół skrócony do jednej linii, bez listwy |
| 3 | `AlertList` | powód w stałej kolumnie 58 px, nazwa 19 px, czas do prawej, szczegół najnowszej w stopce |
| 4+ | `AlertMany` | trzy najnowsze z nazwą 17 px i samą maszyną, reszta zliczona w stopce („+2 WIĘCEJ" i nazwy) |

Kolejność zawsze z `panel/panel/status.py`: **od najmłodszej**, wpis bez `since` na końcu.
Powód na nią nie wpływa. Wcześniej rządziła ranga (`plan`, `question`, `permission`) i było
to nieszkodliwe tylko dopóki wszystko na karcie miało poniżej pięciu minut; odkąd okno należy
do zbioru, ranga wypychała z wierszy właśnie tę blokadę, która przejęła ekran. Uzasadnienie
nowej kolejności: **każda blokada była już pokazana solo, gdy wchodziła** — przy obcięciu do
trzech wierszy warte pokazania są te, których jeszcze nie widziałeś.
**Godzina w pasmie to początek najstarszego czekania na
ekranie**, nie `since` nagłówka — wiersze idą od najmłodszej, więc pierwszy z nich jest
z założenia najnowszy, a pasmo mówi, jak długo to wszystko już stoi.

Pasmo przy wielu blokadach pisze `CZEKAJĄ · 3`, a nie „3 czekają": przy pięciu ta druga forma
jest błędem („5 czekają"), a gołe „czekają" bez związanego liczebnika jest poprawne dla każdej
liczności.

### Pola wpisu

Pełny kontrakt w [`API.md` § 3.2](API.md). Wszystkie są używane:

| Pole | Gdzie na karcie |
|---|---|
| `reason` | nagłówek pasma (1 blokada) i słowo powodu w liście; **nieznana wartość jest legalna** i daje „CLAUDE CZEKA" / „czeka" |
| `project` | nazwa **korzenia** projektu — bohater układów 1a/1b, kolumna w liście |
| `tool` | wiersz pod nazwą (nie w układzie 4+, gdzie zostaje sama maszyna) |
| `machine` | tam samo; sesja może chodzić zdalnie, to mówi, gdzie iść |
| `detail` | kafel `Szczegół` (2 linie), jedna linia w układzie 2, stopka w układzie 3 |
| `since` | „czeka N", godzina w pasmie, kolejność wierszy, oraz otwarcie okna dla zbioru |
| `accountUuid` | przy którym pasie stanie znacznik po zwinięciu |
| `agentType`, `permissionMode` | listwa `Tryb` — odpowiedź na „czemu on w ogóle pyta" |

---

## Warstwa ruchu: dwie klatki, nie animacja

Panel przerysowuje się **linia po linii od góry**, więc animacja w krokach rozjechałaby się
na przebiegu i wyglądała jak usterka. Ruch, który został, to podmiana **dwóch pełnych klatek**:

- **pusta** — pasmo `ACCENT_800`, rail 6 px w `NEUTRAL_900`,
- **pełna** — pasmo zalane `ACCENT` i rail w `ACCENT`,
  a napisy w paśmie schodzą na `BG` (5,51:1 zamiast 2,69:1).

**Rail stoi w obu klatkach** — zalanie go tylko przemalowuje. Pasek pojawiający się
z niczego jest mocniejszym ruchem niż zmiana koloru, a poza oknem `alert_flash_sec` karta
zostawałaby bez lewej krawędzi. Rail idzie na całą wysokość pod pasmem, także przez listwę
`Tryb` i przez stopki list — w makiecie jest `position: absolute`, więc maluje się nad
blokami w przepływie. Pilnuje tego `test_rail_stoi_w_obu_klatkach_kazdego_ukladu`, osobno
dla każdego z czterech układów i obu klatek.

Zalanie brudzi ~13 % klatki, czyli ok. 0,24 s na Turingu — mieści się w ticku. Pełnoekranowy
błysk byłby pełną klatką (1,87 s), czyli powolnym zamalowaniem zamiast błysku. Pilnuje tego
`panel/tests/test_alert.py::test_klatka_pelna_miesci_sie_w_ticku`.

**Nic poza pasmem i railem nie rusza się między klatkami** — tekst skaczący o piksel czytałby
się jako usterka. Makieta ma w zalanym paśmie napis o 2 px wyżej niż w spoczynkowym (osobna
warstwa, inne zaokrąglenie sub-pikselowe); panel trzyma jedną linię bazową dla obu.

Warstwa ruchu rysuje się **na końcu**, nad treścią: inaczej listwa `Tryb` zamalowałaby rail.

**Świadome odstępstwo: godzina w zalanym paśmie.** Makieta jest tu sama ze sobą niezgodna —
`1a-alert` maluje ją pełnym `BG`, a trzy ramki `-p` dają `color-mix(BG 76%, transparent)`,
czyli `#493128`. Panel trzyma pełne `BG` we **wszystkich czterech** układach: to 5,51:1
na akcencie, a wersja 76% daje 3,84:1 — poniżej AA dla tekstu 15 px. Godzina jest jedyną
liczbą na tej karcie, a karta istnieje po to, żeby ktoś wstał od biurka.

---

## Ograniczenia, których nie da się obejść projektem

Każdy punkt jest zmierzony.

**1. Zmiana sceny nie mieści się w ticku.** Przejście pasy → karta brudzi ok. **62 %** klatki
w kilkudziesięciu prostokątach, czyli ~**1,17 s** na Turingu plus 355 ms na AX206. `link.send`
idzie w zwykłej pętli po ekranach, więc tick kosztuje **sumę** obu: ≈ **1,5 s** przy
`tick_sec = 1.0`. Przejście gubi jeden tick — to zaakceptowane, nie do ukrycia.

**2. Próg pełnej klatki to 0,85** (`panel/panel/surface.py`). Zestaw wycinków nigdy nie jest
cięższy od pełnej klatki, więc realnym ograniczeniem jest `MAX_RECTS = 256`. Układ rozsypany
na kilkadziesiąt drobnych elementów **jest** droższy od kilku zwartych bloków — nie przez
bajty, tylko przez liczbę prostokątów. Pilnuje tego
`test_przejscie_do_karty_miesci_sie_pod_progiem_dla_kazdego_ukladu`, osobno dla wszystkich
czterech układów.

**3. Czas MUSI być gruboziarnisty.** AX206 nie umie wycinków, więc każda zmiana napisu to
pełne 355 ms. Sekundy zamieniłyby ~2,5 % obciążenia USB w ~35 % **na cały czas trwania
karty**. Stąd `fmt.waited()`: „chwilę" / „4 min" / „1 h 05 min" / „2 d 3 h".
**Żywego zegara na karcie nie ma** — godzina w paśmie to statyczny moment. Każdy element,
który tyka szybciej niż raz na minutę, jest zakazany.

**4. Karta oddaje ekran po 5 minutach od OSTATNIEJ blokady.** `alert_takeover_sec`
(domyślnie 300 s, liczone od `since` **z serwera**) zwija ją do paska 4 px przy nazwie konta.
Okno należy do zbioru, więc odmierza je najmłodsza blokada, a nie każda z osobna — czas karty
na ekranie jest przez to taki sam jak przy liczeniu per wpis, zmienia się tylko jej zawartość.
Ten stan widać dłużej niż samą kartę.

**5. Znacznik mieści się w pasie bez ruszania układu.** Pasek 4 px siedzi w polu marginesu
(`PAD_X` 14) i ma **pełną wysokość pasa**, niezależnie od liczby wierszy w środku — konto
z kredytami ma cztery wiersze zamiast trzech, a pas i tak jest równy. Powód dochodzi 10 px
wersalikami w linii z nazwą planu i odejmuje sobie miejsce z budżetu tytułu, a nazwa konta
przechodzi na `ACCENT_100`.

**6. Karta bije wszystko, także kartę „Niezgodny kontrakt".** Ta ostatnia schodzi wtedy do
listwy `Tryb`. Odwrotna kolejność zdegradowałaby alert do wiersza na dole ekranu błędu — a to
jest jedyna rzecz na tym panelu, która wymaga, żebyś wstał od biurka.

**7. Debounce 2 s przy wejściu, linger 10 s przy wyjściu**, karta w lingerze jest
**zamrożona**. To osądy, nie pomiary, i wszystkie trzy są kluczami w `panel.json`.

---

## Idiomy, których trzyma się ten kod

- **Współrzędne wyliczane ze stałych** w `panel/panel/layout.py`, nigdy wpisane w kod rysujący.
- **Linie bazowe zmierzone na wyrenderowanej makiecie**, nie policzone z pudełek CSS: krój
  panelu ma inne metryki pionowe niż font makiety, więc ten sam model pudełka kładzie tusz
  o 1–3 px gdzie indziej. Odstęp między pudełkami jest niewidzialny, położenie tuszu widać.
  **Pomiar mierzy DÓŁ TUSZU**, nie środek pudełka — a `anchor="ls"` w Pillow kładzie go na
  `base − 1`. `BANNER_BASE` i `MODE_BASE` stały przez to dwa piksele za nisko, aż audyt
  zgodności zmierzył je ponownie (24,33 px i 306,33 px na makiecie renderowanej w 3×).
  Pilnuje tego `test_tusz_pasma_i_listwy_stoi_tam_gdzie_makieta`.
- **Prostokąty w konwencji pół-otwartej** (`draw.fill_rect`, `draw.rounded`): Pillow liczy
  granice włącznie, więc pasmo `(0, 0, 480, 38)` wychodzi u niego 39 px.
- `draw.ellipsize` na **każdym** napisie o nieznanej długości; `draw.ellipsize_tracked` tam,
  gdzie napis idzie przez `text_tracked` (odstęp międzyliterowy wchodzi do szerokości).
- Zejście o stopień zamiast obcięcia, gdy tekst nie wchodzi: `F_PROJECT` 34 px → 26 px.
- Kursor prawo-do-lewa w nagłówku pasa — elementy odejmują sobie miejsce od prawej, a tytuł
  bierze resztę.
- Trzy stopnie tekstu i nic pomiędzy: 10 px wyłącznie etykiety wersalikami, 11 px dane
  wtórne, od 12 px treść. Etykiety wersalikami mają `letter-spacing` (`draw.text_tracked`).

## Gdzie co siedzi

`panel/panel/layout.py` → `AlertSolo`, `AlertPair`, `AlertList`, `AlertMany` (geometria
i stopnie czcionek) oraz `panel/panel/render.py` → `Renderer._alert*` (rysowanie).
Nowe pole z ramki dochodzi w `status.Blocked`, `render.AlertRow` i `render.alert_state`.
Transport, maszyna stanów, debounce, wypalanie okna i znacznik stoją osobno i są otestowane.
