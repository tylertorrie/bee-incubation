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
import threading
import time
from datetime import datetime
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
    def __init__(self, master, inc: dict = None, on_save=None):
        super().__init__(master)
        self.on_save = on_save
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
            ("Humidity Min (%)",  "55",           "humidity_min"),
            ("Humidity Max (%)",  "75",           "humidity_max"),
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

    def _update_range_hint(self, *_):
        label = self._mode_var.get()
        key   = calc._MODE_BY_LABEL.get(label, "incubation")
        cfg   = calc.TEMP_MODES[key]
        self._range_hint.configure(text=f"Alert range: {cfg['min']}–{cfg['max']} °C")

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
            ("Sample Name *",     "name",              "e.g. AB-103"),
            ("Source / Supplier", "source",            ""),
            ("Lot Number",        "lot_number",        ""),
            ("Live % (x-ray)",    "xray_live_pct",     "e.g. 0.82"),
            ("Parasite % (x-ray)","xray_parasite_pct", "e.g. 0.05"),
            ("Dead % (x-ray)",    "xray_dead_pct",     "e.g. 0.13"),
            ("Total Volume (gal)","total_volume_gal",  ""),
            ("Total Weight (lbs)","total_weight_lbs",  ""),
            ("Import Date",       "import_date",       "YYYY-MM-DD"),
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
                "notes": self._notes.get("1.0", "end").strip()}
        for key in ("source", "lot_number", "import_date"):
            data[key] = self._rows[key].get()
        for key in ("xray_live_pct", "xray_parasite_pct", "xray_dead_pct",
                    "total_volume_gal", "total_weight_lbs"):
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
        self.geometry("460x640")
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
        self._status = _combo(f, ["active", "cooled", "released", "removed"], 230)
        self._status.set("active")
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
        self._status.set(self.tray.get("status") or "active")
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
            "status":        self._status.get(),
            "notes":         self._notes.get("1.0", "end").strip(),
        }
        for key in ("in_date", "out_date"):
            data[key] = self._mrows[key].get() or None
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
        super().__init__()
        db.init_db()
        voc_db.init_voc_tables()
        inspection_db.init_inspection_tables()
        _treeview_style()

        self.title("🐝 Bee Incubation Manager")
        self.geometry("1280x800")
        self.minsize(1000, 680)
        self.configure(fg_color="#0F172A")

        # Govee client
        self._govee = govee_mod.GoveeClient(
            api_key=db.get_setting("govee_api_key"),
            poll_interval_sec=int(db.get_setting("poll_interval_sec", "60")),
        )

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

        self._current_view = None
        self.show_view("dashboard")

        # Start background services
        self._start_govee()
        self._start_qr_server()
        self._start_alert_checker()
        self._git_pull()        # pull latest code from GitHub on startup

        # Refresh status bar periodically
        self._tick()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR, width=190, corner_radius=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        _label(sb, "🐝 Incubation", ("Segoe UI", 15, "bold"), GOLD).pack(
            pady=(20, 4), padx=16, anchor="w")
        _label(sb, "Bee Manager", FONT_S, SUBTEXT).pack(
            pady=(0, 16), padx=16, anchor="w")

        nav_items = [
            ("🏠  Dashboard",     "dashboard"),
            ("🏭  Incubators",    "incubators"),
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
        container = self._dash_scroll
        for w in container.winfo_children():
            w.destroy()

        show_hidden = getattr(self, "_dash_show_hidden", None)
        show_hidden = show_hidden.get() if show_hidden else False
        all_inc     = db.get_incubators(include_hidden=True)
        hidden_n    = sum(1 for i in all_inc if i.get("is_hidden"))
        incubators  = all_inc if show_hidden else [i for i in all_inc if not i.get("is_hidden")]

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

        # Summary row
        all_trays  = db.get_trays(status="active")
        total_gals = sum(t.get("volume_gal") or 0 for t in all_trays)
        summary_f  = ctk.CTkFrame(container, fg_color=CARD, corner_radius=10)
        summary_f.pack(fill="x", pady=(0, 12), padx=4)
        for txt, val in [
            ("Active Incubators", str(len(incubators))),
            ("Total Trays",       str(len(all_trays))),
            ("Total Gals",        f"{total_gals:.1f}"),
            ("Active Alerts",     str(len(db.get_active_alerts()))),
        ]:
            sf = ctk.CTkFrame(summary_f, fg_color="transparent")
            sf.pack(side="left", padx=20, pady=10)
            _label(sf, val, ("Segoe UI", 20, "bold"), GOLD).pack()
            _label(sf, txt, FONT_S, SUBTEXT).pack()

        # 2-column card grid
        grid = ctk.CTkFrame(container, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=4)
        grid.columnconfigure(0, weight=1, uniform="col")
        grid.columnconfigure(1, weight=1, uniform="col")

        for idx, inc in enumerate(incubators):
            card = self._make_inc_card(grid, inc)
            card.grid(row=idx // 2, column=idx % 2,
                      padx=6, pady=6, sticky="nsew")

    def _make_inc_card(self, parent, inc: dict) -> ctk.CTkFrame:
        is_hidden  = bool(inc.get("is_hidden"))
        card_bg    = "#161E2C" if is_hidden else CARD
        title_col  = SUBTEXT  if is_hidden else GOLD
        bdr_col    = "#222D3D" if is_hidden else BORDER

        card = ctk.CTkFrame(parent, fg_color=card_bg, corner_radius=12,
                            border_width=1, border_color=bdr_col)

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 4))
        name_txt = f"{inc['name']}  (hidden)" if is_hidden else inc["name"]
        _label(hdr, name_txt, FONT_H, title_col).pack(side="left")
        # Temp mode chip
        mode_key = inc.get("temp_mode", "incubation")
        mode_cfg = calc.TEMP_MODES.get(mode_key, calc.TEMP_MODES["incubation"])
        ctk.CTkLabel(hdr, text=mode_cfg["label"],
                     font=("Segoe UI", 9, "bold"),
                     fg_color="#1E3A5F", text_color="#7DD3FC",
                     corner_radius=4, height=20,
                     padx=6).pack(side="left", padx=(8, 0))

        reading = self._govee.get_last(inc["id"])
        temp_c  = reading.get("temp_c")
        hum     = reading.get("humidity")

        # Status dot
        if temp_c is not None:
            problems = calc.check_temp_humidity(inc, temp_c, hum)
            dot_color = RED if problems else GREEN
        else:
            dot_color = SUBTEXT
        _label(hdr, "●", ("Segoe UI", 18), dot_color).pack(side="right")

        # ── Readings ──
        rf = ctk.CTkFrame(card, fg_color=CARD2, corner_radius=8)
        rf.pack(fill="x", padx=12, pady=4)
        if temp_c is not None:
            unit = db.get_setting("temp_unit", "C")
            t_str = calc.format_temp(temp_c, unit)
            t_min, t_max = calc.get_temp_range(inc)
            t_col = GREEN if t_min <= temp_c <= t_max else RED
            h_col = GREEN if float(inc.get("humidity_min",55)) <= hum <= float(inc.get("humidity_max",75)) else RED
            _label(rf, f"🌡 {t_str}", FONT_B, t_col).pack(
                side="left", padx=12, pady=6)
            _label(rf, f"💧 {hum:.0f}%", FONT_B, h_col).pack(
                side="right", padx=12, pady=6)
            ts = (reading.get("timestamp") or "")[:16].replace("T", " ")
            _label(rf, ts, FONT_S, SUBTEXT).pack(pady=2)
        else:
            _label(rf, "No sensor data — set Govee device in Settings",
                   FONT_S, SUBTEXT).pack(padx=12, pady=8)

        # ── Tray count ──
        trays      = db.get_trays(incubator_id=inc["id"], status="active")
        total_gals = sum(t.get("volume_gal") or 0 for t in trays)
        tf = ctk.CTkFrame(card, fg_color="transparent")
        tf.pack(fill="x", padx=14, pady=2)
        _label(tf, f"Trays: {len(trays)} / {inc.get('capacity',50)}", FONT_B, TEXT).pack(side="left")
        _label(tf, f"{total_gals:.1f} gal", FONT_B, SUBTEXT).pack(side="right")

        # ── Next event ──
        batches = db.get_batches(incubator_id=inc["id"], status="active")
        events  = calc.get_all_events(batches, lookahead_days=14)
        if events:
            ev   = events[0]
            ecol = RED if ev["urgent"] else (ORANGE if ev["days_away"] <= 5 else TEXT)
            etxt = f"-> {ev['label']}: {calc.format_days(ev['days_away'])}"
            _label(card, etxt, FONT_S, ecol).pack(padx=14, anchor="w", pady=2)
        else:
            _label(card, "No upcoming events", FONT_S, SUBTEXT).pack(
                padx=14, anchor="w", pady=2)

        # ── Inspection status badges (only when visible) ──
        if not is_hidden:
            brow = ctk.CTkFrame(card, fg_color="transparent")
            brow.pack(fill="x", padx=12, pady=(4, 2))
            _label(brow, "Inspections:", FONT_S, SUBTEXT).pack(side="left", padx=(2, 6))
            make_status_badges(brow, inc["id"]).pack(side="left")

        # ── Buttons ──
        bf = ctk.CTkFrame(card, fg_color="transparent")
        bf.pack(fill="x", padx=8, pady=(4, 10))

        if is_hidden:
            # Hidden card — just Unhide + Details
            _btn(bf, "Unhide", lambda i=inc["id"]: self._set_hidden(i, False),
                 width=90, height=28, fg="#065F46", hover=TEAL,
                 text_color="white").pack(side="left", padx=4)
            _btn(bf, "Details", lambda i=inc: self._open_inc_detail_window(i),
                 width=80, height=28, fg=BORDER, hover=CARD2).pack(side="left", padx=2)
        else:
            _btn(bf, "Details", lambda i=inc: self._open_inc_detail_window(i),
                 width=80, height=28, fg=BORDER, hover=CARD2).pack(side="left", padx=4)
            _btn(bf, "+ Batch", lambda i=inc["id"]: self._open_batch_dialog(incubator_id=i),
                 width=78, height=28, fg=BORDER, hover=CARD2).pack(side="left", padx=2)
            _btn(bf, "Inspect", lambda i=inc: self._open_inspection_form(i),
                 width=90, height=28, fg=BLUE, hover="#1D4ED8",
                 text_color="white").pack(side="left", padx=2)
            _btn(bf, "Hide", lambda i=inc["id"]: self._set_hidden(i, True),
                 width=60, height=28, fg=CARD2, hover=BORDER,
                 text_color=SUBTEXT).pack(side="left", padx=2)
            _btn(bf, "+ Tray", lambda i=inc["id"]: self._open_tray_dialog(incubator_id=i),
                 width=75, height=28, fg=DK_GOLD, hover=GOLD,
                 text_color="black").pack(side="right", padx=4)

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

        incubators = db.get_incubators()
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
            info_txt = (f"Capacity: {inc.get('capacity',50)} trays  |  "
                        f"{_mode_cfg['label']}: {_mode_cfg['min']}–{_mode_cfg['max']}°C  |  "
                        f"H: {inc.get('humidity_min',55)}–{inc.get('humidity_max',75)}%")
            _label(left, info_txt, FONT_S, SUBTEXT).pack(anchor="w")

            govee_txt = (f"Govee: {inc.get('govee_device_id') or 'not set'}  "
                         f"({inc.get('govee_sku') or '—'})")
            _label(left, govee_txt, FONT_S, SUBTEXT).pack(anchor="w")

            # Inspection status badges (only for visible incubators)
            if not hidden:
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

        cols = ("Name", "Lot", "Live %", "Parasite %", "Total Gal",
                "Live Gal", "Trays@2gal", "Lbs/Tray", "Notes")
        self._smp_tree = self._make_tree(frame, cols)
        self._smp_tree.pack(fill="both", expand=True, padx=12, pady=4)
        self._smp_tree.bind("<Double-1>", self._on_sample_double_click)
        return frame

    def _refresh_samples(self):
        tree = self._smp_tree
        tree.delete(*tree.get_children())

        lbs_per_gal    = float(db.get_setting("lbs_per_gal", "2.2"))
        target_gal     = float(db.get_setting("target_gals_per_tray", "2.0"))
        samples        = db.get_samples()

        for s in samples:
            live_pct = s.get("xray_live_pct") or 0
            para_pct = s.get("xray_parasite_pct") or 0
            vol      = s.get("total_volume_gal") or 0
            summary  = calc.calc_sample_summary(vol, live_pct, target_gal, lbs_per_gal)

            tree.insert("", "end", iid=str(s["id"]), values=(
                s["name"],
                s.get("lot_number") or "—",
                f"{live_pct*100:.1f}%" if live_pct else "—",
                f"{para_pct*100:.1f}%" if para_pct else "—",
                f"{vol:.2f}" if vol else "—",
                f"{summary['live_gals_total']:.2f}" if vol and live_pct else "—",
                str(summary["tray_count"]) if vol and live_pct else "—",
                f"{summary['raw_lbs_per_tray']:.2f}" if vol and live_pct else "—",
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
        _btn(hdr, "QR Code", self._show_selected_qr,
             fg=CARD, hover=CARD2, width=90).pack(side="right", padx=6)

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

        self._flt_status = _combo(fbar, ["All", "active", "cooled", "released", "removed"], 130)
        self._flt_status.set("All")
        self._flt_status.pack(side="left", padx=6, pady=6)
        self._flt_status.configure(command=lambda _: self._refresh_trays())

        cols = ("Tray #", "Sample", "Incubator", "Batch",
                "Weight (lbs)", "Volume (gal)", "Live Count",
                "Parasite %", "In Date", "Out Date", "Status")
        self._tray_tree = self._make_tree(frame, cols)
        self._tray_tree.pack(fill="both", expand=True, padx=12, pady=4)
        self._tray_tree.bind("<Double-1>", self._on_tray_double_click)
        return frame

    def _refresh_trays(self):
        tree = self._tray_tree
        tree.delete(*tree.get_children())

        inc_id = self._flt_inc_map.get(self._flt_inc.get())
        status = self._flt_status.get()
        status = None if status == "All" else status

        for t in db.get_trays(incubator_id=inc_id, status=status):
            tree.insert("", "end", iid=str(t["id"]), values=(
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
                t.get("status") or "active",
            ))

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

        # Poll interval
        pf = section("Polling & Thresholds")
        self._set["poll_interval_sec"] = _FormRow(pf, 0, "Poll Interval (sec)", "60", 100)
        self._set["date_alert_lookahead"] = _FormRow(pf, 1, "Date Alert Lookahead (days)", "7", 100)
        self._set["temp_unit"] = _FormRow(pf, 2, "Temp Unit",
            widget=_combo(pf, ["C", "F"], 80))

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

        return frame

    def _refresh_settings(self):
        keys = ["govee_api_key", "poll_interval_sec", "date_alert_lookahead",
                "temp_unit", "lbs_per_gal", "target_gals_per_tray",
                "qr_server_port", "qr_server_enabled"]
        for k in keys:
            if k in self._set:
                self._set[k].set(db.get_setting(k))
        self._qr_ip_lbl.configure(
            text=f"Phone scan URL: http://{qr_server.get_local_ip()}:{self._qr_port}/tray/<id>")

    def _save_settings(self):
        keys = ["govee_api_key", "poll_interval_sec", "date_alert_lookahead",
                "temp_unit", "lbs_per_gal", "target_gals_per_tray",
                "qr_server_port", "qr_server_enabled"]
        for k in keys:
            if k in self._set:
                db.set_setting(k, self._set[k].get())
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

        if os.path.exists(new_path):
            if not messagebox.askyesno(
                    "File already exists",
                    f"A database already exists at:\n{new_path}\n\n"
                    "Overwrite it with your current data?",
                    parent=self):
                return

        # Copy current DB to new location
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
            f"Database copied to:\n{new_path}\n\n"
            "Close and reopen the app to start using the new location.\n\n"
            "Tip: on your other computer, go to Settings and click\n"
            "'Move & Restart', then browse to the same Google Drive folder.",
            parent=self)

    def _use_local_db(self):
        """Reset to default local file (remove any configured path)."""
        db.save_config({"db_path": ""})
        messagebox.showinfo(
            "Done — please restart",
            "Database location reset to the default (next to the app files).\n"
            "Restart the app for the change to take effect.",
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
        for tray in db.get_trays(incubator_id=inc["id"]):
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

    def _open_batch_dialog(self, batch: dict = None, incubator_id: int = None):
        BatchDialog(self, batch, incubator_id,
                    on_save=lambda: self._refresh_current())

    def _open_sample_dialog(self, sample: dict = None):
        SampleDialog(self, sample, on_save=lambda: self._refresh_current())

    def _open_tray_dialog(self, tray: dict = None, incubator_id: int = None):
        TrayDialog(self, tray, incubator_id,
                   on_save=lambda: self._refresh_current())

    def _delete_incubator(self, iid: int):
        if messagebox.askyesno("Delete", "Delete this incubator?"):
            db.delete_incubator(iid)
            self._refresh_current()

    def _set_hidden(self, iid: int, hidden: bool):
        db.set_incubator_hidden(iid, hidden)
        self._refresh_current()

    def _open_alerts(self):
        AlertsDialog(self, on_ack=self._refresh_alert_badge)

    def _open_inspection_form(self, inc: dict):
        """Open the inspection form for a given incubator."""
        reading   = self._govee.get_last(inc["id"])
        govee_tmp = reading.get("temp_c")
        InspectionDialog(
            self, inc, govee_temp_c=govee_tmp,
            on_save=self._refresh_current,
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
                    "No rows imported. Check that the spreadsheet has a header row "
                    "with recognizable column names (Name/Sample, Live%, Parasite%, "
                    "Dead%, Volume/Gal, Weight/Lbs)."
                )
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    def _parse_xray_spreadsheet(self, path: str) -> int:
        """
        Parse an x-ray results spreadsheet.
        Auto-detects column names (case-insensitive, partial match).
        Returns count of rows imported.
        """
        if path.lower().endswith(".csv"):
            import csv
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        else:
            wb   = openpyxl.load_workbook(path, data_only=True)
            ws   = wb.active
            hdrs = [str(c.value or "").strip() for c in next(ws.iter_rows(max_row=1))]
            rows = []
            for r in ws.iter_rows(min_row=2, values_only=True):
                rows.append(dict(zip(hdrs, r)))

        def _find(row, *keywords):
            for k in row:
                kl = k.lower()
                if any(kw in kl for kw in keywords):
                    v = row[k]
                    return str(v).strip() if v is not None else None
            return None

        def _pct(val):
            """Convert '82%' or '0.82' or '82' → float 0–1."""
            if val is None:
                return None
            v = str(val).replace("%", "").strip()
            try:
                f = float(v)
                return f / 100.0 if f > 1.0 else f
            except ValueError:
                return None

        count = 0
        for row in rows:
            name = _find(row, "name", "sample", "lot", "id")
            if not name:
                continue
            data = {
                "name":               name,
                "lot_number":         _find(row, "lot"),
                "source":             _find(row, "source", "supplier", "grower"),
                "xray_live_pct":      _pct(_find(row, "live", "viable")),
                "xray_parasite_pct":  _pct(_find(row, "parasit")),
                "xray_dead_pct":      _pct(_find(row, "dead", "stain")),
                "total_volume_gal":   None,
                "total_weight_lbs":   None,
                "import_date":        datetime.now().date().isoformat(),
            }
            vol = _find(row, "volume", "gal", "vol")
            if vol:
                try:
                    data["total_volume_gal"] = float(str(vol).replace(",", ""))
                except ValueError:
                    pass
            wt = _find(row, "weight", "lbs", "lb", "kg")
            if wt:
                try:
                    data["total_weight_lbs"] = float(str(wt).replace(",", ""))
                except ValueError:
                    pass
            db.upsert_sample(data)
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
        col  = RED if n else SUBTEXT
        self._alert_btn.configure(
            text=f"🔔  Alerts  {n}",
            text_color=col,
            fg_color=(RED if n else CARD),
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
        if inc:
            problems = calc.check_temp_humidity(inc, temp_c, humidity)
            for msg in problems:
                db.add_alert("temp_humidity", msg, severity="warning",
                             incubator_id=incubator_id)

        # Refresh UI on main thread
        self.after(0, self._on_reading_ui_refresh)

    def _on_reading_ui_refresh(self):
        self._refresh_alert_badge()
        if self._current_view == "dashboard":
            self._refresh_dashboard()

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

    # ── Git sync ──────────────────────────────────────────────────────────────

    def _git_pull(self):
        """Pull latest code from GitHub in a background thread."""
        def _pull():
            try:
                import subprocess
                app_dir = os.path.dirname(os.path.abspath(__file__))
                result  = subprocess.run(
                    ["git", "-C", app_dir, "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=20,
                )
                if result.returncode == 0:
                    msg = result.stdout.strip() or "Already up to date."
                    print(f"[git pull] {msg}")
                    self.after(0, lambda: self._set_git_status(msg, ok=True))
                else:
                    err = (result.stderr or result.stdout).strip()
                    print(f"[git pull] {err}")
                    self.after(0, lambda: self._set_git_status(f"pull: {err}", ok=False))
            except FileNotFoundError:
                # git not on PATH — silent, not a required dependency
                pass
            except Exception as exc:
                print(f"[git pull] {exc}")

        threading.Thread(target=_pull, daemon=True, name="GitPull").start()

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
