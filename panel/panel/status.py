"""The `alert` frame -> objects the panel knows how to draw.

A parser of the FRAME PAYLOAD, not of a directory. State files live on the machine
running the session, and that machine may be remote — the panel sees only what
arrived over the stream.

Same rule as in the rest of the client: a broken entry must not kill the panel. Every
function here either returns a sensible value or None; it never raises.
"""

# Card captions. Uppercase, because this is a banner, not a sentence.
REASONS = {
    "permission": "NEEDS PERMISSION",
    "question": "QUESTION FOR YOU",
    "plan": "PLAN TO APPROVE",
}

# The same reason in short form — for the list, where a sentence does not fit.
# The renderer is what uppercases it, because the same word also appears in the
# account band once the card collapses.
SHORT = {
    "permission": "allow",
    "question": "question",
    "plan": "plan",
}

SHORT_UNKNOWN = "waiting"

UNKNOWN = "CLAUDE IS WAITING"


class Blocked:
    """A single blocked session."""

    __slots__ = ("key", "machine", "reason", "project", "tool", "detail",
                 "since", "account_uuid", "session_id", "agent_id", "agent_type",
                 "permission_mode")

    def __init__(self, key, machine=None, reason="permission", project=None,
                 tool=None, detail=None, since=None, account_uuid=None,
                 session_id=None, agent_id=None, agent_type=None,
                 permission_mode=None):
        self.key = key
        self.machine = machine
        self.reason = reason
        self.project = project
        self.tool = tool
        self.detail = detail
        self.since = since                  # datetime with tzinfo, or None
        self.account_uuid = account_uuid
        self.session_id = session_id
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.permission_mode = permission_mode

    @property
    def title(self):
        """Card heading. An unknown `reason` is NOT an error — the writer may be newer
        than the panel, and "CLAUDE IS WAITING" is true in every such case."""
        return REASONS.get(self.reason, UNKNOWN)

    @property
    def short(self):
        """The reason in a single word. Same rule as for `title`: an unknown `reason`
        gets a word that is always true."""
        return SHORT.get(self.reason, SHORT_UNKNOWN)

    @property
    def mode_label(self):
        """Diagnostic strip: the permission mode, plus the subagent type when the block
        happened inside one. Without it, "why is it even asking" has no answer —
        auto-approving modes settle a call BEFORE the prompt layer."""
        bits = [b for b in (self.permission_mode, self.agent_type) if b]
        return " · ".join(bits)

    def __repr__(self):
        return "<Blocked %s %s %s>" % (self.reason, self.project, self.machine)


def parse_entry(raw):
    """One entry from the frame. Returns Blocked or None."""
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
        agent_type=_text(raw.get("agentType")),
        permission_mode=_text(raw.get("permissionMode")),
    )


def parse_frame(payload):
    """The `alert` frame payload -> a list of Blocked, ordered for display.

    Order: YOUNGEST FIRST. The reason has no say in it — every block was already
    shown solo when it arrived (it opened its own takeover window then), so when the
    card is cut down to three rows, the ones worth showing are the ones you have not
    seen yet. Reason rank (plan, question, allow) used to govern the order, and that
    was harmless only while everything on the card was under five minutes old; once
    the window came to belong to the SET, rank pushed out of the rows exactly the
    block that had just taken over the screen.

    An entry without a timestamp lands last — not knowing its age must not pass for
    either freshness or staleness. Ties are settled by `key`, so the order is
    deterministic.
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
    out.sort(key=lambda b: (b.since is None,
                            -b.since.timestamp() if b.since else 0.0,
                            b.key))
    return out


def _text(v):
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None
