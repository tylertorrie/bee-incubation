"""
incubation_app.py  —  Leafcutter Bee Incubation Manager
GUI: customtkinter  |  DB: SQLite  |  Sensor: Govee API  |  QR scan: Flask

Run:
    python incubation_app.py

Dependencies:
    pip install customtkinter pillow qrcode flask requests openpyxl
"""
import sys
import os
import subprocess
import threading
import time
from datetime import datetime

# Windows: run helper subprocesses (git, py_compile, poller) hidden so no
# console window flashes up and steals focus while you're working.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
from tkinter import ttk, filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
import govee_client as govee_mod
import qr_server
import voc_db
from voc_panel import VOCPanel
import inspection_db
from inspection_form import InspectionDialog, InspectionsLogPanel, make_status_badges
import email_reporter

# Optional imports
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import qrcode as qrlib
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    import openpyxl
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

try:
    import matplotlib
    matplotlib.use("Agg")          # non-interactive backend — never spawns its own window
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── Version ─────────────────────────────────────────────────────────────────
APP_VERSION = "1.10.1"   # bump on every push (semver: MAJOR.MINOR.PATCH)


def _git_revision() -> str:
    """Short git commit hash + date for the running code, or '' if unavailable."""
    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "-C", app_dir, "log", "-1", "--format=%h · %cd", "--date=short"],
            capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def app_version_string() -> str:
    rev = _git_revision()
    return f"v{APP_VERSION}  ({rev})" if rev else f"v{APP_VERSION}"


# ── Polling ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 15 * 60   # Govee polling is fixed at 15 minutes


# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

GOLD      = "#FFD700"
DK_GOLD   = "#B8860B"
GREEN     = "#4CAF50"
TEAL      = "#10B981"
ORANGE    = "#FF9800"
RED       = "#F44336"
BLUE      = "#3B82F6"
SIDEBAR   = "#111827"
CARD      = "#1F2937"
CARD2     = "#263347"
BORDER    = "#374151"
TEXT      = "#F3F4F6"
SUBTEXT   = "#9CA3AF"
FONT_H    = ("Segoe UI", 14, "bold")
FONT_B    = ("Segoe UI", 11)
FONT_S    = ("Segoe UI", 10)


def _treeview_style():
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Dark.Treeview",
        background=CARD, foreground=TEXT,
        fieldbackground=CARD, borderwidth=0,
        rowheight=26, font=("Segoe UI", 10))
    style.configure("Dark.Treeview.Heading",
        background=SIDEBAR, foreground=GOLD,
        relief="flat", font=("Segoe UI", 10, "bold"))
    style.map("Dark.Treeview",
        background=[("selected", "#3B4F6B")],
        foreground=[("selected", TEXT)])


def _label(parent, text, font=FONT_B, color=TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _btn(parent, text, cmd, width=110, height=32, fg=CARD2, hover=BORDER,
         text_color=TEXT, **kw):
    return ctk.CTkButton(parent, text=text, command=cmd, width=width,
                         height=height, fg_color=fg, hover_color=hover,
                         text_color=text_color, corner_radius=6, **kw)


def _parse_date_loose(s):
    """Parse a date string in common formats (ISO or M/D/Y). Returns date or None."""
    if not s:
        return None
    s = str(s).strip()
    token = s.replace("T", " ").split()[0] if s else s   # the date portion
    for cand in (token, s):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def cool_down_days(tray: dict):
    """Days a tray has been / was cooled. None if not applicable."""
    cd = _parse_date_loose(tray.get("cool_date"))
    if not cd:
        return None
    status = tray.get("status")
    if status == "cooled":
        end = datetime.now().date()
    elif status == "released":
        end = _parse_date_loose(tray.get("out_date")) or datetime.now().date()
    else:
        return None
    return max((end - cd).days, 0)


def _poll_age(timestamp_iso: str | None, interval_sec: int = 300) -> tuple[str, str]:
    """
    Return (display_text, color) for how long ago a Govee poll timestamp was.

    Freshness colors scale with the configured poll interval so they stay
    meaningful at any rate (1 min → 1 hr):
      green  = within ~1.5 cycles (healthy)
      orange = within ~3 cycles   (a poll or two missed)
      red    = beyond that, or missing
    """
    if not timestamp_iso:
        return "Never polled", SUBTEXT
    try:
        then    = datetime.fromisoformat(timestamp_iso)
        minutes = (datetime.now() - then).total_seconds() / 60
    except Exception:
        return "Unknown", SUBTEXT
    if minutes < 1:
        text = "Just now"
    elif minutes < 60:
        text = f"{int(minutes)} min ago"
    elif minutes < 120:
        text = "1 hr ago"
    else:
        text = f"{int(minutes // 60)} hrs ago"

    cycle_min  = max(interval_sec / 60, 1)
    green_cut  = cycle_min * 1.5 + 1     # one cycle + slack
    orange_cut = cycle_min * 3   + 2
    color = GREEN if minutes <= green_cut else (ORANGE if minutes <= orange_cut else RED)
    return text, color


def _entry(parent, placeholder="", width=200):
    return ctk.CTkEntry(parent, placeholder_text=placeholder, width=width,
                        fg_color=CARD, border_color=BORDER, text_color=TEXT)


def _combo(parent, values, width=200):
    return ctk.CTkComboBox(parent, values=values, width=width,
                           fg_color=CARD, border_color=BORDER,
                           button_color=BORDER, text_color=TEXT,
                           dropdown_fg_color=CARD)


# ── Reusable form helper ──────────────────────────────────────────────────────

class _FormRow:
    """One label + entry widget row in a grid form."""
    def __init__(self, parent, row, label, placeholder="", width=220, widget=None):
        _label(parent, label, font=FONT_S, color=SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4, 8), pady=3)
        if widget:
            self.widget = widget
        else:
            self.widget = _entry(parent, placeholder, width=width)
        self.widget.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

    def get(self):
        w = self.widget
        if isinstance(w, ctk.CTkEntry):
            return w.get().strip()
        if isinstance(w, ctk.CTkComboBox):
            return w.get()
        if isinstance(w, ctk.CTkTextbox):
            return w.get("1.0", "end").strip()
        return ""

    def set(self, value):
        w = self.widget
        val = str(value) if value is not None else ""
        if isinstance(w, ctk.CTkEntry):
            w.delete(0, "end"); w.insert(0, val)
        elif isinstance(w, ctk.CTkComboBox):
            w.set(val)
        elif isinstance(w, ctk.CTkTextbox):
            w.delete("1.0", "end"); w.insert("1.0", val)


# ═══════════════════════════════════════════════════════════════════════════════
#   DIALOGS
# ═══════════════════════════════════════════════════════════════════════════════

class IncubatorDialog(ctk.CTkToplevel):
    def __init__(self, master, inc: dict = None, on_save=None, on_delete=None):
        super().__init__(master)
        self.on_save = on_save
        self.on_delete = on_delete
        self.inc = inc or {}
        self.title("Edit Incubator" if inc else "Add Incubator")
        self.geometry("460x500")
        self.resizable(False, False)
        self.grab_set()
        self._build()
        if inc:
            self._populate()

    def _build(self):
        f = ctk.CTkScrollableFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4, pady=4)
        f.columnconfigure(1, weight=1)
        self._f = f

        rows = [
            ("Name",              "Incubator 1",  "name"),
            ("Capacity (trays)",  "50",           "capacity"),
            ("Govee Device ID",   "AB:CD:EF:...", "govee_device_id"),
            ("Govee SKU / Model", "H5075",        "govee_sku"),
        ]
        self._rows = {}
        for i, (lbl, ph, key) in enumerate(rows):
            r = _FormRow(f, i, lbl, ph, width=240)
            self._rows[key] = r

        # Temp mode selector
        n = len(rows)
        _label(f, "Temp Mode", FONT_S, SUBTEXT).grid(
            row=n, column=0, sticky="e", padx=(14, 8), pady=8)
        self._mode_var = ctk.StringVar(value="Incubation")
        mode_names = [v["label"] for v in calc.TEMP_MODES.values()]
        self._mode_seg = ctk.CTkSegmentedButton(
            f, values=mode_names, variable=self._mode_var, width=290)
        self._mode_seg.grid(row=n, column=1, sticky="w", padx=8, pady=8)

        # Range hint label (updates as mode changes)
        n2 = n + 1
        self._range_hint = _label(f, "", FONT_S, SUBTEXT)
        self._range_hint.grid(row=n2, column=1, sticky="w", padx=8, pady=(0, 4))
        self._mode_var.trace_add("write", self._update_range_hint)
        self._update_range_hint()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=14)
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=130).pack(side="right", padx=4)
        _btn(btns, "Cancel", self.destroy, width=100).pack(side="right")

        # Manage actions (only when editing an existing incubator)
        if self.inc.get("id"):
            is_hidden = bool(self.inc.get("is_hidden"))
            _btn(btns, "Unhide" if is_hidden else "Hide",
                 self._toggle_hidden, width=80,
                 fg=CARD2, hover=BORDER, text_color=SUBTEXT).pack(side="left")
            _btn(btns, "Delete", self._delete, width=80,
                 fg="#4B0000", hover=RED, text_color="white").pack(side="left", padx=6)

    def _toggle_hidden(self):
        new_val = not bool(self.inc.get("is_hidden"))
        db.set_incubator_hidden(self.inc["id"], new_val)
        if self.on_save:
            self.on_save()
        self.destroy()

    def _delete(self):
        if not messagebox.askyesno(
            "Delete Incubator",
            f"Delete '{self.inc.get('name')}'?\n\n"
            "This removes the incubator. Its trays and readings remain in the "
            "database but will no longer be linked to an incubator.\n\n"
            "This cannot be undone.",
            icon="warning", parent=self):
            return
        db.delete_incubator(self.inc["id"])
        if self.on_delete:
            self.on_delete()
        elif self.on_save:
            self.on_save()
        self.destroy()

    def _update_range_hint(self, *_):
        label = self._mode_var.get()
        key   = calc._MODE_BY_LABEL.get(label, "incubation")
        cfg   = calc.TEMP_MODES[key]
        hint  = "No temperature alerts" if cfg["min"] is None else f"Alert range: {cfg['min']}–{cfg['max']} °C"
        self._range_hint.configure(text=hint)

    def _populate(self):
        for key, row in self._rows.items():
            row.set(self.inc.get(key, ""))
        mode_key = self.inc.get("temp_mode", "incubation")
        mode_cfg = calc.TEMP_MODES.get(mode_key, calc.TEMP_MODES["incubation"])
        self._mode_var.set(mode_cfg["label"])

    def _save(self):
        data = {k: v for k, v in ((k, r.get()) for k, r in self._rows.items()) if v != ""}
        if not data.get("name"):
            messagebox.showerror("Error", "Name is required.", parent=self)
            return
        data["id"] = self.inc.get("id")
        try:
            data["capacity"] = int(data.get("capacity", 50))
        except (ValueError, TypeError):
            data["capacity"] = 50
        for fld in ("humidity_min", "humidity_max"):
            try:
                data[fld] = float(data[fld])
            except (KeyError, ValueError, TypeError):
                pass
        # Temp mode from segmented button
        label = self._mode_var.get()
        data["temp_mode"] = calc._MODE_BY_LABEL.get(label, "incubation")
        # Preserve fields not editable in this form so they aren't overwritten with None
        for preserve in ("sort_order", "is_hidden", "temp_alerts_enabled"):
            if preserve not in data:
                data[preserve] = self.inc.get(preserve)
        db.upsert_incubator(data)
        if self.on_save:
            self.on_save()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

class BatchDialog(ctk.CTkToplevel):
    """Add or edit an incubation batch (the dates for one incubation run)."""

    DATE_ROWS = [
        ("Start Date",          "start_date"),
        ("Vapona In",           "vapona_in"),
        ("Vapona Out",          "vapona_out"),
        ("Air Out",             "air_out"),
        ("10% Male Emergence",  "male_10pct_emergence"),
        ("Earliest Cool",       "earliest_cool"),
        ("Est. Release",        "estimated_release"),
        ("Latest Release",      "latest_release"),
    ]

    def __init__(self, master, batch: dict = None,
                 incubator_id: int = None, on_save=None):
        super().__init__(master)
        self.on_save = on_save
        self.batch = batch or {}
        self.preselect_inc = incubator_id
        self.title("Edit Batch" if batch else "New Incubation Batch")
        self.geometry("460x660")
        self.resizable(False, False)
        self.grab_set()
        self._build()
        if batch:
            self._populate()

    def _build(self):
        f = ctk.CTkScrollableFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4, pady=4)
        f.columnconfigure(1, weight=1)

        incubators = db.get_incubators()
        inc_names  = [i["name"] for i in incubators]
        self._inc_map = {i["name"]: i["id"] for i in incubators}

        samples   = db.get_samples()
        smp_names = ["(none)"] + [s["name"] for s in samples]
        self._smp_map = {s["name"]: s["id"] for s in samples}

        row = 0
        _label(f, "Batch Name", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._name = _entry(f, "e.g. Inc-3 June 2026", 240)
        self._name.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        row += 1
        _label(f, "Incubator", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._inc_cb = _combo(f, inc_names, 240)
        self._inc_cb.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        if self.preselect_inc:
            for name, iid in self._inc_map.items():
                if iid == self.preselect_inc:
                    self._inc_cb.set(name)

        row += 1
        _label(f, "Sample (opt.)", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._smp_cb = _combo(f, smp_names, 240)
        self._smp_cb.set("(none)")
        self._smp_cb.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        _label(f, "Dates (YYYY-MM-DD)", FONT_B, GOLD).grid(
            row=row+1, column=0, columnspan=2, sticky="w", padx=4, pady=(10,2))
        row += 2

        self._date_rows = {}
        for lbl, key in self.DATE_ROWS:
            r = _FormRow(f, row, lbl, "YYYY-MM-DD", 200)
            self._date_rows[key] = r
            row += 1

        _label(f, "Status", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._status = _combo(f, ["active", "completed", "cancelled"], 200)
        self._status.set("active")
        self._status.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        row += 1

        _label(f, "Notes", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="nw", padx=(4,8), pady=3)
        self._notes = ctk.CTkTextbox(f, height=60, fg_color=CARD,
                                     border_color=BORDER, text_color=TEXT)
        self._notes.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=12)
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=130).pack(side="right", padx=4)
        _btn(btns, "Cancel", self.destroy, width=100).pack(side="right")

    def _populate(self):
        self._name.insert(0, self.batch.get("name") or "")
        if self.batch.get("incubator_name"):
            self._inc_cb.set(self.batch["incubator_name"])
        if self.batch.get("sample_name"):
            self._smp_cb.set(self.batch["sample_name"])
        for key, r in self._date_rows.items():
            r.set(self.batch.get(key) or "")
        self._status.set(self.batch.get("status") or "active")
        if self.batch.get("notes"):
            self._notes.insert("1.0", self.batch["notes"])

    def _save(self):
        inc_name = self._inc_cb.get()
        if not inc_name or inc_name not in self._inc_map:
            messagebox.showerror("Error", "Select an incubator.", parent=self)
            return
        data = {
            "id":           self.batch.get("id"),
            "incubator_id": self._inc_map[inc_name],
            "sample_id":    self._smp_map.get(self._smp_cb.get()),
            "name":         self._name.get().strip(),
            "status":       self._status.get(),
            "notes":        self._notes.get("1.0", "end").strip(),
        }
        for key, r in self._date_rows.items():
            val = r.get()
            data[key] = val if val else None
        db.upsert_batch(data)
        if self.on_save:
            self.on_save()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

class SampleDialog(ctk.CTkToplevel):
    def __init__(self, master, sample: dict = None, on_save=None):
        super().__init__(master)
        self.on_save = on_save
        self.sample = sample or {}
        self.title("Edit Sample" if sample else "Add Sample")
        self.geometry("440x580")
        self.resizable(False, False)
        self.grab_set()
        self._build()
        if sample:
            self._populate()

    def _build(self):
        f = ctk.CTkScrollableFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4, pady=4)
        f.columnconfigure(1, weight=1)

        fields = [
            ("Sample Name *",       "name",              "e.g. RR-4"),
            ("Total Pounds",        "total_weight_lbs",  ""),
            ("Total Kgs",           "total_weight_kg",   ""),
            ("Live Bees per Pound", "live_bees_per_lb",  ""),
            ("Live Bees per KG",    "live_bees_per_kg",  ""),
            ("Parasites",           "parasites",         ""),
            ("Chalkbrood",          "chalkbrood",        ""),
            ("Total Gal Bees",      "total_volume_gal",  ""),
            ("Total KG for 2gal",   "kg_per_2gal",       ""),
            ("Total lbs for 2gal",  "lbs_per_2gal",      ""),
            ("Total Trays",         "total_trays",       ""),
            ("Incubator Space",     "incubator_space",   ""),
        ]
        self._rows = {}
        for i, (lbl, key, ph) in enumerate(fields):
            r = _FormRow(f, i, lbl, ph, width=230)
            self._rows[key] = r

        _label(f, "Notes", FONT_S, SUBTEXT).grid(
            row=len(fields), column=0, sticky="nw", padx=(4,8), pady=3)
        self._notes = ctk.CTkTextbox(f, height=60, fg_color=CARD,
                                     border_color=BORDER, text_color=TEXT)
        self._notes.grid(row=len(fields), column=1, sticky="ew", padx=4, pady=3)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=12)
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=130).pack(side="right", padx=4)
        _btn(btns, "Cancel", self.destroy, width=100).pack(side="right")

    def _populate(self):
        for key, row in self._rows.items():
            row.set(self.sample.get(key, ""))
        if self.sample.get("notes"):
            self._notes.insert("1.0", self.sample["notes"])

    def _save(self):
        name = self._rows["name"].get()
        if not name:
            messagebox.showerror("Error", "Sample name is required.", parent=self)
            return
        data = {"id": self.sample.get("id"), "name": name,
                "notes": self._notes.get("1.0", "end").strip(),
                "incubator_space": self._rows["incubator_space"].get() or None}
        for key in ("total_weight_lbs", "total_weight_kg", "live_bees_per_lb",
                    "live_bees_per_kg", "parasites", "chalkbrood",
                    "total_volume_gal", "kg_per_2gal", "lbs_per_2gal", "total_trays"):
            val = self._rows[key].get()
            try:
                data[key] = float(val) if val else None
            except ValueError:
                data[key] = None
        db.upsert_sample(data)
        if self.on_save:
            self.on_save()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

class TrayDialog(ctk.CTkToplevel):
    def __init__(self, master, tray: dict = None,
                 incubator_id: int = None, on_save=None):
        super().__init__(master)
        self.on_save = on_save
        self.tray = tray or {}
        self.preselect_inc = incubator_id
        self.title("Edit Tray" if tray else "Add Tray")
        self.geometry("460x680")
        self.resizable(False, False)
        self.grab_set()
        self._build()
        if tray:
            self._populate()

    def _build(self):
        f = ctk.CTkScrollableFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4, pady=4)
        f.columnconfigure(1, weight=1)

        incubators = db.get_incubators()
        samples    = db.get_samples()
        batches    = db.get_batches()

        self._inc_map = {i["name"]: i["id"] for i in incubators}
        self._smp_map = {s["name"]: s["id"] for s in samples}
        self._bat_map = {(b.get("name") or f"Batch {b['id']}"): b["id"]
                         for b in batches}
        bat_names = ["(none)"] + list(self._bat_map.keys())

        row = 0
        _label(f, "Tray Number *", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._tray_num = _entry(f, "e.g. T001", 230)
        self._tray_num.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        row += 1
        _label(f, "Sample", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._smp_cb = _combo(f, ["(none)"] + list(self._smp_map.keys()), 230)
        self._smp_cb.set("(none)")
        self._smp_cb.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        row += 1
        _label(f, "Incubator", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._inc_cb = _combo(f, ["(none)"] + list(self._inc_map.keys()), 230)
        self._inc_cb.set("(none)")
        self._inc_cb.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        if self.preselect_inc:
            for name, iid in self._inc_map.items():
                if iid == self.preselect_inc:
                    self._inc_cb.set(name)

        row += 1
        _label(f, "Batch", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._bat_cb = _combo(f, bat_names, 230)
        self._bat_cb.set("(none)")
        self._bat_cb.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        measure_fields = [
            ("Weight (lbs)",      "weight_lbs",         ""),
            ("Volume (gal)",      "volume_gal",         ""),
            ("Live Count",        "live_count",         ""),
            ("Parasite Level (%)", "parasite_level_pct",""),
            ("In Date",           "in_date",            "YYYY-MM-DD"),
            ("Cool Date",         "cool_date",          "YYYY-MM-DD"),
            ("Out Date",          "out_date",           "YYYY-MM-DD"),
        ]
        self._mrows = {}
        for lbl, key, ph in measure_fields:
            row += 1
            r = _FormRow(f, row, lbl, ph, width=230)
            self._mrows[key] = r

        row += 1
        _label(f, "Status", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4,8), pady=3)
        self._status = _combo(f, [lbl for _v, lbl in db.TRAY_STATUS_OPTIONS], 230)
        self._status.set("Incubation")
        self._status.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        row += 1
        _label(f, "Notes", FONT_S, SUBTEXT).grid(
            row=row, column=0, sticky="nw", padx=(4,8), pady=3)
        self._notes = ctk.CTkTextbox(f, height=50, fg_color=CARD,
                                     border_color=BORDER, text_color=TEXT)
        self._notes.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=12)
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=130).pack(side="right", padx=4)
        _btn(btns, "Cancel", self.destroy, width=100).pack(side="right")

    def _populate(self):
        self._tray_num.insert(0, self.tray.get("tray_number") or "")
        if self.tray.get("sample_name"):
            self._smp_cb.set(self.tray["sample_name"])
        if self.tray.get("incubator_name"):
            self._inc_cb.set(self.tray["incubator_name"])
        if self.tray.get("batch_name"):
            self._bat_cb.set(self.tray["batch_name"])
        for key, r in self._mrows.items():
            r.set(self.tray.get(key) or "")
        self._status.set(db.tray_status_label(self.tray.get("status") or "active"))
        if self.tray.get("notes"):
            self._notes.insert("1.0", self.tray["notes"])

    def _save(self):
        tray_num = self._tray_num.get().strip()
        if not tray_num:
            messagebox.showerror("Error", "Tray number is required.", parent=self)
            return
        data = {
            "id":            self.tray.get("id"),
            "tray_number":   tray_num,
            "sample_id":     self._smp_map.get(self._smp_cb.get()),
            "incubator_id":  self._inc_map.get(self._inc_cb.get()),
            "incubation_batch_id": self._bat_map.get(self._bat_cb.get()),
            "status":        db.tray_status_value(self._status.get()),
            "notes":         self._notes.get("1.0", "end").strip(),
        }
        for key in ("in_date", "cool_date", "out_date"):
            data[key] = self._mrows[key].get() or None
        # Auto-release: if an out_date is set and status is still active, mark as released
        if data.get("out_date") and data.get("status") == "active":
            data["status"] = "released"
            self._status.set("Released")
        for key in ("weight_lbs", "volume_gal", "parasite_level_pct"):
            val = self._mrows[key].get()
            try:
                data[key] = float(val) if val else None
            except ValueError:
                data[key] = None
        val = self._mrows["live_count"].get()
        try:
            data["live_count"] = int(val) if val else None
        except ValueError:
            data["live_count"] = None
        try:
            db.upsert_tray(data)
        except Exception as e:
            messagebox.showerror("Error", f"Could not save: {e}", parent=self)
            return
        if self.on_save:
            self.on_save()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

class QRDialog(ctk.CTkToplevel):
    def __init__(self, master, tray: dict, port: int = 5151):
        super().__init__(master)
        self.title(f"QR Code — Tray {tray['tray_number']}")
        self.geometry("360x440")
        self.resizable(False, False)
        self.grab_set()

        url = qr_server.tray_url(tray["id"], port)

        _label(self, f"Tray {tray['tray_number']}", FONT_H, GOLD).pack(pady=(16, 4))
        _label(self, tray.get("sample_name") or "No sample", FONT_S, SUBTEXT).pack()
        _label(self, url, FONT_S, BLUE).pack(pady=(4, 8))

        if HAS_QR and HAS_PIL:
            try:
                qr = qrlib.QRCode(box_size=6, border=2)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="white", back_color="#1F2937")
                self._img = ImageTk.PhotoImage(img)
                tk.Label(self, image=self._img,
                         background="#1F2937").pack(pady=4)
            except Exception:
                _label(self, "(QR image unavailable)", FONT_S, RED).pack()
        else:
            _label(self, "Install qrcode + pillow for QR image", FONT_S, ORANGE).pack()
            _label(self, "Phone scan requires Flask server running", FONT_S, SUBTEXT).pack()

        _btn(self, "Close", self.destroy, width=120,
             fg=CARD2, hover=BORDER).pack(pady=12)


# ─────────────────────────────────────────────────────────────────────────────

class AlertsDialog(ctk.CTkToplevel):
    def __init__(self, master, on_ack=None):
        super().__init__(master)
        self.on_ack = on_ack
        self.title("Active Alerts")
        self.geometry("580x480")
        self.grab_set()
        self._build()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))
        _label(top, "Active Alerts", FONT_H, GOLD).pack(side="left")
        _btn(top, "Acknowledge All", self._ack_all,
             fg=CARD2, hover=BORDER, width=140).pack(side="right")

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=CARD)
        self._scroll.pack(fill="both", expand=True, padx=12, pady=8)
        self._load()

    def _load(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        alerts = db.get_active_alerts()
        if not alerts:
            _label(self._scroll, "No active alerts ✓", FONT_B, GREEN).pack(pady=20)
            return
        sev_colors = {"critical": RED, "warning": ORANGE, "info": BLUE}
        for a in alerts:
            row = ctk.CTkFrame(self._scroll, fg_color=CARD2, corner_radius=8)
            row.pack(fill="x", pady=3, padx=4)
            c = sev_colors.get(a.get("severity", "warning"), ORANGE)
            _label(row, f"● {a['message']}", FONT_S, c).pack(
                side="left", padx=10, pady=6, anchor="w")
            _btn(row, "✓", lambda aid=a["id"]: self._ack(aid),
                 width=36, height=26, fg=BORDER, hover=CARD).pack(
                 side="right", padx=6, pady=4)
            ts = (a.get("triggered_at") or "")[:16].replace("T", " ")
            _label(row, ts, FONT_S, SUBTEXT).pack(
                side="right", padx=4, pady=6)

    def _ack(self, aid):
        db.acknowledge_alert(aid)
        self._load()
        if self.on_ack:
            self.on_ack()

    def _ack_all(self):
        db.acknowledge_all_alerts()
        self._load()
        if self.on_ack:
            self.on_ack()


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN APP
# ═══════════════════════════════════════════════════════════════════════════════

class IncubationApp(ctk.CTk):

    def __init__(self):
        # Tell Windows this is a distinct app so it gets its own taskbar button + icon
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "BeeIncubation.Manager.1")
        except Exception:
            pass

        super().__init__()
        db.init_db()
        voc_db.init_voc_tables()
        inspection_db.init_inspection_tables()
        _treeview_style()

        self.title("Bee Incubation Manager")
        self.geometry("1280x800")
        self.minsize(1000, 680)
        self.configure(fg_color="#0F172A")

        # Apply logo icon to title bar and taskbar
        _app_dir  = os.path.dirname(os.path.abspath(__file__))
        _ico_path = os.path.join(_app_dir, "bee.ico")
        _icon_src = os.path.join(_app_dir, "app_icon.png")
        # Rebuild bee.ico from the committed source if it's missing or stale, so the
        # window icon stays in sync on every machine after a git pull (bee.ico itself
        # is gitignored / generated per-machine).
        try:
            if os.path.exists(_icon_src) and (
                    not os.path.exists(_ico_path)
                    or os.path.getmtime(_icon_src) > os.path.getmtime(_ico_path)):
                from PIL import Image as _IcoImg
                _IcoImg.open(_icon_src).convert("RGBA").resize(
                    (256, 256), _IcoImg.LANCZOS).save(
                    _ico_path, format="ICO",
                    sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
        except Exception:
            pass
        if os.path.exists(_ico_path):
            try:
                self.iconbitmap(_ico_path)
            except Exception:
                pass

        # Govee client (polling fixed at 15 minutes)
        self._govee = govee_mod.GoveeClient(
            api_key=db.get_setting("govee_api_key"),
            poll_interval_sec=POLL_INTERVAL_SEC,
        )
        self._card_widgets: dict = {}  # incubator_id → {temp, hum, dot, ts} labels
        self._detail_inc: dict = {}   # incubator being shown in detail view

        # QR server port
        self._qr_port = int(db.get_setting("qr_server_port", "5151"))

        # Build UI
        self._build_sidebar()
        self._build_main()
        self._build_status_bar()

        # Build all views (hidden until selected)
        self._views = {}
        self._views["dashboard"]    = self._build_dashboard()
        self._views["incubators"]   = self._build_incubators_view()
        self._views["samples"]      = self._build_samples_view()
        self._views["trays"]        = self._build_trays_view()
        self._views["timeline"]     = self._build_timeline_view()
        self._views["inspections"]  = self._build_inspections_view()
        self._views["settings"]     = self._build_settings_view()
        self._views["inc_detail"]   = self._build_inc_detail_view()

        self._current_view = None
        self.show_view("dashboard")

        # Start background services
        self._start_govee()
        self._start_qr_server()
        self._start_alert_checker()
        self._start_email_scheduler()
        self._git_pull()            # immediate pull on startup
        self._start_auto_sync()     # then every 10 minutes

        # Refresh status bar periodically
        self._tick()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR, width=190, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Logo image — show if logo.png exists, otherwise fall back to text
        _logo_png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(_logo_png):
            try:
                from PIL import Image as _PILImage
                import customtkinter as _ctk2
                _raw = _PILImage.open(_logo_png).convert("RGBA")
                # Make white background transparent
                _data = _raw.getdata()
                _raw.putdata([(r, g, b, 0) if r > 230 and g > 230 and b > 230
                              else (r, g, b, a) for r, g, b, a in _data])
                _ctk_img = ctk.CTkImage(light_image=_raw, dark_image=_raw, size=(54, 54))
                ctk.CTkLabel(sb, image=_ctk_img, text="").pack(pady=(18, 2))
            except Exception:
                _label(sb, "🐝", ("Segoe UI", 30), GOLD).pack(pady=(18, 2))
        else:
            _label(sb, "🐝", ("Segoe UI", 30), GOLD).pack(pady=(18, 2))

        _label(sb, "Incubation", ("Segoe UI", 13, "bold"), GOLD).pack(
            pady=(0, 2), padx=16, anchor="center")
        _label(sb, "Bee Manager", FONT_S, SUBTEXT).pack(
            pady=(0, 2), padx=16, anchor="center")
        _label(sb, app_version_string(), ("Segoe UI", 9), "#6B7280").pack(
            pady=(0, 14), padx=16, anchor="center")

        nav_items = [
            ("🏠  Dashboard",     "dashboard"),
            ("🌡️  Incubators",    "incubators"),
            ("🧪  Samples",       "samples"),
            ("📦  Trays",         "trays"),
            ("📅  Timeline",      "timeline"),
            ("🔍  Inspections",   "inspections"),
            ("⚙️  Settings",      "settings"),
        ]
        self._nav_btns = {}
        for label, key in nav_items:
            btn = ctk.CTkButton(
                sb, text=label, anchor="w", height=40,
                fg_color="transparent", hover_color=CARD,
                text_color=TEXT, font=FONT_B, corner_radius=6,
                command=lambda k=key: self.show_view(k)
            )
            btn.pack(fill="x", padx=8, pady=1)
            self._nav_btns[key] = btn

        # Spacer
        ctk.CTkFrame(sb, fg_color="transparent").pack(fill="y", expand=True)

        # Alert badge button
        self._alert_btn = ctk.CTkButton(
            sb, text="🔔  Alerts  0", height=40, anchor="w",
            fg_color=CARD, hover_color=CARD2,
            text_color=SUBTEXT, font=FONT_B, corner_radius=6,
            command=self._open_alerts
        )
        self._alert_btn.pack(fill="x", padx=8, pady=(0, 16))

    def _build_main(self):
        self._main = ctk.CTkFrame(self, fg_color="#0F172A", corner_radius=0)
        self._main.pack(side="left", fill="both", expand=True)

    def _build_status_bar(self):
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR, height=30, corner_radius=0)
        sb.pack(side="bottom", fill="x")
        sb.pack_propagate(False)
        self._status_govee = _label(sb, "Govee: —", FONT_S, SUBTEXT)
        self._status_govee.pack(side="left", padx=16)
        self._status_qr = _label(sb, "QR Server: —", FONT_S, SUBTEXT)
        self._status_qr.pack(side="left", padx=16)
        self._status_time = _label(sb, "", FONT_S, SUBTEXT)
        self._status_time.pack(side="right", padx=16)

    # ── Navigation ────────────────────────────────────────────────────────────

    def show_view(self, name: str):
        for v in self._views.values():
            v.pack_forget()
        # inc_detail is not a top-level nav item — keep the previous nav btn highlighted
        if name != "inc_detail":
            for k, btn in self._nav_btns.items():
                btn.configure(fg_color=CARD if k == name else "transparent",
                              text_color=GOLD if k == name else TEXT)
        view = self._views[name]
        view.pack(fill="both", expand=True)
        self._current_view = name
        getattr(self, f"_refresh_{name}")()

    # ══════════════════════════════════════════════════════════════════════════
    #  DASHBOARD
    # ══════════════════════════════════════════════════════════════════════════

    def _build_dashboard(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Dashboard", FONT_H, GOLD).pack(side="left")
        _btn(hdr, "+ Incubator", lambda: self._open_incubator_dialog(),
             fg=CARD, hover=CARD2, width=130).pack(side="right")
        _btn(hdr, "Refresh", self._refresh_dashboard,
             fg="transparent", hover=CARD, width=90).pack(side="right", padx=6)

        # "Show hidden" toggle — only visible when hidden incubators exist
        self._dash_show_hidden = ctk.BooleanVar(value=False)
        self._dash_hidden_btn  = ctk.CTkButton(
            hdr, text="", width=150, height=28,
            fg_color=CARD2, hover_color=BORDER, text_color=SUBTEXT,
            corner_radius=6, font=FONT_S,
            command=self._toggle_dash_hidden)
        # packed conditionally in _refresh_dashboard

        self._dash_scroll = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", corner_radius=0)
        self._dash_scroll.pack(fill="both", expand=True, padx=12, pady=4)

        frame._card_container = self._dash_scroll
        return frame

    def _toggle_dash_hidden(self):
        self._dash_show_hidden.set(not self._dash_show_hidden.get())
        self._refresh_dashboard()

    def _refresh_dashboard(self):
        self._card_widgets.clear()
        container = self._dash_scroll
        for w in container.winfo_children():
            w.destroy()

        show_hidden = getattr(self, "_dash_show_hidden", None)
        show_hidden = show_hidden.get() if show_hidden else False
        all_inc     = db.get_incubators(include_hidden=True)
        # Dashboard shows only incubators that are turned ON (temp_mode != "off").
        # Off ones live in the Incubators view, where they can be turned back on.
        on_inc      = [i for i in all_inc if not calc.is_off(i)]
        hidden_n    = sum(1 for i in on_inc if i.get("is_hidden"))
        incubators  = on_inc if show_hidden else [i for i in on_inc if not i.get("is_hidden")]

        # Update the "show/hide hidden" button visibility and label
        if hidden_n > 0:
            lbl = (f"Hide {hidden_n} hidden" if show_hidden
                   else f"Show {hidden_n} hidden")
            self._dash_hidden_btn.configure(text=lbl)
            self._dash_hidden_btn.pack(side="right", padx=6)
        else:
            self._dash_hidden_btn.pack_forget()

        if not incubators:
            ctk.CTkFrame(container, fg_color="transparent").pack(pady=40)
            if hidden_n > 0:
                _label(container,
                       f"All {hidden_n} incubator(s) are hidden.\n"
                       "Click 'Show hidden' above or manage them in the Incubators view.",
                       FONT_B, SUBTEXT).pack()
            else:
                _label(container, "No incubators yet.\nClick '+ Incubator' to add one.",
                       FONT_B, SUBTEXT).pack()
            return

        # Summary row — use aggregate query, not full row fetch
        # "In incubator" = active + cooled (count only drops on release)
        _stats         = db.get_tray_stats(status=db.IN_INCUBATOR_STATUSES)
        tray_count     = _stats["count"]
        total_gals     = _stats["total_gals"]
        total_capacity = sum((i.get("capacity") or 0) for i in incubators)
        fill_pct       = round(tray_count / total_capacity * 100) if total_capacity else 0
        fill_col       = GREEN if fill_pct < 80 else (ORANGE if fill_pct < 95 else RED)

        summary_f = ctk.CTkFrame(container, fg_color=CARD, corner_radius=10)
        summary_f.pack(fill="x", pady=(0, 12), padx=4)

        for txt, val, col in [
            ("Active Incubators", str(len(incubators)),            GOLD),
            ("Trays",             f"{tray_count} / {total_capacity}", GOLD),
            ("Capacity",          f"{fill_pct}% full",             fill_col),
            ("Total Gals",        f"{total_gals:.1f}",             GOLD),
            ("Active Alerts",     str(len(db.get_active_alerts())), GOLD),
        ]:
            sf = ctk.CTkFrame(summary_f, fg_color="transparent")
            sf.pack(side="left", padx=20, pady=10)
            _label(sf, val, ("Segoe UI", 20, "bold"), col).pack()
            _label(sf, txt, FONT_S, SUBTEXT).pack()

        # Responsive card grid — rebuilds on window resize (debounced)
        grid = ctk.CTkFrame(container, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=4)
        grid._resize_job = None

        def _build_grid(cols):
            self._card_widgets.clear()
            for child in grid.winfo_children():
                child.destroy()
            for c in range(6):
                grid.columnconfigure(c, weight=1 if c < cols else 0,
                                     uniform="col" if c < cols else "")
            for idx, inc in enumerate(incubators):
                card = self._make_inc_card(grid, inc)
                card.grid(row=idx // cols, column=idx % cols,
                          padx=6, pady=6, sticky="nsew")

        def _on_resize(event=None):
            if grid._resize_job:
                self.after_cancel(grid._resize_job)
            def _do():
                w    = self.winfo_width()
                cols = 4 if w >= 1400 else (3 if w >= 1000 else 2)
                if getattr(grid, "_last_cols", None) != cols:
                    grid._last_cols = cols
                    _build_grid(cols)
            grid._resize_job = self.after(200, _do)

        self.bind("<Configure>", _on_resize, add=True)
        _on_resize()

    def _make_inc_card(self, parent, inc: dict) -> ctk.CTkFrame:
        is_hidden = bool(inc.get("is_hidden"))
        card_bg   = "#161E2C" if is_hidden else CARD
        title_col = SUBTEXT  if is_hidden else GOLD
        bdr_col   = "#222D3D" if is_hidden else BORDER

        card = ctk.CTkFrame(parent, fg_color=card_bg, corner_radius=12,
                            border_width=1, border_color=bdr_col)

        # ── Readings ──
        reading = self._govee.get_last(inc["id"])
        if not reading:
            db_row  = db.get_latest_reading(inc["id"])
            reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"],
                       "timestamp": db_row["timestamp"]} if db_row else {}
        temp_c = reading.get("temp_c")
        hum    = reading.get("humidity")

        t_min, t_max = calc.get_temp_range(inc)
        if temp_c is not None:
            unit  = db.get_setting("temp_unit", "C")
            t_str = calc.format_temp(temp_c, unit)
            t_col = SUBTEXT if t_min is None else (GREEN if t_min <= temp_c <= t_max else RED)
            h_col = TEXT
            problems  = calc.check_temp_humidity(inc, temp_c, hum)
            dot_color = RED if problems else GREEN
        else:
            t_str = "—"
            t_col = h_col = dot_color = SUBTEXT

        # ── Header: name + chips + status dot ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        name_txt = f"{inc['name']}  (hidden)" if is_hidden else inc["name"]
        _label(hdr, name_txt, FONT_H, title_col).pack(side="left")
        mode_key = inc.get("temp_mode", "incubation")
        mode_cfg = calc.TEMP_MODES.get(mode_key, calc.TEMP_MODES["incubation"])
        ctk.CTkLabel(hdr, text=mode_cfg["label"], font=("Segoe UI", 9, "bold"),
                     fg_color="#1E3A5F", text_color="#7DD3FC",
                     corner_radius=4, height=20, padx=6).pack(side="left", padx=(8, 0))
        alerts_on = bool(inc.get("temp_alerts_enabled", 1))
        ctk.CTkButton(
            hdr, text="🔔 Alerts" if alerts_on else "🔕 Alerts Off",
            font=("Segoe UI", 9, "bold"),
            fg_color="#1C3A1C" if alerts_on else "#3A1C1C",
            hover_color=CARD2,
            text_color=GREEN if alerts_on else "#EF4444",
            corner_radius=4, height=20, width=90,
            command=lambda i=inc: self._toggle_temp_alerts(i)
        ).pack(side="left", padx=(6, 0))
        lbl_dot = _label(hdr, "●", ("Segoe UI", 18), dot_color)
        lbl_dot.pack(side="right")

        # ── Large temp / humidity boxes (with per-mode goals) ──
        unit_g          = db.get_setting("temp_unit", "C")
        goal_t, goal_h  = db.get_mode_goals(inc.get("temp_mode", "incubation"))

        sensor_row = ctk.CTkFrame(card, fg_color="transparent")
        sensor_row.pack(fill="x", padx=12, pady=(0, 8))
        sensor_row.columnconfigure(0, weight=1)
        sensor_row.columnconfigure(1, weight=1)

        tf = ctk.CTkFrame(sensor_row, fg_color=CARD2, corner_radius=8)
        tf.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        _label(tf, "Temp", FONT_S, SUBTEXT).pack(pady=(8, 0))
        lbl_temp = _label(tf, t_str if temp_c is not None else "—", ("Segoe UI", 22, "bold"), t_col)
        lbl_temp.pack(pady=(2, 0))
        _label(tf, f"Goal {calc.format_temp(goal_t, unit_g)}" if goal_t is not None else "—",
               FONT_S, SUBTEXT).pack(pady=(0, 8))

        hf = ctk.CTkFrame(sensor_row, fg_color=CARD2, corner_radius=8)
        hf.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        _label(hf, "Humidity", FONT_S, SUBTEXT).pack(pady=(8, 0))
        lbl_hum = _label(hf, f"{hum:.0f}%" if hum is not None else "—", ("Segoe UI", 22, "bold"), h_col)
        lbl_hum.pack(pady=(2, 0))
        _label(hf, f"Goal {goal_h:.0f}%" if goal_h is not None else "—",
               FONT_S, SUBTEXT).pack(pady=(0, 8))

        # ── Bottom row: status/events left, tray info right ──
        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(0, 6))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        # Left: last poll time + next event
        left = ctk.CTkFrame(bottom, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        _poll_txt, _poll_col = _poll_age(reading.get("timestamp"), POLL_INTERVAL_SEC)
        lbl_ts = _label(left, f"Last polled: {_poll_txt}", FONT_S, _poll_col)
        lbl_ts.pack(anchor="w")
        self._card_widgets[inc["id"]] = {"temp": lbl_temp, "hum": lbl_hum,
                                          "dot": lbl_dot, "ts": lbl_ts, "inc": inc}
        batches = db.get_batches(incubator_id=inc["id"], status="active")
        events  = calc.get_all_events(batches, lookahead_days=14)
        if events:
            ev   = events[0]
            ecol = RED if ev["urgent"] else (ORANGE if ev["days_away"] <= 5 else TEXT)
            _label(left, f"→ {ev['label']}: {calc.format_days(ev['days_away'])}", FONT_S, ecol).pack(anchor="w")
        else:
            _label(left, "No upcoming events", FONT_S, SUBTEXT).pack(anchor="w")

        # Right: tray count + fill % — aggregate query, no full row fetch
        right = ctk.CTkFrame(bottom, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        _ts    = db.get_tray_stats(incubator_id=inc["id"], status=db.IN_INCUBATOR_STATUSES)
        capacity   = inc.get("capacity") or 50
        fill_pct   = round(_ts["count"] / capacity * 100) if capacity else 0
        _label(right, f"{_ts['count']} / {capacity} trays", FONT_S, TEXT).pack(anchor="e")
        _label(right, f"{fill_pct}% filled  •  {_ts['total_gals']:.1f} gal", FONT_S, SUBTEXT).pack(anchor="e")

        # ── Inspection badges (hidden when the incubator is off — no inspections needed) ──
        if not is_hidden and not calc.is_off(inc):
            brow = ctk.CTkFrame(card, fg_color="transparent")
            brow.pack(fill="x", padx=12, pady=(2, 10))
            _label(brow, "Inspections:", FONT_S, SUBTEXT).pack(side="left", padx=(2, 6))
            make_status_badges(
                brow, inc["id"],
                on_click=lambda p, i=inc: self._open_inspection_form(i)).pack(side="left")
        else:
            # Hidden / off cards just need bottom padding
            ctk.CTkFrame(card, fg_color="transparent", height=6).pack()

        # Whole card is clickable — navigates to detail.
        # Buttons (e.g. inspection pills) keep their own command and are skipped.
        def _go_detail(event, i=inc):
            self._show_inc_detail(i)

        def _bind_recursive(widget):
            if isinstance(widget, ctk.CTkButton):
                return
            widget.bind("<Button-1>", _go_detail)
            for child in widget.winfo_children():
                _bind_recursive(child)

        _bind_recursive(card)

        return card

    # ══════════════════════════════════════════════════════════════════════════
    #  INCUBATORS VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_incubators_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Incubators", FONT_H, GOLD).pack(side="left")
        _btn(hdr, "+ Add Incubator", lambda: self._open_incubator_dialog(),
             fg=CARD, hover=CARD2, width=140).pack(side="right")

        self._inc_scroll = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", corner_radius=0)
        self._inc_scroll.pack(fill="both", expand=True, padx=12, pady=4)
        return frame

    def _refresh_incubators(self):
        container = self._inc_scroll
        for w in container.winfo_children():
            w.destroy()

        incubators = db.get_incubators(include_hidden=True)
        if not incubators:
            _label(container, "No incubators yet.", FONT_B, SUBTEXT).pack(pady=20)
            return

        for inc in incubators:
            hidden    = bool(inc.get("is_hidden"))
            row_bg    = "#161E2C" if hidden else CARD
            bdr_col   = "#222D3D" if hidden else BORDER
            name_col  = SUBTEXT  if hidden else GOLD

            row = ctk.CTkFrame(container, fg_color=row_bg, corner_radius=10,
                               border_width=1, border_color=bdr_col)
            row.pack(fill="x", padx=4, pady=4)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="both", expand=True, padx=14, pady=10)

            # Name + hidden badge
            name_row = ctk.CTkFrame(left, fg_color="transparent")
            name_row.pack(anchor="w", fill="x")
            _label(name_row, inc["name"], FONT_H, name_col).pack(side="left")
            if hidden:
                ctk.CTkLabel(name_row, text="  HIDDEN  ",
                             fg_color="#374151", text_color=SUBTEXT,
                             corner_radius=4, font=("Segoe UI", 9, "bold"),
                             height=20).pack(side="left", padx=8)

            reading = self._govee.get_last(inc["id"])
            if not reading:
                db_row  = db.get_latest_reading(inc["id"])
                reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"], "timestamp": db_row["timestamp"]} if db_row else {}
            temp_c  = reading.get("temp_c")
            if temp_c is not None:
                unit  = db.get_setting("temp_unit", "C")
                t_str = calc.format_temp(temp_c, unit)
                hum   = reading.get("humidity", 0)
                _label(left, f"🌡 {t_str}   💧 {hum:.0f}%", FONT_B, TEXT).pack(anchor="w")
            else:
                _label(left, "No sensor reading", FONT_B, SUBTEXT).pack(anchor="w")

            _mode_key = inc.get("temp_mode", "incubation")
            _mode_cfg = calc.TEMP_MODES.get(_mode_key, calc.TEMP_MODES["incubation"])
            if _mode_cfg["min"] is None:
                _range_txt = f"{_mode_cfg['label']} (no alerts)"
            else:
                _range_txt = f"{_mode_cfg['label']}: {_mode_cfg['min']}–{_mode_cfg['max']}°C"
            info_txt = f"Capacity: {inc.get('capacity',50)} trays  |  {_range_txt}"
            _label(left, info_txt, FONT_S, SUBTEXT).pack(anchor="w")

            # Per-mode temperature / humidity goals
            _gt, _gh = db.get_mode_goals(_mode_key)
            if _gt is not None or _gh is not None:
                _ug = db.get_setting("temp_unit", "C")
                _bits = []
                if _gt is not None: _bits.append(f"🌡 {calc.format_temp(_gt, _ug)}")
                if _gh is not None: _bits.append(f"💧 {_gh:.0f}%")
                _label(left, "Goal:  " + "    ".join(_bits), FONT_S, SUBTEXT).pack(anchor="w")

            govee_txt = (f"Govee: {inc.get('govee_device_id') or 'not set'}  "
                         f"({inc.get('govee_sku') or '—'})")
            _label(left, govee_txt, FONT_S, SUBTEXT).pack(anchor="w")

            # Temp-mode selector — turn the incubator on/off and pick its mode here
            mode_row = ctk.CTkFrame(left, fg_color="transparent")
            mode_row.pack(anchor="w", pady=(6, 0))
            _label(mode_row, "Temp Mode:", FONT_S, SUBTEXT).pack(side="left", padx=(0, 6))
            _mode_var = ctk.StringVar(value=_mode_cfg["label"])
            ctk.CTkSegmentedButton(
                mode_row,
                values=[v["label"] for v in calc.TEMP_MODES.values()],
                variable=_mode_var,
                command=lambda label, iid=inc["id"]: self._set_inc_mode(iid, label),
                height=26, font=FONT_S,
            ).pack(side="left")

            # Inspection status badges (only for visible, non-off incubators)
            if not hidden and not calc.is_off(inc):
                ibrow = ctk.CTkFrame(left, fg_color="transparent")
                ibrow.pack(anchor="w", pady=(4, 0))
                _label(ibrow, "Inspections:", FONT_S, SUBTEXT).pack(side="left", padx=(0, 6))
                make_status_badges(ibrow, inc["id"]).pack(side="left")

            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=14, pady=10)

            if hidden:
                _btn(right, "Unhide",
                     lambda i=inc["id"]: self._set_hidden(i, False),
                     width=80, height=28, fg="#065F46", hover=TEAL,
                     text_color="white").pack(pady=2)
            else:
                if not calc.is_off(inc):
                    _btn(right, "Inspect",
                         lambda i=inc: self._open_inspection_form(i),
                         width=80, height=28, fg=BLUE, hover="#1D4ED8",
                         text_color="white").pack(pady=2)
                _btn(right, "Hide",
                     lambda i=inc["id"]: self._set_hidden(i, True),
                     width=80, height=28, fg=CARD2, hover=BORDER,
                     text_color=SUBTEXT).pack(pady=2)

            _btn(right, "Edit",
                 lambda i=inc: self._open_incubator_dialog(i),
                 width=80, height=28, fg=BORDER, hover=CARD2).pack(pady=2)
            _btn(right, "+ Batch",
                 lambda i=inc["id"]: self._open_batch_dialog(incubator_id=i),
                 width=80, height=28, fg=BORDER, hover=CARD2).pack(pady=2)
            _btn(right, "Delete",
                 lambda i=inc["id"]: self._delete_incubator(i),
                 width=80, height=28, fg="#4B0000", hover=RED).pack(pady=2)

            # Batches sub-list
            batches = db.get_batches(incubator_id=inc["id"])
            if batches:
                bf = ctk.CTkFrame(row, fg_color=CARD2, corner_radius=6)
                bf.pack(fill="x", padx=12, pady=(0, 10))
                for batch in batches:
                    br = ctk.CTkFrame(bf, fg_color="transparent")
                    br.pack(fill="x", padx=8, pady=2)
                    bname = batch.get("name") or f"Batch {batch['id']}"
                    bstat = batch.get("status", "active")
                    scol  = GREEN if bstat == "active" else SUBTEXT
                    _label(br, f"● {bname}", FONT_S, scol).pack(side="left")
                    sd = batch.get("start_date") or "—"
                    _label(br, f"Start: {sd}", FONT_S, SUBTEXT).pack(side="left", padx=12)
                    _btn(br, "Edit",
                         lambda b=batch: self._open_batch_dialog(batch=b),
                         width=60, height=22, fg=BORDER, hover=CARD).pack(side="right")

    # ══════════════════════════════════════════════════════════════════════════
    #  SAMPLES VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_samples_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Samples & X-Ray Results", FONT_H, GOLD).pack(side="left")
        _btn(hdr, "Import Spreadsheet", self._import_xray,
             fg=BLUE, hover="#1D4ED8", text_color="white", width=160).pack(side="right")
        _btn(hdr, "+ Add Sample", lambda: self._open_sample_dialog(),
             fg=CARD, hover=CARD2, width=130).pack(side="right", padx=6)

        cols = ("Name", "Total Lbs", "Live Bees/Lb", "Parasites", "Chalkbrood",
                "Total Gal", "Lbs for 2gal", "Total Trays", "Inc. Space", "Notes")
        self._smp_tree = self._make_tree(frame, cols)
        self._smp_tree.pack(fill="both", expand=True, padx=12, pady=4)
        self._smp_tree.bind("<Double-1>", self._on_sample_double_click)
        return frame

    def _refresh_samples(self):
        tree = self._smp_tree
        tree.delete(*tree.get_children())

        def _n(v, dec=1):
            return f"{v:,.{dec}f}" if isinstance(v, (int, float)) else "—"

        for s in db.get_samples():
            tree.insert("", "end", iid=str(s["id"]), values=(
                s["name"],
                _n(s.get("total_weight_lbs")),
                _n(s.get("live_bees_per_lb"), 0),
                _n(s.get("parasites")),
                _n(s.get("chalkbrood")),
                _n(s.get("total_volume_gal")),
                _n(s.get("lbs_per_2gal"), 2),
                _n(s.get("total_trays"), 0),
                s.get("incubator_space") or "—",
                s.get("notes") or "",
            ))

    def _on_sample_double_click(self, event):
        sel = self._smp_tree.selection()
        if not sel:
            return
        sid = int(sel[0])
        samples = db.get_samples()
        sample  = next((s for s in samples if s["id"] == sid), None)
        if sample:
            self._open_sample_dialog(sample)

    # ══════════════════════════════════════════════════════════════════════════
    #  TRAYS VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_trays_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Trays", FONT_H, GOLD).pack(side="left")
        _btn(hdr, "+ Add Tray", lambda: self._open_tray_dialog(),
             fg=DK_GOLD, hover=GOLD, text_color="black", width=110).pack(side="right")
        _btn(hdr, "🗑 Delete All", self._delete_all_trays,
             fg=RED, hover="#B91C1C", text_color="white", width=110).pack(side="right", padx=6)
        _btn(hdr, "Set Status →", self._bulk_set_status,
             fg=BLUE, hover="#1D4ED8", text_color="white", width=110).pack(side="right", padx=(6, 0))
        self._bulk_status = _combo(hdr, [lbl for _v, lbl in db.TRAY_STATUS_OPTIONS], 120)
        self._bulk_status.set("Released")
        self._bulk_status.pack(side="right", padx=6)
        _btn(hdr, "QR Code", self._show_selected_qr,
             fg=CARD, hover=CARD2, width=90).pack(side="right", padx=6)
        _btn(hdr, "History", self._show_tray_history,
             fg=CARD, hover=CARD2, width=90).pack(side="right", padx=6)
        _btn(hdr, "Release CSV", lambda: self._release_csv_import(on_complete=self._refresh_trays),
             fg="#7C3AED", hover="#6D28D9", text_color="white", width=110).pack(side="right", padx=6)
        _btn(hdr, "⬇ Release Template", lambda: self._release_csv_template(),
             fg=BORDER, hover=CARD2, width=150).pack(side="right", padx=(0, 2))
        _btn(hdr, "Import CSV", lambda: self._tray_csv_import(on_complete=self._refresh_trays),
             fg=TEAL, hover="#0D9488", text_color="white", width=100).pack(side="right", padx=6)
        _btn(hdr, "⬇ Template", lambda: self._tray_csv_template(),
             fg=BORDER, hover=CARD2, width=110).pack(side="right", padx=(0, 2))

        # Filter bar
        fbar = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=8)
        fbar.pack(fill="x", padx=12, pady=(0, 6))
        _label(fbar, "Filter:", FONT_S, SUBTEXT).pack(side="left", padx=10, pady=6)

        incubators  = db.get_incubators()
        self._flt_inc_map = {"All Incubators": None}
        self._flt_inc_map.update({i["name"]: i["id"] for i in incubators})
        self._flt_inc = _combo(fbar, list(self._flt_inc_map.keys()), 180)
        self._flt_inc.set("All Incubators")
        self._flt_inc.pack(side="left", padx=6, pady=6)
        self._flt_inc.configure(command=lambda _: self._refresh_trays())

        self._flt_status = _combo(fbar, ["All"] + [lbl for _v, lbl in db.TRAY_STATUS_OPTIONS], 130)
        self._flt_status.set("All")
        self._flt_status.pack(side="left", padx=6, pady=6)
        self._flt_status.configure(command=lambda _: self._refresh_trays())

        # Cool-down summary for the currently-shown trays
        self._cooldown_lbl = _label(fbar, "", FONT_S, TEAL)
        self._cooldown_lbl.pack(side="right", padx=12, pady=6)

        cols = ("Tray #", "Sample", "Incubator", "Batch",
                "Weight (lbs)", "Volume (gal)", "Live Count",
                "Parasite %", "In Date", "Out Date", "Cool Days", "Status")
        self._tray_tree = self._make_tree(frame, cols)
        self._tray_tree.pack(fill="both", expand=True, padx=12, pady=4)
        self._tray_tree.bind("<Double-1>", self._on_tray_double_click)
        self._tray_sort_col = None   # currently sorted column index
        self._tray_sort_asc = True

        for ci, col in enumerate(cols):
            self._tray_tree.heading(col, text=col,
                command=lambda c=ci: self._sort_tray_tree(c))

        return frame

    def _sort_tray_tree(self, col_idx: int):
        tree = self._tray_tree
        if self._tray_sort_col == col_idx:
            self._tray_sort_asc = not self._tray_sort_asc
        else:
            self._tray_sort_col = col_idx
            self._tray_sort_asc = True

        # Update heading arrows
        cols = ("Tray #", "Sample", "Incubator", "Batch",
                "Weight (lbs)", "Volume (gal)", "Live Count",
                "Parasite %", "In Date", "Out Date", "Cool Days", "Status")
        for ci, col in enumerate(cols):
            arrow = (" ↑" if self._tray_sort_asc else " ↓") if ci == col_idx else ""
            tree.heading(col, text=col + arrow,
                command=lambda c=ci: self._sort_tray_tree(c))

        rows = [(tree.set(iid, cols[col_idx]), iid) for iid in tree.get_children()]

        def _sort_key(val):
            # Try numeric sort for number columns, fallback to string
            try:
                return (0, float(val.replace("%", "").replace("—", ""))) if val != "—" else (1, "")
            except (ValueError, AttributeError):
                return (0, val.lower()) if val != "—" else (1, "")

        rows.sort(key=lambda x: _sort_key(x[0]), reverse=not self._tray_sort_asc)
        for idx, (_, iid) in enumerate(rows):
            tree.move(iid, "", idx)

    def _refresh_trays(self):
        tree = self._tray_tree
        tree.delete(*tree.get_children())
        # Reset sort state on refresh
        self._tray_sort_col = None
        self._tray_sort_asc = True
        cols = ("Tray #", "Sample", "Incubator", "Batch",
                "Weight (lbs)", "Volume (gal)", "Live Count",
                "Parasite %", "In Date", "Out Date", "Cool Days", "Status")
        for col in cols:
            tree.heading(col, text=col,
                command=lambda c=cols.index(col): self._sort_tray_tree(c))

        inc_id = self._flt_inc_map.get(self._flt_inc.get())
        _flt = self._flt_status.get()
        status = None if _flt == "All" else db.tray_status_value(_flt)

        trays = db.get_trays(incubator_id=inc_id, status=status)
        # Build all row tuples first, then insert in one pass
        rows = [
            (str(t["id"]), (
                t["tray_number"],
                t.get("sample_name") or "—",
                t.get("incubator_name") or "—",
                t.get("batch_name") or "—",
                f"{t['weight_lbs']:.2f}" if t.get("weight_lbs") else "—",
                f"{t['volume_gal']:.2f}" if t.get("volume_gal") else "—",
                t.get("live_count") or "—",
                f"{t['parasite_level_pct']:.1f}%" if t.get("parasite_level_pct") else "—",
                t.get("in_date") or "—",
                t.get("out_date") or "—",
                (lambda d: f"{d}d" if d is not None else "—")(cool_down_days(t)),
                db.tray_status_label(t.get("status") or "active"),
            ))
            for t in trays
        ]
        for iid, values in rows:
            tree.insert("", "end", iid=iid, values=values)

        # Cool-down report: average over shown trays that have a cool-down value
        _durs = [d for d in (cool_down_days(t) for t in trays) if d is not None]
        if _durs:
            self._cooldown_lbl.configure(
                text=f"Cool-down: avg {sum(_durs)/len(_durs):.1f}d  "
                     f"(min {min(_durs)} · max {max(_durs)}, n={len(_durs)})")
        else:
            self._cooldown_lbl.configure(text="")

    def _on_tray_double_click(self, event):
        sel = self._tray_tree.selection()
        if not sel:
            return
        tid = int(sel[0])
        tray = db.get_tray_by_id(tid)
        if tray:
            self._open_tray_dialog(tray=tray)

    def _show_selected_qr(self):
        sel = self._tray_tree.selection()
        if not sel:
            messagebox.showinfo("QR Code", "Select a tray first.")
            return
        tray = db.get_tray_by_id(int(sel[0]))
        if tray:
            QRDialog(self, tray, port=self._qr_port)

    def _show_tray_history(self):
        """Show every season's record for the selected tray number."""
        sel = self._tray_tree.selection()
        if not sel:
            messagebox.showinfo("History", "Select a tray first.", parent=self)
            return
        tray = db.get_tray_by_id(int(sel[0]))
        if not tray:
            return
        tray_number = tray["tray_number"]
        history = db.get_tray_history(tray_number)

        win = ctk.CTkToplevel(self)
        win.title(f"History — {tray_number}")
        win.geometry("760x420")
        win.minsize(640, 320)
        win.grab_set()

        _label(win, f"Tray {tray_number} — {len(history)} record(s)",
               FONT_H, GOLD).pack(anchor="w", padx=16, pady=(14, 6))

        cols = ("Status", "Sample", "Incubator", "In Date", "Out Date", "Notes")
        tree = self._make_tree(win, cols)
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        tree.column("Notes", width=240, anchor="w")

        for h in history:  # newest first (get_tray_history orders by id DESC)
            note = (h.get("notes") or "").replace("\n", "  •  ")
            tree.insert("", "end", values=(
                h.get("status") or "active",
                h.get("sample_name")    or "—",
                h.get("incubator_name") or "—",
                h.get("in_date")  or "—",
                h.get("out_date") or "—",
                note or "—",
            ))

    # ══════════════════════════════════════════════════════════════════════════
    #  TIMELINE VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_timeline_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Upcoming Timeline", FONT_H, GOLD).pack(side="left")

        self._tl_days_var = ctk.StringVar(value="30")
        _label(hdr, "Lookahead:", FONT_S, SUBTEXT).pack(side="right", padx=(6,2))
        tl_spin = _combo(hdr, ["7", "14", "30", "60", "90"], 80)
        tl_spin.set("30")
        tl_spin.pack(side="right", padx=4)
        tl_spin.configure(command=lambda _: self._refresh_timeline())
        self._tl_spin = tl_spin

        self._tl_scroll = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", corner_radius=0)
        self._tl_scroll.pack(fill="both", expand=True, padx=12, pady=4)
        return frame

    def _refresh_timeline(self):
        container = self._tl_scroll
        for w in container.winfo_children():
            w.destroy()

        try:
            lookahead = int(self._tl_spin.get())
        except Exception:
            lookahead = 30

        batches = db.get_batches(status="active")
        events  = calc.get_all_events(batches, lookahead)

        if not events:
            _label(container,
                   "No upcoming events in the next "
                   f"{lookahead} days.\n"
                   "Add batches with dates in the Incubators view.",
                   FONT_B, SUBTEXT).pack(pady=40)
            return

        current_date = None
        for ev in events:
            # Date divider
            if ev["date"] != current_date:
                current_date = ev["date"]
                div = ctk.CTkFrame(container, fg_color=BORDER, height=1)
                div.pack(fill="x", pady=(10, 0))
                day_txt = f"  {current_date}  —  {calc.format_days(ev['days_away'])}  "
                _label(container, day_txt, FONT_S, SUBTEXT).pack(anchor="w", padx=4)

            row = ctk.CTkFrame(container, fg_color=CARD, corner_radius=8)
            row.pack(fill="x", padx=4, pady=2)

            col = RED if ev["urgent"] else (ORANGE if ev["days_away"] <= 5 else GREEN)
            _label(row, f"●  {ev['label']}", FONT_B, col).pack(
                side="left", padx=14, pady=8)
            sub = f"{ev['incubator_name']}  ·  {ev['batch_name']}"
            _label(row, sub, FONT_S, SUBTEXT).pack(side="left", padx=4)
            _label(row, ev["date"], FONT_S, SUBTEXT).pack(side="right", padx=14)

    # ══════════════════════════════════════════════════════════════════════════
    #  SETTINGS VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_settings_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Settings", FONT_H, GOLD).pack(side="left")
        _btn(hdr, "Save Settings", self._save_settings,
             fg=DK_GOLD, hover=GOLD, text_color="black", width=140).pack(side="right")

        scroll = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=4)

        def section(title):
            f = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10)
            f.pack(fill="x", padx=4, pady=(8, 2))
            _label(f, title, FONT_H, GOLD).pack(anchor="w", padx=14, pady=(10, 4))
            g = ctk.CTkFrame(f, fg_color="transparent")
            g.pack(fill="x", padx=14, pady=(0, 12))
            g.columnconfigure(1, weight=1)
            return g

        # Govee
        gf = section("Govee API")
        self._set = {}
        r0 = _FormRow(gf, 0, "API Key", "Paste Govee API key here", 360)
        self._set["govee_api_key"] = r0
        _btn(gf, "Test Connection", self._test_govee, fg=BLUE, hover="#1D4ED8",
             text_color="white", width=150).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        self._govee_status_lbl = _label(gf, "", FONT_S, SUBTEXT)
        self._govee_status_lbl.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        # Background Poller
        bf = section("Background Poller")
        _label(bf,
               "Run a lightweight background task that collects Govee readings\n"
               "even when this window is closed.  Enable it on one computer only\n"
               "— the one that stays on — to avoid duplicate writes.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 6))
        btn_row = ctk.CTkFrame(bf, fg_color="transparent")
        btn_row.pack(anchor="w", padx=14, pady=(0, 4))
        _btn(btn_row, "Enable on this computer",  self._enable_poller,
             fg=GREEN, hover="#15803D", text_color="white", width=200).pack(side="left", padx=(0, 8))
        _btn(btn_row, "Disable on this computer", self._disable_poller,
             fg=RED,   hover="#991B1B", text_color="white", width=200).pack(side="left")
        self._poller_status_lbl = _label(bf, "", FONT_S, SUBTEXT)
        self._poller_status_lbl.pack(anchor="w", padx=14, pady=(0, 8))
        self._refresh_poller_status()

        # Poll interval is fixed at 15 minutes (not user-configurable)
        pf = section("Polling & Thresholds")
        _label(pf, "Govee poll interval: every 15 minutes (fixed)",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=2,
                                     sticky="w", padx=4, pady=4)
        self._set["date_alert_lookahead"] = _FormRow(pf, 1, "Date Alert Lookahead (days)", "7", 100)
        self._set["temp_unit"] = _FormRow(pf, 2, "Temp Unit",
            widget=_combo(pf, ["C", "F"], 80))

        # Temperature Mode Goals — target temp/humidity per mode
        tmg = section("Temperature Mode Goals")
        _label(tmg,
               "Target temperature (°C) and humidity (%) for each mode. Shown on\n"
               "incubator tiles and drawn as dotted goal lines on the charts.",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 6))
        _label(tmg, "Mode",       FONT_S, SUBTEXT).grid(row=1, column=0, sticky="w", padx=4)
        _label(tmg, "Temp °C",    FONT_S, SUBTEXT).grid(row=1, column=1, sticky="w", padx=4)
        _label(tmg, "Humidity %", FONT_S, SUBTEXT).grid(row=1, column=2, sticky="w", padx=4)
        self._goal_entries = {}
        for _ri, _mk in enumerate(calc.ACTIVE_MODES, start=2):
            _gt, _gh = db.get_mode_goals(_mk)
            _label(tmg, calc.TEMP_MODES[_mk]["label"], FONT_B, TEXT).grid(
                row=_ri, column=0, sticky="w", padx=4, pady=3)
            _te = ctk.CTkEntry(tmg, width=80, fg_color=CARD2,
                               border_color=BORDER, text_color=TEXT)
            _te.grid(row=_ri, column=1, sticky="w", padx=4, pady=3)
            _te.insert(0, "" if _gt is None else f"{_gt:g}")
            _he = ctk.CTkEntry(tmg, width=80, fg_color=CARD2,
                               border_color=BORDER, text_color=TEXT)
            _he.grid(row=_ri, column=2, sticky="w", padx=4, pady=3)
            _he.insert(0, "" if _gh is None else f"{_gh:g}")
            self._goal_entries[_mk] = (_te, _he)

        # Calculation
        cf = section("Weight Calculations")
        self._set["lbs_per_gal"] = _FormRow(cf, 0, "Lbs per Gallon (raw cells)", "2.2", 100)
        self._set["target_gals_per_tray"] = _FormRow(cf, 1, "Target Gals per Tray", "2.0", 100)

        # QR Server
        qf = section("QR Scan Server")
        self._set["qr_server_port"] = _FormRow(qf, 0, "Port", "5151", 100)
        self._set["qr_server_enabled"] = _FormRow(qf, 1, "Enabled (1=yes, 0=no)", "1", 60)
        self._qr_ip_lbl = _label(qf, "", FONT_S, BLUE)
        self._qr_ip_lbl.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        # Mobile app section
        mf = section("Mobile App")
        self._set["mobile_passcode"] = _FormRow(
            mf, 0, "Shared passcode", "", 200)
        _label(mf, "Set a passcode before sharing the app online (Tailscale).\n"
                   "Leave blank to disable the login (local network only).",
               FONT_S, SUBTEXT).grid(row=1, column=0, columnspan=2,
                                     sticky="w", padx=4, pady=(2, 4))

        # ── Data Storage ──────────────────────────────────────────────────────
        dsf = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10)
        dsf.pack(fill="x", padx=4, pady=(8, 2))
        _label(dsf, "Data Storage", FONT_H, GOLD).pack(
            anchor="w", padx=14, pady=(10, 2))
        _label(dsf,
               "Move the database to your Google Drive folder so data syncs\n"
               "between computers automatically. The app restarts after saving.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 8))

        dsg = ctk.CTkFrame(dsf, fg_color=CARD2, corner_radius=8)
        dsg.pack(fill="x", padx=14, pady=(0, 14))

        # Current path row
        path_row = ctk.CTkFrame(dsg, fg_color="transparent")
        path_row.pack(fill="x", padx=12, pady=(10, 4))
        _label(path_row, "Current database:", FONT_S, SUBTEXT).pack(side="left")
        self._db_path_lbl = _label(path_row, db.DB_PATH, FONT_S, BLUE)
        self._db_path_lbl.pack(side="left", padx=(8, 0))

        # New path entry row
        new_row = ctk.CTkFrame(dsg, fg_color="transparent")
        new_row.pack(fill="x", padx=12, pady=(4, 10))
        _label(new_row, "Move database to:", FONT_S, SUBTEXT).pack(side="left")
        self._db_folder_entry = ctk.CTkEntry(
            new_row, placeholder_text="Paste or browse to a folder…",
            fg_color=CARD, border_color=BORDER, text_color=TEXT, width=320)
        self._db_folder_entry.pack(side="left", padx=(8, 6))
        _btn(new_row, "Browse", self._browse_db_folder,
             width=80, height=28, fg=BORDER, hover=CARD2).pack(side="left")

        # Status / hint
        self._db_move_status = _label(dsg, "", FONT_S, SUBTEXT)
        self._db_move_status.pack(anchor="w", padx=12, pady=(0, 4))

        # Action buttons
        act_row = ctk.CTkFrame(dsg, fg_color="transparent")
        act_row.pack(fill="x", padx=12, pady=(0, 10))
        _btn(act_row, "Move & Restart", self._move_database,
             fg=DK_GOLD, hover=GOLD, text_color="black",
             width=140, height=30).pack(side="left", padx=(0, 8))
        _btn(act_row, "Use Local File (default)", self._use_local_db,
             fg=BORDER, hover=CARD2, width=180, height=30).pack(side="left")

        # ── Email Reports ─────────────────────────────────────────────────
        ef = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10)
        ef.pack(fill="x", padx=4, pady=(8, 2))
        _label(ef, "Email Reports", FONT_H, GOLD).pack(
            anchor="w", padx=14, pady=(10, 2))
        _label(ef,
               "A daily summary is sent at 7 PM with 24h temps, inspections, batch progress and "
               "tomorrow's calendar.\nFor Gmail: use an App Password (Google Account › Security › "
               "2-Step Verification › App passwords).",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 8))

        eg = ctk.CTkFrame(ef, fg_color=CARD2, corner_radius=8)
        eg.pack(fill="x", padx=14, pady=(0, 14))
        eg.columnconfigure(1, weight=1)

        self._set["smtp_host"]     = _FormRow(eg, 0, "SMTP Host",     "smtp.gmail.com", 220)
        self._set["smtp_port"]     = _FormRow(eg, 1, "SMTP Port",     "587",            80)
        self._set["smtp_tls"]      = _FormRow(eg, 2, "Use TLS (1/0)", "1",              60)
        self._set["smtp_username"] = _FormRow(eg, 3, "Username / Email", "you@gmail.com", 260)
        self._set["smtp_password"] = _FormRow(eg, 4, "Password / App Password", "••••••••", 260)
        self._set["smtp_from"]     = _FormRow(eg, 5, "From Address (optional)", "Incubation App <you@gmail.com>", 300)

        # Recipients box
        _label(eg, "Recipients\n(one per line)", FONT_S, SUBTEXT).grid(
            row=6, column=0, sticky="ne", padx=(14, 8), pady=8)
        self._email_recip_box = ctk.CTkTextbox(
            eg, height=90, fg_color=CARD, border_color=BORDER,
            text_color=TEXT, font=FONT_S, corner_radius=6)
        self._email_recip_box.grid(row=6, column=1, sticky="ew", padx=(0, 14), pady=8)

        # Test + status
        email_btn_row = ctk.CTkFrame(eg, fg_color="transparent")
        email_btn_row.grid(row=7, column=1, sticky="w", padx=(0, 14), pady=(0, 10))
        self._email_status_lbl = _label(email_btn_row, "", FONT_S, SUBTEXT)
        _btn(email_btn_row, "Send Test Email", self._send_test_email,
             fg=BLUE, hover="#1D4ED8", text_color="white",
             width=150, height=30).pack(side="left", padx=(0, 10))
        self._email_status_lbl.pack(side="left")

        return frame

    def _refresh_settings(self):
        keys = ["govee_api_key", "date_alert_lookahead",
                "temp_unit", "lbs_per_gal", "target_gals_per_tray",
                "qr_server_port", "qr_server_enabled", "mobile_passcode",
                "smtp_host", "smtp_port", "smtp_tls",
                "smtp_username", "smtp_password", "smtp_from"]
        for k in keys:
            if k in self._set:
                self._set[k].set(db.get_setting(k))
        self._qr_ip_lbl.configure(
            text=f"Phone scan URL: http://{qr_server.get_local_ip()}:{self._qr_port}/tray/<id>")
        # Recipients text box
        recip_val = db.get_setting("email_recipients", "")
        self._email_recip_box.delete("1.0", "end")
        if recip_val:
            self._email_recip_box.insert("1.0", recip_val)
        # Reload per-mode goals (in case another computer changed them)
        for _mk, (_te, _he) in getattr(self, "_goal_entries", {}).items():
            _gt, _gh = db.get_mode_goals(_mk)
            _te.delete(0, "end"); _te.insert(0, "" if _gt is None else f"{_gt:g}")
            _he.delete(0, "end"); _he.insert(0, "" if _gh is None else f"{_gh:g}")

    def _save_settings(self):
        keys = ["govee_api_key", "date_alert_lookahead",
                "temp_unit", "lbs_per_gal", "target_gals_per_tray",
                "qr_server_port", "qr_server_enabled", "mobile_passcode",
                "smtp_host", "smtp_port", "smtp_tls",
                "smtp_username", "smtp_password", "smtp_from"]
        for k in keys:
            if k in self._set:
                db.set_setting(k, self._set[k].get())
        # Save recipients
        recip_text = self._email_recip_box.get("1.0", "end").strip()
        db.set_setting("email_recipients", recip_text)
        # Per-mode temperature/humidity goals
        for _mk, (_te, _he) in getattr(self, "_goal_entries", {}).items():
            db.set_mode_goals(_mk, _te.get().strip(), _he.get().strip())
        # Update govee key live
        self._govee.set_api_key(db.get_setting("govee_api_key"))
        messagebox.showinfo("Settings", "Settings saved.", parent=self)

    # ── Data storage helpers ──────────────────────────────────────────────────

    def _browse_db_folder(self):
        folder = filedialog.askdirectory(
            title="Choose a folder for the database "
                  "(e.g. your Google Drive folder)",
            mustexist=True,
        )
        if folder:
            self._db_folder_entry.delete(0, "end")
            self._db_folder_entry.insert(0, folder)
            self._db_move_status.configure(
                text=f"Will save to: {folder}\\incubation.db",
                text_color=SUBTEXT)

    def _move_database(self):
        import shutil
        folder = self._db_folder_entry.get().strip()
        if not folder:
            self._db_move_status.configure(
                text="Please browse to or paste a folder path first.",
                text_color=ORANGE)
            return
        if not os.path.isdir(folder):
            self._db_move_status.configure(
                text="That folder doesn't exist. Create it first or pick a different one.",
                text_color=RED)
            return

        new_path = os.path.join(folder, "incubation.db")
        same_file = (os.path.exists(new_path) and
                     os.path.abspath(new_path) == os.path.abspath(db.DB_PATH))

        # If a database is already in the target folder (e.g. a shared Google
        # Drive folder another computer set up), let the user JOIN it instead of
        # overwriting — so a new computer never clobbers the shared data.
        if os.path.exists(new_path) and not same_file:
            choice = messagebox.askyesnocancel(
                "Database already exists there",
                f"A database already exists at:\n{new_path}\n\n"
                "Yes — replace it with THIS computer's current data\n"
                "No — keep the existing (shared) database and just use it here\n"
                "Cancel — do nothing",
                parent=self)
            if choice is None:          # Cancel
                return
            if choice:                  # Yes — overwrite with our data
                try:
                    shutil.copy2(db.DB_PATH, new_path)
                except Exception as exc:
                    self._db_move_status.configure(
                        text=f"Copy failed: {exc}", text_color=RED)
                    return
            # No — leave the existing file untouched and just adopt its path
        elif not same_file:
            # Empty target folder — copy our current data into it
            try:
                shutil.copy2(db.DB_PATH, new_path)
            except Exception as exc:
                self._db_move_status.configure(
                    text=f"Copy failed: {exc}", text_color=RED)
                return

        # Save new path to config so next launch uses it
        db.save_config({"db_path": new_path})

        messagebox.showinfo(
            "Done — please restart",
            f"The app will now use:\n{new_path}\n\n"
            "Close and reopen the app to start using this location.\n\n"
            "Tip: on another computer, install the app, then go to\n"
            "Settings ▸ Data Storage, browse to the same Google Drive folder,\n"
            "click 'Move & Restart', and choose 'No' to join the shared data.",
            parent=self)

    def _use_local_db(self):
        """Reset to default local file (remove any configured path)."""
        db.save_config({"db_path": ""})
        messagebox.showinfo(
            "Done — please restart",
            "Database location reset to the default (next to the app files).\n"
            "Restart the app for the change to take effect.",
            parent=self)

    # ── Background poller ──────────────────────────────────────────────────────

    _TASK_NAME   = "BeeIncubationPoller"
    _POLLER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "govee_poller.py")

    def _poller_installed(self) -> bool:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run")
            winreg.QueryValueEx(key, self._TASK_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False

    def _refresh_poller_status(self):
        import json, socket
        installed = self._poller_installed()
        # Check lock file for active machine info
        lock_file = os.path.join(os.path.dirname(db.DB_PATH), "govee_poller.lock")
        lock_info = ""
        try:
            with open(lock_file, encoding="utf-8") as f:
                lock = json.load(f)
            lock_info = f"  |  Active on: {lock.get('machine')}  (last seen {lock.get('last_seen', '')[:19]})"
        except Exception:
            pass
        if installed:
            self._poller_status_lbl.configure(
                text=f"Enabled on this computer ({socket.gethostname()}){lock_info}",
                text_color=GREEN)
        else:
            self._poller_status_lbl.configure(
                text=f"Not enabled on this computer{lock_info}",
                text_color=SUBTEXT)

    def _kill_poller_processes(self):
        subprocess.run(
            ["wmic", "process", "where", "commandline like '%govee_poller%'", "delete"],
            capture_output=True, creationflags=_NO_WINDOW
        )

    def _enable_poller(self):
        import winreg
        self._kill_poller_processes()  # stop any existing instances first
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        cmd = f'"{pythonw}" "{self._POLLER_SCRIPT}"'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, self._TASK_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
        except Exception as exc:
            messagebox.showerror("Background Poller",
                f"Could not register startup entry:\n{exc}", parent=self)
            return
        # Start it immediately too
        subprocess.Popen([pythonw, self._POLLER_SCRIPT], creationflags=_NO_WINDOW)
        self._refresh_poller_status()
        messagebox.showinfo("Background Poller",
            "Poller enabled and started.\n\n"
            "It will run automatically each time you log in to Windows.\n"
            "Check govee_poller.log (next to incubation.db) for activity.",
            parent=self)

    def _disable_poller(self):
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, self._TASK_NAME)
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass
        except Exception as exc:
            messagebox.showerror("Background Poller",
                f"Could not remove startup entry:\n{exc}", parent=self)
            return
        # Kill all running govee_poller.py processes
        self._kill_poller_processes()
        self._refresh_poller_status()
        messagebox.showinfo("Background Poller",
            "Poller disabled and stopped on this computer.",
            parent=self)

    def _test_govee(self):
        key = self._set["govee_api_key"].get()
        if not key:
            self._govee_status_lbl.configure(text="Enter an API key first.", text_color=ORANGE)
            return
        self._govee_status_lbl.configure(text="Connecting…", text_color=SUBTEXT)
        self.update()

        tmp     = govee_mod.GoveeClient(api_key=key)
        devices = tmp.get_all_devices()

        if not devices:
            self._govee_status_lbl.configure(
                text=f"Connection failed: {tmp.status_label()}", text_color=RED)
            return

        db.set_setting("govee_api_key", key)
        self._govee.set_api_key(key)

        sensors = [d for d in devices if d.get("is_sensor")]
        others  = [d for d in devices if not d.get("is_sensor")]

        self._govee_status_lbl.configure(
            text=f"Connected — {len(sensors)} sensor(s), {len(others)} other device(s). "
                 f"See device list below.", text_color=GREEN)

        # Open a popup showing all devices with copy-ready IDs
        self._show_device_list(devices)

    def _show_device_list(self, devices: list):
        """
        Popup listing every Govee device found, clearly separated into
        Temperature Sensors and Other Devices.  Each row has copy buttons
        for Device ID and SKU so they can be pasted straight into Incubator settings.
        """
        win = ctk.CTkToplevel(self)
        win.title("Govee Devices Found")
        win.geometry("700x520")
        win.grab_set()

        _label(win, "Govee Devices", FONT_H, GOLD).pack(padx=16, pady=(14, 2), anchor="w")
        _label(win,
               "Sensors are highlighted. Copy the Device ID and SKU into each Incubator's settings.",
               FONT_S, SUBTEXT).pack(padx=16, anchor="w")

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=8)

        sensors = [d for d in devices if d.get("is_sensor")]
        others  = [d for d in devices if not d.get("is_sensor")]

        def device_row(parent, dev: dict, highlight: bool):
            bg  = CARD2 if highlight else CARD
            row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=8,
                               border_width=2 if highlight else 0,
                               border_color=GOLD if highlight else BORDER)
            row.pack(fill="x", pady=3, padx=4)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="both", expand=True, padx=12, pady=8)

            name     = dev.get("deviceName") or dev.get("device", "Unknown")
            sku      = dev.get("sku") or dev.get("model", "")
            dev_id   = dev.get("device", "")
            api_ver  = dev.get("api_version", "v2")
            tag      = "SENSOR" if highlight else "device"
            tag_col  = GREEN if highlight else SUBTEXT

            top_row = ctk.CTkFrame(left, fg_color="transparent")
            top_row.pack(fill="x")
            _label(top_row, name, FONT_B, GOLD if highlight else TEXT).pack(side="left")
            _label(top_row, f"  [{tag}]", FONT_S, tag_col).pack(side="left")
            _label(top_row, f"  {api_ver}", FONT_S, SUBTEXT).pack(side="right")

            id_row = ctk.CTkFrame(left, fg_color="transparent")
            id_row.pack(fill="x", pady=(2, 0))
            _label(id_row, f"Device ID:  {dev_id}", ("Courier New", 10), TEXT).pack(side="left")

            sku_row = ctk.CTkFrame(left, fg_color="transparent")
            sku_row.pack(fill="x")
            _label(sku_row, f"SKU/Model:  {sku}", ("Courier New", 10), TEXT).pack(side="left")

            # Copy buttons
            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=10, pady=8)

            def _copy(text, btn):
                self.clipboard_clear()
                self.clipboard_append(text)
                orig = btn.cget("text")
                btn.configure(text="Copied!")
                self.after(1500, lambda b=btn, t=orig: b.configure(text=t))

            btn_id  = _btn(right, "Copy ID",  None, width=82, height=26, fg=BORDER, hover=CARD)
            btn_id.configure(command=lambda t=dev_id, b=btn_id: _copy(t, b))
            btn_id.pack(pady=2)

            btn_sku = _btn(right, "Copy SKU", None, width=82, height=26, fg=BORDER, hover=CARD)
            btn_sku.configure(command=lambda t=sku, b=btn_sku: _copy(t, b))
            btn_sku.pack(pady=2)

        # ── Sensors section ──
        if sensors:
            _label(scroll, f"Temperature / Humidity Sensors  ({len(sensors)})",
                   FONT_B, GREEN).pack(anchor="w", padx=4, pady=(6, 2))
            for d in sensors:
                device_row(scroll, d, highlight=True)
        else:
            _label(scroll, "No temperature sensors found via API.",
                   FONT_B, ORANGE).pack(anchor="w", padx=4, pady=6)
            _label(scroll,
                   "If your sensors connect via a gateway, make sure the gateway\n"
                   "is online and its firmware is up to date in the Govee app.",
                   FONT_S, SUBTEXT).pack(anchor="w", padx=4)

        # ── Other devices section ──
        if others:
            _label(scroll, f"Other Devices  ({len(others)})",
                   FONT_B, SUBTEXT).pack(anchor="w", padx=4, pady=(14, 2))
            for d in others:
                device_row(scroll, d, highlight=False)

        _btn(win, "Close", win.destroy, width=100,
             fg=CARD2, hover=BORDER).pack(pady=10)

    # ══════════════════════════════════════════════════════════════════════════
    #  OPENERS
    # ══════════════════════════════════════════════════════════════════════════

    def _open_incubator_dialog(self, inc: dict = None):
        IncubatorDialog(self, inc, on_save=lambda: self._refresh_current())

    # ══════════════════════════════════════════════════════════════════════════
    #  INCUBATOR DETAIL VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _show_inc_detail(self, inc: dict):
        """Navigate to the full-screen detail view for one incubator."""
        self._detail_inc = inc
        self.show_view("inc_detail")

    def _build_inc_detail_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)
        frame._content = None  # placeholder; built in _refresh_inc_detail
        return frame

    def _refresh_inc_detail(self):
        frame = self._views["inc_detail"]

        # Remember which tab was open before we tear down
        _prev_tab = getattr(frame, "_active_tab", "Inspections")

        # Destroy previous content
        for w in frame.winfo_children():
            w.destroy()

        inc = self._detail_inc
        if not inc:
            _label(frame, "No incubator selected.", FONT_B, SUBTEXT).pack(pady=40)
            return

        # Re-fetch incubator to get fresh data
        fresh = next((x for x in db.get_incubators(include_hidden=True)
                      if x["id"] == inc["id"]), inc)

        unit = db.get_setting("temp_unit", "C")

        # ── Top bar ──────────────────────────────────────────────────────────
        topbar = ctk.CTkFrame(frame, fg_color=SIDEBAR, corner_radius=0)
        topbar.pack(fill="x")

        _btn(topbar, "← Back", lambda: self.show_view("dashboard"),
             width=90, height=32, fg="transparent", hover=CARD,
             text_color=SUBTEXT).pack(side="left", padx=8, pady=8)

        # Incubator switcher: ‹ prev · name dropdown · next ›
        _nav_list = db.get_incubators()  # visible incubators, in display order
        _cur_idx  = next((i for i, x in enumerate(_nav_list)
                          if x["id"] == fresh["id"]), 0)

        def _go_to(idx: int):
            if _nav_list:
                self._show_inc_detail(_nav_list[idx % len(_nav_list)])

        _btn(topbar, "‹", lambda: _go_to(_cur_idx - 1),
             width=34, height=32, fg=CARD, hover=CARD2, text_color=GOLD).pack(
             side="left", padx=(4, 2), pady=8)

        _name_map = {x["name"]: i for i, x in enumerate(_nav_list)}
        _name_var = ctk.StringVar(value=fresh["name"])
        _name_dd  = ctk.CTkOptionMenu(
            topbar, variable=_name_var, values=list(_name_map.keys()),
            width=190, height=32, font=FONT_H,
            fg_color=CARD, button_color=CARD2, button_hover_color=BORDER,
            text_color=GOLD, dropdown_fg_color=CARD,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD2,
            command=lambda name: _go_to(_name_map.get(name, _cur_idx)),
        )
        _name_dd.pack(side="left", padx=2, pady=8)

        _btn(topbar, "›", lambda: _go_to(_cur_idx + 1),
             width=34, height=32, fg=CARD, hover=CARD2, text_color=GOLD).pack(
             side="left", padx=(2, 16), pady=8)

        reading = self._govee.get_last(fresh["id"])
        if not reading:
            db_row  = db.get_latest_reading(fresh["id"])
            reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"]} if db_row else {}

        if reading.get("temp_c") is not None:
            t_min, t_max = calc.get_temp_range(fresh)
            t_col = SUBTEXT if t_min is None else (
                GREEN if t_min <= reading["temp_c"] <= t_max else RED)
            ctk.CTkLabel(topbar,
                text=calc.format_temp(reading["temp_c"], unit),
                font=("Segoe UI", 13, "bold"), text_color=t_col,
                fg_color=CARD, corner_radius=6, padx=10, height=32,
            ).pack(side="left", padx=4, pady=8)
            ctk.CTkLabel(topbar,
                text=f"{reading['humidity']:.0f}% RH",
                font=("Segoe UI", 13, "bold"), text_color=TEXT,
                fg_color=CARD, corner_radius=6, padx=10, height=32,
            ).pack(side="left", padx=4, pady=8)

        # Inspection pills (click to open the inspection report)
        make_status_badges(
            topbar, fresh["id"],
            on_click=lambda p, i=fresh: self._open_inspection_form(i)).pack(
            side="left", padx=12, pady=8)

        _btn(topbar, "Inspect Now", lambda i=fresh: self._open_inspection_form(i),
             width=110, height=32, fg=BLUE, hover="#1D4ED8",
             text_color="white").pack(side="right", padx=(0, 12), pady=8)

        _btn(topbar, "⚙ Edit Setup",
             lambda i=fresh: IncubatorDialog(
                 self, i,
                 on_save=lambda: self._refresh_current(),
                 on_delete=lambda: self.show_view("dashboard")),
             width=110, height=32, fg=CARD2, hover=BORDER,
             text_color=TEXT).pack(side="right", padx=4, pady=8)

        # ── Control row ───────────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=0)
        ctrl.pack(fill="x")

        _label(ctrl, "Temp Mode:", FONT_S, SUBTEXT).pack(side="left", padx=(16, 6), pady=8)

        _det_mode_key = fresh.get("temp_mode", "incubation")
        _det_mode_var = ctk.StringVar(value=calc.TEMP_MODES.get(_det_mode_key, calc.TEMP_MODES["incubation"])["label"])
        _det_range_lbl = _label(ctrl, "", FONT_S, SUBTEXT)

        def _update_range_lbl(cfg, lbl=_det_range_lbl):
            if cfg["min"] is None:
                lbl.configure(text="No alerts")
            else:
                lbl.configure(text=f"{cfg['min']}–{cfg['max']} °C")

        _update_range_lbl(calc.TEMP_MODES.get(_det_mode_key, calc.TEMP_MODES["incubation"]))

        def _on_detail_mode(label, iid=fresh["id"]):
            key  = calc._MODE_BY_LABEL.get(label, "incubation")
            prev = next((x for x in db.get_incubators(include_hidden=True)
                         if x["id"] == iid), {}).get("temp_mode", "incubation")
            db.set_incubator_temp_mode(iid, key)
            self._sync_trays_to_mode(iid, key, prev)
            _update_range_lbl(calc.TEMP_MODES[key])
            self._refresh_current()

        ctk.CTkSegmentedButton(
            ctrl,
            values=[v["label"] for v in calc.TEMP_MODES.values()],
            variable=_det_mode_var,
            command=_on_detail_mode,
            width=320, height=28,
        ).pack(side="left", padx=4)
        _det_range_lbl.pack(side="left", padx=(10, 20))

        _alerts_on = bool(fresh.get("temp_alerts_enabled", 1))
        _alert_sv  = ctk.StringVar(value="🔔  Alerts On" if _alerts_on else "🔕  Alerts Off")

        def _toggle_alert(iid=fresh["id"], sv=_alert_sv):
            cur = next((x for x in db.get_incubators(include_hidden=True) if x["id"] == iid), {})
            new = not bool(cur.get("temp_alerts_enabled", 1))
            db.set_incubator_alerts_enabled(iid, new)
            sv.set("🔔  Alerts On" if new else "🔕  Alerts Off")
            self._refresh_current()

        ctk.CTkButton(
            ctrl, textvariable=_alert_sv, font=FONT_S,
            fg_color=BORDER, hover_color=CARD2, text_color=TEXT,
            width=130, height=28, command=_toggle_alert,
        ).pack(side="left", pady=8)

        # ── Scrollable body ───────────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(frame, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True)

        # ── Temperature / Humidity Chart with time-range selector ────────────
        chart_frame = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10)
        chart_frame.pack(fill="x", padx=16, pady=(12, 8))

        _RANGES = [
            ("1H",    1),
            ("24H",   24),
            ("7D",    24 * 7),
            ("Month", 24 * 30),
        ]
        _chart_range_var = ctk.StringVar(value="24H")

        # Header row: title left, range buttons right
        chart_hdr = ctk.CTkFrame(chart_frame, fg_color="transparent")
        chart_hdr.pack(fill="x", padx=14, pady=(10, 4))
        # Pack right-side elements first so tkinter reserves space before left fills
        _range_btns: dict = {}
        btn_row = ctk.CTkFrame(chart_hdr, fg_color="transparent")
        btn_row.pack(side="right")

        chart_title = _label(chart_hdr, "Last 24 Hours", FONT_B, GOLD)
        chart_title.pack(side="left")

        # Canvas holder — we replace its contents when the range changes
        chart_canvas_frame = ctk.CTkFrame(chart_frame, fg_color="transparent")
        chart_canvas_frame.pack(fill="x", padx=8, pady=(0, 10))

        def _draw_chart(hours: float, label: str):
            for w in chart_canvas_frame.winfo_children():
                w.destroy()
            chart_title.configure(text=f"Last {label}")

            # Highlight active button
            for lbl, btn in _range_btns.items():
                btn.configure(fg_color=GOLD if lbl == label else BORDER,
                              text_color="black" if lbl == label else TEXT)

            if not HAS_MPL:
                _label(chart_canvas_frame, "Install matplotlib to view chart.",
                       FONT_S, SUBTEXT).pack(pady=16)
                return

            readings = db.get_readings_hours(fresh["id"], hours)
            if not readings:
                _label(chart_canvas_frame,
                       f"No readings in the last {label}.",
                       FONT_S, SUBTEXT).pack(pady=16)
                return

            try:
                timestamps = [datetime.fromisoformat(r["timestamp"]) for r in readings]
                temps      = [r["temperature_c"] for r in readings]
                hums       = [r["humidity_pct"]  for r in readings]
                if unit == "F":
                    temps = [calc.c_to_f(t) for t in temps]
                temp_lbl = f"Temp (°{unit})"

                fig = Figure(figsize=(10, 2.8), facecolor="#1F2937")
                ax1 = fig.add_subplot(111)
                ax2 = ax1.twinx()

                ax1.plot(timestamps, temps, color="#FFD700", linewidth=1.8)
                ax2.plot(timestamps, hums,  color="#60A5FA", linewidth=1.2,
                         alpha=0.6, linestyle="--")

                t_min, t_max = calc.get_temp_range(fresh)
                if t_min is not None:
                    _lo = calc.c_to_f(t_min) if unit == "F" else t_min
                    _hi = calc.c_to_f(t_max) if unit == "F" else t_max
                    ax1.axhline(_lo, color="#EF4444", linewidth=0.8, linestyle=":")
                    ax1.axhline(_hi, color="#EF4444", linewidth=0.8, linestyle=":")

                # Dotted goal lines — temp goal in the temp colour, humidity goal in
                # the humidity colour (matches each data line).
                goal_t, goal_h = db.get_mode_goals(fresh.get("temp_mode", "incubation"))
                if goal_t is not None:
                    _gt = calc.c_to_f(goal_t) if unit == "F" else goal_t
                    ax1.axhline(_gt, color="#FFD700", linewidth=1.1, linestyle=":")
                if goal_h is not None:
                    ax2.axhline(goal_h, color="#60A5FA", linewidth=1.1, linestyle=":")

                for ax in (ax1, ax2):
                    ax.set_facecolor("#1F2937")
                    ax.tick_params(colors="#9CA3AF", labelsize=8)
                    for spine in ax.spines.values():
                        spine.set_edgecolor("#374151")

                ax1.set_ylabel(temp_lbl, color="#FFD700", fontsize=8)
                ax2.set_ylabel("Humidity %", color="#60A5FA", fontsize=8)

                # Pick x-axis format based on range
                if hours <= 1:
                    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                    ax1.xaxis.set_major_locator(mdates.MinuteLocator(interval=10))
                elif hours <= 24:
                    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=2))
                elif hours <= 24 * 7:
                    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
                    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=1))
                else:
                    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
                    ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))

                for lbl in ax1.xaxis.get_majorticklabels():
                    lbl.set_rotation(0)
                fig.tight_layout(pad=1.0)

                canvas = FigureCanvasTkAgg(fig, master=chart_canvas_frame)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="x")
            except Exception as exc:
                _label(chart_canvas_frame, f"Chart error: {exc}", FONT_S, RED).pack(pady=10)

        for range_label, range_hours in _RANGES:
            rl, rh = range_label, range_hours
            b = ctk.CTkButton(
                btn_row, text=rl, width=52, height=26,
                fg_color=GOLD if rl == "24H" else BORDER,
                hover_color=CARD2,
                text_color="black" if rl == "24H" else TEXT,
                corner_radius=5, font=FONT_S,
                command=lambda lbl=rl, hrs=rh: _draw_chart(hrs, lbl),
            )
            b.pack(side="left", padx=2)
            _range_btns[rl] = b

        # Defer chart so the screen appears instantly, then render after
        frame.after(50, lambda: _draw_chart(24, "24H"))

        # ── Tabs: Inspections / Batches / Trays / VOC ─────────────────────────
        # Tabs are built lazily — content is only created the first time each tab is selected.
        tabs = ctk.CTkTabview(body, fg_color=CARD, corner_radius=8)
        tabs.pack(fill="both", expand=True, padx=16, pady=(4, 12))

        _tab_built: set = set()

        def _build_inspections_tab():
            it = tabs.tab("Inspections")
            InspectionsLogPanel(it, fixed_incubator_id=fresh["id"]).pack(fill="both", expand=True)

        def _build_batches_tab():
            bt = tabs.tab("Batches")
            bscroll = ctk.CTkScrollableFrame(bt, fg_color="transparent")
            bscroll.pack(fill="both", expand=True)
            batches = db.get_batches(incubator_id=fresh["id"])
            if batches:
                for batch in batches:
                    bf = ctk.CTkFrame(bscroll, fg_color=CARD2, corner_radius=8)
                    bf.pack(fill="x", pady=4, padx=4)
                    bname = batch.get("name") or f"Batch {batch['id']}"
                    _label(bf, bname, FONT_B, GOLD).pack(anchor="w", padx=10, pady=(8, 2))
                    for fld, lbl in calc.BATCH_EVENT_FIELDS:
                        val = batch.get(fld)
                        if val:
                            d    = calc.days_from_now(val)
                            line = f"{lbl}: {val[:10]}  ({calc.format_days(d)})"
                            col  = (RED    if d is not None and d <= 1
                                    else ORANGE if d is not None and d <= 5
                                    else SUBTEXT)
                            _label(bf, line, FONT_S, col).pack(anchor="w", padx=14, pady=1)
                    ctk.CTkFrame(bf, fg_color="transparent", height=6).pack()
            else:
                _label(bscroll, "No batches for this incubator.", FONT_S, SUBTEXT).pack(pady=16)

        def _build_trays_tab():
            tt = tabs.tab("Trays")
            tray_hdr = ctk.CTkFrame(tt, fg_color="transparent")
            tray_hdr.pack(fill="x", padx=8, pady=(8, 4))
            tscroll = ctk.CTkScrollableFrame(tt, fg_color="transparent")
            tscroll.pack(fill="both", expand=True)

            _tray_checks: dict = {}

            def _update_delete_btn():
                n = sum(1 for v in _tray_checks.values() if v.get())
                if n:
                    delete_btn.configure(text=f"Delete {n} Selected",
                                         fg_color=RED, hover_color="#B91C1C", state="normal")
                else:
                    delete_btn.configure(text="Delete Selected",
                                         fg_color=BORDER, hover_color=CARD2, state="disabled")

            def _select_all_toggle():
                all_on = all(v.get() for v in _tray_checks.values()) if _tray_checks else False
                for v in _tray_checks.values():
                    v.set(not all_on)
                _update_delete_btn()

            def _delete_selected():
                ids = [tid for tid, v in _tray_checks.items() if v.get()]
                if not ids:
                    return
                if not messagebox.askyesno("Delete Trays",
                        f"Permanently delete {len(ids)} tray(s)?\nThis cannot be undone.",
                        parent=self):
                    return
                for tid in ids:
                    db.delete_tray(tid)
                _refresh_tray_list()

            def _refresh_tray_list():
                _tray_checks.clear()
                for w in tscroll.winfo_children():
                    w.destroy()
                # Only show trays currently in this incubator (active);
                # released/removed trays drop off the list.
                trays = db.get_trays(incubator_id=fresh["id"], status=db.IN_INCUBATOR_STATUSES)
                tray_count_lbl.configure(text=f"{len(trays)} tray(s)")
                _update_delete_btn()
                if trays:
                    for tray in trays:
                        var = ctk.BooleanVar(value=False)
                        _tray_checks[tray["id"]] = var
                        tr = ctk.CTkFrame(tscroll, fg_color=CARD2, corner_radius=6)
                        tr.pack(fill="x", pady=2, padx=4)
                        ctk.CTkCheckBox(tr, text="", variable=var,
                            width=20, checkbox_width=18, checkbox_height=18,
                            fg_color=RED, hover_color="#B91C1C",
                            command=_update_delete_btn,
                        ).pack(side="left", padx=(8, 4), pady=6)
                        _label(tr, f"Tray {tray['tray_number']}", FONT_B, TEXT).pack(
                            side="left", padx=(4, 8), pady=6)
                        _label(tr,
                            f"{tray.get('sample_name') or '—'}  "
                            f"{tray.get('volume_gal') or '—'} gal  "
                            f"{tray.get('status') or 'active'}",
                            FONT_S, SUBTEXT).pack(side="left", padx=4)
                        _btn(tr, "QR", lambda t=tray: QRDialog(self, t, port=self._qr_port),
                             width=50, height=24, fg=BORDER, hover=CARD).pack(side="right", padx=8)
                else:
                    _label(tscroll, "No trays yet. Add one or import a CSV.",
                           FONT_S, SUBTEXT).pack(pady=16)

            tray_count_lbl = _label(tray_hdr, "", FONT_S, SUBTEXT)
            tray_count_lbl.pack(side="left")
            _btn(tray_hdr, "⬇ Template",
                 lambda: self._tray_csv_template(default_incubator_name=fresh["name"]),
                 width=110, height=28, fg=BORDER, hover=CARD2).pack(side="right", padx=(4, 0))
            _btn(tray_hdr, "Import CSV",
                 lambda: self._tray_csv_import(default_incubator_id=fresh["id"],
                                               on_complete=_refresh_tray_list),
                 width=100, height=28, fg=TEAL, hover="#0D9488",
                 text_color="white").pack(side="right", padx=4)
            _btn(tray_hdr, "Release CSV",
                 lambda: self._release_csv_import(on_complete=_refresh_tray_list),
                 width=110, height=28, fg="#7C3AED", hover="#6D28D9",
                 text_color="white").pack(side="right", padx=4)
            _btn(tray_hdr, "⬇ Release Template", self._release_csv_template,
                 width=150, height=28, fg=BORDER, hover=CARD2).pack(side="right", padx=(0, 2))
            delete_btn = ctk.CTkButton(tray_hdr, text="Delete Selected", width=130, height=28,
                fg_color=BORDER, hover_color=CARD2, text_color=TEXT,
                corner_radius=6, font=FONT_S, state="disabled", command=_delete_selected)
            delete_btn.pack(side="right", padx=4)
            _btn(tray_hdr, "Select All", _select_all_toggle,
                 width=90, height=28, fg=BORDER, hover=CARD2).pack(side="right", padx=(0, 2))
            _refresh_tray_list()

        def _build_voc_tab():
            vt = tabs.tab("VOC Monitor")
            VOCPanel(vt, incubator_id=fresh["id"]).pack(fill="both", expand=True)

        # Register all tab names first (required before .tab() can be called)
        for tab_name in ("Inspections", "Batches", "Trays", "VOC Monitor"):
            tabs.add(tab_name)

        # Map tab name → builder function
        _tab_builders = {
            "Inspections": _build_inspections_tab,
            "Batches":     _build_batches_tab,
            "Trays":       _build_trays_tab,
            "VOC Monitor": _build_voc_tab,
        }

        def _on_tab_change():
            name = tabs.get()
            frame._active_tab = name
            if name not in _tab_built:
                _tab_built.add(name)
                _tab_builders[name]()

        tabs.configure(command=_on_tab_change)

        # Restore the previously active tab (or default to Inspections)
        restore_tab = _prev_tab if _prev_tab in _tab_builders else "Inspections"
        _tab_built.add(restore_tab)
        _tab_builders[restore_tab]()
        if restore_tab != "Inspections":
            tabs.set(restore_tab)
        frame._active_tab = restore_tab

    def _open_inc_detail_window(self, inc: dict):
        """Open a detail window with VOC Monitor, Inspections, Batches and Trays tabs."""
        win = ctk.CTkToplevel(self)
        win.title(f"{inc['name']} — Detail")
        win.geometry("1020x700")
        win.minsize(860, 600)
        win.grab_set()

        # Header row with name + quick Inspect button
        hdr = ctk.CTkFrame(win, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(12, 2))
        _label(hdr, inc["name"], FONT_H, GOLD).pack(side="left")

        # Live M/E badges in header
        reading = self._govee.get_last(inc["id"])
        govee_temp = reading.get("temp_c")
        ibrow = ctk.CTkFrame(hdr, fg_color="transparent")
        ibrow.pack(side="left", padx=16)
        _label(ibrow, "Inspections:", FONT_S, SUBTEXT).pack(side="left", padx=(0, 6))
        make_status_badges(ibrow, inc["id"]).pack(side="left")

        _btn(hdr, "Inspect Now", lambda i=inc: self._open_inspection_form(i),
             width=110, height=30, fg=BLUE, hover="#1D4ED8",
             text_color="white").pack(side="right")

        # Temp mode switcher row
        mrow = ctk.CTkFrame(win, fg_color="transparent")
        mrow.pack(fill="x", padx=16, pady=(0, 6))
        _label(mrow, "Temp Mode:", FONT_S, SUBTEXT).pack(side="left", padx=(0, 8))
        _det_mode_key = inc.get("temp_mode", "incubation")
        _det_mode_cfg = calc.TEMP_MODES.get(_det_mode_key, calc.TEMP_MODES["incubation"])
        _det_mode_var = ctk.StringVar(value=_det_mode_cfg["label"])
        _det_range_lbl = _label(mrow, f"{_det_mode_cfg['min']}–{_det_mode_cfg['max']} °C",
                                FONT_S, SUBTEXT)

        def _on_detail_mode(label, iid=inc["id"], rlbl=_det_range_lbl, rv=_det_mode_var):
            key = calc._MODE_BY_LABEL.get(label, "incubation")
            db.set_incubator_temp_mode(iid, key)
            cfg = calc.TEMP_MODES[key]
            rlbl.configure(text=f"{cfg['min']}–{cfg['max']} °C")
            self._refresh_current()

        ctk.CTkSegmentedButton(
            mrow,
            values=[v["label"] for v in calc.TEMP_MODES.values()],
            variable=_det_mode_var,
            command=_on_detail_mode,
            width=300, height=28
        ).pack(side="left")
        _det_range_lbl.pack(side="left", padx=(12, 0))

        # Alert on/off toggle
        _alerts_on = bool(inc.get("temp_alerts_enabled", 1))
        _alert_btn_txt = ctk.StringVar(value="🔔  Alerts On" if _alerts_on else "🔕  Alerts Off")

        def _toggle_alert_detail(iid=inc["id"], sv=_alert_btn_txt):
            cur_val = db.get_incubators()  # re-fetch to get live flag
            cur_inc = next((x for x in cur_val if x["id"] == iid), {})
            new_val = not bool(cur_inc.get("temp_alerts_enabled", 1))
            db.set_incubator_alerts_enabled(iid, new_val)
            sv.set("🔔  Alerts On" if new_val else "🔕  Alerts Off")
            self._refresh_current()

        ctk.CTkButton(
            mrow, textvariable=_alert_btn_txt,
            font=("Segoe UI", 11),
            fg_color=BORDER, hover_color=CARD2,
            text_color=TEXT,
            width=130, height=28,
            command=_toggle_alert_detail
        ).pack(side="left", padx=(16, 0))

        tabs = ctk.CTkTabview(win, fg_color=CARD)
        tabs.pack(fill="both", expand=True, padx=12, pady=8)

        # ── VOC Monitor tab (first — most used) ───────────────────────────────
        vt = tabs.add("VOC Monitor")
        VOCPanel(vt, incubator_id=inc["id"]).pack(fill="both", expand=True)

        # ── Inspections tab ───────────────────────────────────────────────────
        it = tabs.add("Inspections")
        InspectionsLogPanel(it, fixed_incubator_id=inc["id"]).pack(
            fill="both", expand=True)

        # ── Batches tab ───────────────────────────────────────────────────────
        bt = tabs.add("Batches")
        scroll = ctk.CTkScrollableFrame(bt, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        for batch in db.get_batches(incubator_id=inc["id"]):
            bf = ctk.CTkFrame(scroll, fg_color=CARD2, corner_radius=8)
            bf.pack(fill="x", pady=4, padx=4)
            bname = batch.get("name") or f"Batch {batch['id']}"
            _label(bf, bname, FONT_B, GOLD).pack(anchor="w", padx=10, pady=(8,2))
            for fld, lbl in calc.BATCH_EVENT_FIELDS:
                val = batch.get(fld)
                if val:
                    d = calc.days_from_now(val)
                    line = f"{lbl}: {val[:10]}  ({calc.format_days(d)})"
                    col  = RED if d is not None and d <= 1 else (ORANGE if d is not None and d <= 5 else SUBTEXT)
                    _label(bf, line, FONT_S, col).pack(anchor="w", padx=14, pady=1)
            ctk.CTkFrame(bf, fg_color="transparent", height=6).pack()

        # ── Trays tab ─────────────────────────────────────────────────────────
        tt = tabs.add("Trays")
        tr_scroll = ctk.CTkScrollableFrame(tt, fg_color="transparent")
        tr_scroll.pack(fill="both", expand=True)
        for tray in db.get_trays(incubator_id=inc["id"], status=db.IN_INCUBATOR_STATUSES):
            tr = ctk.CTkFrame(tr_scroll, fg_color=CARD2, corner_radius=6)
            tr.pack(fill="x", pady=2, padx=4)
            _label(tr, f"Tray {tray['tray_number']}", FONT_B, TEXT).pack(
                side="left", padx=10, pady=6)
            details = (
                f"{tray.get('sample_name') or '—'}  "
                f"{tray.get('volume_gal') or '—'} gal  "
                f"{tray.get('status') or 'active'}"
            )
            _label(tr, details, FONT_S, SUBTEXT).pack(side="left", padx=4)
            _btn(tr, "QR", lambda t=tray: QRDialog(self, t, port=self._qr_port),
                 width=50, height=24, fg=BORDER, hover=CARD).pack(side="right", padx=8)

    # ── Shared tray CSV helpers ───────────────────────────────────────────────

    _CSV_HEADERS = ["QR", "Sample", "gals/tray", "Incubator",
                    "Treatment Label", "Date+Time", "User"]

    def _tray_csv_template(self, default_incubator_name: str = ""):
        import csv
        path = filedialog.asksaveasfilename(
            title="Save CSV Template",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="trays_template.csv",
        )
        if not path:
            return
        example = ["T001", "Sample A", "2.0",
                   default_incubator_name or "Incubator 1",
                   "Batch 1", "2026-06-24 09:30", "Jane"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([self._CSV_HEADERS, example])
        messagebox.showinfo("Template Saved",
            f"Template saved to:\n{path}\n\n"
            "Fill in one row per tray, then use 'Import CSV' to load it.\n\n"
            "Columns:\n"
            "  QR             — tray identifier (required)\n"
            "  Sample         — sample name (auto-created if missing)\n"
            "  gals/tray      — volume in gallons\n"
            "  Incubator      — incubator name (required when importing from Trays tab)\n"
            "  Treatment Label— batch / treatment name\n"
            "  Date+Time      — load date (YYYY-MM-DD or YYYY-MM-DD HH:MM)\n"
            "  User           — person who loaded the tray",
            parent=self)

    def _tray_csv_import(self, default_incubator_id: int = None,
                         on_complete=None):
        import csv, re
        path = filedialog.askopenfilename(
            title="Select Tray CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        samples    = {s["name"]: s["id"] for s in db.get_samples()}
        batches    = {b["name"]: b["id"] for b in db.get_batches()}
        _inc_list  = db.get_incubators(include_hidden=True)
        inc_lookup = {i["name"].strip().lower(): i for i in _inc_list}

        def _find_incubator(raw: str):
            key = raw.strip().lower()
            if key in inc_lookup:
                return inc_lookup[key]
            num = re.search(r"\d+", key)
            if num:
                n = num.group()
                for k, inc in inc_lookup.items():
                    if re.search(r"\b" + n + r"\b", k):
                        return inc
            return None

        errors   = []
        imported = 0
        skipped  = 0

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception as exc:
            messagebox.showerror("Import Error", f"Could not read file:\n{exc}", parent=self)
            return

        if not rows:
            messagebox.showwarning("Import", "CSV file is empty.", parent=self)
            return

        missing_cols = [h for h in self._CSV_HEADERS if h not in rows[0]]
        if missing_cols:
            messagebox.showerror("Import Error",
                f"Missing column(s): {', '.join(missing_cols)}\n\n"
                "Download the template to see the correct format.", parent=self)
            return

        for i, row in enumerate(rows, start=2):
            qr = (row.get("QR") or "").strip()
            if not qr:
                errors.append(f"Row {i}: missing QR — skipped")
                skipped += 1
                continue

            def _pf(v):
                try:    return float(str(v).strip()) if v and str(v).strip() else None
                except: return None

            sample_name    = (row.get("Sample")          or "").strip()
            inc_name       = (row.get("Incubator")       or "").strip()
            treatment_name = (row.get("Treatment Label") or "").strip()
            date_raw       = (row.get("Date+Time")       or "").strip()
            user           = (row.get("User")            or "").strip()

            if inc_name:
                matched = _find_incubator(inc_name)
                if matched:
                    inc_id = matched["id"]
                else:
                    inc_id = default_incubator_id
                    errors.append(
                        f"Row {i} ({qr}): incubator '{inc_name}' not recognised"
                        + (f" — assigned to default incubator instead" if inc_id else " — skipped (no default incubator)")
                        + f". Available: {', '.join(x['name'] for x in _inc_list)}"
                    )
                    if inc_id is None:
                        skipped += 1
                        continue
            elif default_incubator_id:
                inc_id = default_incubator_id
            else:
                errors.append(f"Row {i} ({qr}): Incubator column is empty and no default — skipped")
                skipped += 1
                continue

            sample_id = samples.get(sample_name)
            if sample_name and sample_id is None:
                sample_id = db.upsert_sample({"name": sample_name})
                samples[sample_name] = sample_id
                errors.append(f"Row {i} ({qr}): created new sample '{sample_name}'")

            batch_id = batches.get(treatment_name)
            if treatment_name and batch_id is None:
                errors.append(f"Row {i} ({qr}): treatment '{treatment_name}' not found — imported without batch link")

            in_date = date_raw[:10] if date_raw and len(date_raw) >= 10 else (date_raw or None)

            try:
                db.upsert_tray({
                    "tray_number":         qr,
                    "incubator_id":        inc_id,
                    "sample_id":           sample_id,
                    "incubation_batch_id": batch_id,
                    "volume_gal":          _pf(row.get("gals/tray")),
                    "in_date":             in_date,
                    "status":              "active",
                    "notes":               f"Loaded by: {user}" if user else "",
                    "weight_lbs":          None,
                    "live_count":          None,
                    "parasite_level_pct":  None,
                    "out_date":            None,
                })
                imported += 1
            except Exception as exc:
                errors.append(f"Row {i} ({qr}): {exc}")
                skipped += 1

        if on_complete:
            on_complete()

        summary = f"Imported {imported} tray(s)."
        if skipped:
            summary += f"\n{skipped} row(s) skipped."
        if errors:
            new_samples = [e for e in errors if "created new sample" in e]
            warnings    = [e for e in errors if "created new sample" not in e]
            if new_samples:
                summary += f"\n\nNew samples created ({len(new_samples)}):\n"
                summary += "\n".join(e.split("'")[1] for e in new_samples[:10])
            if warnings:
                summary += "\n\nWarnings / Errors:\n" + "\n".join(warnings[:15])
                if len(warnings) > 15:
                    summary += f"\n… and {len(warnings) - 15} more"
        messagebox.showinfo("Import Complete", summary, parent=self)

    _RELEASE_CSV_HEADERS = ["Tray ID #", "Field", "Gals in Tray",
                            "LatLong", "Lat", "Long", "Date+Time", "User"]

    def _release_csv_template(self):
        import csv
        path = filedialog.asksaveasfilename(
            title="Save Release CSV Template",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="tray_release_template.csv",
        )
        if not path:
            return
        example = ["T001", "North Field", "2.0",
                   "44.123,-93.456", "44.123", "-93.456",
                   "2026-06-24 09:30", "Jane"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([self._RELEASE_CSV_HEADERS, example])
        messagebox.showinfo("Template Saved",
            f"Template saved to:\n{path}\n\n"
            "Fill in one row per tray released to the field.\n\n"
            "Columns:\n"
            "  Tray ID #   — tray identifier used to look up the tray (required)\n"
            "  Field       — field or site name\n"
            "  Gals in Tray— volume at release (optional, for record)\n"
            "  LatLong     — combined lat/long string\n"
            "  Lat / Long  — individual coordinates\n"
            "  Date+Time   — release date (YYYY-MM-DD or YYYY-MM-DD HH:MM)\n"
            "  User        — person who released the tray",
            parent=self)

    def _release_csv_import(self, on_complete=None):
        import csv
        path = filedialog.askopenfilename(
            title="Select Release CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception as exc:
            messagebox.showerror("Import Error", f"Could not read file:\n{exc}", parent=self)
            return

        if not rows:
            messagebox.showwarning("Release Import", "CSV file is empty.", parent=self)
            return

        missing = [h for h in self._RELEASE_CSV_HEADERS if h not in rows[0]]
        if missing:
            messagebox.showerror("Import Error",
                f"Missing column(s): {', '.join(missing)}\n\n"
                "Download the release template to see the correct format.", parent=self)
            return

        released = 0
        not_found = []
        errors = []

        for i, row in enumerate(rows, start=2):
            tray_id = (row.get("Tray ID #") or "").strip()
            if not tray_id:
                errors.append(f"Row {i}: missing Tray ID # — skipped")
                continue

            field    = (row.get("Field")       or "").strip()
            latlong  = (row.get("LatLong")     or "").strip()
            lat      = (row.get("Lat")         or "").strip()
            lon      = (row.get("Long")        or "").strip()
            date_raw = (row.get("Date+Time")   or "").strip()
            user     = (row.get("User")        or "").strip()

            out_date = date_raw[:10] if date_raw and len(date_raw) >= 10 else (date_raw or None)

            note_parts = []
            if user:
                note_parts.append(f"Released by: {user}")
            if field:
                note_parts.append(f"Field: {field}")
            if latlong:
                note_parts.append(f"LatLong: {latlong}")
            elif lat or lon:
                note_parts.append(f"Lat: {lat}  Long: {lon}")
            notes_append = "\n".join(note_parts)

            try:
                ok = db.release_tray(tray_id, out_date=out_date, notes_append=notes_append)
                if ok:
                    released += 1
                else:
                    not_found.append(tray_id)
            except Exception as exc:
                errors.append(f"Row {i} ({tray_id}): {exc}")

        if on_complete:
            on_complete()

        summary = f"Released {released} tray(s)."
        if not_found:
            summary += f"\n\n{len(not_found)} tray(s) not found in the system:\n"
            summary += "\n".join(not_found[:20])
            if len(not_found) > 20:
                summary += f"\n… and {len(not_found) - 20} more"
        if errors:
            summary += "\n\nErrors:\n" + "\n".join(errors[:10])
        messagebox.showinfo("Release Import Complete", summary, parent=self)

    def _open_batch_dialog(self, batch: dict = None, incubator_id: int = None):
        BatchDialog(self, batch, incubator_id,
                    on_save=lambda: self._refresh_current())

    def _open_sample_dialog(self, sample: dict = None):
        SampleDialog(self, sample, on_save=lambda: self._refresh_current())

    def _open_tray_dialog(self, tray: dict = None, incubator_id: int = None):
        TrayDialog(self, tray, incubator_id,
                   on_save=lambda: self._refresh_current())

    def _sync_trays_to_mode(self, inc_id: int, new_key: str, prev_key: str):
        """Offer to move trays when an incubator changes into Holding or Incubation:
        - into Holding:    Incubation -> Cooled (start cool-down / hold timer)
        - into Incubation: Cooled -> Incubation (resume incubation)
        """
        if new_key == prev_key:
            return

        if new_key == "holding":
            n = db.count_active_trays(inc_id)
            if n and messagebox.askyesno(
                "Move trays to Cooled?",
                f"This incubator was switched to Holding.\n\n"
                f"Move its {n} tray(s) currently in incubation to 'Cooled' and "
                f"start their cool-down timer (today's date)?",
                parent=self):
                moved = db.cool_trays(inc_id)
                messagebox.showinfo("Trays Cooled",
                    f"Moved {moved} tray(s) to Cooled.", parent=self)
        elif new_key == "incubation":
            n = db.count_cooled_trays(inc_id)
            if n and messagebox.askyesno(
                "Move trays to Incubation?",
                f"This incubator was switched to Incubation.\n\n"
                f"Move its {n} cooled tray(s) back to 'Incubation'? "
                f"(Their cool-down timer will reset.)",
                parent=self):
                moved = db.uncool_trays(inc_id)
                messagebox.showinfo("Trays back in Incubation",
                    f"Moved {moved} tray(s) back to Incubation.", parent=self)

    def _bulk_set_status(self):
        """Change status on all currently selected trays in the Trays tab."""
        sel = self._tray_tree.selection()
        if not sel:
            messagebox.showinfo("Set Status",
                "Select one or more trays first.\n\n"
                "Tip: click a row, then Ctrl+click or Shift+click to select more.",
                parent=self)
            return

        new_status = db.tray_status_value(self._bulk_status.get())
        tray_ids   = [int(iid) for iid in sel]

        out_date           = None
        overwrite_out_date = False
        if new_status in ("released", "removed"):
            # Let the user choose the out date (blank = today)
            today = datetime.now().strftime("%Y-%m-%d")
            dlg = ctk.CTkInputDialog(
                title="Out Date",
                text=f"Out date for {len(tray_ids)} tray(s), format YYYY-MM-DD.\n\n"
                     f"• Leave blank to use today ({today})\n"
                     "• Enter a date to apply it to all selected trays")
            raw = dlg.get_input()
            if raw is None:
                return  # cancelled
            raw = raw.strip()
            if raw:
                try:
                    datetime.strptime(raw, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Invalid Date",
                        f"'{raw}' is not a valid date. Use YYYY-MM-DD.", parent=self)
                    return
                out_date           = raw
                overwrite_out_date = True   # explicit date applies to all selected
            else:
                out_date = today  # blank → today, applied to trays without a date
        else:
            if not messagebox.askyesno(
                "Change Status",
                f"Set status to '{db.tray_status_label(new_status)}' "
                f"for {len(tray_ids)} selected tray(s)?",
                parent=self):
                return

        db.set_trays_status(tray_ids, new_status,
                            out_date=out_date, overwrite_out_date=overwrite_out_date)
        self._refresh_trays()
        self._refresh_alert_badge()
        messagebox.showinfo("Status Updated",
            f"Updated {len(tray_ids)} tray(s) to '{db.tray_status_label(new_status)}'"
            + (f" with out date {out_date}." if out_date else "."), parent=self)

    def _delete_all_trays(self):
        """Delete every tray, behind a two-step confirmation."""
        total = db.get_tray_stats()["count"]
        if not total:
            messagebox.showinfo("Delete All Trays", "There are no trays to delete.", parent=self)
            return

        # Step 1 — initial warning
        if not messagebox.askyesno(
            "Delete ALL Trays?",
            f"This will permanently delete ALL {total} tray(s) from every incubator, "
            "including their full season history.\n\n"
            "This cannot be undone.\n\nContinue?",
            icon="warning", parent=self):
            return

        # Step 2 — type-to-confirm
        dlg = ctk.CTkInputDialog(
            title="Confirm Delete All",
            text=f"Final confirmation.\n\nType  DELETE  to permanently remove "
                 f"all {total} tray(s):")
        entry = (dlg.get_input() or "").strip()
        if entry != "DELETE":
            messagebox.showinfo("Cancelled",
                "Trays were NOT deleted (confirmation text did not match).", parent=self)
            return

        removed = db.delete_all_trays()
        self._refresh_current()
        messagebox.showinfo("Trays Deleted",
            f"Deleted {removed} tray(s). The tray list is now empty.", parent=self)

    def _delete_incubator(self, iid: int):
        if messagebox.askyesno("Delete", "Delete this incubator?"):
            db.delete_incubator(iid)
            self._refresh_current()

    def _set_hidden(self, iid: int, hidden: bool):
        db.set_incubator_hidden(iid, hidden)
        self._refresh_current()

    def _set_inc_mode(self, iid: int, label: str):
        """Set an incubator's temp mode from a selector (e.g. the Incubators view)."""
        key  = calc._MODE_BY_LABEL.get(label, "incubation")
        prev = next((x for x in db.get_incubators(include_hidden=True)
                     if x["id"] == iid), {}).get("temp_mode", "incubation")
        db.set_incubator_temp_mode(iid, key)
        self._sync_trays_to_mode(iid, key, prev)
        self._refresh_current()

    def _after_inspection_saved(self):
        """After saving an inspection, jump back to the dashboard."""
        self.show_view("dashboard")

    def _toggle_temp_alerts(self, inc: dict):
        new_val = not bool(inc.get("temp_alerts_enabled", 1))
        db.set_incubator_alerts_enabled(inc["id"], new_val)
        self._refresh_current()

    def _open_alerts(self):
        AlertsDialog(self, on_ack=self._refresh_alert_badge)

    def _open_inspection_form(self, inc: dict):
        """Open the inspection form for a given incubator."""
        reading   = self._govee.get_last(inc["id"])
        govee_tmp = reading.get("temp_c")
        InspectionDialog(
            self, inc, govee_temp_c=govee_tmp,
            on_save=self._after_inspection_saved,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  INSPECTIONS VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _build_inspections_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        _label(hdr, "Inspections Log", FONT_H, GOLD).pack(side="left")

        # Legend
        leg = ctk.CTkFrame(hdr, fg_color="transparent")
        leg.pack(side="right")
        for st, (bg, fg_col, sym) in [
            ("Done",    ("#065F46", "#10B981", "M✓")),
            ("Missed",  ("#7F1D1D", "#EF4444", "M✗")),
            ("Open now",("#78350F", "#F59E0B", "M!")),
            ("Pending", ("#1F2937", "#6B7280", "M·")),
        ]:
            ctk.CTkLabel(leg, text=f" {sym} ", fg_color=bg,
                         text_color=fg_col, corner_radius=4,
                         font=("Segoe UI", 9, "bold"),
                         width=32, height=20).pack(side="left", padx=3)
            _label(leg, st, FONT_S, SUBTEXT).pack(side="left", padx=(0, 8))

        self._insp_panel = InspectionsLogPanel(frame)
        self._insp_panel.pack(fill="both", expand=True, padx=8, pady=4)
        return frame

    def _refresh_inspections(self):
        self._insp_panel.refresh()

    # ── Import spreadsheet ────────────────────────────────────────────────────

    def _import_xray(self):
        if not HAS_XLSX:
            messagebox.showerror("Missing Library",
                                 "openpyxl not installed.\n"
                                 "Run: pip install openpyxl")
            return
        path = filedialog.askopenfilename(
            title="Import X-Ray Results",
            filetypes=[("Excel / CSV", "*.xlsx *.xls *.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            imported = self._parse_xray_spreadsheet(path)
            if imported:
                messagebox.showinfo(
                    "Import Complete",
                    f"Imported {imported} sample(s) successfully."
                )
                self._refresh_samples()
            else:
                messagebox.showwarning(
                    "Import",
                    "No rows imported. The spreadsheet needs a header row with a "
                    "'Sample Name' column (plus Total Pounds, Live Bees per Pound, "
                    "Parasites, Chalkbrood, Total Gal Bees, Total lbs for 2gal, "
                    "Total Trays, Incubator Space, Notes, etc.)."
                )
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    # Maps the field spreadsheet's headers (normalized) -> sample DB fields.
    _SAMPLE_HEADER_MAP = {
        "sample name":          "name",
        "total pounds":         "total_weight_lbs",
        "total kgs":            "total_weight_kg",
        "live bees per pound":  "live_bees_per_lb",
        "parasites":            "parasites",
        "chalkbrood":           "chalkbrood",
        "total gal bees":       "total_volume_gal",
        "live bees per kg":     "live_bees_per_kg",
        "total kg for 2gal":    "kg_per_2gal",
        "total lbs for 2gal":   "lbs_per_2gal",
        "total trays":          "total_trays",
        "incubator space":      "incubator_space",
        "notes":                "notes",
    }
    _SAMPLE_TEXT_FIELDS = {"name", "incubator_space", "notes"}

    def _parse_xray_spreadsheet(self, path: str) -> int:
        """Import the field sample spreadsheet (CSV or Excel).

        Matches each row to an existing sample by name and updates it (keeping
        tray links); creates a new sample if the name isn't found.
        Returns count of rows imported.
        """
        if path.lower().endswith(".csv"):
            import csv
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        else:
            wb   = openpyxl.load_workbook(path, data_only=True)
            ws   = wb.active
            hdrs = [str(c.value or "").strip() for c in next(ws.iter_rows(max_row=1))]
            rows = [dict(zip(hdrs, r))
                    for r in ws.iter_rows(min_row=2, values_only=True)]

        def _norm(h):
            return " ".join(str(h or "").strip().lower().replace("?", "").split())

        def _num(v):
            if v is None:
                return None
            s = str(v).replace(",", "").replace("%", "").replace("$", "").strip()
            if s == "":
                return None
            try:
                return float(s)
            except ValueError:
                return None

        count = 0
        for row in rows:
            # Map this row's headers to our fields
            mapped = {}
            for raw_key, raw_val in row.items():
                field = self._SAMPLE_HEADER_MAP.get(_norm(raw_key))
                if not field:
                    continue
                if field in self._SAMPLE_TEXT_FIELDS:
                    mapped[field] = (str(raw_val).strip() if raw_val is not None else None)
                else:
                    mapped[field] = _num(raw_val)

            name = (mapped.get("name") or "").strip()
            if not name:
                continue
            mapped["name"] = name
            mapped["import_date"] = datetime.now().date().isoformat()
            db.upsert_sample_by_name(mapped)
            count += 1
        return count

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def _make_tree(self, parent, columns: tuple) -> ttk.Treeview:
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=8)
        tree = ttk.Treeview(frame, columns=columns, show="headings",
                            style="Dark.Treeview")
        vsb  = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
        hsb  = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=max(70, len(col)*9), anchor="center",
                        stretch=True)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)
        # Attach the outer frame so caller can pack it
        tree._outer_frame = frame
        tree.pack = frame.pack  # forward pack to the outer frame
        return tree

    def _refresh_current(self):
        if self._current_view:
            getattr(self, f"_refresh_{self._current_view}")()
        self._refresh_alert_badge()

    def _refresh_alert_badge(self):
        n = len(db.get_active_alerts())
        if n:
            self._alert_btn.configure(
                text=f"🔔  Alerts  ·  {n}",
                text_color="#FFFFFF",
                fg_color="#7F1D1D",      # deep red — clear but not garish
                hover_color="#991B1B",
            )
        else:
            self._alert_btn.configure(
                text="🔔  No alerts",
                text_color=SUBTEXT,
                fg_color=CARD,
                hover_color=CARD2,
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  BACKGROUND SERVICES
    # ══════════════════════════════════════════════════════════════════════════

    def _start_govee(self):
        if not db.get_setting("govee_api_key"):
            return
        self._govee.start_polling(
            incubators_fn=db.get_incubators,
            on_reading=self._on_govee_reading,
        )

    def _on_govee_reading(self, incubator_id: int, temp_c: float, humidity: float):
        """Called from Govee polling thread — save reading, check alerts, refresh UI."""
        db.save_reading(incubator_id, temp_c, humidity)

        incubators = db.get_incubators()
        inc = next((i for i in incubators if i["id"] == incubator_id), None)
        if inc and inc.get("temp_alerts_enabled", 1):
            problems = calc.check_temp_humidity(inc, temp_c, humidity)
            for msg in problems:
                # One standing alert per incubator+problem-type; suppress the
                # per-minute repeats while the condition persists.
                kind = "humidity" if "humid" in msg.lower() else "temp"
                db.add_alert("temp_humidity", msg, severity="warning",
                             incubator_id=incubator_id,
                             dedup_key=f"temp_humidity:{kind}:{incubator_id}")

        # Refresh UI on main thread
        self.after(0, self._on_reading_ui_refresh)

    def _update_dashboard_readings(self):
        """Update only the sensor labels on each card — no rebuild, no shutter."""
        unit = db.get_setting("temp_unit", "C")
        for inc_id, widgets in self._card_widgets.items():
            reading = self._govee.get_last(inc_id)
            if not reading:
                db_row  = db.get_latest_reading(inc_id)
                reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"],
                           "timestamp": db_row["timestamp"]} if db_row else {}
            temp_c = reading.get("temp_c")
            hum    = reading.get("humidity")
            inc    = widgets.get("inc")
            t_min, t_max = calc.get_temp_range(inc)
            _poll_txt, _poll_col = _poll_age(reading.get("timestamp"), POLL_INTERVAL_SEC)
            if temp_c is not None:
                t_str = calc.format_temp(temp_c, unit)
                t_col = SUBTEXT if t_min is None else (GREEN if t_min <= temp_c <= t_max else RED)
                h_col = TEXT
                dot   = RED if calc.check_temp_humidity(inc, temp_c, hum) else GREEN
            else:
                t_str = "—"
                t_col = h_col = dot = SUBTEXT
            try:
                widgets["temp"].configure(text=t_str, text_color=t_col)
                widgets["hum"].configure(text=f"{hum:.0f}%" if hum is not None else "—", text_color=h_col)
                widgets["dot"].configure(text_color=dot)
                widgets["ts"].configure(text=f"Last polled: {_poll_txt}", text_color=_poll_col)
            except Exception:
                pass  # widget may have been destroyed by a full refresh

    def _on_reading_ui_refresh(self):
        self._refresh_alert_badge()
        if self._current_view == "dashboard":
            self._update_dashboard_readings()

    def _start_qr_server(self):
        if db.get_setting("qr_server_enabled", "1") != "1":
            return
        port = int(db.get_setting("qr_server_port", "5151"))
        self._qr_port = port
        qr_server.start(port=port, on_update=lambda tid: self.after(0, self._on_qr_update))

    def _on_qr_update(self):
        self._refresh_alert_badge()
        if self._current_view == "trays":
            self._refresh_trays()

    def _start_alert_checker(self):
        def loop():
            while True:
                try:
                    self._check_date_alerts()
                except Exception as exc:
                    print(f"[AlertChecker] {exc}")
                time.sleep(3600)  # check hourly

        t = threading.Thread(target=loop, daemon=True, name="AlertChecker")
        t.start()

    def _check_date_alerts(self):
        lookahead = int(db.get_setting("date_alert_lookahead", "7"))
        batches   = db.get_batches(status="active")
        for batch in batches:
            for ev in calc.get_upcoming_events(batch, lookahead_days=lookahead):
                if ev["urgent"]:
                    db.add_alert(
                        "date",
                        f"{'TODAY' if ev['days_away']==0 else 'TOMORROW'}: "
                        f"{ev['label']} — {ev['batch_name']} ({ev['incubator_name']})",
                        severity="critical",
                        batch_id=ev.get("batch_id"),
                    )
                elif ev["days_away"] <= lookahead:
                    db.add_alert(
                        "date",
                        f"{ev['label']} in {ev['days_away']}d — "
                        f"{ev['batch_name']} ({ev['incubator_name']})",
                        severity="warning",
                        batch_id=ev.get("batch_id"),
                    )
        self.after(0, self._refresh_alert_badge)

    # ── Email scheduler ────────────────────────────────────────────────────────

    def _start_email_scheduler(self):
        """Background thread: send daily report at 7 PM if SMTP is configured."""
        def _loop():
            last_sent_date = None
            while True:
                try:
                    now = datetime.now()
                    if now.hour == 19 and now.minute < 5:        # 7:00–7:04 PM window
                        today = now.date()
                        if last_sent_date != today:
                            if email_reporter.smtp_configured() and email_reporter.get_recipients():
                                err = email_reporter.send_daily_report()
                                if err:
                                    print(f"[Email] Send failed: {err}")
                                else:
                                    print(f"[Email] Daily report sent ({today})")
                            last_sent_date = today
                except Exception as exc:
                    print(f"[EmailScheduler] {exc}")
                time.sleep(60)

        t = threading.Thread(target=_loop, daemon=True, name="EmailScheduler")
        t.start()

    def _send_test_email(self):
        """Send a test email immediately using current settings (must Save first)."""
        recipients = email_reporter.get_recipients()
        if not recipients:
            self._email_status_lbl.configure(
                text="No recipients — add at least one email and Save Settings.",
                text_color=ORANGE)
            return
        if not email_reporter.smtp_configured():
            self._email_status_lbl.configure(
                text="SMTP host and username are required — Save Settings first.",
                text_color=ORANGE)
            return

        self._email_status_lbl.configure(text="Sending…", text_color=SUBTEXT)
        self.update_idletasks()

        def _send():
            err = email_reporter.send_daily_report()
            def _done():
                if err:
                    self._email_status_lbl.configure(
                        text=f"Failed: {err}", text_color=RED)
                else:
                    self._email_status_lbl.configure(
                        text=f"Sent to {len(recipients)} recipient(s) ✓",
                        text_color=GREEN)
            self.after(0, _done)

        threading.Thread(target=_send, daemon=True).start()

    # ── Git sync ──────────────────────────────────────────────────────────────

    def _git_pull(self):
        """Pull latest code from GitHub in a background thread."""
        def _pull():
            try:
                app_dir = os.path.dirname(os.path.abspath(__file__))
                result  = subprocess.run(
                    ["git", "-C", app_dir, "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
                )
                if result.returncode == 0:
                    msg = result.stdout.strip() or "Already up to date."
                    self._sync_log(f"[git pull] {msg}")
                    self.after(0, lambda: self._set_git_status(msg, ok=True))
                else:
                    err = (result.stderr or result.stdout).strip()
                    self._sync_log(f"[git pull] {err}")
                    self.after(0, lambda: self._set_git_status(f"pull: {err}", ok=False))
            except FileNotFoundError:
                # git not on PATH — silent, not a required dependency
                pass
            except Exception as exc:
                self._sync_log(f"[git pull] {exc}")

        threading.Thread(target=_pull, daemon=True, name="GitPull").start()

    def _sync_log(self, msg: str):
        """Print and append a git-sync message to git_sync.log next to the app."""
        print(msg)
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "git_sync.log")
            if os.path.exists(path) and os.path.getsize(path) > 250_000:
                open(path, "w").close()   # simple rotation
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        except Exception:
            pass

    def _start_auto_sync(self):
        """Keep code in sync with GitHub automatically (every 5 min):
        pull new commits, then commit + push any local edits.

        Disable by setting 'auto_git_sync' to '0' in settings.
        """
        if db.get_setting("auto_git_sync", "1") != "1":
            self._sync_log("[AutoSync] disabled via settings")
            return

        def _loop():
            # Small initial delay so startup isn't competing with the launch pull
            time.sleep(60)
            while True:
                try:
                    self._auto_sync_once()
                except FileNotFoundError:
                    pass   # git not installed — nothing we can do
                except Exception as exc:
                    self._sync_log(f"[AutoSync] {exc}")
                time.sleep(300)   # 5 minutes

        threading.Thread(target=_loop, daemon=True, name="AutoSync").start()

    def _auto_sync_once(self):
        """One full sync pass: pull → (commit local edits) → push. Thread-safe."""
        import socket
        app_dir = os.path.dirname(os.path.abspath(__file__))

        def _git(*args, timeout=40):
            return subprocess.run(["git", "-C", app_dir, *args],
                                  capture_output=True, text=True, timeout=timeout,
                                  creationflags=_NO_WINDOW)

        self._sync_log("[AutoSync] checking for updates…")
        _did_something = False

        # 1. Pull remote changes (fast-forward only — never auto-merge)
        pull = _git("pull", "--ff-only")
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout).strip()
            self.after(0, lambda e=err: self._set_git_status(
                f"sync paused: {e[:50]}", ok=False))
            self._sync_log(f"[AutoSync] pull failed (diverged?): {err}")
            return
        pulled = (pull.stdout or "").strip()
        if pulled and "already up to date" not in pulled.lower():
            self.after(0, lambda m=pulled: self._set_git_status(f"Updated: {m}", ok=True))
            self._sync_log(f"[AutoSync] pulled: {pulled}")
            _did_something = True

        # 2. Commit local source edits (if any, stable, and valid)
        status = _git("status", "--porcelain").stdout.strip()
        if status:
            # Stability guard: don't commit mid-save. Re-check after a short pause.
            time.sleep(3)
            if _git("status", "--porcelain").stdout.strip() != status:
                self._sync_log("[AutoSync] files still changing — will retry next cycle")
                return
            # Safety guard: never propagate code that doesn't compile.
            changed_py = [ln[3:].strip().strip('"') for ln in status.splitlines()
                          if ln.strip().endswith(".py")]
            for rel in changed_py:
                chk = subprocess.run(
                    [sys.executable, "-m", "py_compile", os.path.join(app_dir, rel)],
                    capture_output=True, text=True, creationflags=_NO_WINDOW)
                if chk.returncode != 0:
                    self.after(0, lambda f=rel: self._set_git_status(
                        f"sync paused: {os.path.basename(f)} has errors", ok=False))
                    self._sync_log(f"[AutoSync] {rel} failed py_compile — not committing")
                    return
            _git("add", "-A")
            host  = socket.gethostname()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            commit = _git("commit", "-m", f"Auto-sync from {host} at {stamp}")
            if commit.returncode == 0:
                self._sync_log(f"[AutoSync] committed local changes ({len(status.splitlines())} file(s))")
            else:
                err = (commit.stderr or commit.stdout).strip()
                self._sync_log(f"[AutoSync] commit failed: {err}")
                return

        # 3. Push if we have commits ahead of origin
        ahead = _git("rev-list", "--count", "origin/main..HEAD").stdout.strip()
        if ahead and ahead != "0":
            push = _git("push", "origin", "main")
            if push.returncode == 0:
                self.after(0, lambda n=ahead: self._set_git_status(
                    f"Pushed {n} update(s) ✓", ok=True))
                self._sync_log(f"[AutoSync] pushed {ahead} commit(s)")
                _did_something = True
            else:
                err = (push.stderr or push.stdout).strip()
                self.after(0, lambda e=err: self._set_git_status(
                    f"push failed: {e[:50]}", ok=False))
                self._sync_log(f"[AutoSync] push failed: {err}")
                return

        if not _did_something:
            self._sync_log("[AutoSync] up to date — nothing to pull or push")

    def _set_git_status(self, msg: str, ok: bool):
        """Flash a brief git status message in the status bar."""
        short = msg if len(msg) < 60 else msg[:57] + "…"
        self._status_time.configure(
            text=f"git: {short}",
            text_color=(GREEN if ok else ORANGE),
        )
        # Revert to clock after 6 seconds
        self.after(6000, lambda: self._status_time.configure(
            text=datetime.now().strftime("%Y-%m-%d  %H:%M"),
            text_color=SUBTEXT,
        ))

    # ── Status bar tick ───────────────────────────────────────────────────────

    def _tick(self):
        self._status_govee.configure(
            text=f"Govee: {self._govee.status_label()}",
            text_color=(GREEN if self._govee.connected else SUBTEXT),
        )
        ip   = qr_server.get_local_ip()
        port = self._qr_port
        self._status_qr.configure(
            text=f"QR: {ip}:{port}",
            text_color=(BLUE if qr_server.available() else SUBTEXT),
        )
        self._status_time.configure(
            text=datetime.now().strftime("%Y-%m-%d  %H:%M"),
            text_color=SUBTEXT,
        )
        self.after(30_000, self._tick)  # refresh every 30s


# ═══════════════════════════════════════════════════════════════════════════════
#   ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = IncubationApp()
    app.mainloop()
