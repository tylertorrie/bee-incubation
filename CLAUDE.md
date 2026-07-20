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
so keep the tests green.

After moving GUI code around, also run the smoke test — it builds the app,
visits every view and opens every dialog, with background services stubbed out:

```
python tests/smoke_gui.py
```

It is not collected by pytest (it opens real windows). Note it **stubs the
background services**, so after changing `services.py` or startup, launch the
app for real and confirm you see `[QRServer] Listening`.

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

The desktop GUI is split into a thin app shell plus one module per screen. Each
view module is a **mixin** that `IncubationApp` inherits, so every method keeps
its original `self.` references — find a screen by opening its module, not by
scrolling one huge file.

| File | Purpose |
|------|---------|
| `incubation_app.py` | App shell only: window, titlebar, sidebar, status bar, navigation, dialog wiring, entry point. **Keep it small.** |
| `ui_theme.py` | Colours, fonts, mode accents, widget factories (`_label`, `_btn`, `_entry`, `_combo`, `_FormRow`). |
| `app_config.py` | `APP_VERSION`, `POLL_INTERVAL_SEC`, `_NO_WINDOW`. Import shared constants from here — never from `incubation_app` (circular). |
| `views/dialogs.py` | All modal dialogs (incubator, batch, sample, tray, QR, alerts, VOC/WiFi managers). |
| `views/dashboard_view.py` | Dashboard + Incubators screens, incubator cards. |
| `views/detail_view.py` | Single-incubator screen: chart, temp-mode controls, tabs. |
| `views/trays_view.py` | Tray table, paging/selection, history, CSV import, bulk ops. |
| `views/samples_view.py` | Samples table + x-ray spreadsheet import. |
| `views/analytics_view.py` | KPI cards, bar chart, temp stability, cycle stats. |
| `views/timeline_view.py` | Calendar screen, ICS export, Google Calendar sync. |
| `views/sensibo_controls.py` | Sensibo A/C controls. |
| `services.py` | Background services: Govee polling, alerts, sensor health, DB backups + Drive-conflict scan, email, git pull/auto-sync. |
| `incubation_db.py` | SQLite layer + DB path resolution (`DB_PATH`, `save_config`), backups, conflict detection. |
| `incubation_calc.py` | Dates, temp modes/goals, analytics (time-in-range, degree-days). |
| `inspection_db.py` / `inspection_form.py` | Inspection records + form dialog. |
| `govee_client.py` / `sensibo_client.py` | Sensor + A/C integrations. |
| `voc_db.py` / `voc_panel.py` | VOC / Vapona monitor. |
| `qr_server.py` | Flask server for the phone web app (also served via Tailscale). |
| `email_reporter.py` / `gcal_sync.py` | Emailed reports / calendar sync. |
| `pi/`, `esp32_firmware/` | Vapona sensor service + Arduino sketch (not part of the GUI). |

**Adding a new screen:** create `views/<name>_view.py` with a `<Name>ViewMixin`
class, add it to the `IncubationApp` bases and register it in `self._views`.

## Conventions & gotchas

- **Never commit** `*.db`, `*.db-wal`, `*.db-shm`, `incubation_config.json`,
  or generated `*.ico` / `*.lnk` files — they're in `.gitignore`. Secrets/API
  keys belong in the DB settings or local config, not in source.
- Match the existing style: plain functions, customtkinter widgets, and the
  helper builders (`_label`, `_btn`, `_combo`, `_FormRow`) imported from
  `ui_theme` — don't hand-roll widget styling or hardcode colours.
- Inspection windows: Morning 6:00–9:59, Evening 16:00–21:59.
- Updating code across machines: `git pull` to get changes, and
  `git add … && git commit && git push` to share them. The repo is public.
  (The app also auto-syncs every 5 min, and the host serves the mobile web
  app publicly over HTTPS via Tailscale Funnel.)

## Versioning (bump on every push)

`APP_VERSION` in `app_config.py` is shown in the desktop
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
