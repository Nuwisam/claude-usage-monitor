"""Stan -> wyglad. Port frontend/src/lib/freshness.ts na potrzeby panelu.

SWIADOMA ROZNICA WOBEC WWW: panel NIE MA osobnych stanow swiezosci w rysunku.
Aktualnosc niesie w calosci etykieta wieku odczytu ("16 min temu"), ktora stoi
przy koncie. WWW rozroznia `live`/`stale` kolorem wypelnienia i daje `unknown`
wlasny rysunek, bo tam nie ma zegara przy kazdym wierszu — tutaj jest, wiec drugi
kanal na te sama informacje bylby szumem.

W praktyce: gdy istnieje OSTATNI POMIAR (`rawUtilization`), panel pokazuje go
jak kazda inna wartosc, a o tym, ile jest wart, mowi wiek odczytu obok.

Zasada 4 z AGENTS.md ("unknown nigdy nie jest zerem") jest przez to zachowana
mocniej, nie slabiej: przy braku swiezego pomiaru pokazujemy OSTATNI ZMIERZONY,
a nie zero. Falszywe, pewnie wygladajace zero jest najgorszym trybem awarii tego
narzedzia. Kreskowany tor i slowa `nie wiem` zostaja wylacznie dla przypadku,
w ktorym pomiaru nie bylo NIGDY — tam naprawde nie ma czego narysowac.
"""
from . import fmt


class SeriesView:
    # `full` nie ma wlasnego rysunku — przy 100% pelny tor mowi to sam (draw.bar)
    # — ale zostaje: pinuje go test_setka_to_koniec i niesie jedyny prog w tym UI.
    # Pola `outline` tu nie ma, bo nie czytal go NIKT: ani draw.bar, ani render,
    # ani zaden test. `stub`/`ghost`/`ghost_pct` zostaja mimo ze produkcja ich nie
    # zapala — stoi na nich karta testowa `--probe` (panel/README.md).
    __slots__ = ("measured", "hatch", "stub", "ghost", "ghost_pct",
                 "bar_pct", "full", "number", "words")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def describe_series(s):
    """SeriesStatus -> SeriesView.

    Wartosc bierzemy z `utilization`, a gdy backend jej nie podal (stan `unknown`)
    — z `rawUtilization`, czyli z ostatniego POMIARU. O tym, czy ta liczba jest
    jeszcze cokolwiek warta, mowi wiek odczytu obok, a nie osobny rysunek toru.
    """
    if s is None:
        return missing_view()

    value = s.utilization
    if value is None:
        value = s.raw_utilization

    if value is None:
        # Nigdy nie bylo pomiaru dla tej serii — jedyny przypadek, w ktorym
        # naprawde nie ma czego narysowac.
        return missing_view()

    return SeriesView(
        measured=True,
        hatch=False,
        # Tylda przy wywnioskowanym resecie zostaje: to jedyne miejsce, gdzie
        # liczba nie pochodzi z pomiaru, tylko z rozumowania serwera.
        stub=False,
        ghost=False,
        ghost_pct=0.0,
        bar_pct=fmt.clamp_pct(value),
        full=value >= 100,
        number=("~0" if s.freshness == "inferred_reset" and not value
                else fmt.pct(value)),
        words=None,
    )


def missing_view():
    """Brak JAKIEGOKOLWIEK pomiaru: nie przyszla ramka albo seria nigdy nie miala
    wartosci. Pusty tor czytaloby sie jako zero, wiec tor jest kreskowany ze
    skosem, a zamiast liczby stoja slowa. Zero byloby klamstwem."""
    return SeriesView(measured=False, hatch=True, stub=False,
                      ghost=False, ghost_pct=0.0, bar_pct=0.0, full=False,
                      number=None, words="nie wiem")


def reset_note(s, now_ms, with_day=False):
    """(lead, at) — podpis odliczania. Port resetNote() z freshness.ts.

    `resetsAt: null` ma KILKA powodow i sklejenie ich w jeden napis bylo bledem
    juz raz: Anthropic nie podaje granicy dla okna z 0% zuzycia, a sonda zeruje
    przedawniona granice z cache. Cztery rozne zdania, nie jedno.

    `with_day` dla okna tygodniowego: sama godzina przy resecie za piec dni
    klamie, bo nie mowi ktorego dnia (makieta: `dayHm`).
    """
    if s is None:
        return "brak danych", None
    if s.source in ("spend", "extra_usage"):
        return "bez resetu", None
    stamp = fmt.day_hm if with_day else fmt.hm
    r = fmt.parse_utc(s.resets_at)
    if r is not None:
        target = fmt.ms(r)
        # Prog zaokraglenia TEN SAM co w countdown(), inaczej przy roznicy 300 ms
        # wychodzi sklejka "reset za po resecie".
        secs = int(round((target - now_ms) / 1000.0))
        if secs > 0:
            return "reset za %s" % fmt.countdown(target, now_ms), stamp(r)
        return "reset minął", stamp(r)
    if s.utilization == 0:
        return "okno nie wystartowało", None
    return "czas resetu nieznany", None


def pick_session(series):
    """Hero stoi na sesji 5 h NA STALE. Gdyby przeskakiwal za `isActive`, ten sam
    ekran znaczylby co innego w zaleznosci od pory tygodnia (HeroSession.tsx)."""
    primary = [s for s in series if s.primary]
    for test in (lambda s: s.kind == "session",
                 lambda s: s.bucket_key == "five_hour"):
        for s in primary:
            if test(s):
                return s
    for s in series:
        if s.bucket_key == "five_hour":
            return s
    return None


def pick_weekly(series):
    """Wiersz tygodniowy = seria ZBIORCZA.

    Swiadomie nie pokazujemy tu serii zawezonych (Opus itp.), mimo ze zawezona
    bywa wyzsza. Uzasadnienie: wiersz ma znaczyc to samo w kazdy dzien tygodnia.
    Diagnostyka zostaje w WWW.
    """
    primary = [s for s in series if s.primary]
    for test in (lambda s: s.kind == "weekly_all",
                 lambda s: s.bucket_key == "seven_day"):
        for s in primary:
            if test(s):
                return s
    for s in series:
        if s.bucket_key == "seven_day":
            return s
    return None


class CreditsView:
    __slots__ = ("state", "used", "limit", "currency", "bar_pct", "is_current")

    def __init__(self, state, used=None, limit=None, currency=None,
                 bar_pct=0.0, is_current=False):
        self.state = state          # "on" | "off" | "unknown"
        self.used = used
        self.limit = limit
        self.currency = currency
        self.bar_pct = bar_pct
        self.is_current = is_current


def credits(rung):
    """Szczebel `credits` z kaskady -> wiersz kredytow.

    Trzy stany, bo "wylaczone" i "nie wiem" to dwie rozne rzeczy. Przy "off" BEZ KWOT
    uklad 4a w ogole nie rysuje tego wiersza — pas ma wtedy trzy wiersze.

    Kwoty maja jednak pierwszenstwo przed stanem. Gdy organizacja odetnie kredyty,
    szczebel jest `off`, ale backend nadal podaje `usedMinor`/`limitMinor` z ostatniego
    POMIARU — i to jedyne, co na 480x320 ma sens: "300,04 / 300,00 EUR". Powodu
    wycofania panel nie pisze, bo nie ma na to ani miejsca, ani potrzeby; od wyjasnien
    jest WWW. Znikniecie wiersza bylo by za to realna strata — pas gubilby liczbe,
    ktora wczesniej pokazywal.
    """
    if rung is None or rung.state == "unknown" or rung.state is None:
        return CreditsView("unknown")
    # Przy `off` rysujemy wylacznie PELNA pare kwot. Konto bez kredytow tez ma
    # `usedMinor: 0` (Anthropic podaje tam wyzerowane `spend.used`), a "0,00 / —" to
    # wiersz bez tresci — na 480x320 kosztuje miejsce i niczego nie mowi.
    if rung.state == "off" and not (rung.used_minor is not None and rung.limit_minor):
        return CreditsView("off")
    bar = 0.0
    if rung.used_minor is not None and rung.limit_minor:
        bar = fmt.clamp_pct(rung.used_minor / float(rung.limit_minor) * 100.0)
    return CreditsView(
        rung.state,
        used=fmt.money(rung.used_minor, None, rung.exponent),
        limit=fmt.money(rung.limit_minor, None, rung.exponent),
        currency=rung.currency,
        bar_pct=bar,
        is_current=rung.is_current,
    )


def plan_label(account):
    """"Max 5×" / "Team Standard" — plan MUSI byc widoczny.

    "40%" znaczy co innego na Max 20x niz na miejscu Team Standard, wiec
    UI-HANDOUT stawia to jako zasade 1 prezentacji. Wyprowadzamy z tokenu, a nie
    ze slownika — nowy tier ma sie pokazac sam, choćby surowo (zasada 5).
    """
    if account is None:
        return ""
    token = (account.rate_limit_tier or account.subscription_type
             or account.org_type or "")
    if not token:
        return ""
    text = token.lower()
    for prefix in ("default_", "claude_"):
        text = text.replace(prefix, "")
    words = []
    for word in text.replace("-", "_").split("_"):
        if not word:
            continue
        if len(word) > 1 and word[-1] == "x" and word[:-1].isdigit():
            words.append(word[:-1] + "×")
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)
