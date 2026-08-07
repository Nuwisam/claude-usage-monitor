"""Testy sygnalizatora zablokowanej sesji — sekcja "alert" w client/usage-probe.py.

Tutaj, a nie w `client/`, z tego samego powodu co `test_probe_parsing.py`: to jest kod
dzialajacy w sciezce Twojej pracy, ktory NIGDY nie rzuca wyjatkiem — czyli kazdy jego
blad jest z definicji cichy, a to jedyne miejsce, gdzie cokolwiek go sprawdza.

Sygnalizator byl osobnym skryptem do czasu, gdy pomiar pokazal, ze doklejenie go do
procesu sondy kosztuje 2,7 ms wobec 41,9 ms za osobny proces. Testy zostaly, zmienil sie
tylko plik, z ktorego sa ladowane.

Kazdy przypadek odpowiada zmierzonemu zachowaniu harnessu, nie wyobrazeniu o nim.
Metodyka pomiaru siedzi w docstringu samego skryptu.
"""
import importlib.util
import io
import json
import os
import shutil
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "client" / "usage-probe.py"


@pytest.fixture
def ss(tmp_path, monkeypatch):
    """Swiezy modul z katalogiem stanu w tmp_path. Modul, nie instancja, bo sciezki
    sa w nim stalymi modulu — dokladnie tak, jak widzi je hook."""
    spec = importlib.util.spec_from_file_location("usage_probe_alert", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUTDIR = str(tmp_path)
    mod.STATEDIR = str(tmp_path / "session-status")
    mod.POSTED = str(tmp_path / "posted.txt")
    mod.CONFIG = str(tmp_path / "config.json")
    # Rejestr sesji harnessu tez do tmp_path. Domyslnie katalog NIE ISTNIEJE, czyli zbior
    # zywych sesji jest "nie wiem" i regula smierci nie biegnie — testy, ktore jej nie
    # dotycza, przechodza ta sama sciezka co dotad.
    mod.REGDIR = str(tmp_path / "sessions")
    # Sciezki sondy tez do tmp_path: `main()` jest tu wolane i nie ma prawa dotknac
    # prawdziwego stanu maszyny ani odpalic `claude -p`.
    mod.THROTTLE_FILE = str(tmp_path / "last-probe.txt")
    mod.LOG = str(tmp_path / "usage-samples.jsonl")
    mod.SPOOL = str(tmp_path / "spool.jsonl")
    mod.CLI_OUT = str(tmp_path / "usage-cli.json")
    # Zadnych toastow, zadnej sieci, zadnych procesow potomnych w testach.
    monkeypatch.setattr(mod, "toast", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "spawn_refresh", lambda cfg: None)
    mod.wyslane = []
    monkeypatch.setattr(mod, "post",
                        lambda cfg, url, body: (mod.wyslane.append(body), (200, "{}"))[1])
    return mod


CFG = {"alert_url": "https://example.org/api/session-alert", "ingest_token": "t"}

SID = "sesja-1"


def hook(event, **kw):
    base = {"hook_event_name": event, "session_id": SID,
            "cwd": r"Z:\projects\claude-usage-monitor",
            "transcript_path": str(Path.home() / ".claude" / "projects"
                                   / "z--projects-claude-usage-monitor" / "x.jsonl"),
            "prompt_id": "p1"}
    base.update(kw)
    return base


def names(ss):
    return sorted(n for n, _ in ss.entries())


def rejestr(ss, *session_ids):
    """Rekordy `<pid>.json` w podstawionym rejestrze harnessu.

    Wypelniamy PRZED `alert_dispatch`: `registry_seen` zapisuje sie w `enter()`, a `O_EXCL`
    nie pozwoli tego potem poprawic."""
    os.makedirs(ss.REGDIR, exist_ok=True)
    for i, sid in enumerate(session_ids):
        pid = 1000 + i
        with open(os.path.join(ss.REGDIR, "%d.json" % pid), "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "sessionId": sid, "kind": "interactive",
                       "cwd": r"Z:\projects\x", "version": "2.1.223"}, f)


# --------------------------------------------------------------------- maszyna stanow
def test_permission_request_zaklada_wpis(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "git status"}))
    assert len(names(ss)) == 1
    wpis = ss.snapshot()[0]
    assert wpis["reason"] == "permission"
    assert wpis["detail"] == "git status"
    assert wpis["project"] == "claude-usage-monitor"


def test_pretooluse_zwyklego_narzedzia_nie_robi_nic(ss):
    """Sciezka goraca. `PermissionRequest` odpala WYLACZNIE przy realnym pytaniu do
    czlowieka (zmierzone: Read/Grep/Write/echo — zero wystapien), wiec `PreToolUse`
    nie ma tu nic do roboty i nie wolno mu dotknac dysku."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="Read",
                          tool_input={"file_path": "a.py"}, tool_use_id="toolu_1"))
    assert names(ss) == []
    assert not os.path.isdir(ss.STATEDIR)


@pytest.mark.parametrize("tool,reason", [("AskUserQuestion", "question"),
                                         ("ExitPlanMode", "plan")])
def test_dwa_narzedzia_wchodza_przez_pretooluse(ss, tool, reason):
    """Te dwa ZAWSZE blokuja, wiec `PreToolUse` nie daje przy nich falszywek —
    a niesie `tool_use_id`, ktorego `PermissionRequest` nie ma."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name=tool, tool_input={},
                          tool_use_id="toolu_9"))
    assert ss.snapshot()[0]["reason"] == reason
    assert names(ss) == ["%s__main__toolu_9.json" % SID]


def test_permission_request_nie_dubluje_wejscia_tych_dwoch(ss):
    """Kolejnosc `PreToolUse` vs `PermissionRequest` jest NIEGWARANTOWANA (zmierzone
    20% inwersji), wiec dwa zrodla wejscia dla jednego wywolania daly by wyscig
    o dwa pliki i dwa toasty."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="AskUserQuestion", tool_input={},
                          tool_use_id="toolu_9"))
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="AskUserQuestion",
                          tool_input={}))
    assert len(names(ss)) == 1


def test_posttooluse_zamyka_po_call_key(ss):
    """`PermissionRequest` nie ma `tool_use_id`, wiec wpis stoi na `call_key`.
    Wyjscie liczy OBA kandydaty i kasuje oba — bez wiedzy, ktorym trybem powstal."""
    ti = {"command": "git status"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Bash", tool_input=ti,
                          tool_use_id="toolu_1", tool_response="ok"))
    assert names(ss) == []


def test_posttooluse_zamyka_po_tool_use_id_mimo_zmienionego_tool_input(ss):
    """Zmierzone: harness domerza odpowiedzi do `tool_input` AskUserQuestion miedzy
    wejsciem a wyjsciem (1326 -> 1649 B). Jednolity hash rozjechalby sie dokladnie
    dla narzedzia, dla ktorego mial byc najpewniejszy."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="AskUserQuestion",
                          tool_input={"questions": [{"question": "ktory wariant?"}]},
                          tool_use_id="toolu_7"))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="AskUserQuestion",
                          tool_input={"questions": [{"question": "ktory wariant?"}],
                                      "answers": {"a": "b"}},
                          tool_use_id="toolu_7"))
    assert names(ss) == []


def test_posttoolbatch_domyka_to_czego_posttooluse_nie(ss):
    """Zmierzone: Edit na pliku planu 0/6 domknietych przez `PostToolUse`,
    a 4/4 objete przez `tool_calls[]`. To nie jest nadmiarowosc."""
    ti = {"file_path": "plan.md"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Edit", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolBatch", tool_calls=[
        {"tool_name": "Edit", "tool_input": ti, "tool_use_id": "toolu_2"}]))
    assert names(ss) == []


def test_zdarzenie_z_agent_id_nie_zamyka_wpisu_watku_glownego(ss):
    """Jedyny tryb awarii, ktorego ta funkcja nie toleruje, to falszywe ODBLOKOWANIE.

    Zmierzone: z 393 okien blokady 8 mialo w trakcie obce wywolania narzedzi, 155
    zdarzen, z czego 154 z subagentow — do 52 zdarzen klasy 'wyjscie' podczas jednego
    szesciominutowego dialogu. To stan ustalony, nie wyscig.
    """
    ti = {"command": "git status"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Bash", tool_input=ti,
                          tool_use_id="toolu_1", agent_id="agent-a"))
    assert len(names(ss)) == 1, "subagent zamknal blokade watku glownego"


def test_subagent_ma_wlasny_wpis(ss):
    """Subagenty dziela `session_id` rodzica i odrozniaja sie `agent_id` — potwierdzone
    na 161 295/161 295 rekordach sidechain i zmierzone na zywo (76 zdarzen)."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}, agent_id="agent-a",
                          agent_type="general-purpose"))
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    assert len(names(ss)) == 2


# --------------------------------------------------------------------- zamiatanie
@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop", "SessionEnd"])
def test_zamiatanie_gasi_alert_po_odmowie(ss, event):
    """Nic, co konczy wywolanie inaczej niz normalnym wykonaniem, nie generuje ZADNEGO
    zdarzenia — odmowa przyciskiem, Esc na prompcie i Esc w trakcie dzialania dalyby
    5/5 razy cisze. Zamiatanie po prefiksie jest jedynym mechanizmem gaszacym."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "rm -rf /"}))
    ss.alert_dispatch(CFG, hook(event))
    assert names(ss) == []


def test_zamiatanie_nie_rusza_cudzej_sesji(ss):
    """`SessionEnd` przychodzi ~raz na minute z identyfikatorem dziecka `claude -p`
    odpalanego przez sonde. Zamiatanie globalne wycieraloby alerty co minute.

    Rejestr podstawiamy jawnie i obie sesje w nim SA — inaczej przypadek przechodzilby
    tylko dlatego, ze zbior zywych sesji jest nieznany, i nie mowilby nic."""
    rejestr(ss, SID, "dziecko-claude-p")
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    ss.alert_dispatch(CFG, hook("SessionEnd", session_id="dziecko-claude-p"))
    assert len(names(ss)) == 1


# --------------------------------------------------- smierc wpisu z rejestru
# Rejestr harnessu (`~/.claude/sessions/<pid>.json`) jest jedynym zrodlem wiedzy o tym, czy
# sesja jeszcze zyje. Nigdy `os.kill` — na Windows to `TerminateProcess`.
#
# Zdarzenie zamiatajace ma INNY `session_id` niz wpis i NIE jest `SessionStart`/`SessionEnd`
# (na tych dwoch regula nie biegnie), inaczej przypadek przechodzi inna sciezka.
def wpis_obcej_sesji(ss, sid="obca-sesja", w_rejestrze=True):
    """Wpis sesji `sid`, powstaly gdy ta sesja BYLA w rejestrze (czyli z `registry_seen`)."""
    if w_rejestrze:
        rejestr(ss, sid, SID)
    else:
        rejestr(ss, SID)
    ss.alert_dispatch(CFG, hook("PermissionRequest", session_id=sid, tool_name="Bash",
                                tool_input={"command": "ls"}))
    return names(ss)[0]


def zabij_rekord(ss, sid):
    """Usuwa z rejestru rekord tej sesji — tak jak robi to harness po wyjsciu procesu.

    Uchwyt zamykamy PRZED `os.remove`: Windows nie pozwala skasowac otwartego pliku
    (WinError 32) — ta sama pulapka, ktora `drop()` obchodzi trzema podejsciami."""
    do_usuniecia = []
    for f in os.listdir(ss.REGDIR):
        p = os.path.join(ss.REGDIR, f)
        with open(p, encoding="utf-8") as fh:
            if json.load(fh).get("sessionId") == sid:
                do_usuniecia.append(p)
    for p in do_usuniecia:
        os.remove(p)


def test_wpis_sesji_bez_rekordu_ginie_i_jest_publikowany(ss):
    """Zamknieta karta nie odpala zadnego hooka, ale jej rekord w rejestrze znika (zmierzone
    <=5 s przy zamknieciu okna, 626 ms po zabiciu procesu — usuwa go sam harness)."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [], nazwa
    assert ss.wyslane[-1]["entries"] == [], "opustoszenie zbioru MUSI dojsc do panelu"


def test_wpis_sesji_z_rekordem_zostaje(ss):
    """Kontrola negatywna: czlowiek nadal czeka w tamtej karcie."""
    wpis_obcej_sesji(ss)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1


def test_wpis_bez_registry_seen_nie_podlega_regule(ss):
    """Sesja, ktorej harness nie rejestruje, istnieje realnie: `claude.exe` bez konsoli zyl
    18 s i rekordu nie dostal. Bez tego znacznika jej blokada ginelaby natychmiast."""
    wpis_obcej_sesji(ss, w_rejestrze=False)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1
    assert ss.read_entry(names(ss)[0])["registry_seen"] is False


def test_brak_katalogu_rejestru_nie_kasuje_nic(ss):
    """"Nie wiem" NIGDY nie znaczy "pusty" — inaczej maszyna bez rejestru gasilaby
    wszystkie swoje alerty przy pierwszym zdarzeniu."""
    nazwa = wpis_obcej_sesji(ss)
    shutil.rmtree(ss.REGDIR)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [nazwa]


def test_jeden_nieczytelny_rekord_uniewaznia_caly_przebieg(ss):
    """Katalog przeczytany do polowy skrocilby zbior zywych i skasowal HURTEM cudze wpisy."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    os.mkdir(os.path.join(ss.REGDIR, "31337.json"))     # nazwa pasuje, `open` rzuca
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [nazwa]


def test_pliki_nie_bedace_rekordami_sa_ignorowane(ss):
    """Harness trzyma w tym katalogu tez `.in_use` i `.last_inuse_sweep` — filtruje po
    `<cyfry>.json` i my filtrujemy tak samo. To nie sa "rekordy nieparsowalne"."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    for smiec in (".in_use", ".last_inuse_sweep", "nie-liczba.json"):
        with open(os.path.join(ss.REGDIR, smiec), "w", encoding="utf-8") as f:
            f.write("cokolwiek, nie JSON")
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [], nazwa


def test_nazwa_wpisu_bez_trzech_czlonow_nie_ginie(ss):
    """`smieci.json` potrafi tam byc realnie (patrz test snapshotu). Nazwa spoza schematu
    nie jest martwa — jest obca, wiec jej nie ruszamy."""
    rejestr(ss, SID)
    os.makedirs(ss.STATEDIR, exist_ok=True)
    with open(os.path.join(ss.STATEDIR, "smieci.json"), "w", encoding="utf-8") as f:
        json.dump({"registry_seen": True, "reason": "permission"}, f)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == ["smieci.json"]


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
def test_na_brzegach_sesji_regula_nie_biegnie(ss, event):
    """Zmierzone: na `SessionStart` rekord powstaje 0,2-1,0 s PO pierwszym hooku (13/13),
    a na `SessionEnd` jeszcze ISTNIEJE (14/14). Na tych dwoch jego brak nie dowodzi niczego.

    Sesja zamiatajaca MUSI byc w rejestrze (`hook()` uzywa `SID`, ktory tam wlozylismy),
    inaczej wpis ratuje bezpiecznik "brak biezacej sesji" i przypadek nie mowi nic o brzegach.
    """
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    ss.alert_dispatch(CFG, hook(event))
    assert names(ss) == [nazwa]
    # Kontrola pozytywna: to samo zdarzenie, ktore brzegiem nie jest, wpis KASUJE.
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == []


def test_brak_biezacej_sesji_w_rejestrze_wstrzymuje_regule(ss):
    """Sesja, w ktorej biegnie ten hook, ZYJE. Jesli jej w rejestrze nie ma, to nie rozumiemy
    rejestru — i nie wolno na nim opierac kasowania czegokolwiek."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    zabij_rekord(ss, SID)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [nazwa]


def test_wstrzymanie_zostawia_slad_w_logu(ss):
    """Inaczej "mechanizm umarl po zmianie u Anthropic" i "nie ma czego zbierac" sa
    nierozroznialne, a objawem jest dokladnie ten blad, ktory ta sekcja naprawia."""
    wpis_obcej_sesji(ss)
    shutil.rmtree(ss.REGDIR)
    ss.alert_dispatch(CFG, hook("Stop"))
    with open(ss.LOG, encoding="utf-8") as f:
        linie = [json.loads(l) for l in f if l.strip()]
    assert [r for r in linie if r.get("alert_skip") == "rejestr-niepelny"]


def test_pusty_katalog_stanu_nie_czyta_rejestru(ss, monkeypatch):
    """Sciezka goraca: przy pustym katalogu stanu nie ma czego zbierac, wiec rejestr nie ma
    prawa byc otwierany. Zmierzone, ze to caly koszt tej galezi na typowym zdarzeniu."""
    monkeypatch.setattr(ss, "live_sessions",
                        lambda: pytest.fail("rejestr czytany przy pustym katalogu stanu"))
    ss.alert_dispatch(CFG, hook("Stop"))
    ss.alert_dispatch(CFG, hook("UserPromptSubmit"))


def test_ttl_kasuje_ale_nigdy_nie_ukrywa(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    nazwa = names(ss)[0]
    stary = time.time() - 2 * ss.DEFAULT_TTL_S
    os.utime(os.path.join(ss.STATEDIR, nazwa), (stary, stary))
    ss.sweep_ttl(ss.DEFAULT_TTL_S, time.time())
    assert names(ss) == []


# ------------------------------------------------------- domykanie z transkryptu
# Odmowa i Esc nie generuja ZADNEGO zdarzenia hooka, ale ZAPISUJA `tool_result`
# w transkrypcie — zmierzone na 2.1.223 trzy razy, z trzema roznymi trescami. To jedyna
# droga, ktora gasi alert sesji zamilklej po odmowie, i dziala z zamiatania DOWOLNEJ sesji.
#
# W kazdym przypadku zdarzenie zamiatajace ma INNY `session_id` niz wpis. Bez tego wpis ginie
# od zamiatania po prefiksie, zanim ktokolwiek otworzy transkrypt, i test nie mowi nic.
@pytest.fixture
def tdir(tmp_path):
    """Katalog transkryptow. Nazwa MUSI zostac slugiem projektu, bo z niej liczy sie
    `project` i na tym stoja asercje pozostalych testow."""
    d = tmp_path / "z--projects-claude-usage-monitor"
    d.mkdir()
    return d


def uzycie(tool, ti, tuid):
    """Rekord `assistant` z blokiem `tool_use` — zmierzone, ze wystepuje wylacznie tam."""
    return {"type": "assistant", "timestamp": "2026-08-07T10:00:00.000Z",
            "message": {"content": [{"type": "tool_use", "id": tuid,
                                     "name": tool, "input": ti}]}}


def wynik(tuid, kiedy, prompt_id="p1", is_error=True):
    """Rekord `user` z blokiem `tool_result`. `promptId` jest na TYM rekordzie i tylko tu."""
    return {"type": "user", "timestamp": kiedy, "promptId": prompt_id,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tuid,
                                     "is_error": is_error,
                                     "content": "The user doesn't want to proceed"}]}}


def zapisz(path, *rekordy):
    """Transkrypt z rekordow; `str` przechodzi surowo, zeby dalo sie wstawic zepsuta linie."""
    linie = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
             for r in rekordy]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(linie) + "\n", encoding="utf-8")
    return str(path)


def pozniej(ss):
    return ss._iso(time.time() + 5)


def zamiataj(ss):
    ss.alert_dispatch(CFG, hook("Stop", session_id="inna-sesja"))


def wpis_plan(ss, tp, tuid="toolu_9"):
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id=tuid, transcript_path=tp))


def wpis_permission(ss, tp, ti, **kw):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti,
                                transcript_path=tp, **kw))


def test_transkrypt_gasi_plan_po_odmowie_z_cudzego_zamiatania(ss, tdir):
    """Przypadek ze zgloszenia: plan odrzucony, sesja potem zamilkla. Dzis taki wpis
    nie ma zbieracza, bo `Stop` nie odpala na przerwanej turze."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                wynik("toolu_9", pozniej(ss)))
    wpis_plan(ss, tp)
    assert len(names(ss)) == 1
    zamiataj(ss)
    assert names(ss) == []
    assert ss.wyslane[-1]["entries"] == [], "opustoszenie zbioru MUSI dojsc do panelu"


def test_samo_wywolanie_bez_wyniku_nie_gasi(ss, tdir):
    """Kontrola negatywna na zywym przypadku: czlowiek NADAL czeka."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"))
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_klucz_cytowany_jako_wolny_tekst_nie_gasi(ss, tdir):
    """Dopasowanie po podciagu zgasiloby KAZDA zywa blokade: klucz jest w ogonie zawsze,
    bo siedzi w swoim wlasnym rekordzie `tool_use`."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                {"type": "assistant", "timestamp": "2026-08-07T10:01:00.000Z",
                 "message": {"content": [{"type": "text",
                                          "text": "w logu widze toolu_9, sprawdz to"}]}})
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_brak_pliku_transkryptu_nie_gasi(ss, tdir):
    wpis_plan(ss, str(tdir / "nie-ma-mnie.jsonl"))
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_zepsuta_linia_w_srodku_ogona_nie_gubi_wyniku(ss, tdir):
    """`_safe` na kazdej linii osobno: jedna ucieta linia nie moze zjesc reszty ogona."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                '{"type": "user", "message": {"content": [{"type": "tool_re',
                wynik("toolu_9", pozniej(ss)))
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert names(ss) == []


def test_nieczytelny_transkrypt_jednego_wpisu_nie_blokuje_drugiego(ss, tdir):
    """`_safe` wokol calego odczytu jednego pliku — inaczej jeden zablokowany transkrypt
    zawiesilby wszystkie pozostale blokady na maszynie."""
    kaput = tdir / "katalog-nie-plik.jsonl"
    kaput.mkdir()                                    # open() na katalogu rzuca
    ok = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_b"),
                wynik("toolu_b", pozniej(ss)))
    wpis_plan(ss, str(kaput), tuid="toolu_a")
    wpis_plan(ss, ok, tuid="toolu_b")
    zamiataj(ss)
    assert names(ss) == ["%s__main__toolu_a.json" % SID]


def test_transkrypt_krotszy_niz_ogon_nie_traci_pierwszej_linii(ss, tdir):
    """Pierwsza linia jest niepelna TYLKO wtedy, gdy realnie zaczelismy w srodku pliku."""
    tp = zapisz(tdir / "x.jsonl", wynik("toolu_9", pozniej(ss)))
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert names(ss) == []


def test_wyslany_wpis_nie_niesie_pol_lokalnych(ss, tdir):
    """`transcript_path` niesie nazwe katalogu domowego czlowieka i nie ma odbiorcy
    w `SessionAlert`; `prompt_id` tez nie. Na dysku musza zostac."""
    tp = str(tdir / "x.jsonl")
    wpis_permission(ss, tp, {"command": "ls"})
    wyslany = ss.wyslane[-1]["entries"][0]
    assert "transcript_path" not in wyslany and "prompt_id" not in wyslany
    na_dysku = ss.read_entry(names(ss)[0])
    assert na_dysku["transcript_path"] == tp and na_dysku["prompt_id"] == "p1"


# --- wpisy `permission`: klucz to hash, wiec id trzeba odzyskac przeliczeniem
def test_permission_domyka_sie_przez_przeliczenie_call_key(ss, tdir):
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", pozniej(ss)))
    wpis_permission(ss, tp, ti)
    nazwa = names(ss)[0]
    assert "toolu_x" not in nazwa, \
        "klucz wpisu musi byc hashem, inaczej przypadek przechodzi po regule question/plan"
    zamiataj(ss)
    assert names(ss) == []


def test_to_samo_narzedzie_z_innym_input_nie_gasi(ss, tdir):
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", {"command": "git status"}, "toolu_x"),
                wynik("toolu_x", pozniej(ss)))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1


@pytest.mark.parametrize("is_error", [True, False])
def test_identyczny_retry_bez_wyniku_nie_gasi(ss, tdir, is_error):
    """Zmierzone: 0,24% wywolan ma w oknie 32 KB bajtowo identycznego blizniaka, a scenariusz
    "odmowa, Claude powtarza identyczne wywolanie" wystapil w korpusie 22 razy. Oba maja ten
    sam `call_key`, wiec liczy sie WYLACZNIE ostatni rekord `tool_use`."""
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_1"),
                wynik("toolu_1", pozniej(ss), is_error=is_error),
                uzycie("Bash", ti, "toolu_2"))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1, "zgaszona blokada, ktorej czlowiek jeszcze nie widzial"


def test_kontrola_pozytywna_gdy_mlodszy_retry_tez_rozstrzygniety(ss, tdir):
    """Ten sam stan z bezpiecznikiem niespelnionym — bez tego test wyzej przechodzi takze
    przy mechanizmie wycietym."""
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_1"),
                wynik("toolu_1", pozniej(ss)), uzycie("Bash", ti, "toolu_2"),
                wynik("toolu_2", pozniej(ss)))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert names(ss) == []


def test_wynik_z_innej_tury_nie_gasi(ss, tdir):
    """`prompt_id` obejmuje cala ture czlowieka (zmierzone 5-12 wywolan, 89-199 s)."""
    ti = {"command": "ls"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", pozniej(ss), prompt_id="p2"))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_wynik_starszy_niz_wejscie_w_blokade_nie_gasi(ss, tdir):
    """Domyka retry w TEJ SAMEJ turze, ktorego `promptId` nie lapie. Porownanie po
    sparsowanym czasie: leksykograficznie '...:40.816Z' < '...:40Z', bo '.' < 'Z'."""
    ti = {"command": "ls"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", ss._iso(time.time() - 300)))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_wpis_bez_prompt_id_nie_jest_domykany(ss, tdir):
    """Starsza sonda. Hash z pustym lancuchem nie rozroznia tury, wiec lepiej nie ruszac."""
    ti = {"command": "ls"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", pozniej(ss), prompt_id=None))
    wpis_permission(ss, tp, ti, prompt_id=None)
    zamiataj(ss)
    assert len(names(ss)) == 1


# --- subagent: hook niesie `transcript_path` RODZICA, rekordy leza w osobnym pliku
def test_wpis_subagenta_domyka_sie_z_jego_wlasnego_pliku(ss, tdir):
    rodzic = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"))
    zapisz(tdir / SID / "subagents" / "agent-a1d9fb.jsonl",
           uzycie("ExitPlanMode", {}, "toolu_9"), wynik("toolu_9", pozniej(ss)))
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id="toolu_9", transcript_path=rodzic,
                                agent_id="a1d9fb", agent_type="general-purpose"))
    assert len(names(ss)) == 1
    zamiataj(ss)
    assert names(ss) == [], "plik subagenta nie zostal wyliczony ze `agent_id`"


def test_brak_pliku_subagenta_nie_siega_do_rodzica(ss, tdir):
    """Rodzic ma rozstrzygniecie, ale nie TEGO wywolania — subagent ma swoj plik."""
    rodzic = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                    wynik("toolu_9", pozniej(ss)))
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id="toolu_9", transcript_path=rodzic,
                                agent_id="a1d9fb"))
    zamiataj(ss)
    assert len(names(ss)) == 1


# --------------------------------------------------------------------- zapis i wysylka
def test_o_excl_zachowuje_since(ss):
    """Powtorne wejscie tej samej blokady nie moze przesunac stempla — inaczej
    'czeka 40 min' resetowaloby sie przy kazdym drgnieciu."""
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    since = ss.snapshot()[0]["since"]
    time.sleep(1.05)
    ss.alert_dispatch(CFG, h)
    assert ss.snapshot()[0]["since"] == since


def test_post_tylko_przy_zmianie_zbioru(ss):
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    assert len(ss.wyslane) == 1
    ss.alert_dispatch(CFG, h)                     # ta sama blokada, zbior bez zmian
    assert len(ss.wyslane) == 1
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(ss.wyslane) == 2
    assert ss.wyslane[-1]["entries"] == [], "opustoszenie zbioru MUSI dojsc do panelu"


def test_nieudany_post_nie_zapisuje_znacznika(ss, monkeypatch):
    monkeypatch.setattr(ss, "post", lambda *a, **kw: None)
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    assert not os.path.exists(ss.POSTED), "inaczej zguba nie powtorzylaby sie nigdy"


def test_tryb_lokalny_bez_konfiguracji(ss):
    """Bez `alert_url` skrypt pisze pliki i podnosi toast, nic nie wysyla. To jest
    legalny stan, nie awaria — i awaryjny kanal, gdy serwer lezy."""
    ss.alert_dispatch({}, hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "ls"}))
    assert len(names(ss)) == 1
    assert ss.wyslane == []


def test_polskie_znaki_w_detalu(ss, tmp_path):
    """Bez jawnego utf-8 cp1250 wywala sie na polskiej sciezce — i to jest wyjatek
    w skrypcie, ktory ma prawa nie rzucac."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Edit",
                          tool_input={"file_path": r"C:\Zażółć\gęślą\jaźń.py"}))
    assert "jaźń" in ss.snapshot()[0]["detail"]


def test_detail_jest_przyciety(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "x" * 500}))
    assert len(ss.snapshot()[0]["detail"]) == ss.DETAIL_MAX


# --------------------------------------------------------------------- nazwa projektu
def test_worktree_daje_nazwe_projektu_a_nie_agenta(ss):
    """`basename(cwd)` dalby 'agent-a00ce9ba287d12ab1', a walk-up do `.git` stanalby
    na worktree, bo tam `.git` jest PLIKIEM, nie katalogiem."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"Z:\projects\claude-usage-monitor\.claude\worktrees"
                           r"\agent-a00ce9ba287d12ab1",
                           transcript) == "claude-usage-monitor"


def test_podkatalog_nie_zostaje_nazwa_projektu(ss):
    """Zmierzone: 38 z 73 sesji raportuje wiecej niz jedno `cwd`. Naglowek pokazywalby
    'src' zamiast nazwy projektu."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"Z:\projects\claude-usage-monitor\frontend\src",
                           transcript) == "claude-usage-monitor"


def test_rozjazd_wielkosci_litery_dysku_nie_psuje_dopasowania(ss):
    """~/.claude.json REALNIE ma duplikaty kluczy roznjace sie wylacznie wielkoscia
    litery dysku — ten sam rozjazd dotyczy `cwd`."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"z:\Projects\Claude-Usage-Monitor",
                           transcript) == "Claude-Usage-Monitor"


def test_brak_transkryptu_spada_na_walk_up(ss, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "sub").mkdir()
    assert ss.project_name(str(tmp_path / "repo" / "sub"), None) == "repo"


def test_project_name_nigdy_nie_rzuca(ss):
    for cwd in (None, "", 42, r"C:\\"):
        ss.project_name(cwd, None)


# --------------------------------------------------------------------- odpornosc
def test_smieci_na_wejsciu_nie_rzucaja(ss):
    for h in ({}, {"hook_event_name": "CosNowego"},
              {"hook_event_name": "PermissionRequest"},
              {"hook_event_name": "PostToolUse", "session_id": None},
              {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}):
        ss.alert_dispatch(CFG, h)


def test_uszkodzony_plik_stanu_nie_psuje_snapshotu(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    with open(os.path.join(ss.STATEDIR, "smieci.json"), "w", encoding="utf-8") as f:
        f.write("{to nie jest json")
    assert len(ss.snapshot()) == 1


def test_call_key_jest_stabilny_i_zalezy_od_promptu(ss):
    ti = {"command": "git status"}
    assert ss.call_key("Bash", ti, "p1") == ss.call_key("Bash", ti, "p1")
    assert ss.call_key("Bash", ti, "p1") != ss.call_key("Bash", ti, "p2")
    assert ss.call_key("Bash", ti, "p1") != ss.call_key("Edit", ti, "p1")


def test_zapora_przed_rekurencja(ss, monkeypatch):
    """`claude -p "/usage"` sondy to normalna sesja Claude Code i odpala te same hooki.
    Sam throttle tego nie zatrzyma — kazdy potomek ma wlasny zegar."""
    monkeypatch.setenv(ss.CHILD_ENV, "1")
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(
        hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}))))
    assert ss.main() == 0
    assert names(ss) == []


# --------------------------------------------------------------------- flaga
def test_flaga_wylacza_wszystko(ss):
    ss.alert_dispatch(dict(CFG, session_status=False),
                      hook("PermissionRequest", tool_name="Bash",
                           tool_input={"command": "ls"}))
    assert names(ss) == []
    assert ss.wyslane == []


def test_brak_klucza_znaczy_wlaczone(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(names(ss)) == 1


def test_wylaczenie_gasi_to_co_wisi(ss):
    """Sam `return` by nie wystarczyl: blokada trwajaca w chwili wylaczenia zostalaby
    na panelu do serwerowego TTL, bo nikt juz nie wyslalby korekty."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(names(ss)) == 1 and len(ss.wyslane) == 1
    ss.alert_dispatch(dict(CFG, session_status=False), hook("PostToolUse"))
    assert names(ss) == []
    assert ss.wyslane[-1]["entries"] == [], "panel musi dostac pusty zbior"


def test_wylaczone_przy_pustym_katalogu_nie_gada_do_serwera(ss):
    """Inaczej wylaczona funkcja wysylalaby POST przy KAZDYM zdarzeniu hooka."""
    for _ in range(5):
        ss.alert_dispatch(dict(CFG, session_status=False), hook("PostToolUse"))
    assert ss.wyslane == []


# --------------------------------------------------------------------- scalenie z sonda
def _uruchom(ss, monkeypatch, payload, throttle_swiezy=True):
    """Pelne `main()` sondy z podstawionym stdin."""
    with open(ss.CONFIG, "w", encoding="utf-8") as f:
        json.dump({"alert_url": CFG["alert_url"], "ingest_token": "t",
                   "throttle_sec": 3600}, f)
    if throttle_swiezy:
        with open(ss.THROTTLE_FILE, "w") as f:
            f.write(str(time.time()))
    # `ensure_ascii=False` jest tu NOSNE, nie kosmetyczne: domyslne escapowanie dawaloby
    # payload w czystym ASCII, wiec zaden dekoder nie mialby czego zepsuc i test kodowania
    # przechodzilby zawsze. Claude Code wysyla surowe znaki.
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(payload, ensure_ascii=False)))
    return ss.main()


def test_alert_odpala_sie_PRZED_throttlem(ss, monkeypatch):
    """Regresja na kolejnosci w `main()`. Throttle sondy to 60 s; gdyby alert stal za
    nim, blokada bylaby widoczna dopiero po minucie albo — przy gestych zdarzeniach —
    wcale."""
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "git push --force"})) == 0
    assert len(names(ss)) == 1, "throttle sondy zjadl alert"


def test_sonda_milczy_na_stdout_przy_permission_request(ss, monkeypatch, capsys):
    """`PermissionRequest` jest hookiem DECYZYJNYM: cokolwiek na stdout zmienia
    zachowanie promptu. Kontrakt brzmi 'exit 0 bez JSON-a = oddaj decyzje czlowiekowi'."""
    _uruchom(ss, monkeypatch, hook("PermissionRequest", tool_name="Bash",
                                   tool_input={"command": "ls"}))
    zebrane = capsys.readouterr()
    assert zebrane.out == ""


def test_zapora_przed_rekurencja_ucina_takze_alert(ss, monkeypatch):
    monkeypatch.setenv(ss.CHILD_ENV, "1")
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "ls"})) == 0
    assert names(ss) == []


def test_stdin_hooka_czytany_jako_utf8(ss, monkeypatch):
    """Payload hooka to UTF-8, ale `sys.stdin` w trybie tekstowym dekodowal go
    kodowaniem locale — na ekranie i w toascie wychodzilo 'umieraÄ‡' zamiast
    'umierac' z ogonkami. Asercja idzie przez `snapshot()`, bo on ma jawne utf-8;
    gole `open()` przeczytaloby poprawny plik jako cp1250 i dalo czerwien na dobrym
    kodzie."""
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "echo zażółć gęślą jaźń"})) == 0
    assert "zażółć gęślą jaźń" in ss.snapshot()[0]["detail"]


def test_stdin_z_bajtem_spoza_cp1250_nie_gubi_alertu(ss, monkeypatch):
    """Twardszy tryb tej samej awarii, i to on boli bardziej. cp1250 nie ma
    odpowiednika dla 0x81/0x83/0x88/0x90/0x98, a `sys.stdin` ma
    `errors=surrogateescape` — wiec 'Ł' (C5 81) nie psul sie na ekranie, tylko
    stawal sie samotnym surogatem. Ten wywracal `write_excl` na `.encode("utf-8")`
    ("surrogates not allowed"), a tam stoi `except Exception: pass`: plik wpisu
    powstawal PUSTY. Skutek: toast leci, `read_entry` nie parsuje, `snapshot()`
    pomija, alert nigdy nie dociera na panel — a klucz jest juz zajety, wiec
    ponowienie tez nic nie da."""
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Edit",
                         tool_input={"file_path": r"C:\Łukasz\gęś.py"})) == 0
    assert len(ss.snapshot()) == 1
    assert "Łukasz" in ss.snapshot()[0]["detail"]


class _Stdin:
    """Atrapa musi ZLE dekodowac, inaczej nie ma czego zlapac.

    Prawdziwy `sys.stdin` w procesie hooka dostaje bajty UTF-8 i rozkodowuje je
    kodowaniem locale (na maszynie deweloperskiej cp1250) — `.read()` odtwarza
    wlasnie to, a `.buffer` niesie prawde. Atrapa zwracajaca gotowy `str` byla
    wygodna fikcja: przechodzila tak samo przed poprawka i po niej.

    `surrogateescape`, nie `strict`: zmierzone na procesie potomnym uruchomionym
    tak, jak hooki uruchamia Claude Code — `sys.stdin.errors` to wlasnie
    `surrogateescape`. Bajty bez odpowiednika w cp1250 nie rzucaja wiec od razu,
    tylko zamieniaja sie w samotne surogaty, ktore wywracaja dopiero `.encode()`
    warstwe dalej. Atrapa ze `strict` byla by ostrzejsza od rzeczywistosci i
    kazalaby szukac awarii nie tam, gdzie jest.
    """

    def __init__(self, text):
        self.raw = text.encode("utf-8")
        self.buffer = io.BytesIO(self.raw)

    def read(self):
        return self.raw.decode("cp1250", "surrogateescape")     # jak prawdziwy stdin
