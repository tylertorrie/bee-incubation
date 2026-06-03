"""
incubation_db.py  —  SQLite database for leafcutter bee incubation tracking
Tables: incubators, samples, incubation_batches, trays,
        temp_humidity_readings, alerts, settings
"""
import sqlite3
import os
from datetime import datetime, timedelta

_SRC_DIR    = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_SRC_DIR, "incubation_config.json")


def _load_config() -> dict:
    """Read the small JSON config file that stores the user-chosen DB path."""
    try:
        import json
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data: dict):
    """Persist config (db_path etc.) to disk."""
    import json
    cfg = _load_config()
    cfg.update(data)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def _resolve_db_path() -> str:
    """
    Locate the database file.

    Priority order:
      1. incubation_config.json  db_path key  — set from the Settings UI
      2. incubation.db next to the source files — used whenever the DB already
                                                  exists there (protects existing data)
      3. Google Drive folder     — auto-detected for new installs so data syncs
                                   between computers without any extra setup
      4. Fallback: next to source files

    No OneDrive logic — Google Drive is used exclusively for cloud sync.
    """
    # 1. User-configured path (set via Settings ▸ Data Storage)
    cfg = _load_config()
    if cfg.get("db_path"):
        path = cfg["db_path"]
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        return path

    # Canonical local path
    local = os.path.join(_SRC_DIR, "incubation.db")

    # 2. Existing local DB — never silently move data
    if os.path.exists(local):
        return local

    # 3. Google Drive — new installs only
    #    Google Drive for Desktop mirrors to one of these folder names
    home = os.path.expanduser("~")
    for folder_name in ("Google Drive", "My Drive", "Google Drive My Drive",
                        "GoogleDrive", "Google Drive/My Drive"):
        gd = os.path.join(home, folder_name)
        if os.path.isdir(gd):
            data_dir = os.path.join(gd, "BeeIncubation")
            os.makedirs(data_dir, exist_ok=True)
            return os.path.join(data_dir, "incubation.db")

    # 4. Fallback
    return local


DB_PATH = _resolve_db_path()
print(f"[DB] {DB_PATH}")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _safe_add_column(conn, table: str, column: str, definition: str):
    """Add a column to a table only if it doesn't already exist."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass  # Column already exists — that's fine


def init_db():
    """Create all tables and seed default settings."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS incubators (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                capacity        INTEGER DEFAULT 50,
                govee_device_id TEXT    DEFAULT '',
                govee_sku       TEXT    DEFAULT '',
                temp_mode            TEXT    DEFAULT 'incubation',
                temp_alerts_enabled  INTEGER DEFAULT 1,
                humidity_min         REAL    DEFAULT 55.0,
                humidity_max         REAL    DEFAULT 75.0,
                sort_order           INTEGER DEFAULT 0,
                is_hidden            INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS samples (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL,
                source              TEXT    DEFAULT '',
                lot_number          TEXT    DEFAULT '',
                xray_live_pct       REAL,
                xray_parasite_pct   REAL,
                xray_dead_pct       REAL,
                total_volume_gal    REAL,
                total_weight_lbs    REAL,
                notes               TEXT    DEFAULT '',
                import_date         TEXT
            );

            CREATE TABLE IF NOT EXISTS incubation_batches (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id            INTEGER REFERENCES incubators(id) ON DELETE SET NULL,
                sample_id               INTEGER REFERENCES samples(id)    ON DELETE SET NULL,
                name                    TEXT    DEFAULT '',
                start_date              TEXT,
                vapona_in               TEXT,
                vapona_out              TEXT,
                air_out                 TEXT,
                male_10pct_emergence    TEXT,
                earliest_cool           TEXT,
                estimated_release       TEXT,
                latest_release          TEXT,
                status                  TEXT    DEFAULT 'active',
                notes                   TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS trays (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tray_number             TEXT    UNIQUE NOT NULL,
                sample_id               INTEGER REFERENCES samples(id)             ON DELETE SET NULL,
                incubation_batch_id     INTEGER REFERENCES incubation_batches(id)  ON DELETE SET NULL,
                incubator_id            INTEGER REFERENCES incubators(id)          ON DELETE SET NULL,
                weight_lbs              REAL,
                live_count              INTEGER,
                parasite_level_pct      REAL,
                volume_gal              REAL,
                in_date                 TEXT,
                out_date                TEXT,
                status                  TEXT    DEFAULT 'active',
                notes                   TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS temp_humidity_readings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                incubator_id    INTEGER REFERENCES incubators(id) ON DELETE CASCADE,
                timestamp       TEXT    NOT NULL,
                temperature_c   REAL,
                humidity_pct    REAL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type      TEXT    NOT NULL,
                severity        TEXT    DEFAULT 'warning',
                incubator_id    INTEGER,
                tray_id         INTEGER,
                batch_id        INTEGER,
                message         TEXT    NOT NULL,
                triggered_at    TEXT    NOT NULL,
                acknowledged    INTEGER DEFAULT 0,
                acknowledged_at TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );
        """)
        defaults = [
            ("govee_api_key",           ""),
            ("lbs_per_gal",             "2.2"),
            ("target_gals_per_tray",    "2.0"),
            ("qr_server_port",          "5151"),
            ("qr_server_enabled",       "1"),
            ("poll_interval_sec",       "60"),
            ("temp_unit",               "C"),
            ("date_alert_lookahead",    "7"),
        ]
        for key, value in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )

        # Safe migrations — add columns introduced after the initial schema
        _safe_add_column(conn, "incubators", "is_hidden",           "INTEGER DEFAULT 0")
        _safe_add_column(conn, "incubators", "temp_mode",           "TEXT DEFAULT 'incubation'")
        _safe_add_column(conn, "incubators", "temp_alerts_enabled", "INTEGER DEFAULT 1")


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )


# ── Incubators ────────────────────────────────────────────────────────────────

def get_incubators(include_hidden: bool = False) -> list:
    """Return incubators ordered by sort_order then name.
    Hidden incubators are excluded by default; pass include_hidden=True
    to get the full list (used by the management view).
    """
    with get_conn() as conn:
        if include_hidden:
            sql = "SELECT * FROM incubators ORDER BY is_hidden, name"
            return [dict(r) for r in conn.execute(sql).fetchall()]
        return [dict(r) for r in conn.execute(
            "SELECT * FROM incubators WHERE is_hidden=0 ORDER BY name"
        ).fetchall()]


def upsert_incubator(data: dict) -> int:
    cols = ["name", "capacity", "govee_device_id", "govee_sku",
            "temp_mode", "temp_alerts_enabled", "humidity_min", "humidity_max",
            "sort_order", "is_hidden"]
    with get_conn() as conn:
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols)
            vals = [data.get(c) for c in cols] + [data["id"]]
            conn.execute(f"UPDATE incubators SET {sets} WHERE id=?", vals)
            return int(data["id"])
        vals = [data.get(c) for c in cols]
        cur = conn.execute(
            f"INSERT INTO incubators ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def set_incubator_hidden(incubator_id: int, hidden: bool):
    """Show or hide an incubator without deleting it."""
    with get_conn() as conn:
        conn.execute("UPDATE incubators SET is_hidden=? WHERE id=?",
                     (1 if hidden else 0, incubator_id))


def set_incubator_temp_mode(incubator_id: int, mode: str):
    """Switch temp mode for an incubator ('cool_storage'|'incubation'|'holding')."""
    with get_conn() as conn:
        conn.execute("UPDATE incubators SET temp_mode=? WHERE id=?",
                     (mode, incubator_id))


def set_incubator_alerts_enabled(incubator_id: int, enabled: bool):
    """Enable or disable temperature alerts for one incubator."""
    with get_conn() as conn:
        conn.execute("UPDATE incubators SET temp_alerts_enabled=? WHERE id=?",
                     (1 if enabled else 0, incubator_id))


def delete_incubator(incubator_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM incubators WHERE id=?", (incubator_id,))


# ── Samples ───────────────────────────────────────────────────────────────────

def get_samples() -> list:
    with get_conn() as conn:
        return [dict(r) for r in
                conn.execute(
                    "SELECT * FROM samples ORDER BY import_date DESC, name"
                ).fetchall()]


def upsert_sample(data: dict) -> int:
    cols = ["name", "source", "lot_number", "xray_live_pct", "xray_parasite_pct",
            "xray_dead_pct", "total_volume_gal", "total_weight_lbs", "notes", "import_date"]
    with get_conn() as conn:
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols)
            vals = [data.get(c) for c in cols] + [data["id"]]
            conn.execute(f"UPDATE samples SET {sets} WHERE id=?", vals)
            return int(data["id"])
        vals = [data.get(c, "") for c in cols]
        if not vals[-1]:  # import_date
            vals[-1] = datetime.now().date().isoformat()
        cur = conn.execute(
            f"INSERT INTO samples ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def delete_sample(sample_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM samples WHERE id=?", (sample_id,))


# ── Batches ───────────────────────────────────────────────────────────────────

def get_batches(incubator_id: int = None, status: str = None) -> list:
    with get_conn() as conn:
        q = """SELECT b.*,
                      i.name AS incubator_name,
                      s.name AS sample_name
               FROM incubation_batches b
               LEFT JOIN incubators i ON b.incubator_id = i.id
               LEFT JOIN samples    s ON b.sample_id    = s.id
               WHERE 1=1"""
        params = []
        if incubator_id is not None:
            q += " AND b.incubator_id=?"; params.append(incubator_id)
        if status:
            q += " AND b.status=?"; params.append(status)
        q += " ORDER BY b.start_date DESC"
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def upsert_batch(data: dict) -> int:
    cols = ["incubator_id", "sample_id", "name", "start_date",
            "vapona_in", "vapona_out", "air_out", "male_10pct_emergence",
            "earliest_cool", "estimated_release", "latest_release", "status", "notes"]
    with get_conn() as conn:
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols)
            vals = [data.get(c) for c in cols] + [data["id"]]
            conn.execute(f"UPDATE incubation_batches SET {sets} WHERE id=?", vals)
            return int(data["id"])
        vals = [data.get(c) for c in cols]
        cur = conn.execute(
            f"INSERT INTO incubation_batches ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def delete_batch(batch_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM incubation_batches WHERE id=?", (batch_id,))


# ── Trays ─────────────────────────────────────────────────────────────────────

def get_trays(incubator_id: int = None, sample_id: int = None,
              batch_id: int = None, status: str = None) -> list:
    with get_conn() as conn:
        q = """SELECT t.*,
                      s.name AS sample_name,
                      i.name AS incubator_name,
                      b.name AS batch_name
               FROM trays t
               LEFT JOIN samples             s ON t.sample_id           = s.id
               LEFT JOIN incubators          i ON t.incubator_id        = i.id
               LEFT JOIN incubation_batches  b ON t.incubation_batch_id = b.id
               WHERE 1=1"""
        params = []
        if incubator_id is not None:
            q += " AND t.incubator_id=?";        params.append(incubator_id)
        if sample_id is not None:
            q += " AND t.sample_id=?";           params.append(sample_id)
        if batch_id is not None:
            q += " AND t.incubation_batch_id=?"; params.append(batch_id)
        if status:
            q += " AND t.status=?";              params.append(status)
        q += " ORDER BY t.tray_number"
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_tray_by_id(tray_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT t.*, s.name AS sample_name, i.name AS incubator_name
            FROM trays t
            LEFT JOIN samples    s ON t.sample_id    = s.id
            LEFT JOIN incubators i ON t.incubator_id = i.id
            WHERE t.id=?""", (tray_id,)).fetchone()
        return dict(row) if row else None


def upsert_tray(data: dict) -> int:
    cols = ["tray_number", "sample_id", "incubation_batch_id", "incubator_id",
            "weight_lbs", "live_count", "parasite_level_pct", "volume_gal",
            "in_date", "out_date", "status", "notes"]
    with get_conn() as conn:
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols)
            vals = [data.get(c) for c in cols] + [data["id"]]
            conn.execute(f"UPDATE trays SET {sets} WHERE id=?", vals)
            return int(data["id"])
        vals = [data.get(c) for c in cols]
        cur = conn.execute(
            f"INSERT INTO trays ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def delete_tray(tray_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM trays WHERE id=?", (tray_id,))


# ── Temp / Humidity ───────────────────────────────────────────────────────────

def save_reading(incubator_id: int, temp_c: float, humidity: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO temp_humidity_readings
               (incubator_id, timestamp, temperature_c, humidity_pct)
               VALUES (?, ?, ?, ?)""",
            (incubator_id, datetime.now().isoformat(), temp_c, humidity)
        )


def get_latest_reading(incubator_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM temp_humidity_readings
               WHERE incubator_id=? ORDER BY timestamp DESC LIMIT 1""",
            (incubator_id,)
        ).fetchone()
        return dict(row) if row else None


def get_readings_24h(incubator_id: int) -> list:
    """Return all readings for one incubator in the past 24 hours, oldest first."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM temp_humidity_readings
            WHERE incubator_id=? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (incubator_id, cutoff)).fetchall()]


def get_recent_readings(incubator_id: int, limit: int = 48) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT * FROM temp_humidity_readings
               WHERE incubator_id=? ORDER BY timestamp DESC LIMIT ?""",
            (incubator_id, limit)
        ).fetchall()]


def prune_old_readings(days: int = 30):
    """Remove readings older than `days` days to keep DB small."""
    cutoff = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM temp_humidity_readings WHERE timestamp < date(?, ?)",
            (cutoff, f"-{days} days")
        )


# ── Alerts ────────────────────────────────────────────────────────────────────

def add_alert(alert_type: str, message: str, severity: str = "warning",
              incubator_id: int = None, tray_id: int = None, batch_id: int = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO alerts
               (alert_type, severity, incubator_id, tray_id, batch_id, message, triggered_at)
               VALUES (?,?,?,?,?,?,?)""",
            (alert_type, severity, incubator_id, tray_id, batch_id,
             message, datetime.now().isoformat())
        )


def get_active_alerts() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT a.*,
                      i.name AS incubator_name
               FROM alerts a
               LEFT JOIN incubators i ON a.incubator_id = i.id
               WHERE a.acknowledged=0
               ORDER BY a.triggered_at DESC"""
        ).fetchall()]


def acknowledge_alert(alert_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE alerts SET acknowledged=1, acknowledged_at=? WHERE id=?",
            (datetime.now().isoformat(), alert_id)
        )


def get_alerts_24h() -> list:
    """Return all alerts (any status) triggered in the past 24 hours."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT a.*, i.name AS incubator_name
            FROM alerts a
            LEFT JOIN incubators i ON a.incubator_id = i.id
            WHERE a.triggered_at >= ?
            ORDER BY a.triggered_at DESC
        """, (cutoff,)).fetchall()]


def acknowledge_all_alerts():
    with get_conn() as conn:
        conn.execute(
            "UPDATE alerts SET acknowledged=1, acknowledged_at=? WHERE acknowledged=0",
            (datetime.now().isoformat(),)
        )
