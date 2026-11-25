"""lock.py: the single-instance locks, on real files.

Each lock is taken in a CHILD process where a second holder is needed: msvcrt's byte
locks are per handle, but the property under test — another process cannot take it —
is only honestly shown by another process.
"""
import subprocess
import sys
import time

import pytest

pytest.importorskip("msvcrt")

from panel import lock    # noqa: E402

HOLD = ("import sys, time; sys.path.insert(0, %r); from panel import lock; "
        "h = lock.single_instance(%r); print('held', flush=True); time.sleep(30)")


def hold_in_child(path):
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.Popen([sys.executable, "-c", HOLD % (here, path)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_free_lock_is_free_and_stays_free(tmp_path):
    path = str(tmp_path / "panel.lock")
    assert lock.is_free(path)
    assert lock.is_free(path), "the probe must give the lock back"
    handle = lock.single_instance(path)
    handle.close()


def test_a_lock_held_elsewhere_is_not_free(tmp_path):
    path = str(tmp_path / "panel.lock")
    p = hold_in_child(path)
    try:
        assert not lock.is_free(path)
        with pytest.raises(lock.AlreadyRunning) as e:
            lock.single_instance(path)
        assert "panel already running" in str(e.value), \
            "the phrase deploy/panel-common.ps1 waits for"
    finally:
        p.kill()
        p.wait()
    deadline = time.monotonic() + 5
    while not lock.is_free(path) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert lock.is_free(path)


def test_the_name_goes_into_the_message(tmp_path):
    path = str(tmp_path / "tray.lock")
    p = hold_in_child(path)
    try:
        with pytest.raises(lock.AlreadyRunning) as e:
            lock.single_instance(path, name="tray")
        assert str(e.value).startswith("tray already running")
    finally:
        p.kill()
        p.wait()


def test_acquire_waits_and_then_gives_up(tmp_path):
    path = str(tmp_path / "guard.lock")
    p = hold_in_child(path)
    try:
        t0 = time.monotonic()
        with pytest.raises(lock.AlreadyRunning):
            lock.acquire(path, timeout=1.0)
        assert time.monotonic() - t0 >= 1.0
    finally:
        p.kill()
        p.wait()
    handle = lock.acquire(path, timeout=5.0)
    handle.close()


def test_app_still_exports_the_lock():
    from panel import app
    assert app.AlreadyRunning is lock.AlreadyRunning
    assert app.single_instance is lock.single_instance
