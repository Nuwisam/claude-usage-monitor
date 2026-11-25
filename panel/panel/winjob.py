"""Win32 Job Objects: a child process, and everything it starts, tied to its watcher.

The panel's processes run as a chain (keep.py): guardian -> supervisor -> client. Each
watcher starts the process under it through `spawn()`, which puts that process into a
FRESH job with kill-on-close. Two things follow, and both are the point:

  * killing the job kills the whole subtree. The venv's `Scripts\\pythonw.exe` is a
    launcher stub that starts the real interpreter as a child, so "the process" is
    always two processes; a kill by PID took only one of them;
  * when the watcher dies, by any route, the kernel closes its handle to the job and
    the job's processes die with it. No client is ever left drawing, and holding
    panel.lock, with nobody watching it.

The process is created SUSPENDED, put into the job, and only then resumed. Created
running, the stub could start the interpreter before the assignment, and the
interpreter would be outside the job.

A fresh job per start, never a reused one: right after TerminateJobObject the old
processes are still dying, the job is not empty yet, and a process that is already in
a job can only join one that is empty or nested inside its own.

Only documented calls (kernel32), no pywin32: the panel's dependencies are pinned
and few (requirements.txt), and this needs a dozen functions.
"""
import ctypes
import os
import subprocess
from ctypes import wintypes

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102

JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JobObjectBasicAccountingInformation = 1
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9

# The exit code a job kill leaves on its processes. Any value works — the watcher
# knows it killed — but a recognisable one reads better in a log than "1".
KILLED_EXIT = 0xDEAD


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]


_MAX_PIDS = 64


class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
    _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * _MAX_PIDS)]


def _proto(name, restype, *argtypes):
    fn = getattr(_k32, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


_CreateJobObjectW = _proto("CreateJobObjectW", wintypes.HANDLE,
                           ctypes.c_void_p, wintypes.LPCWSTR)
_SetInformationJobObject = _proto("SetInformationJobObject", wintypes.BOOL,
                                  wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                  wintypes.DWORD)
_QueryInformationJobObject = _proto("QueryInformationJobObject", wintypes.BOOL,
                                    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
_AssignProcessToJobObject = _proto("AssignProcessToJobObject", wintypes.BOOL,
                                   wintypes.HANDLE, wintypes.HANDLE)
_TerminateJobObject = _proto("TerminateJobObject", wintypes.BOOL,
                             wintypes.HANDLE, wintypes.UINT)
_CreateProcessW = _proto("CreateProcessW", wintypes.BOOL,
                         wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                         ctypes.c_void_p, wintypes.LPCWSTR,
                         ctypes.POINTER(STARTUPINFOW),
                         ctypes.POINTER(PROCESS_INFORMATION))
_ResumeThread = _proto("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_TerminateProcess = _proto("TerminateProcess", wintypes.BOOL,
                           wintypes.HANDLE, wintypes.UINT)
_WaitForSingleObject = _proto("WaitForSingleObject", wintypes.DWORD,
                              wintypes.HANDLE, wintypes.DWORD)
_GetExitCodeProcess = _proto("GetExitCodeProcess", wintypes.BOOL,
                             wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_CloseHandle = _proto("CloseHandle", wintypes.BOOL, wintypes.HANDLE)


def _error(what):
    code = ctypes.get_last_error()
    return OSError(code, "%s failed: %s" % (what, ctypes.FormatError(code).strip()))


def new_job(breakaway_ok=False):
    """A fresh, unnamed job with kill-on-close. The handle is NOT inheritable (NULL
    security attributes), so no child can keep the job alive after its owner died.

    `breakaway_ok` lets a child created with CREATE_BREAKAWAY_FROM_JOB leave the job
    (`open_detached`); a child created without that flag stays in, as always.
    """
    job = _CreateJobObjectW(None, None)
    if not job:
        raise _error("CreateJobObjectW")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
             | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
    if breakaway_ok:
        flags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK
    info.BasicLimitInformation.LimitFlags = flags
    if not _SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                    ctypes.byref(info), ctypes.sizeof(info)):
        err = _error("SetInformationJobObject")
        _CloseHandle(job)
        raise err
    return job


class SpawnError(OSError):
    """The process could not be started inside its job. `pid` is set when it had been
    created (and then terminated) — for a log line, and for a test to check it died."""

    def __init__(self, *args, pid=None):
        super().__init__(*args)
        self.pid = pid


class Child:
    """One process started by `spawn()`, with the job that holds it and its subtree."""

    def __init__(self, pid, process, job, cmdline):
        self.pid = pid
        self.cmdline = cmdline
        self._process = process
        self._job = job

    # -- state -------------------------------------------------------------

    def poll(self):
        """The exit code, or None while the process runs."""
        return self.wait(0)

    def wait(self, timeout):
        """Waits up to `timeout` seconds; the exit code, or None if still running."""
        if self._process is None:
            raise ValueError("child is closed")
        ms = INFINITE if timeout is None else max(0, int(timeout * 1000))
        r = _WaitForSingleObject(self._process, ms)
        if r == WAIT_TIMEOUT:
            return None
        if r != WAIT_OBJECT_0:
            raise _error("WaitForSingleObject")
        code = wintypes.DWORD()
        if not _GetExitCodeProcess(self._process, ctypes.byref(code)):
            raise _error("GetExitCodeProcess")
        return code.value

    def active(self):
        """How many processes are still in the job: the stub, the interpreter, and
        anything they started. Zero is the only proof the whole subtree is gone."""
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not _QueryInformationJobObject(self._job, JobObjectBasicAccountingInformation,
                                          ctypes.byref(info), ctypes.sizeof(info), None):
            raise _error("QueryInformationJobObject")
        return info.ActiveProcesses

    def pids(self):
        info = JOBOBJECT_BASIC_PROCESS_ID_LIST()
        if not _QueryInformationJobObject(self._job, JobObjectBasicProcessIdList,
                                          ctypes.byref(info), ctypes.sizeof(info), None):
            raise _error("QueryInformationJobObject")
        return [int(info.ProcessIdList[i]) for i in range(info.NumberOfProcessIdsInList)]

    def peak_memory(self):
        """(peak of any one process, peak of the whole job), in bytes of commit."""
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        if not _QueryInformationJobObject(self._job, JobObjectExtendedLimitInformation,
                                          ctypes.byref(info), ctypes.sizeof(info), None):
            raise _error("QueryInformationJobObject")
        return info.PeakProcessMemoryUsed, info.PeakJobMemoryUsed

    # -- ending ------------------------------------------------------------

    def kill(self, timeout=10.0):
        """Kills the whole job and waits until it is empty. True when it is.

        TerminateJobObject starts no process: under memory pressure, which is when
        this runs, CreateProcess for a `taskkill` can itself fail. taskkill stays as
        the fallback for the one case where the job call fails.
        """
        if not _TerminateJobObject(self._job, KILLED_EXIT):
            _TerminateProcess(self._process, KILLED_EXIT)
            try:
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=CREATE_NO_WINDOW, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
        return self.wait_empty(timeout)

    def wait_empty(self, timeout):
        """Waits up to `timeout` s for the stub to exit and the job to run dry."""
        import time
        deadline = time.monotonic() + timeout
        self.wait(timeout)
        while True:
            try:
                if self.active() == 0:
                    return True
            except OSError:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def close(self):
        """Releases the handles. Closing the job kills whatever is still in it."""
        for name in ("_process", "_job"):
            handle = getattr(self, name)
            if handle:
                _CloseHandle(handle)
            setattr(self, name, None)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def spawn(argv, cwd=None, breakaway_ok=False):
    """Starts `argv` suspended, inside a fresh job, then lets it run. Returns a Child.

    Handles are not inherited (bInheritHandles=FALSE): the children are pythonw
    processes with no use for ours, and an inherited job handle would keep the job
    alive past the death of the watcher that owns it.

    On any failure after the process exists, the process is terminated and its
    handles closed: a suspended pythonw left behind would never run and never exit.
    """
    cmdline = subprocess.list2cmdline(argv)
    job = new_job(breakaway_ok=breakaway_ok)
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    buf = ctypes.create_unicode_buffer(cmdline)
    if not _CreateProcessW(None, buf, None, None, False, CREATE_SUSPENDED, None,
                           cwd, ctypes.byref(si), ctypes.byref(pi)):
        err = _error("CreateProcessW")
        _CloseHandle(job)
        raise SpawnError(err.errno, err.strerror)
    try:
        if not _AssignProcessToJobObject(job, pi.hProcess):
            raise _error("AssignProcessToJobObject")
        if _ResumeThread(pi.hThread) == 0xFFFFFFFF:
            raise _error("ResumeThread")
    except OSError as e:
        _TerminateProcess(pi.hProcess, 1)
        _WaitForSingleObject(pi.hProcess, 5000)
        _CloseHandle(pi.hThread)
        _CloseHandle(pi.hProcess)
        _CloseHandle(job)
        raise SpawnError(e.errno, e.strerror, pid=pi.dwProcessId)
    _CloseHandle(pi.hThread)
    return Child(pi.dwProcessId, pi.hProcess, job, cmdline)


def open_detached(argv):
    """Starts a program meant to outlive us — the log viewer the tray menu opens.

    Asked to break away from our job first, so that a supervisor restart does not
    close the window the owner is reading. A job higher up (the scheduled task's
    own) may refuse breakaway; then the program starts inside, and lives as long as
    the job does.
    """
    for flags in (CREATE_BREAKAWAY_FROM_JOB, 0):
        try:
            subprocess.Popen(argv, creationflags=flags, close_fds=True,
                             cwd=os.path.dirname(argv[-1]) or None)
            return True
        except OSError:
            continue
    return False
