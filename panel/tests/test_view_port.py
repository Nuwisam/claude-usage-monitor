"""view.py — stan na wyglad. Tu pilnujemy zasady 4 z AGENTS.md.

Najwazniejszy test w tym pliku to `test_unknown_nigdy_nie_jest_zerem`. Falszywe,
pewnie wygladajace zero jest najgorszym trybem awarii tego narzedzia: uzytkownik
odpala duze zadanie i trafia w sciane.
"""
import pytest

from panel import fmt, model, view

NOW = fmt.ms(fmt.parse_utc("2026-07-26T19:07:40Z"))


def series(**kw):
    d = {"seriesKey": "limit:session|session|-|-", "label": "Session",
         "source": "limit", "kind": "session", "bucketKey": "five_hour",
         "primary": True, "freshness": "live", "sortOrder": 15}
    d.update(kw)
    return model.SeriesStatus(d)


# --- zasada 4 ---------------------------------------------------------------

def test_unknown_pokazuje_ostatni_pomiar_nie_zero():
    """Zasada 4 w wersji panelowej: gdy backend nie zna biezacej wartosci,
    pokazujemy OSTATNIA ZMIERZONA, nigdy zero. O tym, ile ta liczba jest warta,
    mowi wiek odczytu obok — panel nie ma osobnych stanow swiezosci w rysunku."""
    v = view.describe_series(series(freshness="unknown", utilization=None,
                                    rawUtilization=42))
    assert v.number == "42", "ostatni pomiar, a nie slowo i nie zero"
    assert v.bar_pct == 42 and v.measured is True
    assert v.words is None


def test_unknown_na_setce_nie_gubi_setki():
    """Przypadek z produkcji: konto milczy 12 h, tydzien byl na 100%. Pokazanie
    zera albo pustego toru bylo by tu najgorszym mozliwym klamstwem."""
    v = view.describe_series(series(freshness="unknown", utilization=None,
                                    rawUtilization=100))
    assert v.number == "100" and v.full is True and v.bar_pct == 100


def test_brak_jakiegokolwiek_pomiaru_to_slowa_nie_zero():
    """Jedyny przypadek, w ktorym naprawde nie ma czego narysowac."""
    for v in (view.missing_view(),
              view.describe_series(series(freshness="unknown", utilization=None,
                                          rawUtilization=None)),
              view.describe_series(None)):
        assert v.number is None and v.words == "nie wiem"
        assert v.bar_pct == 0 and v.measured is False and v.hatch is True


def test_inferred_reset_ma_tylde():
    """Tylda odroznia wnioskowanie od pomiaru (freshness.ts:84) — to jedyne
    miejsce, gdzie liczba nie pochodzi z pomiaru, tylko z rozumowania serwera."""
    v = view.describe_series(series(freshness="inferred_reset", utilization=0,
                                    rawUtilization=97))
    assert v.number == "~0" and v.bar_pct == 0


@pytest.mark.parametrize("freshness", ["live", "stale"])
def test_live_i_stale_wygladaja_tak_samo(freshness):
    """SWIADOME odstepstwo od WWW: obok stoi wiek odczytu, ktory mowi to samo
    dokladniej. Drugi kanal na te sama informacje bylby szumem."""
    v = view.describe_series(series(freshness=freshness, utilization=31))
    assert v.measured is True and v.number == "31" and v.hatch is False


def test_setka_to_koniec():
    # Jedyny prog w calym UI. 100% to nie "prawie koniec" — to koniec szczebla.
    assert view.describe_series(series(utilization=100)).full is True
    assert view.describe_series(series(utilization=99)).full is False


# --- podpisy resetu ---------------------------------------------------------

def test_cztery_powody_braku_granicy_daja_cztery_napisy():
    """resetsAt: null ma kilka przyczyn i sklejenie ich w jeden napis bylo bledem
    juz raz (freshness.ts:105-125)."""
    napisy = {
        view.reset_note(series(source="spend", utilization=41), NOW)[0],
        view.reset_note(series(utilization=0), NOW)[0],
        view.reset_note(series(utilization=44), NOW)[0],
        view.reset_note(series(utilization=44,
                               resetsAt="2026-07-26T19:00:00Z"), NOW)[0],
    }
    assert napisy == {"bez resetu", "okno nie wystartowało",
                      "czas resetu nieznany", "reset minął"}


def test_reset_w_przyszlosci_ma_odliczanie_i_godzine():
    lead, at = view.reset_note(series(utilization=31,
                                      resetsAt="2026-07-26T20:00:00Z"), NOW)
    assert lead.startswith("reset za ") and at is not None
    assert "po resecie" not in lead, "prog zaokraglenia musi byc ten sam co w countdown()"


def test_tydzien_dostaje_dzien_tygodnia():
    """Reset za 6 dni: sama godzina klamie, bo nie mowi ktorego dnia. Decyduje
    o tym `at_stamp`, ten sam co w WWW — przyimek jest w srodku stempla."""
    _, at = view.reset_note(series(resetsAt="2026-08-01T16:00:00Z", utilization=30),
                            NOW)
    przyimek, dzien = at.split()[:2]
    assert przyimek in ("w", "we") and dzien in fmt.DAYS


def test_brak_serii_nie_wybucha():
    assert view.reset_note(None, NOW) == ("brak danych", None)


# --- wybor serii ------------------------------------------------------------

def test_hero_stoi_na_sesji_nie_na_isActive():
    """Gdyby hero szedl za isActive, ten sam ekran znaczylby co innego
    w zaleznosci od pory tygodnia (HeroSession.tsx:20-22)."""
    tydzien = series(seriesKey="limit:weekly_all|weekly|-|-", kind="weekly_all",
                     bucketKey="seven_day", isActive=True, utilization=99)
    sesja = series(isActive=False, utilization=3)
    assert view.pick_session([tydzien, sesja]) is sesja


def test_pickery_pomijaja_duplikaty():
    """Renderujemy tylko serie primary — bucket i limit to ta sama liczba
    podana dwa razy (status.py:96-120)."""
    duplikat = series(seriesKey="bucket:five_hour", primary=False, kind=None,
                      utilization=2)
    prawdziwa = series(utilization=2)
    assert view.pick_session([duplikat, prawdziwa]) is prawdziwa

    wk_dup = series(seriesKey="bucket:seven_day", primary=False, kind=None,
                    bucketKey="seven_day")
    wk = series(seriesKey="limit:weekly_all|weekly|-|-", kind="weekly_all",
                bucketKey="seven_day")
    assert view.pick_weekly([wk_dup, wk]) is wk


def test_tydzien_bierze_zbiorczy_nie_zawezony():
    """Swiadoma decyzja: wiersz ma znaczyc to samo w kazdy dzien tygodnia."""
    scoped = series(seriesKey="limit:weekly_scoped|weekly|fable|-",
                    kind="weekly_scoped", bucketKey="seven_day_fable",
                    utilization=99)
    wszystkie = series(seriesKey="limit:weekly_all|weekly|-|-", kind="weekly_all",
                       bucketKey="seven_day", utilization=30)
    assert view.pick_weekly([scoped, wszystkie]) is wszystkie


def test_brak_pasujacej_serii_daje_none():
    assert view.pick_session([]) is None
    assert view.pick_weekly([]) is None


# --- kredyty ----------------------------------------------------------------

def test_kredyty_maja_trzy_stany():
    """"wylaczone" i "nie wiem" to DWIE ROZNE rzeczy (cascade.py:11-13)."""
    on = view.credits(model.CascadeRung({"key": "credits", "state": "on",
                                         "usedMinor": 3820, "limitMinor": 9000,
                                         "currency": "USD", "exponent": 2,
                                         "isCurrent": True}))
    assert (on.state, on.used, on.limit, on.currency) == ("on", "38,20", "90,00", "USD")
    assert on.bar_pct == pytest.approx(42.44, abs=0.01) and on.is_current

    assert view.credits(model.CascadeRung({"key": "credits", "state": "off"})).state == "off"
    assert view.credits(model.CascadeRung({"key": "credits", "state": "unknown"})).state == "unknown"
    assert view.credits(None).state == "unknown", "brak szczebla to brak wiedzy"


def test_wycofane_kredyty_pokazuja_kwoty_zamiast_znikac():
    """Gdy organizacja odetnie kredyty, szczebel jest `off`, ale kwoty z OSTATNIEGO
    pomiaru zostaja — i to jedyne, co na 480x320 ma sens. Wiersz, ktory znika, to strata
    liczby, ktora panel wczesniej pokazywal; powodu wycofania panel nie pisze wcale."""
    c = view.credits(model.CascadeRung({"key": "credits", "state": "off",
                                        "reason": "org_level_disabled_until",
                                        "usedMinor": 30004, "limitMinor": 30000,
                                        "currency": "EUR", "exponent": 2}))
    assert (c.used, c.limit, c.currency) == ("300,04", "300,00", "EUR")
    assert c.bar_pct == 100.0, "nadwyzka nie moze rozepchnac paska"
    assert c.state == "off", "stan zostaje `off` — kwoty go nie zmieniaja"


def test_kredyty_wylaczone_bez_kwot_nadal_nie_maja_czego_pokazac():
    c = view.credits(model.CascadeRung({"key": "credits", "state": "off"}))
    assert c.state == "off" and c.used is None


def test_konto_bez_kredytow_nie_dostaje_wiersza_z_zerem():
    """Konto, ktore kredytow nigdy nie mialo, ma `usedMinor: 0` i ZADNEGO limitu —
    Anthropic podaje tam wyzerowane `spend.used`. Wiersz "0,00 / —" zajalby miejsce
    i nie powiedzialby nic; wczesniej tego wiersza nie bylo i ma go nie byc dalej."""
    c = view.credits(model.CascadeRung({"key": "credits", "state": "off",
                                        "usedMinor": 0, "currency": "USD",
                                        "exponent": 2}))
    assert c.used is None, "polowa pary kwot to nie sa kwoty"


def test_kredyty_bez_limitu_nie_dziela_przez_zero():
    c = view.credits(model.CascadeRung({"key": "credits", "state": "on",
                                        "usedMinor": 100, "limitMinor": 0}))
    assert c.bar_pct == 0.0


# --- plan -------------------------------------------------------------------

@pytest.mark.parametrize("tier,want", [
    ("default_claude_max_5x", "Max 5×"),
    ("default_claude_max_20x", "Max 20×"),
    ("default_claude_team_standard", "Team Standard"),
])
def test_plan_z_tokenu(tier, want):
    assert view.plan_label(model.AccountStatus({"rateLimitTier": tier})) == want


def test_nieznany_tier_pokazuje_sie_surowo():
    """Zasada 5: nowy token ma zadzialac bez migracji. Pusta etykieta lamalaby
    zasade 1 prezentacji — plan MUSI byc widoczny, bo 40% znaczy co innego
    na Max 20x niz na miejscu Team."""
    a = model.AccountStatus({"rateLimitTier": "raven"})
    assert view.plan_label(a) == "Raven"


def test_plan_ma_zapasowe_zrodla():
    assert view.plan_label(model.AccountStatus({"subscriptionType": "pro"})) == "Pro"
    assert view.plan_label(model.AccountStatus({"orgType": "claude_team"})) == "Team"
    assert view.plan_label(model.AccountStatus({})) == ""
    assert view.plan_label(None) == ""
