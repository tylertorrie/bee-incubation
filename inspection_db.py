"""
inspection_db.py — Scheduled incubator inspection records.

Two inspection windows per day:
  Morning : 06:00 – 09:59   (6 AM – 10 AM)
  Evening : 16:00 – 21:59   (4 PM – 10 PM)
  Outside these windows → period stored as 'manual'

Status per window (today):
  'done'    — inspection recorded in this window today
  'missed'  — window has closed with no inspection
  'open'    — window is currently open, inspection pending
  'pending' — window hasn't opened yet today
"""
from datetime import datetime, date
import incubation_db as _db

MORNING_START = 6     # inclusive
MORNING_END   = 10    # exclusive  (09:59 is last valid minute)
EVENING_START = 16    # inclusive  (4 PM)
EVENING_END   = 22    # exclusive  (21:59 is last valid minute)

TEMP_ALERT_THRESHOLD = 5.0   # °C — alert if |thermo - govee| > this


# ── Table init ────────────────────────────────────────────────────────────────

def init_inspection_tables():
    """Create inspections table if it doesn't exist (idempotent)."""
    with _db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS inspections (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id        INTEGER REFERENCES incubators(id) ON DELETE CASCADE,
                timestamp           TEXT    NOT NULL,
                period              TEXT    NOT NULL DEFAULT 'manual',
                thermometer_temp_c  REAL,
                govee_temp_c        REAL,
                temp_diff_c         REAL,
                temp_alert          INTEGER DEFAULT 0,
                heat_pumps_ok       INTEGER DEFAULT 0,
                parasites_emerging  INTEGER DEFAULT 0,
                bees_emerging       INTEGER DEFAULT 0,
                fans_ok             INTEGER DEFAULT 0,
                black_lights_ok     INTEGER DEFAULT 0,
                notes               TEXT    DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_inspections_inc_ts
                ON inspections (incubator_id, timestamp DESC);
        """)


# ── Period helpers ────────────────────────────────────────────────────────────

def get_current_period() -> str:
    """Return 'morning', 'evening', or 'manual' based on current hour."""
    h = datetime.now().hour
    if MORNING_START <= h < MORNING_END:
        return "morning"
    if EVENING_START <= h < EVENING_END:
        return "evening"
    return "manual"


def get_today_inspections(incubator_id: int) -> dict:
    """Return {'morning': row_or_None, 'evening': row_or_None} for today."""
    today = date.today().isoformat()
    result = {"morning": None, "evening": None}
    with _db.get_conn() as conn:
        for period in ("morning", "evening"):
            row = conn.execute("""
                SELECT * FROM inspections
                WHERE incubator_id=? AND period=?
                  AND date(timestamp)=?
                ORDER BY timestamp DESC LIMIT 1
            """, (incubator_id, period, today)).fetchone()
            result[period] = dict(row) if row else None
    return result


def get_inspection_status(incubator_id: int) -> dict:
    """
    Return {'morning': status_str, 'evening': status_str}
    where status is one of: 'done' | 'missed' | 'open' | 'pending'
    """
    h    = datetime.now().hour
    done = get_today_inspections(incubator_id)

    def _status(period, start, end):
        if done.get(period):
            return "done"
        if h >= end:
            return "missed"
        if h >= start:
            return "open"
        return "pending"

    return {
        "morning": _status("morning", MORNING_START, MORNING_END),
        "evening": _status("evening", EVENING_START, EVENING_END),
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

def save_inspection(data: dict) -> int:
    """Insert one inspection row. Returns new row id."""
    now = data.get("timestamp") or datetime.now().isoformat()
    with _db.get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO inspections
              (incubator_id, timestamp, period,
               thermometer_temp_c, govee_temp_c, temp_diff_c, temp_alert,
               heat_pumps_ok, parasites_emerging, bees_emerging,
               fans_ok, black_lights_ok, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["incubator_id"],
            now,
            data.get("period", "manual"),
            data.get("thermometer_temp_c"),
            data.get("govee_temp_c"),
            data.get("temp_diff_c"),
            int(bool(data.get("temp_alert", False))),
            int(bool(data.get("heat_pumps_ok", False))),
            int(bool(data.get("parasites_emerging", False))),
            int(bool(data.get("bees_emerging", False))),
            int(bool(data.get("fans_ok", False))),
            int(bool(data.get("black_lights_ok", False))),
            data.get("notes", ""),
        ))
        return cur.lastrowid


def get_inspections(incubator_id: int = None, limit: int = 2000) -> list:
    """
    Return list of inspection dicts, newest first.
    If incubator_id is None, return all incubators.
    """
    with _db.get_conn() as conn:
        if incubator_id:
            rows = conn.execute("""
                SELECT i.*, inc.name as incubator_name
                FROM inspections i
                LEFT JOIN incubators inc ON i.incubator_id = inc.id
                WHERE i.incubator_id = ?
                ORDER BY i.timestamp DESC LIMIT ?
            """, (incubator_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT i.*, inc.name as incubator_name
                FROM inspections i
                LEFT JOIN incubators inc ON i.incubator_id = inc.id
                ORDER BY i.timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_inspection_by_id(inspection_id: int) -> dict | None:
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inspections WHERE id=?", (inspection_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_inspection(inspection_id: int):
    with _db.get_conn() as conn:
        conn.execute("DELETE FROM inspections WHERE id=?", (inspection_id,))
