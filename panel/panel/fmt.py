"""Formaty — port frontend/src/lib/time.ts i format.ts.

Napisy MUSZA wychodzic identyczne jak w WWW, bo to ten sam produkt ogladany
z dwoch stron biurka. Testy w tests/test_fmt_port.py trzymaja to za slowo.

Czas: countdowny licza sie z zegara SERWERA (kotwica `serverNow`), ale godziny
wyswietlane sa w strefie LOKALNEJ — dokladnie jak `time.ts:hm`, ktore uzywa
`d.getHours()`. Dzieki temu "reset o 20:00" zgadza sie z zegarkiem na reku.

Sekundy zostaja tam, gdzie sa w makiecie: w odliczaniu ponizej godziny i w wieku
odczytu ponizej minuty. Kosztuja pelna klatke co sekunde (panel nie umie
odswiezyc fragmentu), ale wchodza dokladnie wtedy, gdy pracujesz — a wtedy sa
najbardziej potrzebne. Poza praca wartosci same wchodza w minuty i godziny
i panel milknie. Jedyny wyjatek to zegar w naglowku: on tyka niezaleznie od
pracy, wiec pokazuje HH:MM.

Z time.ts nie portujemy `stamp()` (stempel BEZ przyimka): na panelu nie ma dla
niego wolajacego, bo nie ma podpisow "bez zmian od" ani "od". Gdy taki podpis
sie pojawi, `stamp()` jest bliznakiem `at_stamp()` z time.ts:71-83.
"""
import re
from datetime import datetime, timezone

_HAS_ZONE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")

# Skroty dni DOKLADNIE jak time.ts:44 — indeksowane od NIEDZIELI, jak getDay().
# Pythonowe weekday() liczy od poniedzialku, wiec indeks robi _day_index(), a nie
# przestawiona tablica: napisy maja wychodzic identycznie jak w WWW.
DAYS = ("ndz.", "pon.", "wt.", "śr.", "czw.", "pt.", "sob.")


def parse_utc(iso):
    """ISO -> datetime z tzinfo. Bez strefy dopinamy UTC, jak parseUtc w time.ts.

    Nie-string zwraca None, nie wyjatek. To jedyne wejscie surowych znacznikow
    z serwera do calego klienta (stemple serii, `resetsAt`, `serverNow`), a regula
    jest tu twarda: zepsuta ramka nie moze zabic panelu. `re.search` na liczbie
    rzuca TypeError, ktory przeszedlby przez tick() i run() az do excepthooka —
    czyli jedno pole zle zserializowane przez backend gasiloby ekran.
    """
    if not iso or not isinstance(iso, str):
        return None
    text = iso if _HAS_ZONE.search(iso) else iso + "Z"
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_local(d):
    return d.astimezone() if d is not None else None


def ms(d):
    """datetime -> milisekundy epoki, zeby liczyc jak w JS."""
    return None if d is None else d.timestamp() * 1000.0


def _p2(n):
    return "%02d" % n


def hm(d):
    d = to_local(d)
    return "%s:%s" % (_p2(d.hour), _p2(d.minute)) if d else "—"


def hms(d):
    d = to_local(d)
    return "%s:%s:%s" % (_p2(d.hour), _p2(d.minute), _p2(d.second)) if d else "—"


def dm(d):
    d = to_local(d)
    return "%s.%s" % (_p2(d.day), _p2(d.month)) if d else "—"


def _day_index(d):
    """Indeks do DAYS w konwencji JS getDay(): 0 to niedziela."""
    return (d.weekday() + 1) % 7


def _day_diff(d, now):
    """Roznica w DNIACH KALENDARZOWYCH po lokalnych polnocach (time.ts:51-55).

    Nigdy delta_ms / 86_400_000: doba przy zmianie czasu ma 23 albo 25 h, a para
    chwil po dwoch stronach polnocy rozni sie o dzien niezaleznie od tego, ile ms
    je dzieli. Czlowiek czyta "wczoraj o 23:50", nie "26 godzin temu".
    """
    return (to_local(d).date() - to_local(now).date()).days


def at_stamp(d, now_ms):
    """Stempel chwili czytanej WZGLEDEM TERAZ, z przyimkiem — port atStamp().

        dzis          ->  "o 11:58"
        +/- 1 dzien   ->  "wczoraj o 23:50" / "jutro o 20:00"
        +/- 2..6 dni  ->  "w śr. o 11:58"   ALE  "we wt. o 11:58"
        dalej         ->  "26.07 o 11:58"   ("w 26.07" nie jest polszczyzna)
        inny rok      ->  "26.07.2025 o 11:58"

    Przyimek jest W SRODKU stempla, bo polszczyzna zmienia go razem z formatem,
    a wolajacy nie ma prawa wiedziec, ktory wariant wyszedl.

    Parametru `precise` z time.ts:94 nie portujemy: sekundowy wariant zapala sie
    wylacznie w podpisie "potwierdzone …" w hero WWW, ktorego panel nie ma. To
    samo kryterium, po ktorym z SeriesView wypadlo pole `outline` — nie ma
    czytelnika, nie ma pola.
    """
    if d is None:
        return "—"
    now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    diff = _day_diff(d, now)
    if diff == 0:
        return "o %s" % hm(d)
    if diff == -1:
        return "wczoraj o %s" % hm(d)
    if diff == 1:
        return "jutro o %s" % hm(d)
    local = to_local(d)
    if abs(diff) <= 6:
        # "we wtorek", nie "w wtorek" — jedyny wyjatek i dlatego stoi tu.
        idx = _day_index(local)
        return "%s %s o %s" % ("we" if idx == 2 else "w", DAYS[idx], hm(d))
    year = "" if local.year == to_local(now).year else ".%d" % local.year
    return "%s%s o %s" % (dm(d), year, hm(d))


def countdown(target_ms, now_ms):
    """"2 d 4 h" / "3 h 05 min" / "12 min 34 s" / "po resecie" / "bez resetu"."""
    if target_ms is None:
        return "bez resetu"
    s = int(round((target_ms - now_ms) / 1000.0))
    if s <= 0:
        return "po resecie"
    d, rest = divmod(s, 86400)
    h, rest = divmod(rest, 3600)
    m, sec = divmod(rest, 60)
    if d > 0:
        return "%d d %d h" % (d, h)
    if h > 0:
        return "%d h %s min" % (h, _p2(m))
    return "%d min %s s" % (m, _p2(sec))


def ago(since_ms, now_ms):
    """"3 s temu" / "5 min temu" / "1 h 25 min temu" / "3 d 4 h temu".

    Szczebel dobowy w ksztalcie countdown(), bo odkad swiezosc niesie sama
    etykieta, trzydniowa cisza musi czytac sie od razu — "76 h 00 min temu"
    wymaga dzielenia w glowie. Granica dokladnie na 24 h daje "1 d 0 h temu";
    countdown() drukuje "1 d 0 h" dla tego samego wejscia, wiec to spojne.
    """
    if since_ms is None:
        return "—"
    s = max(0, int(round((now_ms - since_ms) / 1000.0)))
    if s < 60:
        return "%d s temu" % s
    m = s // 60
    if m < 60:
        return "%d min temu" % m
    h = m // 60
    if h < 24:
        return "%d h %s min temu" % (h, _p2(m % 60))
    return "%d d %d h temu" % (h // 24, h % 24)


def waited(since_ms, now_ms):
    """Jak dlugo Claude czeka: "chwilę" / "4 min" / "1 h 05 min" / "2 d 3 h".

    GRUBOZIARNISTE, i to nie jest kwestia gustu. AX206 nie umie wycinkow, wiec kazda
    zmiana napisu na karcie to pelna klatka i 355 ms na USB — a druga sciana kosztuje
    swoje. Sekundy zamienilyby ~2,5% obciazenia lacza w ~35% na caly czas trwania karty.
    Ponizej minuty nie ma wiec liczby, tylko slowo: jest tam i tak nic do policzenia.

    Zarzut "zegar w banerze i tak tyka co sekunde" nie stosuje sie: na karcie nie ma
    zywego zegara, godzina w banerze to statyczny moment pojawienia sie promptu.

    Odroznia sie od `ago()` brakiem "temu": to jest czas trwania, nie stempel.
    """
    if since_ms is None:
        return "—"
    s = max(0, int(round((now_ms - since_ms) / 1000.0)))
    if s < 60:
        return "chwilę"
    m = s // 60
    if m < 60:
        return "%d min" % m
    h = m // 60
    if h < 24:
        return "%d h %s min" % (h, _p2(m % 60))
    return "%d d %d h" % (h // 24, h % 24)


def pct(v):
    """31 -> "31", 30.5 -> "30,5". None zostaje None — o tym, co pokazac zamiast
    liczby, decyduje widok, bo w stanie `unknown` odpowiedzia jest slowo."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                      # NaN
        return None
    if f == int(f):
        return str(int(f))
    return ("%.1f" % f).replace(".", ",")


def money(minor, currency=None, exponent=2):
    """(3820, "USD", 2) -> "38,20 USD".

    Liczone na liczbach CALKOWITYCH. Backend nigdy nie splaszcza kwot do floata
    (schemas.py: "w jednostkach mniejszych z wykladnikiem, nigdy jako float")
    i nie ma powodu, zeby robic to po drodze do ekranu.
    """
    if minor is None:
        return None
    exp = 2 if exponent is None else int(exponent)
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(int(minor)), 10 ** exp) if exp > 0 else (abs(int(minor)), 0)
    text = "%s%d" % (sign, whole)
    if exp > 0:
        text += "," + str(frac).rjust(exp, "0")
    return "%s %s" % (text, currency) if currency else text


def clamp_pct(v):
    """Pasek nie moze wyjechac za tor ani wjechac na minus."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return max(0.0, min(100.0, f))


class ServerClock:
    """Zegar serwera kotwiczony na monotonicznym.

    time.ts uzywa Date.now(), bo przegladarka nie ma nic lepszego. Panel chodzi
    miesiacami, wiec kotwiczymy na time.monotonic() — skok NTP albo zmiana czasu
    nie przesuna countdownow. Semantyka widoczna na zewnatrz jest ta sama.
    """

    def __init__(self, monotonic):
        self._monotonic = monotonic
        self._server_ms = None
        self._anchor = None

    def anchor(self, server_now_iso):
        d = parse_utc(server_now_iso)
        if d is None:
            return False
        self._server_ms = d.timestamp() * 1000.0
        self._anchor = self._monotonic()
        return True

    @property
    def anchored(self):
        return self._server_ms is not None

    def now_ms(self):
        if self._server_ms is None:
            return datetime.now(timezone.utc).timestamp() * 1000.0
        return self._server_ms + (self._monotonic() - self._anchor) * 1000.0

    def now(self):
        return datetime.fromtimestamp(self.now_ms() / 1000.0, tz=timezone.utc)
