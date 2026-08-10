"""Two kinds of gap in the history — pure `_find_gaps` logic, no database.

`client_silent` means "nobody was working", `no_samples` means "the client was running,
but not a single sample arrived for this series" — that is, a FAILURE. The same difference
that in /status separates `inferred_reset` from `unknown`, and on the chart it has to look
different. A chart painting both cases alike lies exactly where this tool must not lie.
"""
from datetime import datetime, timedelta

from app.routers.read import _find_gaps

T0 = datetime(2026, 7, 26, 12, 0, 0)
END = datetime(2026, 7, 26, 20, 0, 0)
PROG = timedelta(minutes=15)


def m(*mins):
    return [T0 + timedelta(minutes=x) for x in mins]


def kinds(gaps):
    return [(g.kind, g.from_, g.to) for g in gaps]


def test_no_batches_means_client_silence():
    # batches every 5 min until 12:20, then nothing; the samples likewise
    ts = m(0, 5, 10, 15, 20)
    gaps = _find_gaps(T0, END, batch_times=ts, sample_times=ts, threshold=PROG)
    assert kinds(gaps) == [("client_silent", T0 + timedelta(minutes=20), END)]


def test_batches_exist_but_no_series_samples_is_a_failure_not_idleness():
    """The heart of this test: the client is alive, the series is silent."""
    batches = m(*range(0, 481, 5))          # batches across the whole range, every 5 min
    samples = m(0, 5, 10)                   # the series' samples end at 12:10
    gaps = _find_gaps(T0, END, batch_times=batches, sample_times=samples, threshold=PROG)

    assert [g.kind for g in gaps] == ["no_samples"]
    assert gaps[0].from_ == T0 + timedelta(minutes=10)
    assert gaps[0].to == END


def test_client_silence_is_not_counted_twice_as_no_samples():
    """When the client is silent there are no samples either — but that is one fact, not
    two. Without the subtraction every silence would be painted twice, in both patterns."""
    ts = m(0, 5, 10)
    gaps = _find_gaps(T0, END, batch_times=ts, sample_times=ts, threshold=PROG)
    assert [g.kind for g in gaps] == ["client_silent"]


def test_both_gap_kinds_in_one_range_in_time_order():
    # 12:00-12:10 data, 12:10-13:00 client silence, then batches with no samples to the end
    batches = m(0, 5, 10) + m(*range(60, 481, 5))
    samples = m(0, 5, 10)
    gaps = _find_gaps(T0, END, batch_times=batches, sample_times=samples, threshold=PROG)

    assert [g.kind for g in gaps] == ["client_silent", "no_samples"]
    assert gaps[0].from_ == T0 + timedelta(minutes=10)
    assert gaps[0].to == T0 + timedelta(minutes=60)
    assert gaps[1].from_ == T0 + timedelta(minutes=60)   # begins where the silence ends
    assert gaps[1].to == END


def test_slivers_shorter_than_the_threshold_are_not_reported():
    """Where two silent periods meet, a stretch of a few minutes with a batch is left. That
    is no series failure, only an ordinary interval — it has no right to a chart pattern."""
    batches = m(0, 40, 45, 90)          # two silences with a short breath, 12:40-12:45
    samples = m(0)
    gaps = _find_gaps(T0, T0 + timedelta(minutes=90),
                      batch_times=batches, sample_times=samples, threshold=PROG)
    assert [g.kind for g in gaps] == ["client_silent", "client_silent"]


def test_an_empty_range_is_one_silence_for_the_whole_span():
    gaps = _find_gaps(T0, END, batch_times=[], sample_times=[], threshold=PROG)
    assert kinds(gaps) == [("client_silent", T0, END)]


def test_dense_work_without_breaks_gives_no_gaps():
    ts = m(*range(0, 481, 5))
    assert _find_gaps(T0, END, batch_times=ts, sample_times=ts, threshold=PROG) == []
