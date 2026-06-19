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
Google Drive so every computer shares the same live data.

**Shared folder:** `My Drive\TNT Pollination\Incubation App`
(the `incubation.db` file lives in there). On each computer this appears under
that account's Google Drive — e.g. `G:\My Drive\TNT Pollination\Incubation App`
or `C:\Users\<you>\My Drive\TNT Pollination\Incubation App`. Either path works
in the Browse dialog; the drive letter just depends on how Drive is mounted.

> ⚠️ **One person at a time.** This is a single SQLite file synced by Google
> Drive, not a multi-user server. It's safe for several people to *use* the app,
> but **two people editing at the same time can create Drive "conflict copies"
> and lose data.** Coordinate so only one computer is making changes at once,
> and let Drive finish syncing (tray icon idle) before someone else opens it.

### Switching this computer to the shared folder

1. Make sure **Google Drive for Desktop** is installed and signed in.
2. Open the app → **Settings → Data Storage**.
3. Click **Browse** and navigate to `My Drive\TNT Pollination\Incubation App`.
4. Click **Move & Restart**:
   - If it warns the database **already exists** there, click **No** —
     *"keep the existing (shared) database and just use it here."* This adopts
     the shared data without overwriting it.
   - Only click **Yes** (overwrite) if *this* computer holds the data you want
     to become the shared copy.
5. Close and reopen the app. **Settings → Data Storage** should now show the
   `TNT Pollination\Incubation App` path.

### Adding another user (a different Google account)

1. **Share the folder.** In Google Drive (web), right-click
   `TNT Pollination` (or just the `Incubation App` subfolder) → **Share**, add
   the other person's Gmail, and set them to **Editor**.
2. **They add it to their Drive.** The other person opens Drive, finds the
   shared folder under **Shared with me**, and chooses **Add shortcut to Drive**
   (or **Make available offline** in Drive for Desktop) so it syncs to their PC.
3. **They install the app** — follow *First-time setup* above.
4. **They point the app at the shared folder** — *Settings → Data Storage →
   Browse* to their local copy of `…\TNT Pollination\Incubation App`, click
   **Move & Restart**, and choose **No** to join the existing data (per the
   warning above).

Remember the *one person at a time* rule — Google Drive sync is not a substitute
for a real database server.

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
