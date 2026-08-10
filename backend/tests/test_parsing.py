"""Normalizer tests on a REAL payload from a Max account (step 0), not an invented one.

fixtures/usage_max.json is the exact /api/oauth/usage response recorded by the probe.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.parsing import (
    Observation, humanize, limit_series_key, meter_withdrawn, parse_pct, parse_ts,
    parse_usage, window_start_index,
)
from tests.team import USAGE_ACTIVE, USAGE_POOL_EXHAUSTED, USAGE_WITHDRAWN, usage

FIX = Path(__file__).parent / "fixtures" / "usage_max.json"
REAL = json.loads(FIX.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------- primitives
@pytest.mark.parametrize("raw,expected", [
    (10.0, 10.0), (29, 29.0), ("73", 73.0), ("73%", 73.0), (" 12.5 % ", 12.5),
    (None, None), (True, None), ("abc", None), ({}, None),
])
def test_parse_pct(raw, expected):
    assert parse_pct(raw) == expected


def test_parse_ts_iso_with_microseconds():
    # exactly the format the endpoint returns; microseconds are TRUNCATED (see test below)
    dt = parse_ts("2026-07-26T19:59:59.822592+00:00")
    assert dt == datetime(2026, 7, 26, 19, 59, 59)
    assert dt.tzinfo is None          # naive UTC is kept


def test_parse_ts_truncates_response_microseconds():
    """Anthropic stamps `resets_at` with the microseconds of ITS OWN RESPONSE, not of the
    window boundary. In one real response `five_hour` ends in `.056340` and `seven_day`
    in `.056361` — 21 us apart, because the fields are computed one after another.

    Without truncation the comparison `last_resets_at == o.resets_at` is practically always
    false, which silently disables dedup, the monotonicity guard and reset boundary
    detection. Measured: 63 samples in 6 h gave 63 different `resets_at`; after truncating, 2.
    """
    a = parse_ts("2026-07-27T00:59:59.056340+00:00")
    b = parse_ts("2026-07-27T00:59:59.981119+00:00")
    assert a == b, "two responses for the same window boundary must give the same moment"
    assert a is not None and a.microsecond == 0


def test_parse_ts_offset_converted_to_utc():
    assert parse_ts("2026-07-26T21:59:59+02:00") == datetime(2026, 7, 26, 19, 59, 59)


def test_parse_ts_epoch_from_statusline():
    # 1785099599 == 2026-07-26T20:59:59Z; the statusline gives epoch, the endpoint ISO
    assert parse_ts(1785099599) == datetime(2026, 7, 26, 20, 59, 59)


def test_parse_ts_epoch_and_iso_give_same_moment():
    iso = parse_ts("2026-07-26T20:59:59+00:00")
    epoch = parse_ts(1785099599)
    assert iso == epoch


def test_same_reset_window_distinguishes_jitter_from_real_reset():
    """Real values observed live: one session window, 49 samples in 3 h."""
    from app.parsing import same_reset_window
    EPS = 300

    a = parse_ts("2026-07-27T00:59:59.014384+00:00")
    b = parse_ts("2026-07-27T01:00:00.982268+00:00")
    assert same_reset_window(a, b, EPS), "2 s of jitter is still the same window"

    # a session reset shifts the boundary by 5 h, a weekly one by 7 days — not to be
    # confused with noise
    assert not same_reset_window(a, parse_ts("2026-07-27T05:59:59+00:00"), EPS)
    assert not same_reset_window(a, parse_ts("2026-08-03T00:59:59+00:00"), EPS)


def test_same_reset_window_treats_no_reset_as_a_state():
    """`spend` has no window boundary. Two absences are the same state, a boundary appearing
    is a change — otherwise a series with no reset would write a new row on every sample."""
    from app.parsing import same_reset_window
    assert same_reset_window(None, None, 300)
    assert not same_reset_window(None, parse_ts("2026-07-27T00:59:59+00:00"), 300)


# --------------------------------------------------------------------- the current window
T0 = datetime(2026, 7, 27, 10, 0, 0)
B1 = datetime(2026, 7, 27, 11, 0, 0)          # session window boundary
B2 = B1 + timedelta(hours=5)                  # boundary after the reset
EPS, MONO = 300, 0.5


def series_run(*items):
    """(minute from T0, utilization, boundary) -> rows of a series' run."""
    return [(T0 + timedelta(minutes=m), u, r) for m, u, r in items]


def test_window_start_boundary_jitter_is_not_a_reset():
    """The same threshold as same_reset_window — otherwise 61 "resets" a day come back."""
    r = series_run((0, 10.0, B1), (5, 12.0, B1 + timedelta(seconds=2)),
                   (10, 12.0, B1 - timedelta(seconds=1)))
    assert window_start_index(r, EPS, MONO) == 0


def test_window_start_catches_drop_and_boundary_jump():
    r = series_run((0, 46.0, B1), (30, 46.0, B1), (65, 0.0, B2), (70, 3.0, B2))
    assert window_start_index(r, EPS, MONO) == 2


def test_window_start_reset_in_progress_then_boundary_return_is_not_a_second_reset():
    """The probe zeroes a stale boundary; a new one returns only with the next cache."""
    r = series_run((0, 46.0, B1), (65, 0.0, None), (70, 2.0, None), (75, 2.0, B2))
    assert window_start_index(r, EPS, MONO) == 1


def test_window_start_catches_reset_when_usage_grew():
    """The previous window ended low and right after the reset there is more — there is
    neither a drop nor a boundary jump, only the sample's date is left."""
    r = series_run((0, 1.0, B1), (70, 4.0, None), (75, 6.0, None))
    assert window_start_index(r, EPS, MONO) == 1


def test_window_start_catches_drop_when_boundary_entirely_unknown():
    r = series_run((0, 8.0, None), (5, 9.0, None), (10, 0.0, None), (15, 2.0, None))
    assert window_start_index(r, EPS, MONO) == 2


def test_window_start_null_utilization_does_not_lose_the_drop():
    r = series_run((0, 46.0, None), (5, None, None), (10, 0.0, None))
    assert window_start_index(r, EPS, MONO) == 2


def test_window_start_returns_last_reset_in_range():
    B3 = B2 + timedelta(hours=5)
    r = series_run((0, 46.0, B1), (65, 2.0, B2), (200, 30.0, B2), (370, 1.0, B3))
    assert window_start_index(r, EPS, MONO) == 3


def test_window_start_empty_and_single_row():
    assert window_start_index([], EPS, MONO) == 0
    assert window_start_index(series_run((0, 5.0, B1)), EPS, MONO) == 0


def test_parse_ts_garbage_does_not_raise():
    for junk in (None, True, "yesterday", "", [], {}):
        assert parse_ts(junk) is None


# ------------------------------------------------------------------------- the real payload
def test_real_payload_has_17_keys():
    r = parse_usage(REAL)
    assert len(r.seen_keys) == 17


def test_detects_buckets_absent_from_the_documentation():
    """Five keys were neither in the validator inside the binary nor in the reference repo.
    The parser must take them without blinking — that is the whole point of an open series
    set."""
    r = parse_usage(REAL)
    for new_key in ("amber_ladder", "iguana_necktie", "nimbus_quill",
                    "tangelo", "omelette_promotional"):
        assert new_key in r.seen_keys


def test_empty_buckets_go_to_null_keys_not_observations():
    r = parse_usage(REAL)
    assert "seven_day_opus" in r.null_keys
    assert not [o for o in r.observations if o.bucket_key == "seven_day_opus"]


def test_five_hour_and_seven_day_have_values():
    r = parse_usage(REAL)
    by = {o.series_key: o for o in r.observations}
    assert by["bucket:five_hour"].utilization == pytest.approx(REAL["five_hour"]["utilization"])
    assert by["bucket:seven_day"].utilization == pytest.approx(REAL["seven_day"]["utilization"])
    assert by["bucket:five_hour"].resets_at is not None


def test_bucket_keeps_dollar_fields_in_extra():
    """limit_dollars/remaining_dollars/used_dollars are null on Max, but must survive all
    the way to the database — they probably fill in with credits or on Team."""
    r = parse_usage(REAL)
    by = {o.series_key: o for o in r.observations}
    assert "limit_dollars" in by["bucket:five_hour"].extra


def test_limits_give_own_series_with_is_active_and_severity():
    r = parse_usage(REAL)
    lim = [o for o in r.observations if o.source == "limit"]
    assert len(lim) == len(REAL["limits"])
    active = [o for o in lim if o.is_active]
    assert len(active) == 1
    # On the test account the binding limit is the weekly one, NOT the session one.
    assert active[0].kind == "weekly_all"
    assert all(o.severity == "normal" for o in lim)


def test_limit_scoped_carries_model_name():
    r = parse_usage(REAL)
    scoped = [o for o in r.observations if o.kind == "weekly_scoped"]
    assert scoped and scoped[0].model_display_name == "Fable"
    assert "fable" in scoped[0].series_key


def test_spend_is_a_first_class_series():
    """On a Team account this IS the binding limit ('org's monthly spend limit'), so it
    cannot be a side field."""
    r = parse_usage(REAL)
    sp = [o for o in r.observations if o.source == "spend"]
    assert len(sp) == 1
    assert sp[0].series_key == "spend:org"
    # amounts stay in minor units with an exponent, with no flattening to float
    assert sp[0].extra["used"] == {"amount_minor": 0, "currency": "USD", "exponent": 2}


def test_extra_usage_is_a_series():
    r = parse_usage(REAL)
    assert any(o.series_key == "extra:usage" for o in r.observations)


def test_real_payload_generates_no_problems():
    assert parse_usage(REAL).problems == []


def test_all_utilization_is_in_0_100_range():
    for o in parse_usage(REAL).observations:
        if o.utilization is not None:
            assert 0.0 <= o.utilization <= 100.0


# ------------------------------------------------------------------------- robustness
def test_unknown_bucket_is_accepted():
    p = dict(REAL)
    p["brand_new_bucket"] = {"utilization": 42.5, "resets_at": "2026-08-01T00:00:00+00:00"}
    r = parse_usage(p)
    by = {o.series_key: o for o in r.observations}
    assert by["bucket:brand_new_bucket"].utilization == 42.5
    assert r.problems == []


def test_corrupted_field_does_not_abort_the_rest():
    """Better to write 15 of 17 series than to reject the whole measurement."""
    p = dict(REAL)
    p["five_hour"] = "suddenly a string"
    r = parse_usage(p)
    assert r.problems                                  # it is reported
    assert any(o.series_key == "bucket:seven_day" for o in r.observations)   # rest got through


def test_limits_not_a_list_does_not_raise():
    p = dict(REAL); p["limits"] = {"not": "a list"}
    r = parse_usage(p)
    assert r.problems and r.observations


def test_empty_and_invalid_payload():
    assert parse_usage({}).observations == []
    assert parse_usage(None).problems


def test_percent_as_int_and_utilization_as_float():
    """limits[].percent is an integer, bucket.utilization a floating-point value —
    both paths must yield a float."""
    r = parse_usage(REAL)
    for o in r.observations:
        assert o.utilization is None or isinstance(o.utilization, float)


def test_series_key_is_stable():
    a = limit_series_key("weekly_scoped", "weekly", "Fable", None)
    b = limit_series_key("weekly_scoped", "weekly", "Fable", None)
    assert a == b == "limit:weekly_scoped|weekly|fable|-"


def test_series_key_does_not_exceed_255():
    k = limit_series_key("x" * 300, "y" * 300, "z" * 300, "w" * 300)
    assert len(k) <= 255


# ------------------------------------------ withdrawn meter (the organization ceiling)
def by_key(result):
    return {o.series_key: o for o in result.observations}


def test_withdrawn_meter_does_not_record_zero_as_a_measurement():
    """The organization ceiling is exhausted: Anthropic zeroes the meter instead of ceasing
    to answer. `percent: 0` and `used: 0.00 EUR` with `enabled: false` are NOT a measurement
    of usage — they are the absence of one. Written down literally, they read as 'you have
    the whole limit' at the very moment of the block."""
    p = usage(USAGE_WITHDRAWN)
    assert p["spend"]["percent"] == 0 and p["spend"]["used"]["amount_minor"] == 0

    o = by_key(parse_usage(p))["spend:org"]
    assert o.utilization is None
    assert o.unavailable_reason == "org_level_disabled_until"
    # Amounts and flags stay untouched — nothing is lost but the phantom zero.
    assert o.extra["used"]["amount_minor"] == 0
    assert o.extra["enabled"] is False

    eu = by_key(parse_usage(p))["extra:usage"]
    assert eu.utilization is None
    assert eu.unavailable_reason == "org_level_disabled_until"
    assert eu.extra["spend_limit_reached"] is True


def test_exhausted_private_pool_is_not_a_withdrawn_meter():
    """The most dangerous defect this change could bring. With YOUR OWN pool exhausted the
    meter works and tells the truth (used 300.04 against a limit of 300.00 EUR => 100%).
    Taking that for a withdrawal would take away the one correct number exactly when it is
    needed most."""
    p = usage(USAGE_POOL_EXHAUSTED)
    assert p["spend"]["enabled"] is True and p["spend"]["disabled_reason"] is None

    assert meter_withdrawn(p["spend"]) is None
    assert meter_withdrawn(p["extra_usage"]) is None

    c = by_key(parse_usage(p))
    assert c["spend:org"].utilization == 100.0
    assert c["spend:org"].unavailable_reason is None
    assert c["extra:usage"].utilization == 100.0


def test_working_credits_keep_percent_and_have_no_reason():
    c = by_key(parse_usage(usage(USAGE_ACTIVE)))
    assert c["spend:org"].utilization == 93.0
    assert c["spend:org"].unavailable_reason is None
    assert c["extra:usage"].utilization == pytest.approx(92.656)


def test_account_without_credits_gets_no_withdrawal_reason():
    """The payload of a Max account that never enabled credits is structurally IDENTICAL to
    a withdrawal payload: `enabled:false`, `cap:null`, `percent:0`, `used:0`. They differ
    ONLY in `disabled_reason` — and only that field has the right to decide."""
    assert REAL["spend"]["enabled"] is False
    assert REAL["spend"].get("disabled_reason") is None

    c = by_key(parse_usage(REAL))
    assert c["spend:org"].unavailable_reason is None
    assert c["spend:org"].utilization == 0.0        # here zero IS a measurement


@pytest.mark.parametrize("block", [
    None, "string", 42, [], {}, {"disabled_reason": None},
    {"disabled_reason": ""}, {"disabled_reason": "   "}, {"disabled_reason": 7},
    {"enabled": False},
])
def test_meter_withdrawn_does_not_raise_or_invent_a_reason(block):
    """`extra` is sometimes None and sometimes not a dict; the parser has no right to raise."""
    assert meter_withdrawn(block) is None


def test_meter_withdrawn_does_not_interpret_the_reason_text():
    """The set of reasons IS open — on one account two different strings were observed
    within a day. Every non-empty string means 'withdrawn', and the string itself travels
    on verbatim (rule 5)."""
    for reason in ("org_level_disabled_until", "org_spend_cap_reached",
                   "something_completely_new_2027"):
        assert meter_withdrawn({"enabled": False, "disabled_reason": reason}) == reason
        assert meter_withdrawn({"is_enabled": False, "disabled_reason": reason}) == reason


def test_reason_with_open_gate_does_not_erase_the_measurement():
    """Should a reason ever arrive with `enabled: true`, the meter still works — and its
    number stands. The verdict rests on the flag, not on the mere presence of the text."""
    assert meter_withdrawn({"enabled": True, "disabled_reason": "whatever"}) is None


def test_spend_label_does_not_call_the_pool_an_organization_limit():
    """`spend:org` describes the pool ALLOCATED TO YOU (300.00 EUR/month), not the ceiling
    of the whole organization — that one does not exist in the contract and has neither an
    amount nor a percentage. The old label called the series something it is not, and it was
    precisely that label which captioned the phantom zero when the company ceiling ran out."""
    o = by_key(parse_usage(usage(USAGE_ACTIVE)))["spend:org"]
    assert o.display_label == "Spend limit (your pool)"
    assert "organization" not in o.display_label
    assert len(o.display_label) <= 200        # the display_label column is String(200)
    assert o.series_key == "spend:org", "the key is identity and must not be touched"
