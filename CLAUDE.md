# Bee Incubation Manager — project guide for Claude Code

Desktop app for tracking leafcutter bee incubators: temperatures, inspections,
Govee sensor readings, and VOC/Vapona monitoring. Python + customtkinter GUI,
data in a single SQLite file that syncs between computers via Google Drive.

## Running the app

```
python incubation_app.py        # launches the GUI (main entry point)
```

- Requires Python 3.12+ (3.14 also works). Install deps with
  `pip install -r requirements.txt`.
- It's a GUI app — to verify a change, launch it and look at the window. On
  Windows, `pythonw.exe incubation_app.py` runs it without a console window
  (that's what the desktop shortcut uses).
- `run_app.py` and `create_shortcut.py` are helper scripts (launcher / desktop
  shortcut + icon generation).

## Tests

Pure logic (`incubation_calc`, `incubation_db` helpers) is covered by pytest:

```
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

DB tests run against a throwaway temp database (never the live Google Drive DB).
The app's auto-sync runs this suite before it pushes and won't push if it fails,
so keep the tests green. There is no automated coverage of the GUI itself.

## How the database is found (important)

`incubation_db.py` resolves the DB path in this order:

1. `db_path` in **`incubation_config.json`** — set via the app's
   *Settings ▸ Data Storage* screen. **This file is per-machine and gitignored.**
2. `incubation.db` sitting next to the source files (legacy/local).
3. Auto-detected Google Drive `BeeIncubation` folder (new installs).
4. Fallback: next to the source files.

The **shared** database lives in Google Drive at
`My Drive\TNT Pollination\Incubation App\incubation.db`. Each machine points at
its own local mount of that folder (drive letter varies, e.g. `G:\My Drive\...`
or `C:\Users\<user>\My Drive\...`) via `incubation_config.json`.

> ⚠️ **One writer at a time.** This is a SQLite file synced by Google Drive, not
> a database server. Concurrent edits from two computers can create Drive
> "conflict copies" and lose data. Don't add logic that assumes multi-user
> concurrency or long-lived write connections.

## File map

| File | Purpose |
|------|---------|
| `incubation_app.py` | Main GUI (customtkinter). Largest file; tabs, dialogs, Settings. |
| `incubation_db.py` | SQLite layer + DB path resolution (`DB_PATH`, `save_config`). |
| `incubation_calc.py` | Incubation date/stage calculations. |
| `inspection_db.py` / `inspection_form.py` | Inspection records + form dialog. |
| `govee_client.py` | Govee temp/humidity sensor API integration. |
| `voc_db.py` / `voc_panel.py` | VOC / Vapona monitor. |
| `qr_server.py` | Local Flask server for phone access (QR code). |
| `email_reporter.py` | Emailed reports. |
| `esp32_firmware/` | Arduino/ESP32 sketch for a DIY sensor (not Python). |

## Conventions & gotchas

- **Never commit** `*.db`, `*.db-wal`, `*.db-shm`, `incubation_config.json`,
  or generated `*.ico` / `*.lnk` files — they're in `.gitignore`. Secrets/API
  keys belong in the DB settings or local config, not in source.
- Match the existing style: plain functions, customtkinter widgets, the helper
  builders (`_label`, `_btn`, `_combo`) already used throughout `incubation_app.py`.
- Inspection windows: Morning 6:00–9:59, Evening 16:00–21:59.
- Updating code across machines: `git pull` to get changes, and
  `git add … && git commit && git push` to share them. The repo is public.
  (The app also auto-syncs every 5 min, and the host serves the mobile web
  app publicly over HTTPS via Tailscale Funnel.)

## Versioning (bump on every push)

`APP_VERSION` near the top of `incubation_app.py` is shown in the desktop
sidebar (with the short git hash). **Bump it with every update**, using
semantic versioning `MAJOR.MINOR.PATCH`:

- **PATCH** (`1.6.0 → 1.6.1`) — small fixes, tweaks, copy/UI adjustments.
- **MINOR** (`1.6.1 → 1.7.0`) — new features (a new page, a new capability).
- **MAJOR** (`1.6.0 → 2.0.0`) — big releases or changes to how things fundamentally work.

Include the version bump in the same commit as the change it describes.

## When working on this app

- After editing, launch the GUI to confirm it still starts and the changed
  screen behaves. Watch the console for the `[DB] <path>` line — it shows which
  database the app is actually using.
- Be careful with anything touching `incubation_db.py` path resolution or the
  Settings ▸ Data Storage "Move & Restart" flow — mistakes there can point users
  at the wrong database or overwrite shared data.
