"""
voc_db.py  —  Database tables and operations for VOC / Vapona monitoring.

Tables added:
  presets           — chemical profiles with ppm thresholds
  sensor_positions  — front / back PID sensor registry per incubator
  voc_runs          — one incubation cycle with a named chemical
  voc_readings      — timestamped ppm + temp readings from ESP32
  voc_alert_events  — logged alert crossings

Zone logic:
  critical_low  ppm < low_alert          → Red
  low           low_alert ≤ ppm < low_warn → Amber
  ok            low_warn  ≤ ppm ≤ high_warn → Green
  high          high_warn < ppm ≤ high_alert → Amber
  critical_high ppm > high_alert         → Red
"""
import json
from datetime import datetime
from incubation_db import get_conn

# ── Default chemical presets ──────────────────────────────────────────────────

DEFAULT_PRESETS = [
    {
        "chemical_name":  "DDVP (Vapona)",
        "description":    "Organophosphate fumigant — standard leafcutter bee treatment.",
        "low_alert_ppm":  0.20,
        "low_warn_ppm":   0.25,
        "high_warn_ppm":  0.60,
        "high_alert_ppm": 0.70,
        "confirmed":      0,
        "is_builtin":     1,
    },
    {
        "chemical_name":  "Conk (permethrin)",
        "description":    "Pyrethroid fumigant. PID response weaker per unit than DDVP — "
                          "thresholds are placeholders; confirm against your protocol.",
        "low_alert_ppm":  0.05,
        "low_warn_ppm":   0.08,
        "high_warn_ppm":  0.25,
        "high_alert_ppm": 0.35,
        "confirmed":      0,
        "is_builtin":     1,
    },
    {
        "chemical_name":  "Other / Custom",
        "description":    "Generic VOC treatment — set all thresholds manually before use.",
        "low_alert_ppm":  0.00,
        "low_warn_ppm":   0.00,
        "high_warn_ppm":  0.00,
        "high_alert_ppm": 0.00,
        "confirmed":      0,
        "is_builtin":     1,
    },
]

# ── Zone helper ───────────────────────────────────────────────────────────────

def get_zone(ppm, preset: dict) -> tuple:
    """
    Returns (zone_key, label, hex_color) for a ppm reading
    against the given preset thresholds.
    """
    if ppm is None:
        return "no_data", "No Data", "#6B7280"
    la = float(preset.get("low_alert_ppm")  or 0)
    lw = float(preset.get("low_warn_ppm")   or 0)
    hw = float(preset.get("high_warn_ppm")  or 999)
    ha = float(preset.get("high_alert_ppm") or 999)
    if ppm < la:
        return "critical_low",  "Critical Low",  "#EF4444"
    if ppm < lw:
        return "low",           "Low",           "#F59E0B"
    if ppm <= hw:
        return "ok",            "OK",            "#10B981"
    if ppm <= ha:
        return "high",          "High",          "#F59E0B"
    return "critical_high", "Critical High", "#EF4444"


# ── Table initialisation ──────────────────────────────────────────────────────

def init_voc_tables():
    """Create VOC tables (idempotent) and seed default presets."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS presets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                chemical_name   TEXT    NOT NULL UNIQUE,
                description     TEXT    DEFAULT '',
                low_alert_ppm   REAL    DEFAULT 0.20,
                low_warn_ppm    REAL    DEFAULT 0.25,
                high_warn_ppm   REAL    DEFAULT 0.60,
                high_alert_ppm  REAL    DEFAULT 0.70,
                confirmed       INTEGER DEFAULT 0,
                is_builtin      INTEGER DEFAULT 0,
                created_at      TEXT,
                updated_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS sensor_positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id    INTEGER REFERENCES incubators(id) ON DELETE CASCADE,
                position        TEXT    NOT NULL CHECK(position IN ('front','back')),
                sensor_serial   TEXT    DEFAULT ''
            );

            -- App-authoritative device registry. Each physical sensor (Pi)
            -- reports its stable hardware_id; the app owns name/incubator/
            -- position so devices can be renamed/reassigned from the app.
            CREATE TABLE IF NOT EXISTS voc_devices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                hardware_id     TEXT    UNIQUE NOT NULL,
                name            TEXT    DEFAULT '',
                incubator_id    INTEGER REFERENCES incubators(id) ON DELETE SET NULL,
                position        TEXT    DEFAULT 'front',
                last_seen       TEXT,
                created_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS voc_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id    INTEGER REFERENCES incubators(id) ON DELETE CASCADE,
                preset_id       INTEGER REFERENCES presets(id),
                chemical_name   TEXT,
                preset_snapshot TEXT,
                start_time      TEXT,
                end_time        TEXT,
                notes           TEXT    DEFAULT '',
                status          TEXT    DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS voc_readings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id    INTEGER,
                run_id          INTEGER REFERENCES voc_runs(id),
                position        TEXT,
                timestamp       TEXT    NOT NULL,
                voc_ppm         REAL,
                temp_c          REAL
            );

            CREATE INDEX IF NOT EXISTS idx_voc_readings_inc_ts
                ON voc_readings (incubator_id, timestamp DESC);

            CREATE TABLE IF NOT EXISTS voc_alert_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id    INTEGER,
                run_id          INTEGER,
                position        TEXT,
                ppm             REAL,
                zone            TEXT,
                message         TEXT,
                timestamp       TEXT,
                acknowledged    INTEGER DEFAULT 0
            );

            -- Wi-Fi networks the sensors should know about. Every Pi is
            -- provisioned with ALL of these; NetworkManager auto-joins whichever
            -- is in range, so moving a sensor between incubators needs no config.
            CREATE TABLE IF NOT EXISTS wifi_networks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ssid            TEXT    UNIQUE NOT NULL,
                psk             TEXT    DEFAULT '',
                priority        INTEGER DEFAULT 0,
                label           TEXT    DEFAULT '',
                created_at      TEXT
            );
        """)
        for p in DEFAULT_PRESETS:
            conn.execute("""
                INSERT OR IGNORE INTO presets
                  (chemical_name, description, low_alert_ppm, low_warn_ppm,
                   high_warn_ppm, high_alert_ppm, confirmed, is_builtin,
                   created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (p["chemical_name"], p["description"],
                 p["low_alert_ppm"], p["low_warn_ppm"],
                 p["high_warn_ppm"], p["high_alert_ppm"],
                 p["confirmed"], p["is_builtin"], now, now))


# ── Presets ───────────────────────────────────────────────────────────────────

def get_presets(include_archived: bool = False) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM presets ORDER BY is_builtin DESC, chemical_name"
        ).fetchall()]


def get_preset(preset_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
        return dict(row) if row else None


def get_preset_by_name(name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM presets WHERE chemical_name=?", (name,)
        ).fetchone()
        return dict(row) if row else None


def upsert_preset(data: dict) -> int:
    cols = ["chemical_name", "description", "low_alert_ppm", "low_warn_ppm",
            "high_warn_ppm", "high_alert_ppm", "confirmed", "is_builtin"]
    now  = datetime.now().isoformat()
    with get_conn() as conn:
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols) + ", updated_at=?"
            vals = [data.get(c) for c in cols] + [now, data["id"]]
            conn.execute(f"UPDATE presets SET {sets} WHERE id=?", vals)
            return int(data["id"])
        vals = [data.get(c) for c in cols] + [now, now]
        cur  = conn.execute(
            f"INSERT INTO presets ({','.join(cols)}, created_at, updated_at) "
            f"VALUES ({','.join('?'*(len(cols)+2))})", vals)
        return cur.lastrowid


def delete_preset(preset_id: int) -> bool:
    """Delete only if no runs reference it. Returns True on success."""
    with get_conn() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM voc_runs WHERE preset_id=?", (preset_id,)
        ).fetchone()[0]
        if used:
            return False
        conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        return True


def reset_builtin_preset(chemical_name: str) -> bool:
    """Restore default threshold values for a built-in preset."""
    defaults = {p["chemical_name"]: p for p in DEFAULT_PRESETS}
    if chemical_name not in defaults:
        return False
    d = defaults[chemical_name]
    with get_conn() as conn:
        conn.execute("""
            UPDATE presets SET low_alert_ppm=?, low_warn_ppm=?,
            high_warn_ppm=?, high_alert_ppm=?, updated_at=?
            WHERE chemical_name=?""",
            (d["low_alert_ppm"], d["low_warn_ppm"],
             d["high_warn_ppm"], d["high_alert_ppm"],
             datetime.now().isoformat(), chemical_name))
    return True


# ── Sensor positions ──────────────────────────────────────────────────────────

def get_sensor_positions(incubator_id: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sensor_positions WHERE incubator_id=? ORDER BY position",
            (incubator_id,)
        ).fetchall()]


def ensure_sensor_positions(incubator_id: int):
    """Create front/back sensor position rows if they don't exist yet."""
    with get_conn() as conn:
        for pos in ("front", "back"):
            conn.execute("""
                INSERT OR IGNORE INTO sensor_positions (incubator_id, position)
                SELECT ?, ? WHERE NOT EXISTS (
                    SELECT 1 FROM sensor_positions
                    WHERE incubator_id=? AND position=?)""",
                (incubator_id, pos, incubator_id, pos))


# ── Runs ──────────────────────────────────────────────────────────────────────

def get_active_run(incubator_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT r.*, p.chemical_name as preset_name
            FROM voc_runs r
            LEFT JOIN presets p ON r.preset_id = p.id
            WHERE r.incubator_id=? AND r.status='active'
            ORDER BY r.start_time DESC LIMIT 1""",
            (incubator_id,)
        ).fetchone()
        return dict(row) if row else None


def get_runs(incubator_id: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT r.*, p.chemical_name as preset_name
            FROM voc_runs r LEFT JOIN presets p ON r.preset_id=p.id
            WHERE r.incubator_id=? ORDER BY r.start_time DESC""",
            (incubator_id,)
        ).fetchall()]


def start_run(incubator_id: int, preset_id: int, notes: str = "") -> int:
    """End any active run for this incubator, then start a new one."""
    preset = get_preset(preset_id)
    if not preset:
        raise ValueError(f"Preset {preset_id} not found")
    snapshot = json.dumps({
        "low_alert_ppm":  preset["low_alert_ppm"],
        "low_warn_ppm":   preset["low_warn_ppm"],
        "high_warn_ppm":  preset["high_warn_ppm"],
        "high_alert_ppm": preset["high_alert_ppm"],
    })
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            UPDATE voc_runs SET status='ended', end_time=?
            WHERE incubator_id=? AND status='active'""",
            (now, incubator_id))
        cur = conn.execute("""
            INSERT INTO voc_runs
              (incubator_id, preset_id, chemical_name, preset_snapshot,
               start_time, notes, status)
            VALUES (?,?,?,?,?,?,'active')""",
            (incubator_id, preset_id, preset["chemical_name"],
             snapshot, now, notes))
        return cur.lastrowid


def end_run(run_id: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE voc_runs SET status='ended', end_time=?
            WHERE id=?""",
            (datetime.now().isoformat(), run_id))


def run_snapshot(run: dict) -> dict:
    """Return the threshold dict for a run (snapshot if available, else live preset)."""
    if run.get("preset_snapshot"):
        try:
            return json.loads(run["preset_snapshot"])
        except Exception:
            pass
    if run.get("preset_id"):
        p = get_preset(run["preset_id"])
        if p:
            return p
    return {"low_alert_ppm": 0, "low_warn_ppm": 0,
            "high_warn_ppm": 999, "high_alert_ppm": 999}


# ── Readings ──────────────────────────────────────────────────────────────────

def save_reading(incubator_id: int, run_id: int | None,
                 position: str, voc_ppm: float, temp_c: float | None = None,
                 timestamp: str | None = None):
    """Insert one reading. `timestamp` (ISO string) preserves the original
    reading time for buffered/late-synced data; defaults to now()."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO voc_readings
              (incubator_id, run_id, position, timestamp, voc_ppm, temp_c)
            VALUES (?,?,?,?,?,?)""",
            (incubator_id, run_id, position,
             timestamp or datetime.now().isoformat(), voc_ppm, temp_c))


def get_run_readings(run_id: int) -> list:
    """All readings for a run, ordered by time."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM voc_readings WHERE run_id=? ORDER BY timestamp",
            (run_id,)
        ).fetchall()]


def get_latest_readings(incubator_id: int, n: int = 1) -> dict:
    """Return {'front': latest_row_or_None, 'back': latest_row_or_None}."""
    result = {"front": None, "back": None}
    with get_conn() as conn:
        for pos in ("front", "back"):
            row = conn.execute("""
                SELECT * FROM voc_readings
                WHERE incubator_id=? AND position=?
                ORDER BY timestamp DESC LIMIT 1""",
                (incubator_id, pos)
            ).fetchone()
            result[pos] = dict(row) if row else None
    return result


def get_recent_readings(incubator_id: int, hours: int = 24) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM voc_readings
            WHERE incubator_id=?
              AND timestamp >= datetime('now', ?)
            ORDER BY timestamp""",
            (incubator_id, f"-{hours} hours")
        ).fetchall()]


# ── Devices (app-authoritative registry) ──────────────────────────────────────

def get_device_by_hw(hardware_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM voc_devices WHERE hardware_id=?",
                           (hardware_id,)).fetchone()
        return dict(row) if row else None


def get_devices() -> list:
    """All registered devices, joined with incubator name, newest-seen first."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT d.*, i.name AS incubator_name
            FROM voc_devices d
            LEFT JOIN incubators i ON d.incubator_id = i.id
            ORDER BY (d.last_seen IS NULL), d.last_seen DESC, d.id""").fetchall()]


def register_device(hardware_id: str, name: str = "",
                    incubator_id: int | None = None,
                    position: str | None = None) -> int:
    """Insert a device if new (unassigned by default). Returns its id.
    Seeds name/incubator/position only when the row is first created so an
    app-side reassignment is never overwritten by the sensor's payload."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO voc_devices
              (hardware_id, name, incubator_id, position, last_seen, created_at)
            VALUES (?,?,?,?,?,?)""",
            (hardware_id, name or "", incubator_id,
             (position or "front"), now, now))
        return conn.execute("SELECT id FROM voc_devices WHERE hardware_id=?",
                           (hardware_id,)).fetchone()[0]


def touch_device(hardware_id: str):
    with get_conn() as conn:
        conn.execute("UPDATE voc_devices SET last_seen=? WHERE hardware_id=?",
                     (datetime.now().isoformat(), hardware_id))


def update_device(device_id: int, name: str = None,
                  incubator_id: int | None = "__keep__",
                  position: str = None):
    """Edit a device's assignment from the app. Pass only the fields to change."""
    sets, vals = [], []
    if name is not None:
        sets.append("name=?"); vals.append(name)
    if incubator_id != "__keep__":
        sets.append("incubator_id=?"); vals.append(incubator_id)
    if position is not None:
        sets.append("position=?"); vals.append(position)
    if not sets:
        return
    vals.append(device_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE voc_devices SET {', '.join(sets)} WHERE id=?", vals)


def delete_device(device_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM voc_devices WHERE id=?", (device_id,))


# ── Wi-Fi networks (provisioned to every sensor) ──────────────────────────────

def get_wifi_networks() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM wifi_networks ORDER BY priority DESC, ssid"
        ).fetchall()]


def upsert_wifi_network(ssid: str, psk: str = "", priority: int = 0,
                        label: str = "", net_id: int | None = None) -> int:
    """Add or update a network (unique by SSID). Returns its id."""
    ssid = (ssid or "").strip()
    if not ssid:
        raise ValueError("SSID is required")
    now = datetime.now().isoformat()
    with get_conn() as conn:
        if net_id:
            conn.execute(
                "UPDATE wifi_networks SET ssid=?, psk=?, priority=?, label=? "
                "WHERE id=?", (ssid, psk or "", priority, label or "", net_id))
            return net_id
        conn.execute("""
            INSERT INTO wifi_networks (ssid, psk, priority, label, created_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(ssid) DO UPDATE SET
                psk=excluded.psk, priority=excluded.priority,
                label=excluded.label""",
            (ssid, psk or "", priority, label or "", now))
        return conn.execute("SELECT id FROM wifi_networks WHERE ssid=?",
                            (ssid,)).fetchone()[0]


def delete_wifi_network(net_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM wifi_networks WHERE id=?", (net_id,))


def suggest_thresholds(incubator_id: int, hours: int = 48,
                       run_id: int | None = None) -> dict | None:
    """Derive starting threshold values from real captured readings.

    Returns {count, hours, baseline, median, p90, peak, suggested{la,lw,hw,ha}}
    or None if there aren't enough readings. The suggested bands bracket the
    sustained treatment level (the "OK" green zone) and are only a starting
    point — the operator must adjust them to their actual protocol.
    """
    if run_id is not None:
        rows = get_run_readings(run_id)
    else:
        rows = get_recent_readings(incubator_id, hours)
    vals = sorted(r["voc_ppm"] for r in rows
                  if isinstance(r.get("voc_ppm"), (int, float)))
    if len(vals) < 5:
        return None

    def pct(p):
        i = min(len(vals) - 1, max(0, int(round(p / 100 * (len(vals) - 1)))))
        return vals[i]

    baseline = pct(10)
    median   = pct(50)
    p90      = pct(90)
    peak     = vals[-1]

    # Sustained treatment level ≈ median of the upper half (treatment-on samples)
    upper = vals[len(vals) // 2:]
    plateau = upper[len(upper) // 2] if upper else median

    la = round(max(baseline, plateau * 0.40), 3)
    lw = round(plateau * 0.70, 3)
    hw = round(plateau * 1.30, 3)
    ha = round(min(plateau * 1.70, peak * 1.10), 3)
    # Enforce strict ordering with small nudges if the data is very flat
    step = max(0.001, round(plateau * 0.05, 3))
    if lw <= la: lw = round(la + step, 3)
    if hw <= lw: hw = round(lw + step, 3)
    if ha <= hw: ha = round(hw + step, 3)

    return {
        "count": len(vals), "hours": hours,
        "baseline": round(baseline, 3), "median": round(median, 3),
        "p90": round(p90, 3), "peak": round(peak, 3),
        "suggested": {"la": la, "lw": lw, "hw": hw, "ha": ha},
    }


# ── Alert events ──────────────────────────────────────────────────────────────

def log_alert_event(incubator_id: int, run_id: int | None,
                    position: str, ppm: float, zone: str, message: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO voc_alert_events
              (incubator_id, run_id, position, ppm, zone, message, timestamp)
            VALUES (?,?,?,?,?,?,?)""",
            (incubator_id, run_id, position, ppm, zone,
             message, datetime.now().isoformat()))
