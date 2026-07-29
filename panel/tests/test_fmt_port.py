"""fmt.py ma dawac DOKLADNIE te same napisy co frontend/src/lib/time.ts i format.ts.

To ten sam produkt ogladany z dwoch stron biurka — rozjazd w formacie czasu
byloby widac natychmiast i wygladalby na blad danych, a nie na blad formatu.
Kazdy przypadek ma w komentarzu miejsce w oryginale.
"""
import pytest

from panel import fmt


@pytest.mark.parametrize("secs,want", [
    (2 * 86400 + 4 * 3600, "2 d 4 h"),      # time.ts:60
    (3 * 3600 + 5 * 60, "3 h 05 min"),      # time.ts:61 — zero wiodace w minutach
    (12 * 60 + 34, "12 min 34 s"),          # time.ts:62 — zero wiodace w sekundach
    (3600, "1 h 00 min"),
    (59, "0 min 59 s"),
    (0, "po resecie"),                      # time.ts:56
    (-5, "po resecie"),
])
def test_countdown(secs, want):
    assert fmt.countdown(secs * 1000.0, 0.0) == want


def test_countdown_bez_celu():
    # time.ts:54 — brak granicy to NIE to samo co granica w przeszlosci.
    assert fmt.countdown(None, 0.0) == "bez resetu"


@pytest.mark.parametrize("smiec", [1769459260, [], object()])
def test_parse_utc_nie_wywraca_sie_na_nie_stringu(smiec):
    """Jedyne wejscie surowych znacznikow z serwera do klienta. Zepsuta ramka nie
    moze zabic panelu, a `re.search` na liczbie rzuca TypeError, ktory przeszedlby
    przez cala petle az do excepthooka — jedno zle zserializowane pole gasiloby
    ekran na dobre."""
    assert fmt.parse_utc(smiec) is None


@pytest.mark.parametrize("secs,want", [
    (0, "0 s temu"),
    (3, "3 s temu"),                        # time.ts:68
    (59, "59 s temu"),
    (60, "1 min temu"),                     # time.ts:70
    (5 * 60, "5 min temu"),
    (3600 + 25 * 60, "1 h 25 min temu"),    # time.ts:71
    (-10, "0 s temu"),                      # ujemny wiek przycinamy do zera
])
def test_ago(secs, want):
    assert fmt.ago(0.0, secs * 1000.0) == want


@pytest.mark.parametrize("value,want", [
    (31, "31"),                             # format.ts:8 — calkowite bez ogona
    (100.0, "100"),
    (0, "0"),
    (30.5, "30,5"),                         # przecinek dziesietny, nie kropka
    (None, None),                           # None ZOSTAJE None — o slowie decyduje widok
])
def test_pct(value, want):
    assert fmt.pct(value) == want


@pytest.mark.parametrize("args,want", [
    ((3820, "USD", 2), "38,20 USD"),        # format.ts:13-23
    ((9000, "USD", 2), "90,00 USD"),
    ((5, "USD", 2), "0,05 USD"),            # grosze bez utraty zera wiodacego
    ((0, "USD", 2), "0,00 USD"),
    ((3820, None, 2), "38,20"),
    ((3820, "USD", 0), "3820 USD"),         # wykladnik 0 = brak czesci ulamkowej
    ((-150, "USD", 2), "-1,50 USD"),
    ((None, "USD", 2), None),
])
def test_money(args, want):
    assert fmt.money(*args) == want


def test_money_nie_przechodzi_przez_float():
    """Backend trzyma kwoty w jednostkach mniejszych wlasnie po to, zeby nie
    zgubic grosza (schemas.py). Splaszczenie do floata po drodze zmarnowalo by
    ten wysilek — 0.1+0.2 to nie 0.3."""
    assert fmt.money(2 ** 53 + 1, "USD", 2).startswith("90071992547409")


@pytest.mark.parametrize("value,want", [
    (None, 0.0), (-5, 0.0), (0, 0.0), (42, 42.0), (100, 100.0), (250, 100.0),
])
def test_clamp_pct(value, want):
    # format.ts:46 — pasek nie moze wyjechac za tor ani wjechac na minus.
    assert fmt.clamp_pct(value) == want


def test_parse_utc_bez_strefy_zaklada_utc():
    # time.ts:9-10 — new Date("...") bez strefy to w JS czas LOKALNY, wiec
    # doklejamy Z. Ta sama pulapka po stronie Pythona.
    assert fmt.parse_utc("2026-07-26T18:00:00").utcoffset().total_seconds() == 0
    assert fmt.parse_utc("2026-07-26T18:00:00Z") == fmt.parse_utc("2026-07-26T18:00:00")
    assert fmt.parse_utc(None) is None
    assert fmt.parse_utc("to nie data") is None


def test_godziny_sa_lokalne():
    """time.ts:31 uzywa getHours(), czyli strefy przegladarki. Panel robi tak samo:
    'reset o 20:00' ma sie zgadzac z zegarkiem na reku, nie z UTC."""
    d = fmt.parse_utc("2026-07-26T18:00:00Z")
    assert fmt.hm(d) == fmt.hm(fmt.to_local(d))
    assert fmt.hm(None) == "—"


def test_day_hm_ma_dzien():
    # Tydzien resetuje sie za kilka dni — sama godzina nie mowi ktorego dnia.
    assert fmt.day_hm(fmt.parse_utc("2026-07-26T12:00:00Z")).split()[0] in fmt.DAYS


def test_server_clock_idzie_monotonicznie():
    """Kotwica na time.monotonic(), nie na zegarze systemowym: panel chodzi
    miesiacami i skok NTP nie moze przesunac odliczen."""
    t = [100.0]
    clock = fmt.ServerClock(lambda: t[0])
    assert not clock.anchored
    assert clock.anchor("2026-07-26T19:07:40Z")
    start = clock.now_ms()
    t[0] += 5.0
    assert clock.now_ms() - start == pytest.approx(5000.0)
    assert clock.anchored


def test_server_clock_bez_kotwicy_nie_wybucha():
    clock = fmt.ServerClock(lambda: 0.0)
    assert clock.now_ms() > 0
