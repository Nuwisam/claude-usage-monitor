"""The panel's log.

The client runs under `pythonw.exe` from a scheduled task, so there is NO console.
Without catching exceptions and without faulthandler, a restart loop firing every
minute would leave behind not a single line — and that is exactly the failure
one wants to see after coming back to the machine.
"""
import faulthandler
import logging
import logging.handlers
import os
import sys

LOGGER_NAME = "panel"
_fault_file = None


def setup(path, level="INFO", console=None):
    """Sets up the file log (rotation 2 MB x 3) and, with a console, the screen too."""
    global _fault_file
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.handlers[:] = []

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    except OSError as e:
        # A missing log must not stop the panel — its job is to show the limits.
        print("nie moge otworzyc logu %s: %s" % (path, e), file=sys.stderr)

    if console is None:
        console = sys.stderr is not None and sys.stderr.isatty()
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        logger.addHandler(stream)

    # A traceback from a hard failure (e.g. inside ctypes) goes to a separate file —
    # faulthandler writes to a descriptor, so it cannot go through logging.
    #
    # The previous handle is closed EXPLICITLY. setup() is called more than once in
    # this process — run.pyw configures the log again after catching AlreadyRunning
    # or ConfigError — and merely assigning to the global left behind an open
    # descriptor that faulthandler still held a reference to.
    if _fault_file is not None:
        try:
            faulthandler.disable()
            _fault_file.close()
        except Exception:
            pass
        _fault_file = None
    fault_path = path + ".fault"
    try:
        # NOT wiped at startup: the whole point of this file is the trace a restart
        # loop leaves, and a truncate would eat precisely those earlier iterations
        # that say what it all started from. Instead, one rotation at 1 MB — the
        # main log has been rotated from the start, this one was bounded by nothing.
        if os.path.getsize(fault_path) > 1024 * 1024:
            os.replace(fault_path, fault_path + ".1")
    except OSError:
        pass
    try:
        _fault_file = open(fault_path, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file)
    except OSError:
        pass

    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("nieobsluzony wyjatek", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook

    def threadhook(args):
        logger.critical("nieobsluzony wyjatek w watku %s",
                        getattr(args.thread, "name", "?"),
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    try:
        import threading
        threading.excepthook = threadhook
    except Exception:
        pass

    return logger


def get():
    return logging.getLogger(LOGGER_NAME)
