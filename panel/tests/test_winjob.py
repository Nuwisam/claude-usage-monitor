"""winjob on real processes: what the whole watcher chain rests on.

These start real Python processes, because the property under test is the kernel's,
not ours: that killing a job takes the GRANDCHILD too. The venv's pythonw.exe is a
stub that starts the real interpreter as a child, so "the client" is always two
processes, and a kill that took only the stub would leave the interpreter drawing.
"""
import ctypes
import os
import subprocess
import sys
import time

import pytest

winjob = pytest.importorskip("panel.winjob")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = ctypes.c_void_p
_k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
_k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
_k32.CloseHandle.argtypes = (ctypes.c_void_p,)


def alive(pid):
    h = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        return _k32.WaitForSingleObject(h, 0) == 0x102       # WAIT_TIMEOUT: running
    finally:
        _k32.CloseHandle(h)


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def parent_script(tmp_path, python):
    """A process that starts a long-sleeping grandchild and writes its pid down."""
    pidfile = tmp_path / "grandchild.pid"
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "open(%r, 'w').write(str(p.pid))\n"
        "time.sleep(120)\n" % str(pidfile))
    return [python, str(script)], pidfile


def test_exit_code_comes_back():
    child = winjob.spawn([sys.executable, "-c", "raise SystemExit(7)"])
    try:
        assert child.wait(30) == 7
        assert child.wait_empty(10)
    finally:
        child.close()


def test_poll_is_none_while_running_and_kill_empties_the_job():
    child = winjob.spawn([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        assert child.poll() is None
        assert child.pid in child.pids()
        assert child.kill(15)
        assert child.poll() == winjob.KILLED_EXIT
        assert not alive(child.pid)
    finally:
        child.close()


def test_kill_takes_the_grandchild(tmp_path):
    argv, pidfile = parent_script(tmp_path, sys.executable)
    child = winjob.spawn(argv)
    try:
        assert wait_for(pidfile.exists), "the grandchild never started"
        grandchild = int(pidfile.read_text())
        assert alive(grandchild)
        assert grandchild in child.pids()
        assert child.kill(15)
        assert wait_for(lambda: not alive(grandchild), 10), "the grandchild survived"
    finally:
        child.close()


def test_closing_the_job_kills_it_too(tmp_path):
    """The watcher's death in miniature: the kernel closes its handles, nothing else."""
    argv, pidfile = parent_script(tmp_path, sys.executable)
    child = winjob.spawn(argv)
    assert wait_for(pidfile.exists)
    grandchild = int(pidfile.read_text())
    pid = child.pid
    child.close()
    assert wait_for(lambda: not alive(pid) and not alive(grandchild), 10)


def test_through_a_real_venv_stub(tmp_path):
    """The production path: the venv's launcher stub, which starts the interpreter as
    ITS child and juggles a job of its own. The interpreter, and what it starts, must
    still be inside ours."""
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True, timeout=120)
    stub = venv / "Scripts" / "python.exe"
    argv, pidfile = parent_script(tmp_path, str(stub))
    child = winjob.spawn(argv)
    try:
        assert wait_for(pidfile.exists, 30), "the grandchild never started"
        grandchild = int(pidfile.read_text())
        pids = child.pids()
        # stub + interpreter + grandchild
        assert child.pid in pids and grandchild in pids and len(pids) >= 3, pids
        assert child.kill(15)
        assert wait_for(lambda: not any(alive(p) for p in pids), 10)
    finally:
        child.close()


def test_a_failed_assignment_leaves_no_suspended_process(monkeypatch):
    monkeypatch.setattr(winjob, "_AssignProcessToJobObject", lambda job, proc: False)
    with pytest.raises(winjob.SpawnError) as e:
        winjob.spawn([sys.executable, "-c", "import time; time.sleep(120)"])
    assert e.value.pid
    assert wait_for(lambda: not alive(e.value.pid), 10)


def test_a_missing_program_is_a_spawn_error(tmp_path):
    with pytest.raises(winjob.SpawnError):
        winjob.spawn([str(tmp_path / "no-such.exe")])


def test_peak_memory_is_reported():
    child = winjob.spawn([sys.executable, "-c",
                          "b = bytearray(64 * 1024 * 1024); import time; time.sleep(1)"])
    try:
        assert child.wait(30) == 0
        peak_process, peak_job = child.peak_memory()
        assert peak_process >= 64 * 1024 * 1024
        assert peak_job >= peak_process
    finally:
        child.close()
