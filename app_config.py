"""
app_config.py  —  shared app-level constants.

Version, polling cadence and the Windows no-console subprocess flag, kept in
one place so the app shell, view modules and services can all import them
without importing incubation_app (which would be circular).
"""
import os
import subprocess

# Windows: run helper subprocesses (git, py_compile, poller) hidden so no
# console window flashes up and steals focus while you're working.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── Version ─────────────────────────────────────────────────────────────────
APP_VERSION = "1.48.0"   # bump on every push (semver: MAJOR.MINOR.PATCH)


def _git_revision() -> str:
    """Short git commit hash + date for the running code, or '' if unavailable."""
    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "-C", app_dir, "log", "-1", "--format=%h · %cd", "--date=short"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, creationflags=_NO_WINDOW,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def app_version_string() -> str:
    rev = _git_revision()
    return f"v{APP_VERSION}  ({rev})" if rev else f"v{APP_VERSION}"


# ── Polling ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 15 * 60   # Govee polling is fixed at 15 minutes
