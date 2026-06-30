"""
incubation_db.py  —  SQLite database for leafcutter bee incubation tracking
Tables: incubators, samples, incubation_batches, trays,
        temp_humidity_readings, alerts, settings
"""
import sqlite3
import os
import re
from datetime import datetime, timedelta

_SRC_DIR    = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_SRC_DIR, "incubation_config.json")

# ── Tray status: stored value -> display label ────────────────────────────────
# "active" is shown as "Incubation". Stored values stay stable so existing
# queries (status="active", etc.) keep working.
TRAY_STATUS_OPTIONS  = [("active", "Incubation"), ("cooled", "Cooled"), ("released", "Released")]
TRAY_STATUS_LABELS   = {"active": "Incubation", "cooled": "Cooled",
                        "released": "Released", "removed": "Removed"}
_TRAY_STATUS_BY_LABEL = {lbl: val for val, lbl in TRAY_STATUS_OPTIONS}


def tray_status_label(value) -> str:
    return TRAY_STATUS_LABELS.get(value, value or "Incubation")


def tray_status_value(label) -> str:
    return _TRAY_STATUS_BY_LABEL.get(label, label)


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
            ("poll_interval_sec",       "900"),   # informational only — polling is hardcoded to 15 min
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
        # Marks readings that have been collapsed into 12-hour averages
        _safe_add_column(conn, "temp_humidity_readings", "is_downsampled", "INTEGER DEFAULT 0")
        conn.execute("UPDATE temp_humidity_readings SET is_downsampled=0 WHERE is_downsampled IS NULL")
        # Key used to suppress repeated/duplicate alerts
        _safe_add_column(conn, "alerts", "dedup_key", "TEXT")
        # Sample fields matching the field spreadsheet (live bees/lb, etc.)
        for _col, _typ in [
            ("total_weight_kg", "REAL"), ("live_bees_per_lb", "REAL"),
            ("live_bees_per_kg", "REAL"), ("parasites", "REAL"),
            ("chalkbrood", "REAL"), ("kg_per_2gal", "REAL"),
            ("lbs_per_2gal", "REAL"), ("total_trays", "REAL"),
            ("incubator_space", "TEXT"),
        ]:
            _safe_add_column(conn, "samples", _col, _typ)

        # Backfill NULLs left by the migration (rows that existed before the column was added)
        conn.execute("UPDATE incubators SET is_hidden=0           WHERE is_hidden IS NULL")
        conn.execute("UPDATE incubators SET temp_mode='incubation' WHERE temp_mode IS NULL")
        conn.execute("UPDATE incubators SET temp_alerts_enabled=1  WHERE temp_alerts_enabled IS NULL")

        # ── Migration: drop UNIQUE constraint on trays.tray_number ──────────────
        # Tray QR codes can be reused across seasons (same physical tray, different
        # sample/incubator). We allow multiple rows per tray_number; the "current"
        # record is the one with status='active'.  SQLite requires table recreation
        # to remove a UNIQUE constraint.
        _has_unique = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='index' AND tbl_name='trays' AND name='sqlite_autoindex_trays_1'"
        ).fetchone()[0]
        if _has_unique:
            conn.executescript("""
                ALTER TABLE trays RENAME TO _trays_old;

                CREATE TABLE trays (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    tray_number             TEXT    NOT NULL,
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

                INSERT INTO trays (id, tray_number, sample_id, incubation_batch_id,
                    incubator_id, weight_lbs, live_count, parasite_level_pct,
                    volume_gal, in_date, out_date, status, notes)
                SELECT id, tray_number, sample_id, incubation_batch_id,
                    incubator_id, weight_lbs, live_count, parasite_level_pct,
                    volume_gal, in_date, out_date, status, notes
                FROM _trays_old;
                DROP TABLE _trays_old;
            """)

        # When a tray entered cooling (for cool-down duration tracking)
        # Added after the UNIQUE-drop rebuild so a fresh DB keeps the column.
        _safe_add_column(conn, "trays", "cool_date", "TEXT")

        # Performance indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trays_incubator_status ON trays(incubator_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trays_status ON trays(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trays_number ON trays(tray_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_incubator_ts ON temp_humidity_readings(incubator_id, timestamp)")


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
    _order = "sort_order, CAST(REPLACE(LOWER(name),'incubator ','') AS INTEGER), name"
    with get_conn() as conn:
        if include_hidden:
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM incubators ORDER BY is_hidden, {_order}"
            ).fetchall()]
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM incubators WHERE is_hidden=0 ORDER BY {_order}"
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
    """Switch temp mode for an incubator ('off'|'cool_storage'|'incubation'|'holding')."""
    with get_conn() as conn:
        conn.execute("UPDATE incubators SET temp_mode=? WHERE id=?",
                     (mode, incubator_id))


def get_mode_goals(mode: str) -> tuple:
    """Return (goal_temp_c, goal_humidity_pct) for a temp mode.

    Uses the per-mode override stored in settings if set, otherwise the built-in
    default from incubation_calc.TEMP_MODES. Returns (None, None) for 'off'.
    """
    import incubation_calc as calc
    def_t, def_h = calc.get_mode_goal_defaults(mode)

    def _resolve(key, fallback):
        raw = get_setting(key, "")
        if raw in ("", None):
            return fallback
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fallback

    return (_resolve(f"goal_temp_{mode}",     def_t),
            _resolve(f"goal_humidity_{mode}", def_h))


def set_mode_goals(mode: str, goal_temp, goal_humidity):
    """Persist per-mode temperature & humidity goals. Blank/None clears the override."""
    set_setting(f"goal_temp_{mode}",
                "" if goal_temp in (None, "") else goal_temp)
    set_setting(f"goal_humidity_{mode}",
                "" if goal_humidity in (None, "") else goal_humidity)


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


def get_sample(sample_id: int) -> dict | None:
    if not sample_id:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
        return dict(row) if row else None


def get_tray_counts_by_sample(statuses=("active", "cooled")) -> dict:
    """Return {sample_id: tray_count}. Counts in-incubator trays by default."""
    q = "SELECT sample_id, COUNT(*) FROM trays WHERE sample_id IS NOT NULL"
    params = []
    if statuses:
        q += " AND status IN (%s)" % ",".join("?" * len(statuses))
        params.extend(statuses)
    q += " GROUP BY sample_id"
    with get_conn() as conn:
        return {r[0]: r[1] for r in conn.execute(q, params).fetchall()}


def upsert_sample(data: dict) -> int:
    cols = ["name", "source", "lot_number", "xray_live_pct", "xray_parasite_pct",
            "xray_dead_pct", "total_volume_gal", "total_weight_lbs",
            "total_weight_kg", "live_bees_per_lb", "live_bees_per_kg",
            "parasites", "chalkbrood", "kg_per_2gal", "lbs_per_2gal",
            "total_trays", "incubator_space", "notes", "import_date"]
    with get_conn() as conn:
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols)
            vals = [data.get(c) for c in cols] + [data["id"]]
            conn.execute(f"UPDATE samples SET {sets} WHERE id=?", vals)
            return int(data["id"])
        vals = [data.get(c) for c in cols]
        if not vals[-1]:  # import_date
            vals[-1] = datetime.now().date().isoformat()
        cur = conn.execute(
            f"INSERT INTO samples ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def upsert_sample_by_name(data: dict) -> int | None:
    """Match an existing sample by name (case-insensitive) and update only the
    provided fields; create a new sample if the name isn't found. Keeps tray
    links intact. Returns the sample id."""
    name = (data.get("name") or "").strip()
    if not name:
        return None
    valid = {"source", "lot_number", "total_volume_gal", "total_weight_lbs",
             "total_weight_kg", "live_bees_per_lb", "live_bees_per_kg",
             "parasites", "chalkbrood", "kg_per_2gal", "lbs_per_2gal",
             "total_trays", "incubator_space", "notes", "import_date"}
    fields = [k for k in data if k in valid]
    with get_conn() as conn:
        # Use TRIM so trailing/leading spaces in existing names don't prevent a match
        row = conn.execute(
            "SELECT id FROM samples WHERE TRIM(name)=? COLLATE NOCASE", (name,)).fetchone()
        if row:
            sid = row["id"]
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                conn.execute(f"UPDATE samples SET {sets} WHERE id=?",
                             [data[k] for k in fields] + [sid])
            return sid
        cols = ["name"] + fields
        vals = [name] + [data[k] for k in fields]
        cur = conn.execute(
            f"INSERT INTO samples ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def merge_duplicate_samples() -> int:
    """Find samples whose names are identical (case-insensitive, trimmed) and
    merge each group into one record.

    The survivor is the record with the most non-NULL detail fields.
    All trays, batches, and incubation_batches that reference a duplicate are
    re-pointed to the survivor, then the duplicates are deleted.
    Returns the number of duplicate records removed.
    """
    detail_cols = [
        "source", "lot_number", "total_volume_gal", "total_weight_lbs",
        "total_weight_kg", "live_bees_per_lb", "live_bees_per_kg",
        "parasites", "chalkbrood", "kg_per_2gal", "lbs_per_2gal",
        "total_trays", "incubator_space", "notes",
    ]
    removed = 0
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM samples ORDER BY id").fetchall()
        # Group by normalised name
        groups: dict[str, list[int]] = {}
        for r in rows:
            key = (r["name"] or "").strip().lower()
            groups.setdefault(key, []).append(r["id"])

        for ids in groups.values():
            if len(ids) < 2:
                continue
            # Choose the survivor: the record with the most populated detail cols
            def _score(sid):
                s = conn.execute("SELECT * FROM samples WHERE id=?", (sid,)).fetchone()
                if not s:
                    return 0
                return sum(1 for c in detail_cols if s[c] is not None)

            ids_sorted = sorted(ids, key=_score, reverse=True)
            survivor = ids_sorted[0]
            duplicates = ids_sorted[1:]

            # Copy any non-NULL fields from duplicates that the survivor is missing
            s_row = dict(conn.execute("SELECT * FROM samples WHERE id=?", (survivor,)).fetchone())
            for dup in duplicates:
                d_row = dict(conn.execute("SELECT * FROM samples WHERE id=?", (dup,)).fetchone())
                updates = {c: d_row[c] for c in detail_cols
                           if s_row.get(c) is None and d_row.get(c) is not None}
                if updates:
                    sets = ", ".join(f"{c}=?" for c in updates)
                    conn.execute(f"UPDATE samples SET {sets} WHERE id=?",
                                 list(updates.values()) + [survivor])
                    s_row.update(updates)

            # Re-point all FK references to the survivor
            for dup in duplicates:
                conn.execute("UPDATE trays SET sample_id=? WHERE sample_id=?", (survivor, dup))
                conn.execute("UPDATE batches SET sample_id=? WHERE sample_id=?", (survivor, dup))
                conn.execute("DELETE FROM samples WHERE id=?", (dup,))
                removed += 1

    return removed


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
                      s.live_bees_per_lb AS sample_live_per_lb,
                      s.live_bees_per_kg AS sample_live_per_kg,
                      s.chalkbrood AS sample_chalkbrood,
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
            if isinstance(status, (list, tuple, set)):
                q += " AND t.status IN (%s)" % ",".join("?" * len(status))
                params.extend(status)
            else:
                q += " AND t.status=?";          params.append(status)
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


def get_tray_by_number(tray_number: str, active_only: bool = False) -> dict | None:
    """Return the tray row for tray_number (case-insensitive exact match).
    Prefers the active row; falls back to the most recently inserted historical row.
    Pass active_only=True to return None when the tray has no active record."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, s.name AS sample_name, i.name AS incubator_name
            FROM trays t
            LEFT JOIN samples    s ON t.sample_id    = s.id
            LEFT JOIN incubators i ON t.incubator_id = i.id
            WHERE t.tray_number = ? COLLATE NOCASE
            ORDER BY CASE WHEN t.status='active' THEN 0 ELSE 1 END, t.id DESC
        """, (tray_number,)).fetchall()
        if not rows:
            return None
        if active_only and rows[0]["status"] != "active":
            return None
        return dict(rows[0])


def get_tray_history(tray_number: str) -> list:
    """Return all rows for a tray_number, newest first — for season history display."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT t.*, s.name AS sample_name, i.name AS incubator_name
            FROM trays t
            LEFT JOIN samples    s ON t.sample_id    = s.id
            LEFT JOIN incubators i ON t.incubator_id = i.id
            WHERE t.tray_number=?
            ORDER BY t.id DESC
        """, (tray_number,)).fetchall()]


def find_trays(query: str, limit: int = 25) -> list:
    """Flexible, case-insensitive tray search for the UI.

    Matches in order of preference:
      - exact tray number (any case): "tray0123" == "Tray0123"
      - contains the typed text:       "0123" -> "Tray0123"
      - contains just the digits:      "123"  -> "Tray0123"
    Returns one row per tray number (active record preferred), best matches first.
    """
    q = (query or "").strip()
    if not q:
        return []
    digits = re.sub(r"\D", "", q)

    conds  = ["t.tray_number = ? COLLATE NOCASE",
              "t.tray_number LIKE ? COLLATE NOCASE"]
    params = [q, f"%{q}%"]
    if digits and digits != q:
        conds.append("t.tray_number LIKE ? COLLATE NOCASE")
        params.append(f"%{digits}%")

    sql = f"""
        SELECT t.*, s.name AS sample_name,
               s.live_bees_per_lb AS sample_live_per_lb,
               s.live_bees_per_kg AS sample_live_per_kg,
               i.name AS incubator_name
        FROM trays t
        LEFT JOIN samples    s ON t.sample_id    = s.id
        LEFT JOIN incubators i ON t.incubator_id = i.id
        WHERE {" OR ".join(conds)}
        ORDER BY (t.tray_number = ? COLLATE NOCASE) DESC,
                 CASE WHEN t.status='active' THEN 0 ELSE 1 END,
                 t.tray_number, t.id DESC
    """
    params.append(q)  # for the ORDER BY exact-match-first

    seen, out = set(), []
    with get_conn() as conn:
        for r in conn.execute(sql, params).fetchall():
            d = dict(r)
            if d["tray_number"] in seen:
                continue            # keep one row per tray number (best first)
            seen.add(d["tray_number"])
            out.append(d)
            if len(out) >= limit:
                break
    return out


def release_tray(tray_number: str, out_date: str = None, notes_append: str = "") -> bool:
    """Mark the CURRENT (active) tray with this number as released (sent to field).

    Only the active record is touched — historical released rows from previous
    seasons are left untouched so the tray's history stays intact.
    Returns True if an active record was found and updated, else False.
    """
    from datetime import date as _date
    tray = get_tray_by_number(tray_number, active_only=True)
    if not tray:
        return False
    new_notes = tray.get("notes") or ""
    if notes_append:
        new_notes = (new_notes + "\n" + notes_append).strip()
    with get_conn() as conn:
        conn.execute(
            "UPDATE trays SET status=?, out_date=?, notes=? WHERE id=?",
            ("released", out_date or _date.today().isoformat(), new_notes, tray["id"]),
        )
    return True


def upsert_tray(data: dict) -> int:
    cols = ["tray_number", "sample_id", "incubation_batch_id", "incubator_id",
            "weight_lbs", "live_count", "parasite_level_pct", "volume_gal",
            "in_date", "out_date", "cool_date", "status", "notes"]
    with get_conn() as conn:
        # Explicit ID — always update that exact row
        if data.get("id"):
            sets = ", ".join(f"{c}=?" for c in cols)
            vals = [data.get(c) for c in cols] + [data["id"]]
            conn.execute(f"UPDATE trays SET {sets} WHERE id=?", vals)
            return int(data["id"])

        tray_num = data.get("tray_number", "")
        if tray_num:
            # Check for an existing active row with this tray number.
            # If found, update it in-place (same tray, same season).
            # If only historical rows exist, fall through to INSERT (new season).
            existing = conn.execute(
                "SELECT id FROM trays WHERE tray_number=? AND status='active'",
                (tray_num,)
            ).fetchone()
            if existing:
                row_id = existing["id"]
                sets = ", ".join(f"{c}=?" for c in cols)
                vals = [data.get(c) for c in cols] + [row_id]
                conn.execute(f"UPDATE trays SET {sets} WHERE id=?", vals)
                return row_id

        # No active row found — insert as a new record (new season reuse or brand-new tray)
        vals = [data.get(c) for c in cols]
        cur = conn.execute(
            f"INSERT INTO trays ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals)
        return cur.lastrowid


def delete_tray(tray_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM trays WHERE id=?", (tray_id,))


def count_active_trays(incubator_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM trays WHERE incubator_id=? AND status='active'",
            (incubator_id,)).fetchone()[0]


def count_cooled_trays(incubator_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM trays WHERE incubator_id=? AND status='cooled'",
            (incubator_id,)).fetchone()[0]


def cool_trays(incubator_id: int, cool_date: str = None) -> int:
    """Move an incubator's active (in-incubation) trays to 'cooled' and stamp
    cool_date. Returns the number moved."""
    from datetime import date as _date
    cd = cool_date or _date.today().isoformat()
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM trays WHERE incubator_id=? AND status='active'",
            (incubator_id,)).fetchone()[0]
        conn.execute(
            "UPDATE trays SET status='cooled', cool_date=COALESCE(cool_date, ?) "
            "WHERE incubator_id=? AND status='active'",
            (cd, incubator_id))
        return n


def uncool_trays(incubator_id: int) -> int:
    """Move an incubator's 'cooled' trays back to 'active' (Incubation) and
    clear cool_date so a later cool-down starts fresh. Returns the number moved."""
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM trays WHERE incubator_id=? AND status='cooled'",
            (incubator_id,)).fetchone()[0]
        conn.execute(
            "UPDATE trays SET status='active', cool_date=NULL "
            "WHERE incubator_id=? AND status='cooled'",
            (incubator_id,))
        return n


def set_trays_status(tray_ids: list, status: str, out_date: str = None,
                     overwrite_out_date: bool = False) -> int:
    """Set status on many trays at once. Returns the number of rows updated.

    For 'released'/'removed' an out_date is stamped:
      - pass out_date to use a specific date (defaults to today if None)
      - overwrite_out_date=False only fills trays that have no out_date yet
      - overwrite_out_date=True sets the date on every selected tray
    """
    if not tray_ids:
        return 0
    from datetime import date as _date
    placeholders = ",".join("?" * len(tray_ids))
    with get_conn() as conn:
        if status in ("released", "removed"):
            stamp = out_date or _date.today().isoformat()
            if overwrite_out_date:
                conn.execute(
                    f"UPDATE trays SET status=?, out_date=? WHERE id IN ({placeholders})",
                    [status, stamp, *tray_ids],
                )
            else:
                conn.execute(
                    f"UPDATE trays SET status=?, out_date=COALESCE(out_date, ?) "
                    f"WHERE id IN ({placeholders})",
                    [status, stamp, *tray_ids],
                )
        else:
            conn.execute(
                f"UPDATE trays SET status=? WHERE id IN ({placeholders})",
                [status, *tray_ids],
            )
        return conn.total_changes


def delete_all_trays() -> int:
    """Delete every tray row. Returns the number of rows removed.
    Destructive — callers must confirm with the user first."""
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM trays").fetchone()[0]
        conn.execute("DELETE FROM trays")
        return n


# Tray statuses that mean the tray is physically still in the incubator
# (count drops only when a tray is released/removed).
IN_INCUBATOR_STATUSES = ("active", "cooled")


def get_tray_stats(incubator_id: int = None, status=None) -> dict:
    """Return {count, total_gals} using SQL aggregates — much faster than fetching all rows.
    `status` may be a single value or a list/tuple of values."""
    q      = "SELECT COUNT(*) AS cnt, COALESCE(SUM(volume_gal), 0) AS gals FROM trays WHERE 1=1"
    params = []
    if incubator_id is not None:
        q += " AND incubator_id=?"; params.append(incubator_id)
    if status:
        if isinstance(status, (list, tuple, set)):
            q += " AND status IN (%s)" % ",".join("?" * len(status))
            params.extend(status)
        else:
            q += " AND status=?";   params.append(status)
    with get_conn() as conn:
        row = conn.execute(q, params).fetchone()
        return {"count": row[0], "total_gals": row[1]}


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
    return get_readings_hours(incubator_id, 24)


def get_readings_hours(incubator_id: int, hours: float) -> list:
    """Return readings for one incubator over the past N hours, oldest first."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
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


def downsample_old_readings(days: int = 120) -> dict:
    """
    Collapse raw readings older than `days` days into 12-hour averages, kept forever.

    Recent data (within `days`) stays at full minute-by-minute resolution so the
    detail charts are unaffected.  Anything older is grouped per incubator into
    two buckets per day (00:00 and 12:00), averaged, and the raw rows replaced by
    a single averaged row per bucket — about a 700x reduction at a 60s poll rate.

    Idempotent: averaged rows are flagged is_downsampled=1 and skipped on later
    runs, so only newly-aged-out data is processed each time.

    Returns {"collapsed": <raw rows removed>, "buckets": <averaged rows written>}.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        # How many raw rows are about to be collapsed (for reporting)
        raw = conn.execute(
            "SELECT COUNT(*) FROM temp_humidity_readings "
            "WHERE is_downsampled=0 AND timestamp < ?",
            (cutoff,)
        ).fetchone()[0]
        if not raw:
            return {"collapsed": 0, "buckets": 0}

        # 1. Insert one averaged row per incubator per 12-hour bucket.
        #    Bucket start = that day's 00:00 (hour < 12) or 12:00 (hour >= 12).
        cur = conn.execute("""
            INSERT INTO temp_humidity_readings
                  (incubator_id, timestamp, temperature_c, humidity_pct, is_downsampled)
            SELECT incubator_id,
                   strftime('%Y-%m-%dT', timestamp)
                     || CASE WHEN CAST(strftime('%H', timestamp) AS INTEGER) < 12
                             THEN '00:00:00' ELSE '12:00:00' END,
                   AVG(temperature_c),
                   AVG(humidity_pct),
                   1
            FROM temp_humidity_readings
            WHERE is_downsampled=0 AND timestamp < ?
            GROUP BY incubator_id,
                     strftime('%Y-%m-%d', timestamp),
                     CASE WHEN CAST(strftime('%H', timestamp) AS INTEGER) < 12
                          THEN 0 ELSE 1 END
        """, (cutoff,))
        buckets = cur.rowcount

        # 2. Delete the raw rows we just averaged (the new rows are is_downsampled=1)
        conn.execute(
            "DELETE FROM temp_humidity_readings "
            "WHERE is_downsampled=0 AND timestamp < ?",
            (cutoff,)
        )
        return {"collapsed": raw, "buckets": buckets}


# ── Alerts ────────────────────────────────────────────────────────────────────

def add_alert(alert_type: str, message: str, severity: str = "warning",
              incubator_id: int = None, tray_id: int = None, batch_id: int = None,
              cooldown_min: int = 60, dedup_key: str = None) -> bool:
    """Insert an alert, suppressing repeats so a persistent problem doesn't spam.

    A new alert is skipped when a matching one is either still unacknowledged, or
    was triggered within the last `cooldown_min` minutes. "Matching" is decided by
    `dedup_key` when supplied (e.g. "temp_humidity:3"), otherwise by the tuple
    (alert_type, incubator_id, batch_id, tray_id, message).

    Returns True if an alert was inserted, False if it was suppressed.
    """
    now    = datetime.now()
    cutoff = (now - timedelta(minutes=cooldown_min)).isoformat()
    with get_conn() as conn:
        if dedup_key is not None:
            existing = conn.execute(
                """SELECT id FROM alerts
                   WHERE dedup_key=? AND (acknowledged=0 OR triggered_at >= ?)
                   LIMIT 1""",
                (dedup_key, cutoff)
            ).fetchone()
        else:
            existing = conn.execute(
                """SELECT id FROM alerts
                   WHERE alert_type=?
                     AND IFNULL(incubator_id,-1)=IFNULL(?,-1)
                     AND IFNULL(batch_id,-1)=IFNULL(?,-1)
                     AND IFNULL(tray_id,-1)=IFNULL(?,-1)
                     AND message=?
                     AND (acknowledged=0 OR triggered_at >= ?)
                   LIMIT 1""",
                (alert_type, incubator_id, batch_id, tray_id, message, cutoff)
            ).fetchone()
        if existing:
            return False
        conn.execute(
            """INSERT INTO alerts
               (alert_type, severity, incubator_id, tray_id, batch_id,
                message, triggered_at, dedup_key)
               VALUES (?,?,?,?,?,?,?,?)""",
            (alert_type, severity, incubator_id, tray_id, batch_id,
             message, now.isoformat(), dedup_key)
        )
        return True


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


def auto_acknowledge_alerts(dedup_keys: list[str]):
    """Auto-resolve active alerts matching any of the given dedup_keys.
    Called when a condition (e.g. temp back in range) has self-cleared."""
    if not dedup_keys:
        return
    placeholders = ",".join("?" * len(dedup_keys))
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            f"UPDATE alerts SET acknowledged=1, acknowledged_at=? "
            f"WHERE acknowledged=0 AND dedup_key IN ({placeholders})",
            [now, *dedup_keys],
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
