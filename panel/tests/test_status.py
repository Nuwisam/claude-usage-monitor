"""Parser ramki `alert`. Zepsuta ramka nie moze zabic panelu.

To jest jedyne wejscie danych o zablokowanych sesjach do klienta, a ich zrodlem jest
payload hooka Claude Code — czyli ksztalt, ktory zmienia sie miedzy wersjami klienta.
Kazdy test tutaj sprawdza wiec to samo pytanie z innej strony: czy panel przezyje
wpis, ktorego nie rozumie.
"""
from panel import status


def frame(*entries):
    return {"contractVersion": 3, "serverNow": "2026-08-05T21:07:00Z",
            "alerts": list(entries)}


def entry(**kw):
    base = {"key": "s__main__k", "reason": "permission", "project": "proj",
            "machine": "laptop", "tool": "Bash", "since": "2026-08-05T21:00:00Z"}
    base.update(kw)
    return base


def test_typowa_ramka():
    out = status.parse_frame(frame(entry()))
    assert len(out) == 1
    assert out[0].title == "NEEDS PERMISSION"
    assert out[0].short == "allow"
    assert (out[0].tool, out[0].machine) == ("Bash", "laptop")


def test_uszkodzony_wpis_nie_zabija_reszty():
    out = status.parse_frame(frame(entry(key="a"), "to nie jest obiekt", None,
                                   {"reason": "permission"}, entry(key="b")))
    assert [b.key for b in out] == ["a", "b"], "wpis bez klucza jest nie do zamkniecia"


def test_nieznany_reason_nie_jest_bledem():
    """Writer moze byc nowszy niz panel. 'CLAUDE IS WAITING' jest prawda w kazdym
    takim przypadku, a pusty ekran nie."""
    out = status.parse_frame(frame(entry(reason="cos-nowego")))
    assert out[0].title == status.UNKNOWN


def test_brak_since_nie_wyrzuca_wpisu():
    out = status.parse_frame(frame(entry(since=None)))
    assert len(out) == 1 and out[0].since is None


def test_since_w_przyszlosci_przechodzi():
    """Zegary maszyn sie rozjezdzaja. Wpis z przyszlosci jest dziwny, ale prawdziwy —
    o tym, co z nim zrobic, decyduje `fmt.waited`, ktore nie schodzi ponizej zera."""
    out = status.parse_frame(frame(entry(since="2099-01-01T00:00:00Z")))
    assert len(out) == 1


def test_smieci_zamiast_ramki():
    for junk in (None, [], "tekst", {"alerts": "nie-lista"}, {}):
        assert status.parse_frame(junk) == []


def test_kolejnosc_od_najmlodszej_bez_wzgledu_na_powod():
    """Powod nie ma wplywu na kolejnosc — decyduje sam wiek.

    Karta obcina sie do trzech wierszy, a kazda blokada byla juz pokazana solo, gdy
    wchodzila. Warte pokazania sa wiec te, ktorych jeszcze nie widziales. Dawna ranga
    (plan, pytanie, zgoda) wypychala z wierszy blokade, ktora wlasnie przejela ekran.
    """
    out = status.parse_frame(frame(
        entry(key="perm-stary", reason="permission", since="2026-08-05T20:00:00Z"),
        entry(key="pyt", reason="question", since="2026-08-05T21:00:00Z"),
        entry(key="plan", reason="plan", since="2026-08-05T21:06:00Z"),
        entry(key="perm-nowy", reason="permission", since="2026-08-05T21:05:00Z"),
    ))
    assert [b.key for b in out] == ["plan", "perm-nowy", "pyt", "perm-stary"]


def test_wpis_bez_stempla_laduje_na_koncu():
    """Brak wiedzy o wieku nie moze udawac ani swiezosci, ani starosci."""
    out = status.parse_frame(frame(
        entry(key="bez", reason="plan", since=None),
        entry(key="stary", reason="permission", since="2026-08-05T20:00:00Z"),
        entry(key="nowy", reason="permission", since="2026-08-05T21:05:00Z"),
    ))
    assert [b.key for b in out] == ["nowy", "stary", "bez"]


def test_duplikat_klucza_liczy_sie_raz():
    out = status.parse_frame(frame(entry(key="x"), entry(key="x")))
    assert len(out) == 1


def test_brak_maszyny_nie_jest_bledem():
    out = status.parse_frame(frame(entry(machine=None)))
    assert out[0].tool == "Bash" and out[0].machine is None


def test_tryb_i_subagent_wchodza_do_listwy():
    """`permissionMode` i `agentType` sa w kontrakcie od poczatku (docs/API.md § 3.2)
    i to one odpowiadaja na pytanie 'czemu on w ogole pyta'."""
    out = status.parse_frame(frame(entry(permissionMode="plan",
                                         agentType="general-purpose")))
    assert out[0].mode_label == "plan · general-purpose"


def test_listwa_trybu_znosi_brak_obu_pol():
    out = status.parse_frame(frame(entry()))
    assert out[0].mode_label == ""


def test_nieznany_reason_ma_skrot():
    """Ta sama zasada co przy `title`: nieznany powod dostaje slowo prawdziwe zawsze."""
    out = status.parse_frame(frame(entry(reason="cos-nowego")))
    assert out[0].short == status.SHORT_UNKNOWN
