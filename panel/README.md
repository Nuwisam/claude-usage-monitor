# panel — limity Claude na wyświetlaczu USB 480×320

Klient headless, który subskrybuje `/api/stream` i rysuje stan limitów na module
AX206 stojącym na biurku. Układ **4a** z makiety: dwa konta w pasach, procent
obok bloku, kredyty na dole pasa.

```
python -m panel                 pętla (to samo, co robi zadanie harmonogramu)
python -m panel --list          jakie moduły widać i na których portach
python -m panel --identify 0    namaluj wielki numer na wskazanym module
python -m panel --probe         karta testowa: kolory, cztery rysunki paska, ogonki
python -m panel --once          jedna klatka z prawdziwych danych i wyjście
```

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

## Odświeżanie

Tick co sekundę, ale klatka leci na panel **tylko gdy obraz się różni** — przy
307 kB na transfer to jedyna oszczędność, jaką ten firmware zostawia. Zmierzone
w spoczynku: 3 klatki na 45 s, czyli ~2,5 % czasu na USB.

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
token ingestu, a `/usage-monitor-enrollment` przepisuje plik sondy.

```json
{
  "stream_url": "https://usage.example.org/claude-usage/api/stream",
  "stream_token": "<wpis z STREAM_TOKENS o etykiecie panel>",
  "account_1": {"uuid": "...", "name": "you@example.org"},
  "account_2": {"uuid": "...", "name": "billing@example.org"},
  "device": {"port_path": "3.4"},
  "brightness": 5
}
```

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

Zadanie harmonogramu musi mieć „uruchom **tylko gdy użytkownik jest zalogowany**"
(dysk sieciowy jest mapowany per sesja, a USB wymaga sesji interaktywnej) i wyłączone
„zatrzymaj po 3 dniach" — domyślnie włączone, zabijałoby panel co tydzień.

## Diagnostyka

| objaw | gdzie patrzeć |
|---|---|
| panel czarny / stara treść | `panel.log`; czy zadanie chodzi; czy inny program nie trzyma modułu |
| „panel zajęty przez inny proces" | inny program albo drugi egzemplarz klienta |
| `missed_csw` rośnie | wysłaliśmy złą liczbę bajtów — błąd w kodzie, nie w sprzęcie |
| liczby stoją, wiek rośnie | strumień żyje, ale sonda milczy — to poprawny obraz |
| „nie wiem" na obu kontach | brak ramek; sprawdź token i UUID-y w `panel.json` |
| twarda awaria bez śladu | `panel.log.fault` (faulthandler — nie ma konsoli pod `pythonw`) |
