# Bee Incubation Manager

Desktop app for tracking leafcutter bee incubators — temperatures, inspections,
Govee sensor readings, and VOC/Vapona monitoring.

Built with Python + customtkinter. Data stored in SQLite, synced between devices
via Google Drive.

---

## First-time setup (new computer)

### 1. Install Python

Download and install **Python 3.12** from https://www.python.org/downloads/  
During install, tick **"Add Python to PATH"**.

### 2. Clone the repo

Open a terminal (search **cmd** or **PowerShell** in the Start menu) and run:

```
git clone https://github.com/tylertorrie/bee-incubation.git
cd bee-incubation
```

### 3. Install dependencies

```
pip install -r requirements.txt
```

### 4. Launch the app

```
python incubation_app.py
```

---

## Syncing data via Google Drive

The app database (`incubation.db`) is **not** stored in git — it syncs through
Google Drive so both computers share the same live data.

### On your first (main) computer

1. Make sure **Google Drive for Desktop** is installed and signed in.
2. Open the app and go to **Settings → Data Storage**.
3. Click **Browse** and navigate to a folder inside your Google Drive  
   (e.g. `Google Drive\BeeIncubation`).
4. Click **Move & Restart**.  
   The app copies your database into that folder and restarts using it.

### On your second computer

1. Make sure **Google Drive for Desktop** is installed and signed in  
   (same Google account — the `BeeIncubation` folder will sync down automatically).
2. Clone the repo and install dependencies (steps 1–3 above).
3. Launch the app once — it will start with an empty local database.
4. Go to **Settings → Data Storage → Browse**, navigate to the same  
   `Google Drive\BeeIncubation` folder, and click **Move & Restart**.  
   The app will now use the shared database going forward.

> **Tip:** Google Drive for Desktop shows as a mapped drive like `G:\` or appears
> under `C:\Users\<you>\Google Drive`. Either path works in the Browse dialog.

---

## Updating the app

The app pulls the latest code automatically on startup (background git pull).
You can also update manually:

```
git pull
```

To push code changes you've made:

```
git add incubation_app.py incubation_db.py   # (or whichever files changed)
git commit -m "description of change"
git push
```

---

## File overview

```
incubation_app.py     — Main GUI (customtkinter)
incubation_db.py      — Database layer (SQLite), DB path resolution
inspection_db.py      — Inspection records (morning/evening windows)
inspection_form.py    — Inspection form dialog + log panel
govee_client.py       — Govee sensor API integration
voc_db.py / voc_panel.py  — VOC / Vapona monitor
qr_server.py          — Local Flask server for phone access
requirements.txt      — Python dependencies
.gitignore            — Excludes *.db, incubation_config.json, cache files
```

`incubation_config.json` — created automatically on first run, stores the
per-machine DB path. **Not committed to git** (each computer has its own path).

---

## Inspection windows

| Window  | Hours         |
|---------|---------------|
| Morning | 6:00 – 9:59   |
| Evening | 16:00 – 21:59 |

Status badges on each incubator tile show **done / missed / open / pending**
for each window today.
