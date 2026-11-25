"""The tray supervisor's entry point (panel/tray.py), started by the guardian.

Run through %LOCALAPPDATA%\\claude-usage-monitor\\panel-tray.pyw, a dozen lines of
redirection that run THIS file from the repo — the same convention as run.pyw.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _note(what):
    """A traceback with no console to print it to, and no log set up yet."""
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        with open(os.path.join(base, "claude-usage-monitor", "panel-tray.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s\n%s\n" % (what, traceback.format_exc()))
    except Exception:
        pass


def main():
    try:
        from panel.tray import main as run
    except Exception:
        # Exit 1, never 0: the guardian reads only keep.EXIT_QUIT (with tray.state
        # agreeing) as a wish to stop, and restarts on everything else.
        _note("CRITICAL panel/tray.py did not import")
        return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
