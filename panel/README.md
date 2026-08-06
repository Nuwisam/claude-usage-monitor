# panel — limity Claude na wyświetlaczach USB

Klient headless, który subskrybuje `/api/stream` i rysuje stan limitów na
ekranach stojących na biurku. Układ **4a** z makiety: dwa konta w pasach, procent
obok bloku, kredyty na dole pasa. Renderer jest jeden i rysuje jedno logiczne
płótno 480×320; ekrany różnią się tym, co z tym płótnem robią.

```
python -m panel                          pętla (to samo, co robi zadanie harmonogramu)
python -m panel --list                   jakie ekrany widać i na których portach
python -m panel --identify ax206#0       namaluj wielki numer na wskazanym ekranie
python -m panel --probe [--backend N]    karta testowa: kolory, paski, ogonki, prostokąty
python -m panel --once                   jedna klatka z prawdziwych danych i wyjście
```

Jedna wyrenderowana klatka idzie na **wszystkie** skonfigurowane ekrany; każdy ma
własne połączenie, własny backoff i własną pamięć tego, co pokazuje. Sterowniki
siedzą w [`panel/drivers/`](panel/drivers) — jeden plik na typ ekranu, rejestr
jest jawnym słownikiem w `__init__.py`.

Bez sprzętu:

```
python tools/render-png.py --scene states --zoom 3 --rgb565 --out out.png
python tools/replay.py %LOCALAPPDATA%\claude-usage-monitor\panel.log.sse
python -m pytest tests -q
```

## Sprzęt — co trzeba wiedzieć, zanim się coś zepsuje

Moduł: `USB\VID_1908&PID_0102`, GEMBIRD/QDtech „USB-Display", układ **Appotech
AX206**. Protokół to firmowa komenda SCSI `0xCD`
opakowana w parę CBW/CSW transportu USB Bulk-Only Mass Storage; punkt wyjścia to
[`dpf-ax`](https://github.com/dreamlayers/dpf-ax), ale **ten firmware różni się
od opisanego tam** i różnice są tu największym źródłem niespodzianek.

| | |
|---|---|
| rozdzielczość | 480 × 320, RGB565 **starszy bajt pierwszy**, bez obrotu |
| pełna klatka | 307 200 B → **~355 ms** dla ciemnego układu (≈2,8 kl./s) |
| pakowanie pikseli | 1,55 ms (`bytes.translate` + jedno OR na dużych liczbach; numpy zbędne) |
| jasność | `SETPROPERTY`/`BRIGHTNESS`, 0–7 |
| uchwyt | **wyłączny** — albo AIDA64, albo my |
| sterownik urządzenia | libusb-win32 (`libusb0.sys`) — ten sam, którego używa AIDA64 |
| biblioteka klienta | **libusb-1.0** (pakiet `libusb` z `requirements.txt`) |

**Sterownik i biblioteka to dwie różne rzeczy** i mylenie ich jest tu gotową
pułapką. Urządzenie zostaje pod libusb-win32; zmieniła się tylko biblioteka, przez
którą z nim rozmawiamy. Backend windowsowy libusb-1.0 obsługuje urządzenia
związane z `libusb0.sys` (zmierzone: 1200 klatek, `missed_csw = 0`), więc
przepinanie sterownika na WinUSB jest niepotrzebne i zerwałoby AIDA64.

Powód zmiany biblioteki jest jeden: `libusb_get_port_numbers()` podaje **łańcuch
portów** z tego samego uchwytu, który otwieramy. API 0.1 z libusb-win32 nie
podawało żadnej topologii (`bus-0`, `devnum=0`) — moduł trzeba było szukać
w rejestrze Windows i łączyć jedno z drugim po kolejności.

**Czas klatki zależy od treści**, mimo stałej liczby bajtów na drucie. Zmierzone
na tym module, ten sam blit 307 200 B: karta testowa (ciemna) 354 ms, pełna czerń
356 ms, pasy nasyconych kolorów 514 ms. Układ docelowy jest ciemny, więc obowiązuje
~355 ms. Syntetyczne testy nasyconymi pasami mierzą najgorszy przypadek —
i właśnie one kazały przez chwilę uwierzyć w nieistniejącą regresję po
migracji (libusb-win32 dawał na tych samych pasach 503–533 ms).

**Rysuje wyłącznie pełne klatki.** Prostokąt w komendzie blit nie jest obszarem
do przerysowania — ustawia *okno rysowania*, w które firmware wlewa cały strumień,
zawijając go. Mniejsze okno pokazuje więc **ogon** ładunku, nie jego początek
(ładunek dopełniony zerami daje okno czarne — tak wyglądały pierwsze, mylące
wyniki). Transfer zawsze musi mieć 307 200 B, więc blit w okno 480×60 kosztuje
tyle samo co pełny ekran. Prostokąt równy dokładnie jednemu wypełnieniu okna też
nie jest potwierdzany. `blit()` odrzuca więc prostokąty częściowe, zamiast po
cichu nic nie robić.

**Brak CSW to nasz błąd, nie kaprys panelu.** Identyczna komenda potwierdza się
normalnie, dopóki przed nią nie pójdzie blit o złej liczbie bajtów; potem potok
milczy aż do `reset()`. Licznik `missed_csw` czytaj jako „wysłaliśmy coś źle".

**Z zestawu komend `dpf-ax` działa tylko blit i jasność.** `FILLRECT` (0x11),
`COPYRECT` (0x13) i właściwość `FGCOLOR` nie są potwierdzane nawet po resecie.
Komenda odpytania o geometrię jest **zawodna**: raz zwraca poprawne 480×320, raz
milczy, a nieudana próba psuje następną transakcję — dlatego geometria jest
konfiguracją, a `probe_geometry()` woła się na samym końcu `--probe`.

**Ekran trzyma ostatnią klatkę** bez podłączonego hosta. Klient celowo nie czyści
go przy wyjściu: po zamknięciu komputera na biurku zostaje ostatni znany stan.

Gotowa biblioteka [`pyax206`](https://github.com/sayajinpt/pyax206) implementuje
identyczny protokół (komenda blit zgadza się co do bajtu), ale na tym egzemplarzu
nie działa: jej `init()` traktuje brak CSW jako błąd krytyczny i wywala się
w każdej próbie, także po czystym resecie.

## The second screen: Turing rev A over a serial port

A different device in every respect except the pixel format. Measured on the unit
on this desk; the driver is [`panel/drivers/turing_rev_a.py`](panel/drivers/turing_rev_a.py).

| | |
|---|---|
| identity | `USB\VID_1A86&PID_5722\USB35INCHIPSV2`, CompatibleIds `USB\Class_02&SubClass_02&Prot_01` |
| driver | in-box **usbser.sys** (CDC-ACM), no CH34x install on a fresh machine |
| BusReportedDeviceDesc | `UsbMonitor`; Windows shows it as "Urządzenie szeregowe USB (COMn)" |
| resolution | 320 × 480 **portrait**; our 480 × 320 layout is rotated 90° host-side |
| pixels | RGB565 **low byte first** (the AX206 wants the other order) |
| wire | 115200 8N1 with rtscts — **the configured baud is ignored**, 1 M and 2 M measure the same |
| throughput | **6.1 µs/byte, zero fixed overhead**; full frame 307 200 B = 1.87 s |
| partial updates | **real** — a rect updates exactly that rect |
| brightness | percent, 0..100, inverted on the wire |
| acknowledgement | **none, ever** |

**It is rev A, and that was measured, not assumed.** It ignores the rev B
handshake (`0xCA 'HELLO' … 0xCA`) at 115200 and at 1 Mbaud, and it draws
correctly for every rev A command we send. Rev B is a different command set
(`0xCA`–`0xCE`) and will get its own file next to this one, never a flag inside it.

**Nothing is acknowledged, so nothing detects a wrong frame.** There is no CSW
analogue and no read-back: an unplugged, upside-down or desynchronised screen
accepts bytes exactly like a healthy one. Two consequences the code is built
around:

- A **torn write is not recoverable by reopening the port.** The firmware counts
  pixel bytes; closing the COM port does not touch the MCU, so the next command's
  6 bytes get eaten as pixels and the stream desynchronises for good — with an
  odd offset that means permanently byte-swapped colour. The driver pads the exact
  remainder (the count survives in pyserial's `_overlapped_write.InternalHigh`
  even though the exception drops it) and forces a full repaint. **If the padding
  fails too, only unplugging the screen fixes it.**
- **Every write is time-bounded.** pyserial's default `write_timeout=None` waits
  forever on Windows, and with rtscts a screen that deasserts CTS would freeze the
  tick loop — and with it *the other screen*, silently, with the scheduled task
  seeing a live process and never restarting it.

`RESET` (101) is never sent (unmeasured, and a firmware mid-payload would eat it
as pixels). `SET_ORIENTATION` (121) is never sent either: rotation is host-side
and measured, and without acknowledgement a coordinate-space mistake would show
up as a scrambled screen rather than an error.

**Identity is the port chain, same as the AX206.** pyserial reports `1-8.4`; we
keep `8.4` and drop the bus for exactly the reason `Hub_#` was dropped before.
`USB35INCHIPSV2` is a firmware constant, so it filters (other WCH serial devices
share the VID/PID and must never receive bitmap commands) but never selects.
Verified on **one** unit — a second identical screen has not been tested.

### Partial updates: what actually gets written

A full frame every second is impossible here (1.87 s), and unnecessary. The
client diffs the packed 565 buffer in device space on an 8 × 8 tile grid,
coalesces tiles into rectangles without ever covering a clean pixel, and cuts each
rectangle straight out of the full-frame payload. Measured on real frames
(cost = bytes × 6.1 µs):

| change | rectangles | bytes | scan | on the wire |
|---|---|---|---|---|
| one tick (seconds counter) | 3 | 1 536 | 2.3 ms | 9 ms |
| clock minute rollover | 3 | 1 792 | 2.0 ms | 11 ms |
| `base` → `states` | 63 | 87 680 | 4.5 ms | 535 ms |
| `base` → `edges` | 69 | 129 920 | 5.8 ms | 793 ms |

So the steady state is ~1 % of a tick and a **scene change costs 0.5–1.2 s** — the
top of that range is the bands → alert card transition (62.5 % dirty, 45 crops,
1.17 s), which is the most expensive one this client draws. Rare, always cheaper
than a full frame, and the loop drops ticks rather than catching up. Above 256
rectangles or 85 % of the frame the client sends the whole frame instead: past that
many rectangles the Python loop stops being worth it.

**The threshold is 85 %, not 60 %, and the old wording here was wrong.** It said a
bounding box on a scene change *is* the whole frame — true for a bounding-box
coalescer, and this one is not that: `coalesce()` provably never covers a clean
pixel, and the wire is linear at 6.1 µs/byte with only a 6 B rectangle header, so
a set of crops is **never** heavier than the full frame. The measurement that
forced the change: the bands → alert card transition dirties 62.5 % of the frame
in 45 rectangles, landing just above the old threshold and turning 1.16 s of crops
into a 1.87 s full frame for nothing. `tests/test_alert.py` pins the number.

**The periodic full repaint is timed from the last FULL write**, not from the last
write. With partial updates the clock writes something every minute, so timing it
off "last write" would mean it never fires — and on a link that confirms nothing,
that repaint is the only way back from a silent desync.

## Skąd biorą się dane

`GET /api/stream?account=<uuid>&account=<uuid>` z `Authorization: Bearer`.
To **jedyny** endpoint przyjmujący token — `/api/status` jest wyłącznie za SSO
(`backend/app/auth.py:54-89`), więc panel nie ma skąd zrobić pollingu i nie
potrzebuje go: każda ramka `account` niesie pełną kartę `AccountStatus`, więc
zgubiona ramka jest nieszkodliwa.

- `bye` po 900 s jest **normalne** — wznawiamy natychmiast, bez migania „brak łącza".
- Timeout gniazda 35 s (ponad dwa odstępy ping). Bez tego półotwarte TCP przez
  Apache wisiałoby w nieskończoność, a panel pokazywałby stare liczby z pełnym
  przekonaniem.
- Odczyt strumienia idzie przez `read1()`, nie `read()`. `read(n)` czeka na całe
  `n` bajtów, więc kolejne karty i pingi zostawały w buforze, a panel stał
  z pierwszą ramką **wyglądając na żywego**.

## Zablokowana sesja Claude Code

Panel pokazuje nie tylko zużycie, ale i to, że **Claude na Ciebie czeka**: prośbę
o zgodę na narzędzie, `AskUserQuestion` albo `ExitPlanMode`. Sygnał zbiera
`client/usage-probe.py` na maszynie z sesją, wysyła do backendu i ten rozsyła
go ramką `alert` (`docs/API.md` § 3.2). **Sesja może chodzić na maszynie zdalnej** —
panel widzi wyłącznie to, co przyszło strumieniem.

Prezentacja jest dwustopniowa i to jest decyzja, nie etap:

1. Karta **przejmuje cały ekran** przez `alert_takeover_sec` — liczba sekund (domyślnie
   300), `0` (od razu trójkąt, bez karty) albo `"infinity"` (karta stoi, dopóki nie
   odpowiesz, i zużycie jest przez ten czas niewidoczne). Jej
   **baner miga na czerwono** przez `alert_flash_sec` — bez tego karta wchodzi cicho,
   bo jest ciemna jak reszta ekranu, i przy kilku ekranach na biurku łatwo ją przegapić.
   Wartość to liczba sekund (domyślnie 20), `0` wyłącza, a `"infinity"` miga przez całe
   życie karty. Zapala się raz na **klucz** blokady, więc tyknięcie „czeka N min" nie
   miga. Każde mrugnięcie to ~0,73 s łącza na obu ekranach — przy `"infinity"` karta
   zajmuje je przez cały czas, gdy stoi.

   **Miga sam baner, nie ekran.** Pełnoekranowy błysk jest tu strukturalnie niemożliwy:
   to z definicji pełna klatka, a Turing maluje ją 1,87 s progresywnie, więc na szkle
   wychodzi powolne zamalowanie, a nie błysk (sprawdzone na sprzęcie — pierwsza wersja
   robiła dokładnie to). Baner to 20% klatki: jeden wycinek, 61 kB, ~375 ms na Turingu
   i 355 ms na AX206, czyli oba zdążą w ticku.
2. Potem zwija się do **czerwonego trójkąta obok nazwy konta**. Stan przestaje być
   *przejmujący*, nie przestaje być *prawdziwy* — zużycie wraca na ekran, a to, że
   coś czeka, dalej widać.

Okno liczy się od `since` z serwera, nie od chwili, w której panel zobaczył wpis:
inaczej restart panelu wskrzeszałby kartę dla blokady sprzed godziny. Nowa blokada
to nowy klucz, czyli nowe okno.

- **`blocked_debounce_sec` (2 s)** — ile blokada musi trwać, zanim karta wejdzie.
  Bez tego zgoda udzielona od razu dawałaby błysk pełnego ekranu.
- **`blocked_linger_sec` (10 s)** — ile karta zostaje po zniknięciu blokady,
  **zamrożona**. Zamrożenie jest istotne: bez niego „czeka N min" tykałoby dalej na
  prompcie, na który już odpowiedziałeś, a każdy przeskok tego napisu to pełna
  klatka na AX206.
- **`session_alerts: false`** wyłącza całość — ramka jest wtedy ignorowana i panel
  zachowuje się dokładnie jak przedtem. To flaga **wyświetlania**; źródło gasi się
  osobno, kluczem `session_status` w `config.json` na maszynie z sesją. Dwie flagi,
  bo to bywają dwie różne maszyny.

Alert **bez dopasowania** do żadnego skonfigurowanego konta ląduje na pasie
**górnym**. Reguła jest celowo prosta: statyczne mapowanie maszyna → pas rozjechałoby
się po pierwszym `/login`, a przełączanie kont jest tu rutyną. Konto bierze się
z `oauthAccount.accountUuid` odczytanego na maszynie z sesją — zasada 7 projektu.

Czas czekania jest **gruboziarnisty** („chwilę" / „4 min" / „1 h 05 min" / „2 d 3 h")
i to nie jest kwestia gustu: AX206 nie umie wycinków, więc każda zmiana napisu to
pełne 355 ms, a sekundy zamieniłyby ~2,5 % obciążenia USB w ~35 % na cały czas
trwania karty. Żywego zegara na karcie nie ma — godzina w banerze to statyczny
moment pojawienia się promptu.

Karta jest **wersją roboczą**: zbudowana wyłącznie z istniejącej palety, czcionek
i idiomów, żeby mechanika działała, zanim ktoś ją zaprojektuje. Podgląd bez sprzętu:

```
python tools/render-png.py --alert permission --zoom 3 --rgb565 --out alert.png
python tools/render-png.py --alert multi --zoom 3 --rgb565 --out alert-multi.png
python tools/render-png.py --triangle --zoom 3 --rgb565 --out trojkat.png
```

Gdyby alert się zawiesił, furtka jest na maszynie z sesją, nie tutaj:
`del %LOCALAPPDATA%\claude-usage-monitor\session-status\*`.

## Odświeżanie

Tick co sekundę, ale na ekran leci **tylko to, co się różni**. Ile z tego wynika,
zależy od sprzętu: AX206 rysuje wyłącznie pełne klatki, więc oszczędnością jest
niewysłanie identycznej (zmierzone w spoczynku: 3 klatki na 45 s, ~2,5 % czasu na
USB); ekran szeregowy przyjmuje prostokąty, więc typowy tick to ~1,5 kB zamiast
307 kB.

Sekundy zostają tam, gdzie są w makiecie: w odliczaniu poniżej godziny i w wieku
odczytu poniżej minuty. Wchodzą dokładnie wtedy, gdy pracujesz — a wtedy są
najbardziej potrzebne. Poza pracą wartości same wchodzą w minuty i godziny,
i panel milknie. Wyjątkiem jest zegar w nagłówku: tyka niezależnie od pracy,
więc pokazuje HH:MM.

## Zasady rysowania, których nie wolno uprościć

- **Panel nie ma osobnych stanów świeżości w rysunku.** Aktualność niesie
  w całości etykieta wieku odczytu przy koncie. Gdy backend nie zna bieżącej
  wartości (`utilization: null`, konto milczy dłużej niż `CLIENT_SILENT_SEC`),
  panel pokazuje **ostatni pomiar** (`rawUtilization`) jak każdą inną liczbę,
  a „12 h temu" obok mówi, ile jest wart.
- **Nigdy zero zamiast braku wiedzy** (zasada 4 z `AGENTS.md`) — powyższe tę
  zasadę wzmacnia, nie osłabia: konto, które milczy od 12 h z tygodniem na 100 %,
  pokazuje 100 %, a nie uspokajające zero. Kreskowany tor ze skosem i słowa
  `nie wiem` zostają wyłącznie dla serii, która **nigdy** nie miała pomiaru —
  tam faktycznie nie ma czego narysować, a znak `%` wtedy znika („nie wiem %"
  to realna pułapka).
- **Plan zawsze widoczny.** „40 %" znaczy co innego na Max 20× niż na miejscu
  Team. Nieznany tier pokazuje się surowo, zamiast zniknąć (zasada 5).
- **Wiek liczymy z `confirmedAt`, nigdy z `capturedAt`** — dedup nie zapisuje
  próbki przy niezmienionej wartości, więc `capturedAt` bywa o godziny starsze.
- **Odliczanie kotwiczy się na `serverNow`**, lokalnie tylko tyka. Kotwica siedzi
  na `time.monotonic()`, więc skok NTP nie przesunie countdownów.
- **Znacznik łącza różni się rysunkiem, nie kolorem** (kropka / pierścień /
  przekreślony). Gdy strumień padnie, wiek odczytu rośnie obu kontom naraz
  i wygląda to identycznie jak „przestałeś pracować".

## Instalacja

```powershell
.\deploy\install-task.ps1          # venv poza repo + zadanie na logowanie
.\deploy\install-task.ps1 -Uninstall
```

Konfiguracja: `%LOCALAPPDATA%\claude-usage-monitor\panel.json` — ten sam katalog
co `config.json` sondy, ale **osobny plik**: token strumienia ma inny zakres niż
token ingestu, a plik sondy bywa nadpisywany przy jej aktualizacji.

```json
{
  "stream_url": "https://usage.example.org/claude-usage/api/stream",
  "stream_token": "<wpis z STREAM_TOKENS o etykiecie panel>",
  "account_1": {"uuid": "...", "name": "you@example.org"},
  "account_2": {"uuid": "...", "name": "billing@example.org"},
  "panels": [
    {"backend": "ax206",        "port_path": "3.4", "brightness": 5},
    {"backend": "turing-rev-a", "port_path": "8.4", "brightness": 40, "name": "prawy",
     "rotate": 180}
  ]
}
```

**`rotate` mówi, jak ekran wisi**, w stopniach przeciwnie do ruchu wskazówek
zegara, **doliczane do obrotu, który sterownik i tak stosuje** (`turing-rev-a` ma
własne 90°, więc `180` daje 270°). Wolno **tylko `0` albo `180`** — ćwierć obrotu
wymagałaby układu pionowego 320×480, a rysowany jest jeden układ 3:2; sama zmiana
kąta dałaby albo skalowanie (ten układ to włoskowate linie, nie przetrwa go),
albo ładunek o długości, która nie pasuje do prostokąta. Pominięte znaczy `0`.
Do sprawdzenia bez zmiany pliku: `python -m panel --identify turing-rev-a#0
--rotate 180`.

**Jasność jest per ekran, bo skale są nieporównywalne**: `ax206` to 0..7
(właściwość firmware'u), `turing-rev-a` to 0..100 %. Pominięta znaczy „domyślna
tego sterownika". Górne `brightness` obok `panels` jest **błędem konfiguracji**,
nie kompromisem — `5` znaczyłoby środek zakresu na jednym ekranie i prawie
zgaszony na drugim.

**Stary kształt (`"device": {...}` + górne `brightness`) nadal działa** i zamienia
się w jednoelementową listę `ax206`. Wolno go zmigrować, bo miał dokładnie jedno
możliwe znaczenie — był jeden sterownik. To jest różnica wobec `location`, gdzie
niewiarygodna była sama wartość. `device` i `panels` naraz to błąd: scalanie
znaczyłoby zgadywanie. Nieznany klucz w selektorze też jest błędem — kiedyś nie
pasował do niczego i cicho spadał do „jedyne, co widać".

**Konta to dwa nazwane pola, nie lista** — kształt konfiguracji jest tu kształtem
ekranu, więc trzeciego konta nie da się dopisać przez nieuwagę. Po `/login` na
nowe konto trzeba wskazać je tutaj i zrestartować klienta: subskrypcja ustala się
przy nawiązaniu połączenia, a SSE nie ma kanału zwrotnego.

**Panel wskazuje się w konfiguracji i klient nigdy nie sięga po inny.** Docelowo
na magistrali będą dwa takie same moduły (jeden pod innym programem) i oba mają ten sam
numer seryjny `WCH32` — to stała firmware'u, nie numer egzemplarza. Windows
wyprowadza z tego seryjnego zarówno ID instancji (`USB\VID_1908&PID_0102\WCH32`),
jak i `ContainerID`, więc **obie te wartości będą dla dwóch modułów identyczne**
i jako selektory nie odróżniają niczego.

Kluczem jest więc `port_path` — łańcuch portów, np. `"3.4"`: port 3 kontrolera,
port 4 huba na tym porcie. Trzy rzeczy, które o nim wiedzieć:

- **Nie zawiera licznika enumeracji.** Poprzednia wersja brała
  `LocationInformation` z rejestru (`"Port_#0004.Hub_#0005"`), gdzie `Hub_#NNNN`
  jest indeksem instancji huba nadawanym przy wykrywaniu. Ten indeks przeskoczył
  przy nieruszonej wtyczce i klient przestał widzieć swój moduł. Stary
  `"location"` w `panel.json` kończy się teraz **czytelnym błędem konfiguracji**
  z instrukcją, nie cichą migracją.
- **Numer magistrali do klucza nie wchodzi** — jest syntetycznym indeksem
  kontrolera, czyli tej samej natury co `Hub_#`. `--list` go pokazuje jako
  diagnostykę. Gdyby dwa moduły dały ten sam łańcuch (możliwe tylko przy dwóch
  kontrolerach USB o tym samym numerze portu), wybór kończy się błędem —
  nigdy strzałem na chybił trafił.
- **Przełożenie wtyczki zmienia klucz.** To nieuniknione przy identyfikacji po
  topologii, dlatego `--list` wypisuje gotową linię do wklejenia.

Zmierzone: `ports=(3,4)` zgadza się co do liczby z `DEVPKEY_Device_LocationPaths`
(`...#USB(3)#USB(4)`) i przeżywa reset urządzenia mimo skaczącego adresu USB.
Sprawdzone dla **jednego** modułu naraz. Różnica
wobec poprzedniej wersji jest jednak jakościowa: nie ma już czego z czym parować,
bo łańcuch przychodzi z tej samej enumeracji co otwierany uchwyt. Który moduł na
biurku jest który, i tak rozstrzyga wyłącznie `--identify` — tego żaden odczyt
nie załatwi.

Instalator przyjmuje liczbę ekranów: `.\deploy\install-task.ps1 -Panels 2`. Bez
niej „OK" po pierwszej linii z logu znaczyłoby „rysuje jeden z dwóch". Skrypt
celowo nie czyta `panel.json` — przy pierwszej instalacji tego pliku jeszcze nie ma.

Zadanie harmonogramu musi mieć „uruchom **tylko gdy użytkownik jest zalogowany**"
(dysk sieciowy jest mapowany per sesja, a USB wymaga sesji interaktywnej) i wyłączone
„zatrzymaj po 3 dniach" — domyślnie włączone, zabijałoby panel co tydzień.

## Diagnostyka

| objaw | gdzie patrzeć |
|---|---|
| panel czarny / stara treść | `panel.log`; czy zadanie chodzi; czy inny program nie trzyma modułu |
| „panel zajęty przez inny proces" | inny program albo drugi egzemplarz klienta |
| `missed_csw` rośnie | wysłaliśmy złą liczbę bajtów — błąd w kodzie, nie w sprzęcie (tylko AX206; ekran szeregowy nic nie potwierdza) |
| ekran szeregowy: kolory zamienione, obraz „przesunięty" | rozjazd bajtowy po urwanym zapisie. Pełne przemalowanie tego **nie** naprawia — wypnij i wepnij wtyczkę |
| jeden ekran rysuje, drugi nie | każdy panel ma własny backoff; szukaj w logu linii z jego tagiem (`turing-rev-a 8.4: …`) |
| liczby stoją, wiek rośnie | strumień żyje, ale sonda milczy — to poprawny obraz |
| „nie wiem" na obu kontach | brak ramek; sprawdź token i UUID-y w `panel.json` |
| twarda awaria bez śladu | `panel.log.fault` (faulthandler — nie ma konsoli pod `pythonw`) |
