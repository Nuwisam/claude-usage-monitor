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


def test_typical_frame():
    out = status.parse_frame(frame(entry()))
    assert len(out) == 1
    assert out[0].title == "NEEDS PERMISSION"
    assert out[0].short == "allow"
    assert (out[0].tool, out[0].machine) == ("Bash", "laptop")


def test_corrupt_entry_does_not_kill_the_rest():
    out = status.parse_frame(frame(entry(key="a"), "this is not an object", None,
                                   {"reason": "permission"}, entry(key="b")))
    assert [b.key for b in out] == ["a", "b"], "an entry without a key cannot be dismissed"


def test_unknown_reason_is_not_an_error():
    """The writer may be newer than the panel. 'CLAUDE IS WAITING' is true in every
    such case; an empty screen is not."""
    out = status.parse_frame(frame(entry(reason="something-new")))
    assert out[0].title == status.UNKNOWN


def test_missing_since_does_not_drop_entry():
    out = status.parse_frame(frame(entry(since=None)))
    assert len(out) == 1 and out[0].since is None


def test_since_in_the_future_passes():
    """Machine clocks drift apart. An entry from the future is odd but real — what to
    do with it is decided by `fmt.waited`, which does not go below zero."""
    out = status.parse_frame(frame(entry(since="2099-01-01T00:00:00Z")))
    assert len(out) == 1


def test_garbage_instead_of_frame():
    for junk in (None, [], "text", {"alerts": "not-a-list"}, {}):
        assert status.parse_frame(junk) == []


def test_order_by_youngest_regardless_of_reason():
    """The reason has no bearing on the order — age alone decides.

    The card cuts itself to three rows, and every block was already shown solo when it
    came in. The ones worth showing are therefore the ones not seen yet. The old rank
    (plan, question, permission) pushed out of the rows the very block that had just
    taken the screen over.
    """
    out = status.parse_frame(frame(
        entry(key="perm-old", reason="permission", since="2026-08-05T20:00:00Z"),
        entry(key="question", reason="question", since="2026-08-05T21:00:00Z"),
        entry(key="plan", reason="plan", since="2026-08-05T21:06:00Z"),
        entry(key="perm-new", reason="permission", since="2026-08-05T21:05:00Z"),
    ))
    assert [b.key for b in out] == ["plan", "perm-new", "question", "perm-old"]


def test_entry_without_timestamp_lands_last():
    """Not knowing the age must pass for neither freshness nor staleness."""
    out = status.parse_frame(frame(
        entry(key="missing", reason="plan", since=None),
        entry(key="old", reason="permission", since="2026-08-05T20:00:00Z"),
        entry(key="new", reason="permission", since="2026-08-05T21:05:00Z"),
    ))
    assert [b.key for b in out] == ["new", "old", "missing"]


def test_duplicate_key_counts_once():
    out = status.parse_frame(frame(entry(key="x"), entry(key="x")))
    assert len(out) == 1


def test_missing_machine_is_not_an_error():
    out = status.parse_frame(frame(entry(machine=None)))
    assert out[0].tool == "Bash" and out[0].machine is None


def test_mode_and_subagent_enter_the_strip():
    """`permissionMode` and `agentType` have been in the contract from the start
    (docs/API.md § 3.2) and they are what answers 'why is it asking at all'."""
    out = status.parse_frame(frame(entry(permissionMode="plan",
                                         agentType="general-purpose")))
    assert out[0].mode_label == "plan · general-purpose"


def test_mode_strip_survives_missing_both_fields():
    out = status.parse_frame(frame(entry()))
    assert out[0].mode_label == ""


def test_unknown_reason_has_a_short_form():
    """The same rule as with `title`: an unknown reason gets a word that is always true."""
    out = status.parse_frame(frame(entry(reason="something-new")))
    assert out[0].short == status.SHORT_UNKNOWN
