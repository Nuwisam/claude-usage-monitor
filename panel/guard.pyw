"""The scheduled task's target: the guardian (panel/guard.py). pythonw.exe, no console.

The task does not point here directly. %LOCALAPPDATA%\\claude-usage-monitor\\
panel-guard.pyw holds a dozen lines of redirection that run THIS file from the repo,
the same convention as run.pyw, so an edit in the repo takes effect at the next start.
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
        with open(os.path.join(base, "claude-usage-monitor", "panel-guard.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s\n%s\n" % (what, traceback.format_exc()))
    except Exception:
        pass


def main():
    try:
        from panel.guard import main as run
    except Exception:
        _note("CRITICAL panel/guard.py did not import")
        return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
