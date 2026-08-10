"""Parser of the `alert` frame. A broken frame must not kill the panel.

This is the only way data about blocked sessions gets into the client, and its source is
the payload of a Claude Code hook — that is, a shape that changes between client versions.
Every test here therefore asks the same question from another side: will the panel
survive an entry it does not understand.
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
    """The writer may be newer than the panel. 'CLAUDE IS WAITING' is true in every
    such case; an empty screen is not."""
    out = status.parse_frame(frame(entry(reason="cos-nowego")))
    assert out[0].title == status.UNKNOWN


def test_brak_since_nie_wyrzuca_wpisu():
    out = status.parse_frame(frame(entry(since=None)))
    assert len(out) == 1 and out[0].since is None


def test_since_w_przyszlosci_przechodzi():
    """Machine clocks drift apart. An entry from the future is odd but real — what to
    do with it is decided by `fmt.waited`, which does not go below zero."""
    out = status.parse_frame(frame(entry(since="2099-01-01T00:00:00Z")))
    assert len(out) == 1


def test_smieci_zamiast_ramki():
    for junk in (None, [], "tekst", {"alerts": "nie-lista"}, {}):
        assert status.parse_frame(junk) == []


def test_kolejnosc_od_najmlodszej_bez_wzgledu_na_powod():
    """The reason has no bearing on the order — age alone decides.

    The card cuts itself to three rows, and every block was already shown solo when it
    came in. The ones worth showing are therefore the ones not seen yet. The old rank
    (plan, question, permission) pushed out of the rows the very block that had just
    taken the screen over.
    """
    out = status.parse_frame(frame(
        entry(key="perm-stary", reason="permission", since="2026-08-05T20:00:00Z"),
        entry(key="pyt", reason="question", since="2026-08-05T21:00:00Z"),
        entry(key="plan", reason="plan", since="2026-08-05T21:06:00Z"),
        entry(key="perm-nowy", reason="permission", since="2026-08-05T21:05:00Z"),
    ))
    assert [b.key for b in out] == ["plan", "perm-nowy", "pyt", "perm-stary"]


def test_wpis_bez_stempla_laduje_na_koncu():
    """Not knowing the age must pass for neither freshness nor staleness."""
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
    """`permissionMode` and `agentType` have been in the contract from the start
    (docs/API.md § 3.2) and they are what answers 'why is it asking at all'."""
    out = status.parse_frame(frame(entry(permissionMode="plan",
                                         agentType="general-purpose")))
    assert out[0].mode_label == "plan · general-purpose"


def test_listwa_trybu_znosi_brak_obu_pol():
    out = status.parse_frame(frame(entry()))
    assert out[0].mode_label == ""


def test_nieznany_reason_ma_skrot():
    """The same rule as with `title`: an unknown reason gets a word that is always true."""
    out = status.parse_frame(frame(entry(reason="cos-nowego")))
    assert out[0].short == status.SHORT_UNKNOWN
