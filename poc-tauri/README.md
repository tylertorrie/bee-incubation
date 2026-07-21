# Tauri + React + TypeScript POC

Proof-of-concept for rewriting the desktop UI in **Tauri + React + TypeScript +
Tailwind**, while **reusing the existing Python backend** instead of rewriting
~7k lines of tested logic.

## Architecture

```
┌─────────────────────────┐     HTTP      ┌──────────────────────────┐
│  Tauri window (desktop)  │  ───────────► │  FastAPI (backend/api.py)│
│  React + TS + Tailwind   │  :8756        │  wraps the EXISTING code │
│  (frontend/)             │ ◄───────────  │  incubation_db, calc, …  │
└─────────────────────────┘   real data   └───────────┬──────────────┘
                                                       │ sqlite3
                                                       ▼
                                        the same Google-Drive incubation.db
```

- The React app is **also the mobile web app** if served as a plain web build —
  one UI codebase replaces both the desktop customtkinter app *and* the 2.7k-line
  Flask mobile app.
- The backend is **read-only in this POC** so it can never write a Drive
  conflict copy against the live app. Writes (the tray move, etc.) come in the
  real migration once the DB story (SQLite vs Supabase) is decided.

## Run it

**1. Backend** (reuses the existing Python modules):
```
cd poc-tauri/backend
python -m uvicorn api:app --port 8756
```

**2a. Frontend in the browser** (fastest — proves the whole stack):
```
cd poc-tauri/frontend
npm install
node node_modules/esbuild/install.js   # only if the esbuild binary was skipped
npm run dev            # http://localhost:5173
```

**2b. Frontend as the Tauri desktop app** (needs the Rust toolchain):
```
cd poc-tauri/frontend
npm run tauri dev      # first build compiles Rust deps (a few minutes)
```

## What the POC demonstrates
- React/TS/Tailwind rendering a **real view** (the Trays table) against the
  existing Python logic — ~4,600 live trays, computed cool-days, status badges.
- Filters (incubator / status), column sorting, and the full selection model:
  click, **Shift+click range**, and **Select All** — matching the desktop app.
- A "Move →" action wired to the same rules (shows intent; read-only here).

## Next steps for the real migration
- Decide DB: keep SQLite/Drive vs move to Supabase (removes the one-writer rule).
- Add the write endpoints (move_trays, status, edits) behind the API.
- Port views one at a time (Dashboard, Detail, Settings, Analytics, Inspections).
- Fold the mobile PWA into this same React app (responsive) and retire Flask.
- Bundle the Python backend as a Tauri sidecar for a single installable app.
