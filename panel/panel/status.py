"""Ramka `alert` -> obiekty, ktore panel umie narysowac.

Parser LADUNKU RAMKI, nie katalogu. Pliki stanu leza na maszynie z sesja, a ta bywa
zdalna — panel widzi wylacznie to, co przyszlo strumieniem.

Regula jak w reszcie klienta: zepsuty wpis nie moze zabic panelu. Kazda funkcja tutaj
albo zwraca sensowna wartosc, albo None; nigdy nie rzuca.
"""

# Napisy na karcie. Wersalikami, bo to jest baner, a nie zdanie.
REASONS = {
    "permission": "CZEKA NA ZGODĘ",
    "question": "PYTANIE DO CIEBIE",
    "plan": "PLAN DO ZATWIERDZENIA",
}

# Ktory powod idzie w naglowek karty, gdy blokad jest kilka. Plan przed pytaniem, pytanie
# przed zgoda: im wiekszy kawalek pracy stoi za blokada, tym drozej kosztuje kazda minuta
# czekania. Remis rozstrzyga NAJSTARSZE `since`.
RANK = {"plan": 0, "question": 1, "permission": 2}

UNKNOWN = "CLAUDE CZEKA"


class Blocked:
    """Jedna zablokowana sesja."""

    __slots__ = ("key", "machine", "reason", "project", "tool", "detail",
                 "since", "account_uuid", "session_id", "agent_id")

    def __init__(self, key, machine=None, reason="permission", project=None,
                 tool=None, detail=None, since=None, account_uuid=None,
                 session_id=None, agent_id=None):
        self.key = key
        self.machine = machine
        self.reason = reason
        self.project = project
        self.tool = tool
        self.detail = detail
        self.since = since                  # datetime z tzinfo albo None
        self.account_uuid = account_uuid
        self.session_id = session_id
        self.agent_id = agent_id

    @property
    def title(self):
        """Naglowek karty. Nieznany `reason` NIE jest bledem — writer moze byc nowszy
        niz panel, a "CLAUDE CZEKA" jest prawda w kazdym takim przypadku."""
        return REASONS.get(self.reason, UNKNOWN)

    @property
    def rank(self):
        return RANK.get(self.reason, len(RANK))

    @property
    def label(self):
        """Co widac w wierszu pod nazwa projektu: narzedzie i maszyna."""
        bits = [b for b in (self.tool, self.machine and "maszyna: %s" % self.machine)
                if b]
        return " · ".join(bits)

    def __repr__(self):
        return "<Blocked %s %s %s>" % (self.reason, self.project, self.machine)


def parse_entry(raw):
    """Jeden wpis z ramki. Zwraca Blocked albo None."""
    from . import fmt

    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        return None
    reason = raw.get("reason")
    return Blocked(
        key=key,
        machine=_text(raw.get("machine")),
        reason=reason if isinstance(reason, str) else "permission",
        project=_text(raw.get("project")),
        tool=_text(raw.get("tool")),
        detail=_text(raw.get("detail")),
        since=fmt.parse_utc(raw.get("since")),
        account_uuid=_text(raw.get("accountUuid")),
        session_id=_text(raw.get("sessionId")),
        agent_id=_text(raw.get("agentId")),
    )


def parse_frame(payload):
    """Ladunek ramki `alert` -> lista Blocked, uporzadkowana do wyswietlenia.

    Kolejnosc: najpierw priorytet powodu, potem najstarsze `since`. Wpis bez stempla
    laduje na koncu swojej grupy — brak wiedzy o wieku nie moze udawac swiezosci ani
    starosci.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("alerts")
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for item in raw:
        entry = parse_entry(item)
        if entry is None or entry.key in seen:
            continue
        seen.add(entry.key)
        out.append(entry)
    out.sort(key=lambda b: (b.rank, b.since is None,
                            b.since.timestamp() if b.since else 0.0))
    return out


def _text(v):
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None
