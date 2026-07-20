"""
views/dialogs.py  —  modal dialog windows for the desktop GUI.

Extracted from incubation_app.py as a pure relocation (no behaviour change):
IncubatorDialog, BatchDialog, SampleDialog, TrayDialog, QRDialog, AlertsDialog,
_VocDeviceManager and _WifiNetworkManager.
"""
import time
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
import qr_server
import voc_db

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)

# Optional imports (same graceful degradation as the main module)
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


class IncubatorDialog(ctk.CTkToplevel):
    def __init__(self, master, inc: dict = None, on_save=None, on_delete=None):
        super().__init__(master, fg_color=BG)
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
            ("Sensibo Device ID(s)", "ID1, ID2",  "sensibo_device_id"),
            ("Incubation Start (YYYY-MM-DD)", "2026-06-01", "incubation_start"),
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
        super().__init__(master, fg_color=BG)
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
        super().__init__(master, fg_color=BG)
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
            ("Total Kgs",           "total_weight_kg",   ""),
            ("Total Pounds",        "total_weight_lbs",  ""),
            ("Live Bees per KG",    "live_bees_per_kg",  ""),
            ("Live Bees per Pound", "live_bees_per_lb",  ""),
            ("Parasites",           "parasites",         ""),
            ("Chalkbrood",          "chalkbrood",        ""),
            ("Total Gal Bees",      "total_volume_gal",  ""),
            ("Total KG for 2gal",   "kg_per_2gal",       ""),
            ("Total lbs for 2gal",  "lbs_per_2gal",      ""),
            ("Expected Trays",      "total_trays",       ""),
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

        if data.get("id"):
            # Editing an existing sample — update it directly.
            db.upsert_sample(data)
        else:
            # New sample: if one with this name already exists, update it (keeps
            # tray links) rather than silently creating a duplicate.
            existing = db.find_sample_by_name(name)
            if existing:
                if messagebox.askyesno(
                    "Sample already exists",
                    f"A sample named “{existing['name']}” already exists.\n\n"
                    "Update that sample with these values?  (Recommended — keeps "
                    "all its trays linked so the data shows on them.)\n\n"
                    "Choose No to create a separate new sample anyway.",
                    parent=self):
                    db.upsert_sample_by_name(data)
                else:
                    db.upsert_sample(data)
            else:
                db.upsert_sample(data)
        if self.on_save:
            self.on_save()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

class TrayDialog(ctk.CTkToplevel):
    def __init__(self, master, tray: dict = None,
                 incubator_id: int = None, on_save=None):
        super().__init__(master, fg_color=BG)
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
        super().__init__(master, fg_color=BG)
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
        super().__init__(master, fg_color=BG)
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


class _VocDeviceManager(ctk.CTkToplevel):
    """Assign Vapona (VOC) sensors to incubators / positions. App-authoritative:
    each Pi reports a stable hardware_id and the app owns name/incubator/position."""

    _POSITIONS = ["front", "back"]
    _ONLINE_SECS = 40 * 60  # a sensor polling every 15 min is "online" within 40 min

    def __init__(self, master, on_close=None):
        super().__init__(master, fg_color=BG)
        self.on_close = on_close
        self.title("Vapona Sensors")
        self.geometry("720x520")
        self.grab_set()
        # incubator id -> display name, plus an "Unassigned" choice
        self._incs = [i for i in db.get_incubators() if not i.get("is_hidden")]
        self._inc_label = {i["id"]: i["name"] for i in self._incs}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))
        _label(top, "Vapona Sensors", FONT_H, GOLD).pack(side="left")
        _btn(top, "Refresh", self._load, fg=CARD2, hover=BORDER,
             width=90).pack(side="right")
        _label(self, "Sensors report a hardware ID automatically. Assign each to an "
                     "incubator and position, then Save.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=18, pady=(0, 4))
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=CARD)
        self._scroll.pack(fill="both", expand=True, padx=12, pady=8)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 12))
        _btn(btns, "Save Assignments", self._save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=160).pack(side="right", padx=4)
        _btn(btns, "Close", self._close, width=100).pack(side="right")

        self._load()

    def _online(self, last_seen: str) -> bool:
        if not last_seen:
            return False
        try:
            dt = datetime.fromisoformat(last_seen)
            return (datetime.now() - dt).total_seconds() <= self._ONLINE_SECS
        except Exception:
            return False

    def _load(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._rows = []
        try:
            devs = voc_db.get_devices()
        except Exception as exc:
            _label(self._scroll, f"Could not load devices: {exc}",
                   FONT_S, RED).pack(pady=20)
            return
        if not devs:
            _label(self._scroll, "No sensors have reported yet.\n"
                   "Power on a Pi sensor — it will appear here automatically.",
                   FONT_S, SUBTEXT).pack(pady=24)
            return

        inc_choices = ["Unassigned"] + [self._inc_label[i["id"]] for i in self._incs]
        for d in devs:
            card = ctk.CTkFrame(self._scroll, fg_color=CARD2, corner_radius=8)
            card.pack(fill="x", pady=4, padx=4)
            card.columnconfigure(1, weight=1)

            online = self._online(d.get("last_seen"))
            dot = "● Online" if online else "○ Offline"
            seen = (d.get("last_seen") or "")[:16].replace("T", " ")
            _label(card, dot, FONT_S, GREEN if online else SUBTEXT).grid(
                row=0, column=0, sticky="w", padx=10, pady=(8, 0))
            _label(card, f"hardware: {d['hardware_id']}", FONT_S, SUBTEXT).grid(
                row=0, column=1, sticky="w", padx=6, pady=(8, 0))
            _label(card, f"last seen {seen}" if seen else "never",
                   FONT_S, SUBTEXT).grid(row=0, column=2, sticky="e", padx=10, pady=(8, 0))

            body = ctk.CTkFrame(card, fg_color="transparent")
            body.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=8)

            _label(body, "Name", FONT_S, SUBTEXT).grid(row=0, column=0, padx=(2, 4))
            name_e = ctk.CTkEntry(body, width=150, fg_color=CARD,
                                  border_color=BORDER, text_color=TEXT)
            name_e.grid(row=0, column=1, padx=4)
            name_e.insert(0, d.get("name") or "")

            _label(body, "Incubator", FONT_S, SUBTEXT).grid(row=0, column=2, padx=(12, 4))
            inc_var = ctk.StringVar(
                value=self._inc_label.get(d.get("incubator_id"), "Unassigned"))
            inc_cb = ctk.CTkComboBox(body, values=inc_choices, variable=inc_var,
                                     width=150, fg_color=CARD, border_color=BORDER,
                                     button_color=BORDER, text_color=TEXT,
                                     dropdown_fg_color=CARD, state="readonly")
            inc_cb.grid(row=0, column=3, padx=4)

            _label(body, "Position", FONT_S, SUBTEXT).grid(row=0, column=4, padx=(12, 4))
            pos_var = ctk.StringVar(value=d.get("position") or "front")
            pos_cb = ctk.CTkComboBox(body, values=self._POSITIONS, variable=pos_var,
                                     width=90, fg_color=CARD, border_color=BORDER,
                                     button_color=BORDER, text_color=TEXT,
                                     dropdown_fg_color=CARD, state="readonly")
            pos_cb.grid(row=0, column=5, padx=4)

            _btn(body, "🗑", lambda did=d["id"]: self._delete(did),
                 width=36, height=28, fg=BORDER, hover=RED,
                 text_color="white").grid(row=0, column=6, padx=(12, 2))

            self._rows.append((d["id"], name_e, inc_var, pos_var))

    def _inc_id_from_label(self, label):
        if label in (None, "", "Unassigned"):
            return None
        for iid, name in self._inc_label.items():
            if name == label:
                return iid
        return None

    def _save(self):
        for dev_id, name_e, inc_var, pos_var in self._rows:
            voc_db.update_device(
                dev_id,
                name=name_e.get().strip(),
                incubator_id=self._inc_id_from_label(inc_var.get()),
                position=pos_var.get())
        messagebox.showinfo("Vapona Sensors", "Assignments saved.", parent=self)
        self._load()

    def _delete(self, dev_id):
        if not messagebox.askyesno(
                "Delete sensor",
                "Remove this sensor from the app?\n\nIf it is still powered on it "
                "will re-appear (unassigned) the next time it reports.", parent=self):
            return
        voc_db.delete_device(dev_id)
        self._load()

    def _close(self):
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass
        self.destroy()


class _WifiNetworkManager(ctk.CTkToplevel):
    """Manage the Wi-Fi networks pushed to every Vapona sensor. Every Pi is
    provisioned with all of these, so moving a sensor between incubators (each
    on its own network) needs no reconfiguration — it auto-joins whichever is
    in range."""

    def __init__(self, master):
        super().__init__(master, fg_color=BG)
        self.title("Sensor Wi-Fi Networks")
        self.geometry("620x480")
        self.grab_set()
        self._build()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))
        _label(top, "Sensor Wi-Fi Networks", FONT_H, GOLD).pack(side="left")
        _btn(top, "+ Add Network", self._add, fg=GREEN, hover="#15803D",
             text_color="white", width=130).pack(side="right")
        _label(self, "Each incubator's network. All sensors receive every network "
                     "and auto-connect to whichever is in range.\n"
                     "Note: passwords are stored in the shared database.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=18, pady=(0, 4))
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=CARD)
        self._scroll.pack(fill="both", expand=True, padx=12, pady=8)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 12))
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=120).pack(side="right", padx=4)
        _btn(btns, "Close", self.destroy, width=100).pack(side="right")
        self._load()

    def _load(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._rows = []
        nets = voc_db.get_wifi_networks()
        # Column headers
        hdr = ctk.CTkFrame(self._scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=4, pady=(2, 0))
        for txt, w in (("SSID", 170), ("Password", 170), ("Priority", 70), ("", 40)):
            _label(hdr, txt, FONT_S, SUBTEXT, width=w, anchor="w").pack(
                side="left", padx=4)
        if not nets:
            _label(self._scroll, "No networks yet — click “+ Add Network”.",
                   FONT_S, SUBTEXT).pack(pady=16)
        for n in nets:
            self._row_widget(n)

    def _row_widget(self, n):
        row = ctk.CTkFrame(self._scroll, fg_color=CARD2, corner_radius=6)
        row.pack(fill="x", padx=4, pady=3)
        ssid_e = ctk.CTkEntry(row, width=170, fg_color=CARD,
                              border_color=BORDER, text_color=TEXT)
        ssid_e.pack(side="left", padx=4, pady=6)
        ssid_e.insert(0, n.get("ssid") or "")
        psk_e = ctk.CTkEntry(row, width=170, fg_color=CARD, show="•",
                             border_color=BORDER, text_color=TEXT)
        psk_e.pack(side="left", padx=4, pady=6)
        psk_e.insert(0, n.get("psk") or "")
        prio_e = ctk.CTkEntry(row, width=60, fg_color=CARD,
                              border_color=BORDER, text_color=TEXT)
        prio_e.pack(side="left", padx=4, pady=6)
        prio_e.insert(0, str(n.get("priority") or 0))
        # Reveal/hide password
        def _toggle(e=psk_e, b=None):
            e.configure(show="" if e.cget("show") else "•")
        _btn(row, "👁", lambda: _toggle(), width=32, height=26,
             fg=BORDER, hover=CARD).pack(side="left", padx=(0, 2))
        _btn(row, "🗑", lambda nid=n.get("id"): self._delete(nid),
             width=32, height=26, fg=BORDER, hover=RED,
             text_color="white").pack(side="left", padx=(2, 6))
        self._rows.append((n.get("id"), ssid_e, psk_e, prio_e))

    def _add(self):
        # Persist current edits, then append a blank row
        self._save(silent=True)
        voc_db.upsert_wifi_network(ssid=f"New Network {len(self._rows)+1}")
        self._load()

    def _save(self, silent=False):
        seen = set()
        for nid, ssid_e, psk_e, prio_e in self._rows:
            ssid = ssid_e.get().strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            try:
                prio = int(prio_e.get().strip() or "0")
            except ValueError:
                prio = 0
            voc_db.upsert_wifi_network(ssid=ssid, psk=psk_e.get(),
                                       priority=prio, net_id=nid)
        if not silent:
            messagebox.showinfo("Sensor Wi-Fi",
                "Networks saved. Sensors pick up changes within a few minutes.",
                parent=self)
            self._load()

    def _delete(self, nid):
        if not nid:
            return
        if messagebox.askyesno(
                "Delete network", "Remove this Wi-Fi network?\n\n"
                "Sensors keep any profile already installed until they are "
                "reprovisioned or reflashed.", parent=self):
            voc_db.delete_wifi_network(nid)
            self._load()

