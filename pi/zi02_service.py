#!/usr/bin/env python3
"""
zi02_service.py — Winsen ZI02 PID VOC sensor → Bee Incubation app.

Reads the ZI02 over UART, averages readings into a fixed window, buffers them
in a local SQLite database (so nothing is lost during network drops), and syncs
buffered readings to the incubation app's HTTP ingest endpoint over Tailscale.

Runs as a systemd service (see vapsens.service). All configuration comes from
environment variables (loaded by systemd from /etc/vapsens/vapsens.conf).

Sensor frame (9 bytes): FF 86 conc_hi conc_lo 34 03 00 00 checksum
  ppm      = (conc_hi*256 + conc_lo) / 1000
  checksum = (0x100 - sum(bytes 1..7)) & 0xFF
The ZI02 has no temperature output, so temp_c is always sent as null; the app
gets incubator temperature from its Govee sensors separately.
"""
import os
import sys
import time
import json
import signal
import socket
import sqlite3
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

try:
    import serial  # pyserial
except ImportError:
    sys.stderr.write("pyserial not installed — run: pip3 install pyserial\n")
    sys.exit(1)


# ── Configuration (from environment / EnvironmentFile) ────────────────────────

def _env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default

APP_URL        = _env("APP_URL")                       # e.g. http://100.101.102.103:5151
INGEST_TOKEN   = _env("INGEST_TOKEN", "")              # must match app's voc_ingest_token ('' = open)
INCUBATOR_ID   = int(_env("INCUBATOR_ID", "0"))        # which incubator this sensor is in
POSITION       = _env("POSITION", "front")             # 'front' or 'back'
SENSOR_ID      = _env("SENSOR_ID", socket.gethostname())
SERIAL_PORT    = _env("SERIAL_PORT", "/dev/serial0")
BAUD           = int(_env("BAUD", "9600"))
SAMPLE_SECONDS = int(_env("SAMPLE_SECONDS", "30"))     # averaging window per stored reading
SYNC_SECONDS   = int(_env("SYNC_SECONDS", "15"))       # how often to push to the app
BATCH_MAX      = int(_env("BATCH_MAX", "500"))         # max readings per POST
PRUNE_DAYS     = int(_env("PRUNE_DAYS", "14"))         # drop synced rows older than this
BUFFER_DB      = _env("BUFFER_DB", "/var/lib/vapsens/buffer.db")
HTTP_TIMEOUT   = int(_env("HTTP_TIMEOUT", "10"))

_stop = threading.Event()


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')}  {msg}", flush=True)


# ── Local buffer (SQLite) ─────────────────────────────────────────────────────

class Buffer:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                ppm     REAL NOT NULL,
                synced  INTEGER DEFAULT 0
            )""")
        self._db.commit()
        self._lock = threading.Lock()

    def add(self, ts, ppm):
        with self._lock:
            self._db.execute("INSERT INTO readings (ts, ppm, synced) VALUES (?,?,0)",
                             (ts, ppm))
            self._db.commit()

    def unsynced(self, limit):
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, ppm FROM readings WHERE synced=0 ORDER BY id LIMIT ?",
                (limit,)).fetchall()
        return rows

    def mark_synced(self, ids):
        if not ids:
            return
        with self._lock:
            self._db.executemany("UPDATE readings SET synced=1 WHERE id=?",
                                 [(i,) for i in ids])
            self._db.commit()

    def prune(self, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            self._db.execute("DELETE FROM readings WHERE synced=1 AND ts < ?", (cutoff,))
            self._db.commit()

    def pending_count(self):
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM readings WHERE synced=0").fetchone()[0]


# ── Sensor reader ─────────────────────────────────────────────────────────────

def checksum_ok(frame):
    s = sum(frame[1:8]) & 0xFF
    return ((0x100 - s) & 0xFF) == frame[8]


def reader_loop(buf):
    """Read frames, average over SAMPLE_SECONDS, store one row per window."""
    ser = None
    window_start = time.monotonic()
    samples = []

    while not _stop.is_set():
        # (Re)open the serial port, retrying on failure
        if ser is None:
            try:
                ser = serial.Serial(SERIAL_PORT, BAUD, timeout=2)
                log(f"serial open: {SERIAL_PORT} @ {BAUD}")
            except Exception as exc:
                log(f"serial open failed ({exc}); retrying in 5s")
                _stop.wait(5)
                continue

        try:
            raw = ser.read(9)
        except Exception as exc:
            log(f"serial read error ({exc}); reopening")
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            continue

        buf_bytes = bytearray(raw)
        # Frame-align: parse any complete 9-byte frames present
        while len(buf_bytes) >= 9:
            if buf_bytes[0] != 0xFF:
                buf_bytes.pop(0)
                continue
            frame = bytes(buf_bytes[:9])
            if checksum_ok(frame):
                ppm = (frame[2] * 256 + frame[3]) / 1000.0
                samples.append(ppm)
                del buf_bytes[:9]
            else:
                buf_bytes.pop(0)

        # Close the averaging window
        if time.monotonic() - window_start >= SAMPLE_SECONDS:
            if samples:
                avg = round(sum(samples) / len(samples), 4)
                ts  = datetime.now(timezone.utc).isoformat()
                buf.add(ts, avg)
                log(f"stored {avg:.4f} ppm (avg of {len(samples)} samples)")
                samples = []
            window_start = time.monotonic()

    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass


# ── Syncer (buffer → app HTTP) ────────────────────────────────────────────────

def _post_batch(rows):
    """POST a batch of buffered rows. Returns True on success (HTTP 200 ok)."""
    payload = {
        "incubator_id": INCUBATOR_ID,
        "position": POSITION,
        "sensor_id": SENSOR_ID,
        "readings": [{"voc_ppm": ppm, "temp_c": None, "ts": ts}
                     for (_id, ts, ppm) in rows],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{APP_URL}/reading", data=data,
                                 headers={"Content-Type": "application/json"})
    if INGEST_TOKEN:
        req.add_header("X-Ingest-Token", INGEST_TOKEN)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = json.loads(resp.read() or b"{}")
        return resp.status == 200 and body.get("ok", False)


def syncer_loop(buf):
    last_prune = 0.0
    while not _stop.is_set():
        try:
            rows = buf.unsynced(BATCH_MAX)
            if rows:
                if _post_batch(rows):
                    buf.mark_synced([r[0] for r in rows])
                    log(f"synced {len(rows)} reading(s); {buf.pending_count()} pending")
                else:
                    log("app rejected batch; will retry")
            # Prune old synced rows about once an hour
            if time.monotonic() - last_prune > 3600:
                buf.prune(PRUNE_DAYS)
                last_prune = time.monotonic()
        except urllib.error.URLError as exc:
            log(f"app unreachable ({exc.reason}); buffering, will retry")
        except Exception as exc:
            log(f"sync error ({exc}); will retry")
        _stop.wait(SYNC_SECONDS)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not APP_URL:
        sys.exit("APP_URL not set (see /etc/vapsens/vapsens.conf)")
    if not INCUBATOR_ID:
        sys.exit("INCUBATOR_ID not set (which incubator is this sensor in?)")

    log(f"starting: incubator={INCUBATOR_ID} position={POSITION} "
        f"sensor={SENSOR_ID} -> {APP_URL}")
    buf = Buffer(BUFFER_DB)

    def _sig(*_):
        log("stopping…")
        _stop.set()
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    t_read = threading.Thread(target=reader_loop, args=(buf,), daemon=True)
    t_sync = threading.Thread(target=syncer_loop, args=(buf,), daemon=True)
    t_read.start()
    t_sync.start()
    while not _stop.is_set():
        _stop.wait(1)
    t_read.join(timeout=5)
    t_sync.join(timeout=5)
    log("stopped")


if __name__ == "__main__":
    main()
