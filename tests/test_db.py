"""Tests for incubation_db helpers against an isolated temp database."""
import os
import sqlite3


def test_settings_roundtrip(temp_db):
    temp_db.set_setting("foo", "bar")
    assert temp_db.get_setting("foo") == "bar"
    assert temp_db.get_setting("missing", "default") == "default"


def test_mode_goals_default_override_and_clear(temp_db):
    # Defaults come from calc.TEMP_MODES
    assert temp_db.get_mode_goals("incubation") == (30.0, 65.0)
    # Override persists
    temp_db.set_mode_goals("incubation", 31.5, 70)
    assert temp_db.get_mode_goals("incubation") == (31.5, 70.0)
    # Blank clears back to the default
    temp_db.set_mode_goals("incubation", "", "")
    assert temp_db.get_mode_goals("incubation") == (30.0, 65.0)
    # Off mode has no goals
    assert temp_db.get_mode_goals("off") == (None, None)


def test_add_alert_dedup(temp_db):
    assert temp_db.add_alert("test", "message", dedup_key="k1") is True
    # Same dedup key while still unacknowledged -> suppressed
    assert temp_db.add_alert("test", "message", dedup_key="k1") is False


def test_incubator_crud_and_temp_mode(temp_db):
    iid = temp_db.upsert_incubator(
        {"name": "Inc 1", "capacity": 50, "temp_mode": "incubation"})
    incs = temp_db.get_incubators(include_hidden=True)
    assert any(i["id"] == iid and i["name"] == "Inc 1" for i in incs)

    temp_db.set_incubator_temp_mode(iid, "off")
    got = next(i for i in temp_db.get_incubators(include_hidden=True) if i["id"] == iid)
    assert got["temp_mode"] == "off"


def test_daily_backup_is_valid_and_idempotent(temp_db):
    path = temp_db.make_daily_backup(keep_days=30)
    assert path and os.path.exists(path)
    c = sqlite3.connect(path)
    try:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        c.close()
    # Second call the same day is a no-op returning the same file
    assert temp_db.make_daily_backup(keep_days=30) == path
    assert len(temp_db.list_backups()) == 1


def test_find_drive_conflicts(temp_db, tmp_path):
    # Clean folder -> nothing flagged
    assert temp_db.find_drive_conflicts() == []
    # A Google Drive conflict copy next to the live DB is detected
    (tmp_path / "incubation (1).db").write_bytes(b"not a real db")
    hits = temp_db.find_drive_conflicts()
    assert len(hits) == 1
    assert os.path.basename(hits[0]) == "incubation (1).db"
    # The live DB, WAL, and daily backups must NOT be flagged
    temp_db.make_daily_backup()
    (tmp_path / "incubation.db-wal").write_bytes(b"")
    names = [os.path.basename(h) for h in temp_db.find_drive_conflicts()]
    assert names == ["incubation (1).db"]
