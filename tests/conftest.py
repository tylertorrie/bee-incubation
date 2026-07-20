"""Shared pytest fixtures. Runs each DB test against an isolated temp database."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import incubation_db as db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point incubation_db at a throwaway SQLite file and initialise the schema.

    Backups land in tmp_path/Backups and conflict scans read tmp_path, so tests
    never touch the real (Google Drive) database.
    """
    dbfile = tmp_path / "incubation.db"
    monkeypatch.setattr(db, "DB_PATH", str(dbfile))
    db.init_db()
    return db
