"""
govee_poller.py — Background Govee temperature/humidity collector.

Runs as a Windows scheduled task (installed via Settings → Background Poller
in the main app).  Polls Govee every <poll_interval_sec> seconds and writes
readings into the shared incubation.db.

Lock file  (govee_poller.lock, sits next to incubation.db):
  Only one machine should be writing at a time.  The lock file records the
  active machine's hostname and a last-seen timestamp, refreshed every poll
  cycle.  If another machine holds a fresh lock (< LOCK_TIMEOUT_MIN minutes
  old) this process exits immediately so there is no concurrent writing.
"""
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timedelta

# ── Locate the source directory so sibling modules can be imported ────────────
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SRC_DIR)

import incubation_db as db
import govee_client as govee_mod

# ── Configuration ─────────────────────────────────────────────────────────────
LOCK_TIMEOUT_MIN = 3        # lock is considered stale after this many minutes
LOG_MAX_BYTES    = 500_000  # rotate (truncate) log file when it exceeds ~500 KB

_DB_DIR    = os.path.dirname(db.DB_PATH)
_LOCK_FILE = os.path.join(_DB_DIR, "govee_poller.lock")
_LOG_FILE  = os.path.join(_DB_DIR, "govee_poller.log")
_HOSTNAME  = socket.gethostname()


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging():
    if os.path.exists(_LOG_FILE) and os.path.getsize(_LOG_FILE) > LOG_MAX_BYTES:
        open(_LOG_FILE, "w").close()
    logging.basicConfig(
        filename=_LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ── Lock file helpers ─────────────────────────────────────────────────────────

def _read_lock() -> dict:
    try:
        with open(_LOCK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_lock():
    with open(_LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "machine":   _HOSTNAME,
            "pid":       os.getpid(),
            "last_seen": datetime.now().isoformat(),
        }, f)


def _release_lock():
    try:
        if _read_lock().get("machine") == _HOSTNAME:
            os.remove(_LOCK_FILE)
    except Exception:
        pass


def _lock_held_by_other() -> tuple[bool, dict]:
    """Return (is_held, lock_data).  Stale locks (> LOCK_TIMEOUT_MIN) are ignored."""
    lock = _read_lock()
    if not lock or lock.get("machine") == _HOSTNAME:
        return False, lock
    try:
        last = datetime.fromisoformat(lock["last_seen"])
        if datetime.now() - last < timedelta(minutes=LOCK_TIMEOUT_MIN):
            return True, lock
    except Exception:
        pass
    return False, lock


# ── Main polling loop ─────────────────────────────────────────────────────────

def _wait_for_db(log, timeout_min: int = 10, retry_sec: int = 30) -> bool:
    """
    Block until the DB directory is reachable.
    Handles Google Drive not yet mounted when Windows fires the Run key at login.
    """
    db_dir   = os.path.dirname(db.DB_PATH)
    deadline = time.time() + timeout_min * 60
    attempt  = 0
    while time.time() < deadline:
        if os.path.isdir(db_dir):
            return True
        attempt += 1
        log.warning(
            f"DB directory not reachable yet ({db_dir}). "
            f"Attempt {attempt} — retrying in {retry_sec}s "
            f"(timeout {timeout_min} min)."
        )
        time.sleep(retry_sec)
    log.error(f"DB directory never became reachable after {timeout_min} min. Exiting.")
    return False


def main():
    # Log to a local fallback file first so startup errors are captured even
    # before the shared Google Drive folder is accessible.
    _fallback_log = os.path.join(_SRC_DIR, "govee_poller_startup.log")
    logging.basicConfig(
        filename=_fallback_log,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)
    log.info(f"Poller starting — machine={_HOSTNAME}  pid={os.getpid()}")
    log.info(f"Waiting for DB directory: {os.path.dirname(db.DB_PATH)}")

    if not _wait_for_db(log):
        sys.exit(1)

    # Drive is mounted — switch over to the shared log file
    logging.getLogger().handlers.clear()
    _setup_logging()
    log.info(f"Poller starting — machine={_HOSTNAME}  pid={os.getpid()}")
    log.info(f"DB: {db.DB_PATH}")

    held, lock = _lock_held_by_other()
    if held:
        log.warning(
            f"Lock held by '{lock.get('machine')}' "
            f"(last seen {lock.get('last_seen')}). "
            "Another machine is already polling — exiting."
        )
        sys.exit(0)

    _write_lock()
    log.info("Lock acquired.")

    api_key = db.get_setting("govee_api_key")
    if not api_key:
        log.error("No Govee API key found in settings. Exiting.")
        _release_lock()
        sys.exit(1)

    poll_sec = int(db.get_setting("poll_interval_sec", "60"))
    client   = govee_mod.GoveeClient(api_key=api_key, poll_interval_sec=poll_sec)
    log.info(f"Polling every {poll_sec}s")

    # Debug: log raw DB contents once on startup
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT id, name, is_hidden, govee_device_id FROM incubators").fetchall()
            log.info(f"DB check — {len(rows)} total row(s) in incubators table")
            for r in rows:
                log.info(f"  id={r[0]} name={r[1]} is_hidden={r[2]} device={r[3]}")
    except Exception as exc:
        log.error(f"DB check failed: {exc}")

    # Run reading maintenance (downsampling) at startup, then once every 24h.
    # The poller is the single writer, so this respects the one-writer rule.
    _last_maintenance = 0.0
    _MAINTENANCE_INTERVAL = 24 * 60 * 60  # seconds

    def _run_maintenance():
        try:
            result = db.downsample_old_readings(days=120)
            if result["collapsed"]:
                log.info(
                    f"Maintenance: collapsed {result['collapsed']} raw reading(s) "
                    f"older than 120 days into {result['buckets']} 12-hour average(s)."
                )
            else:
                log.info("Maintenance: no readings old enough to downsample.")
        except Exception as exc:
            log.exception(f"Maintenance (downsample) failed: {exc}")

    try:
        while True:
            _write_lock()  # refresh timestamp so other machines see we're alive

            if time.time() - _last_maintenance >= _MAINTENANCE_INTERVAL:
                _run_maintenance()
                _last_maintenance = time.time()

            incubators = db.get_incubators(include_hidden=True)
            log.info(f"Poll cycle — {len(incubators)} incubator(s) (including hidden)")
            for inc in incubators:
                device_id = (inc.get("govee_device_id") or "").strip()
                sku       = (inc.get("govee_sku") or "").strip()
                if not device_id:
                    log.info(f"  {inc['name']}: no Govee device configured — skipping")
                    continue
                log.info(f"  {inc['name']}: polling device={device_id} sku={sku}")
                temp_c, humidity = client.poll_incubator(inc)
                if temp_c is not None and humidity is not None:
                    if temp_c > 50:  # Govee API returns °F — convert to °C for storage
                        temp_c = round((temp_c - 32) * 5 / 9, 2)
                    db.save_reading(inc["id"], temp_c, humidity)
                    log.info(f"  {inc['name']}: {temp_c:.2f}°C  {humidity:.1f}%")
                else:
                    log.warning(f"  {inc['name']}: no reading returned — client error: {client._error}")
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        log.info("Stopped by keyboard interrupt.")
    except Exception as exc:
        log.exception(f"Unexpected error: {exc}")
    finally:
        _release_lock()
        log.info("Lock released. Poller stopped.")


if __name__ == "__main__":
    main()
