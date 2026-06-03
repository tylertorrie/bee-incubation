"""
run_app.py — Auto-reloading launcher for Bee Incubation Manager.

Run this instead of incubation_app.py:
    python run_app.py

Watches all .py files in the same folder. Whenever one is saved,
the app restarts automatically (1-2 second delay to let saves settle).

Close the app window normally to exit — the launcher exits too.
"""

import subprocess
import sys
import time
from pathlib import Path

WATCH_DIR  = Path(__file__).parent
APP_SCRIPT = WATCH_DIR / "incubation_app.py"
PYTHON     = sys.executable
WATCH_EXTS = {".py"}
POLL_SEC   = 1.5   # how often to check for file changes
DEBOUNCE   = 1.5   # seconds to wait after a change before restarting


def _snapshot():
    """Return {filename: mtime} for every .py file in the folder."""
    result = {}
    for p in WATCH_DIR.iterdir():
        if p.suffix in WATCH_EXTS and not p.name.startswith("__"):
            try:
                result[p.name] = p.stat().st_mtime
            except OSError:
                pass
    return result


def _changed(before: dict, after: dict) -> list:
    """Return list of filenames that are new or have a newer mtime."""
    return [
        name for name, mtime in after.items()
        if before.get(name) != mtime
    ]


def main():
    print()
    print("=" * 54)
    print("  🐝  Bee Incubation Manager — Auto-Reload Launcher")
    print("=" * 54)
    print("  Watching .py files for changes and restarting app.")
    print("  Close the app window normally to quit everything.")
    print("=" * 54)
    print()

    while True:
        snapshot = _snapshot()
        print(f"[Launcher] Starting app…")
        proc = subprocess.Popen([PYTHON, str(APP_SCRIPT)])

        restart_requested = False

        while proc.poll() is None:         # while app is still running
            time.sleep(POLL_SEC)
            current  = _snapshot()
            modified = _changed(snapshot, current)

            if modified:
                print(f"[Launcher] Changed: {', '.join(modified)}")
                print(f"[Launcher] Waiting {DEBOUNCE:.0f}s for saves to settle…")
                time.sleep(DEBOUNCE)

                # Re-snapshot after debounce so we don't re-trigger on the same save
                snapshot = _snapshot()

                print("[Launcher] Restarting app…\n")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

                restart_requested = True
                break

        if not restart_requested:
            # App closed normally by the user — exit the launcher too
            print("\n[Launcher] App closed. Goodbye!")
            break


if __name__ == "__main__":
    main()
