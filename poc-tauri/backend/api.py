"""
POC FastAPI backend for the Tauri/React rewrite.

This is the whole point of the proof-of-concept: instead of rewriting the
~7k lines of battle-tested Python logic in Rust/TypeScript, we wrap the EXISTING
modules (incubation_db, incubation_calc, ...) behind a thin local HTTP API and
let a React front-end talk to it.

READ-ONLY on purpose: the POC only reads, so it can never create a Google-Drive
conflict copy against the live app. Writes (the tray move, etc.) come in the
real migration once the DB story (SQLite vs Supabase) is settled.

Run:
    cd poc-tauri/backend
    uvicorn api:app --reload --port 8756
"""
import os
import sys

# Make the existing app modules importable (they live in the repo root).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import incubation_db as db                     # noqa: E402  (existing logic, reused as-is)
from incubation_calc import cool_down_days     # noqa: E402

from fastapi import FastAPI                     # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app = FastAPI(title="Bee Incubation POC API")

# The Vite dev server (5173) and the Tauri webview call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "tauri://localhost", "http://tauri.localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True, "db_path": db.DB_PATH}


@app.get("/api/incubators")
def incubators():
    """Reuses the existing db.get_incubators() untouched."""
    out = []
    for i in db.get_incubators(include_hidden=True):
        out.append({
            "id": i["id"],
            "name": i["name"],
            "temp_mode": i.get("temp_mode") or "incubation",
        })
    return out


@app.get("/api/trays")
def trays(incubator_id: int | None = None, status: str | None = None):
    """Reuses the existing db.get_trays() and adds a computed cool-days field."""
    rows = db.get_trays(incubator_id=incubator_id, status=status)
    out = []
    for t in rows:
        try:
            cool_days = cool_down_days(t)   # reads cool_date/status from the tray
        except Exception:
            cool_days = None
        out.append({
            "id": t["id"],
            "tray_number": t.get("tray_number"),
            "sample_name": t.get("sample_name"),
            "incubator_name": t.get("incubator_name"),
            "weight_lbs": t.get("weight_lbs"),
            "live_count": t.get("live_count"),
            "parasite_level_pct": t.get("parasite_level_pct"),
            "in_date": t.get("in_date"),
            "out_date": t.get("out_date"),
            "cool_date": t.get("cool_date"),
            "cool_days": cool_days,
            "status": t.get("status"),
        })
    return out
