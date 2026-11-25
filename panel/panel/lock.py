"""Single-instance file locks: `panel.lock`, `tray.lock`, `guard.lock`.

Moved out of app.py when the panel got processes that WATCH it (tray.py, guard.py).
They take the same kind of lock and probe the client's, and importing app.py for that
would pull the renderer, the stream and every driver into a process whose whole job
is to stay small and alive.

The lock is one byte taken with `msvcrt.locking` on a file opened "a+b". Append mode
puts the file position at the end, so on the empty file the lock lives on byte 0 —
the same byte in every process, as long as nobody writes into the file. Nothing does:
the file is a name, not a store.
"""
import os
import time

from . import config as C


class AlreadyRunning(Exception):
    pass


def lock_path(name):
    return os.path.join(C.OUTDIR, "%s.lock" % name)


def single_instance(path=None, name="panel"):
    """A file lock. Returns a handle that has to be held until the process ends.

    The exclusive USB handle already guarantees that only one process draws — but the
    second instance would then spin in a loop of "panel held by another process",
    which reads like a hardware failure rather than like "it is already running".

    `name` goes into the message. The default keeps it "panel already running", the
    exact phrase deploy/panel-common.ps1 (`Wait-PanelLog`) waits for.
    """
    import msvcrt

    path = path or lock_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+b")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise AlreadyRunning("%s already running (lock %s)" % (name, path))
    return handle


def acquire(path=None, name="panel", timeout=20.0, clock=time.monotonic,
            sleep=time.sleep):
    """`single_instance`, retried for up to `timeout` seconds.

    For a process that a watcher has just restarted: the one it replaces was killed a
    moment ago, and a kill is asynchronous — under memory pressure the old process can
    hold its lock for seconds after the kill returned. Failing at once would turn every
    restart into "already running".
    """
    deadline = clock() + timeout
    while True:
        try:
            return single_instance(path, name)
        except AlreadyRunning:
            if clock() >= deadline:
                raise
        sleep(0.5)


def is_free(path=None, name="panel"):
    """Whether nobody holds the lock right now. Takes it and gives it back at once.

    The unlock is EXPLICIT and on the byte that was locked: leaving it to close()
    hands the release to the CRT and the kernel, and a release that lags would make
    the very next start of the client back off on a lock nobody holds any more.
    """
    import msvcrt

    path = path or lock_path(name)
    try:
        handle = single_instance(path, name)
    except AlreadyRunning:
        return False
    except OSError:
        # The directory cannot be created or the file opened: that is not "someone
        # holds it", and reporting it as held would park the watcher in a state that
        # says another instance is running.
        return True
    try:
        try:
            handle.seek(0, os.SEEK_END)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    finally:
        handle.close()
    return True
