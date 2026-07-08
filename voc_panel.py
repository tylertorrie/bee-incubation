"""
voc_panel.py  —  VOC / Vapona monitor dashboard panel.

Embeds into the incubator detail window as a tab.

Requires: matplotlib  (pip install matplotlib)
"""
import csv
import json
import os
from datetime import datetime, timezone
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

import voc_db
import incubation_db as _idb

# The only chemical used — VOC monitoring runs continuously against this preset.
DDVP_NAME = "DDVP (Vapona)"

# ── Optional matplotlib ───────────────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── Colours (match main app's design-handoff theme) ──────────────────────────
GOLD    = "#FFD700"
DK_GOLD = "#B8860B"
GREEN   = "#10B981"
AMBER   = "#F59E0B"
RED     = "#FF3B30"
BLUE    = "#3B82F6"
TEAL    = "#06B6D4"
PANEL   = "#151E2E"
CARD    = "#1B2536"
CARD2   = "#202B3D"
BORDER  = "#374151"
BORDER2 = "#232F42"
TEXT    = "#F3F4F6"
SUBTEXT = "#9CA3AF"
FAINT   = "#6B7280"
BARBG   = "#0B1220"
ORANGE  = "#FF9800"

FONT_H = ("Segoe UI", 13, "bold")
FONT_B = ("Segoe UI", 11)
FONT_S = ("Segoe UI", 10)
FONT_M = ("Segoe UI", 20, "bold")

REFRESH_MS = 5 * 60 * 1000   # 5 minutes


def _lbl(parent, text, font=FONT_B, color=TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _btn(parent, text, cmd, width=110, height=30, fg=CARD2,
         hover=BORDER, tc=TEXT, **kw):
    return ctk.CTkButton(parent, text=text, command=cmd, width=width,
                         height=height, fg_color=fg, hover_color=hover,
                         text_color=tc, corner_radius=6, **kw)


# ══════════════════════════════════════════════════════════════════════════════
#  Run management dialogs
# ══════════════════════════════════════════════════════════════════════════════

class NewRunDialog(ctk.CTkToplevel):
    """Start a new incubation run for an incubator."""

    def __init__(self, master, incubator_id: int, on_save=None):
        super().__init__(master)
        self.incubator_id = incubator_id
        self.on_save      = on_save
        self.title("Start New VOC Run")
        self.geometry("460x440")
        self.resizable(False, False)
        self.grab_set()
        self._build()

    def _build(self):
        _lbl(self, "Start New VOC Monitoring Run", FONT_H, GOLD).pack(
            padx=20, pady=(16, 4), anchor="w")

        f = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12,
                         border_width=1, border_color=BORDER2)
        f.pack(fill="x", padx=16, pady=8)
        f.columnconfigure(1, weight=1)

        # Chemical preset
        presets    = voc_db.get_presets()
        self._pmap = {p["chemical_name"]: p for p in presets}
        names      = list(self._pmap.keys())

        _lbl(f, "Chemical", FONT_S, SUBTEXT).grid(
            row=0, column=0, sticky="w", padx=(12, 8), pady=6)
        self._chem_cb = ctk.CTkComboBox(
            f, values=names, width=260, fg_color=CARD2,
            border_color=BORDER, text_color=TEXT,
            command=self._on_preset_change)
        self._chem_cb.set(names[0] if names else "")
        self._chem_cb.grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        # Threshold preview
        self._thresh_frame = ctk.CTkFrame(f, fg_color=CARD2, corner_radius=8)
        self._thresh_frame.grid(row=1, column=0, columnspan=2,
                                sticky="ew", padx=12, pady=(0, 8))
        self._thresh_lbl = _lbl(self._thresh_frame, "", FONT_S, SUBTEXT)
        self._thresh_lbl.pack(padx=10, pady=6)

        # Unconfirmed warning
        self._warn_lbl = _lbl(f, "", FONT_S, AMBER)
        self._warn_lbl.grid(row=2, column=0, columnspan=2,
                             sticky="w", padx=12, pady=(0, 4))

        # Notes
        _lbl(f, "Notes", FONT_S, SUBTEXT).grid(
            row=3, column=0, sticky="nw", padx=(12, 8), pady=6)
        self._notes = ctk.CTkTextbox(f, height=60, fg_color=CARD,
                                     border_color=BORDER, text_color=TEXT)
        self._notes.grid(row=3, column=1, sticky="ew", padx=8, pady=6)

        self._on_preset_change(self._chem_cb.get())

        # Edit thresholds
        _btn(self, "Edit Thresholds", self._edit_thresholds,
             fg=BORDER, hover=CARD2, width=150).pack(
             anchor="w", padx=20, pady=(0, 4))

        # Buttons
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=12)
        _btn(bf, "Start Run", self._save, fg=DK_GOLD, hover=GOLD,
             tc="black", width=130).pack(side="right", padx=4)
        _btn(bf, "Cancel", self.destroy, width=100).pack(side="right")

    def _on_preset_change(self, name):
        p = self._pmap.get(name, {})
        if p:
            self._thresh_lbl.configure(
                text=(f"Safe range: {p['low_warn_ppm']:.2f} – "
                      f"{p['high_warn_ppm']:.2f} ppm    "
                      f"Alert: <{p['low_alert_ppm']:.2f} or "
                      f">{p['high_alert_ppm']:.2f} ppm"))
            warn = "" if p.get("confirmed") else \
                "⚠ Thresholds not confirmed — verify before relying on alerts."
            self._warn_lbl.configure(text=warn)

    def _edit_thresholds(self):
        name = self._chem_cb.get()
        p    = self._pmap.get(name)
        if p:
            PresetEditorDialog(self, preset=p,
                               on_save=lambda: self._reload_presets())

    def _reload_presets(self):
        presets    = voc_db.get_presets()
        self._pmap = {p["chemical_name"]: p for p in presets}
        names      = list(self._pmap.keys())
        self._chem_cb.configure(values=names)
        self._on_preset_change(self._chem_cb.get())

    def _save(self):
        name = self._chem_cb.get()
        p    = self._pmap.get(name)
        if not p:
            messagebox.showerror("Error", "Select a chemical preset.", parent=self)
            return
        if name == "Other / Custom" and not p.get("confirmed"):
            messagebox.showerror(
                "Error",
                "Custom preset thresholds must be set and confirmed "
                "before starting a run.\nClick 'Edit Thresholds' first.",
                parent=self)
            return
        notes = self._notes.get("1.0", "end").strip()
        run_id = voc_db.start_run(self.incubator_id, p["id"], notes)
        if self.on_save:
            self.on_save(run_id)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

class PresetEditorDialog(ctk.CTkToplevel):
    """Edit or create a chemical threshold preset."""

    def __init__(self, master, preset: dict = None, on_save=None, incubator_id=None):
        super().__init__(master)
        self.preset  = preset or {}
        self.on_save = on_save
        self._inc_id = incubator_id
        self.title("Edit Chemical Preset" if preset else "New Chemical Preset")
        self.geometry("460x560")
        self.resizable(False, False)
        self.grab_set()
        self._build()

    def _build(self):
        f = ctk.CTkScrollableFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4)
        f.columnconfigure(1, weight=1)

        def row(r, lbl, ph, key):
            _lbl(f, lbl, FONT_S, SUBTEXT).grid(
                row=r, column=0, sticky="w", padx=(8, 6), pady=4)
            e = ctk.CTkEntry(f, placeholder_text=ph, width=220,
                             fg_color=CARD, border_color=BORDER, text_color=TEXT)
            e.grid(row=r, column=1, sticky="ew", padx=6, pady=4)
            val = self.preset.get(key)
            if val is not None:
                e.insert(0, str(val))
            return e

        self._name  = row(0, "Chemical Name *", "e.g. DDVP (Vapona)", "chemical_name")
        self._desc  = row(1, "Description",      "",                   "description")
        self._la    = row(2, "Low Alert ppm",     "0.20",               "low_alert_ppm")
        self._lw    = row(3, "Low Warn ppm",      "0.25",               "low_warn_ppm")
        self._hw    = row(4, "High Warn ppm",     "0.60",               "high_warn_ppm")
        self._ha    = row(5, "High Alert ppm",    "0.70",               "high_alert_ppm")

        # Confirmed checkbox
        self._conf_var = ctk.BooleanVar(value=bool(self.preset.get("confirmed")))
        _lbl(f, "Confirmed / Calibrated", FONT_S, SUBTEXT).grid(
            row=6, column=0, sticky="w", padx=(8, 6), pady=4)
        ctk.CTkCheckBox(f, variable=self._conf_var, text="",
                        fg_color=GOLD, hover_color=DK_GOLD,
                        text_color=TEXT).grid(row=6, column=1, sticky="w", padx=6)

        # Suggest thresholds from captured readings
        if self._inc_id:
            _btn(f, "📊 Suggest from recent readings", self._suggest,
                 fg=BLUE, hover="#1D4ED8", tc="white", width=260).grid(
                 row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 2))

        # Range hint
        hint = ctk.CTkFrame(f, fg_color=CARD2, corner_radius=8)
        hint.grid(row=8, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        _lbl(hint,
             "Required order:  low_alert < low_warn < high_warn < high_alert",
             FONT_S, SUBTEXT).pack(padx=10, pady=6)

        # Reset builtin
        if self.preset.get("is_builtin"):
            _btn(f, "Reset to Defaults", self._reset, fg=BORDER, hover=CARD2,
                 width=160).grid(row=9, column=1, sticky="w", padx=6, pady=4)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=12)
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             tc="black", width=130).pack(side="right", padx=4)
        _btn(btns, "Cancel", self.destroy, width=100).pack(side="right")

    def _suggest(self):
        """Fill the threshold fields from this incubator's real captured readings."""
        if not self._inc_id:
            return
        dlg = ctk.CTkInputDialog(
            title="Suggest from readings",
            text="Analyze this incubator's VOC readings from the last how many "
                 "hours?\n(Cover the treatment you just captured — e.g. 48)")
        raw = dlg.get_input()
        if raw is None or not raw.strip():
            return
        try:
            hours = int(float(raw.strip()))
        except ValueError:
            messagebox.showerror("Suggest", "Enter a number of hours.", parent=self)
            return
        s = voc_db.suggest_thresholds(self._inc_id, hours)
        if not s:
            messagebox.showinfo("Suggest",
                "Not enough readings in that window (need at least 5).\n"
                "Run a treatment with the sensor first, then try again.", parent=self)
            return
        su = s["suggested"]
        for entry, key in ((self._la, "la"), (self._lw, "lw"),
                           (self._hw, "hw"), (self._ha, "ha")):
            entry.delete(0, "end")
            entry.insert(0, str(su[key]))
        messagebox.showinfo("Suggested from your data",
            f"Analyzed {s['count']} readings over the last {hours}h:\n"
            f"    baseline {s['baseline']}   median {s['median']}   "
            f"peak {s['peak']} ppm\n\n"
            "Filled the fields with a starting band around the treatment level.\n"
            "These are a starting point — adjust to your actual Vapona protocol,\n"
            "then Save and tick 'Confirmed' once you trust them.", parent=self)

    def _reset(self):
        if messagebox.askyesno(
                "Reset", "Restore original default threshold values?", parent=self):
            voc_db.reset_builtin_preset(self.preset["chemical_name"])
            if self.on_save:
                self.on_save()
            self.destroy()

    def _save(self):
        name = self._name.get().strip()
        if not name:
            messagebox.showerror("Error", "Chemical name is required.", parent=self)
            return
        try:
            vals = {
                "la": float(self._la.get()),
                "lw": float(self._lw.get()),
                "hw": float(self._hw.get()),
                "ha": float(self._ha.get()),
            }
        except ValueError:
            messagebox.showerror("Error", "All ppm values must be numbers.", parent=self)
            return
        if not (vals["la"] < vals["lw"] < vals["hw"] < vals["ha"]):
            messagebox.showerror(
                "Validation Error",
                "Values must satisfy:\n"
                "low_alert < low_warn < high_warn < high_alert",
                parent=self)
            return
        data = {
            "id":             self.preset.get("id"),
            "chemical_name":  name,
            "description":    self._desc.get().strip(),
            "low_alert_ppm":  vals["la"],
            "low_warn_ppm":   vals["lw"],
            "high_warn_ppm":  vals["hw"],
            "high_alert_ppm": vals["ha"],
            "confirmed":      int(self._conf_var.get()),
            "is_builtin":     int(self.preset.get("is_builtin", 0)),
        }
        voc_db.upsert_preset(data)
        if self.on_save:
            self.on_save()
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  Main VOC Panel
# ══════════════════════════════════════════════════════════════════════════════

class VOCPanel(ctk.CTkFrame):
    """Continuous VOC (Vapona) monitor for one incubator.

    Works like the temperature history: readings stream in continuously (the
    app only stores them while the incubator is on), and this panel shows the
    current level plus a trend chart against the fixed Vapona (DDVP) thresholds.
    No runs, no chemical picker.
    """

    RANGES = [("24H", 24), ("7D", 24 * 7), ("Month", 24 * 30)]

    def __init__(self, master, incubator_id: int, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.incubator_id = incubator_id
        self._hours    = 24
        self._fig      = None
        self._ax       = None
        self._canvas   = None
        self._after_id = None

        voc_db.ensure_sensor_positions(incubator_id)
        self._build()
        self.refresh()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _preset(self) -> dict:
        return voc_db.get_preset_by_name(DDVP_NAME) or {
            "low_alert_ppm": 0.20, "low_warn_ppm": 0.25,
            "high_warn_ppm": 0.60, "high_alert_ppm": 0.70, "confirmed": 0}

    def _mode(self) -> str:
        inc = next((i for i in _idb.get_incubators(include_hidden=True)
                    if i["id"] == self.incubator_id), None)
        return (inc.get("temp_mode") if inc else "incubation") or "incubation"

    @staticmethod
    def _age_seconds(ts):
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except Exception:
            return None

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        _lbl(hdr, "Vapona Monitor", FONT_H, GOLD).pack(side="left", padx=(4, 10))
        self._unconf_lbl = _lbl(hdr, "", FONT_S, AMBER)
        self._unconf_lbl.pack(side="left")

        _btn(hdr, "Edit thresholds", self._edit_thresholds,
             fg=CARD2, hover=BORDER, width=130, height=26).pack(side="right", padx=(0, 4))
        rng = ctk.CTkFrame(hdr, fg_color="transparent")
        rng.pack(side="right", padx=(0, 8))
        self._range_btns = {}
        for lbl, hrs in self.RANGES:
            b = ctk.CTkButton(
                rng, text=lbl, width=56, height=26, corner_radius=6,
                font=("Segoe UI", 10, "bold"),
                fg_color=GOLD if hrs == self._hours else CARD2,
                text_color="#1A1206" if hrs == self._hours else SUBTEXT,
                hover_color=BORDER, command=lambda h=hrs: self._set_range(h))
            b.pack(side="left", padx=2)
            self._range_btns[hrs] = b

        # Current level card
        cur = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12,
                           border_width=1, border_color=BORDER2)
        cur.pack(fill="x", padx=8, pady=4)
        self._cur_val = _lbl(cur, "—", ("Segoe UI", 30, "bold"), TEXT)
        self._cur_val.pack(side="left", padx=(16, 8), pady=12)
        col = ctk.CTkFrame(cur, fg_color="transparent")
        col.pack(side="left", pady=12)
        self._cur_zone = _lbl(col, "", FONT_B, SUBTEXT)
        self._cur_zone.pack(anchor="w")
        self._cur_sub = _lbl(col, "", FONT_S, SUBTEXT)
        self._cur_sub.pack(anchor="w")

        # Chart
        self._chart_outer = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12,
                                         border_width=1, border_color=BORDER2)
        self._chart_outer.pack(fill="both", expand=True, padx=8, pady=4)
        if HAS_MPL:
            self._init_chart()
        else:
            _lbl(self._chart_outer,
                 "Install matplotlib for trend charts:\npip install matplotlib",
                 FONT_B, SUBTEXT).pack(expand=True, pady=40)

        # Bottom bar
        bb = ctk.CTkFrame(self, fg_color="transparent")
        bb.pack(fill="x", padx=8, pady=(4, 8))
        _btn(bb, "Export CSV", self._export_csv, fg=CARD2, hover=BORDER,
             width=110, height=28).pack(side="right", padx=4)
        _btn(bb, "Save Chart", self._export_chart, fg=CARD2, hover=BORDER,
             width=110, height=28).pack(side="right", padx=4)
        _btn(bb, "Refresh Now", self.refresh, fg=CARD2, hover=BORDER,
             width=110, height=28).pack(side="right", padx=4)

    def _init_chart(self):
        self._fig = Figure(figsize=(6, 2.8), facecolor=PANEL)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor(PANEL)
        self._fig.subplots_adjust(left=0.08, right=0.97, top=0.94, bottom=0.18)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self._chart_outer)
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

    def _set_range(self, hours):
        self._hours = hours
        for h, b in self._range_btns.items():
            on = (h == hours)
            b.configure(fg_color=GOLD if on else CARD2,
                        text_color="#1A1206" if on else SUBTEXT)
        self.refresh()

    # ── Refresh ────────────────────────────────────────────────────────────────

    def refresh(self):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        p = self._preset()
        self._unconf_lbl.configure(
            text="" if p.get("confirmed") else "⚠ thresholds not confirmed")
        self._update_current(p)
        if HAS_MPL:
            self._update_chart(p)
        self._after_id = self.after(REFRESH_MS, self.refresh)

    def destroy(self):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        super().destroy()

    def _update_current(self, p):
        if self._mode() == "off":
            self._cur_val.configure(text="—", text_color=SUBTEXT)
            self._cur_zone.configure(text="Incubator off", text_color=SUBTEXT)
            self._cur_sub.configure(
                text="VOC is not collected while the incubator is off.",
                text_color=SUBTEXT)
            return
        latest = voc_db.get_latest_readings(self.incubator_id).get("front")
        if not latest or latest.get("voc_ppm") is None:
            self._cur_val.configure(text="—", text_color=SUBTEXT)
            self._cur_zone.configure(text="No readings yet", text_color=SUBTEXT)
            self._cur_sub.configure(text="Waiting for sensor data…", text_color=SUBTEXT)
            return
        ppm = latest["voc_ppm"]
        _, zlbl, zcol = voc_db.get_zone(ppm, p)
        self._cur_val.configure(text=f"{ppm:.3f}", text_color=zcol)
        self._cur_zone.configure(text=f"{zlbl}  ·  ppm", text_color=zcol)
        age = self._age_seconds(latest.get("timestamp"))
        if age is None:
            self._cur_sub.configure(text="", text_color=SUBTEXT)
        else:
            mins = int(age // 60)
            when = "just now" if mins < 1 else f"{mins} min ago"
            stale = age > 600
            self._cur_sub.configure(
                text=f"updated {when}" + (" · sensor may be offline" if stale else ""),
                text_color=(AMBER if stale else SUBTEXT))

    def _update_chart(self, p):
        rows = voc_db.get_recent_readings(self.incubator_id, self._hours)
        pts = [(r["timestamp"], r["voc_ppm"]) for r in rows
               if r.get("voc_ppm") is not None]
        ax = self._ax
        ax.clear()
        ax.set_facecolor(PANEL)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER2)
        ax.tick_params(colors=SUBTEXT, labelsize=8)
        ax.set_ylabel("VOC (ppm)", color=SUBTEXT, fontsize=8)

        if not pts:
            ax.text(0.5, 0.5, "No readings in this range",
                    transform=ax.transAxes, ha="center", va="center",
                    color=SUBTEXT, fontsize=10)
            self._fig.canvas.draw_idle()
            return

        # Build datetime series, breaking the line across gaps in the data
        xs, ys, prev = [], [], None
        for ts, ppm in pts:
            try:
                dt = datetime.fromisoformat(ts)
                # Sensor timestamps are stored in UTC; plot in local wall-clock
                # time so the curve lines up with the temperature charts.
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
            except Exception:
                continue
            if prev is not None and (dt - prev).total_seconds() > 1800:
                xs.append(prev + (dt - prev) / 2)
                ys.append(float("nan"))
            xs.append(dt)
            ys.append(ppm)
            prev = dt

        hw = p.get("high_warn_ppm", 0.60)
        ha = p.get("high_alert_ppm", 0.70)
        yvals = [v for v in ys if v == v]
        y_max = max(max(yvals) * 1.15, ha * 1.25, 0.8) if yvals else ha * 1.25

        # High-side threshold bands (low side is normal when not fumigating)
        ax.axhspan(0,  hw,    facecolor="#10B981", alpha=0.10, zorder=0)
        ax.axhspan(hw, ha,    facecolor="#F59E0B", alpha=0.14, zorder=0)
        ax.axhspan(ha, y_max, facecolor="#EF4444", alpha=0.14, zorder=0)
        ax.axhline(hw, color="#F59E0B", linewidth=0.9, linestyle="--", alpha=0.7)
        ax.axhline(ha, color="#EF4444", linewidth=0.9, linestyle="--", alpha=0.7)

        ax.plot(xs, ys, color="#FFD700", linewidth=1.8, zorder=3)
        ax.set_ylim(0, y_max)
        ax.grid(axis="y", color=BORDER2, linewidth=0.4, alpha=0.5)

        if self._hours <= 24:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        elif self._hours <= 24 * 7:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        for lbl in ax.xaxis.get_majorticklabels():
            lbl.set_rotation(0)
        self._fig.canvas.draw_idle()

    # ── Actions ────────────────────────────────────────────────────────────────

    def _edit_thresholds(self):
        p = self._preset()
        if not p.get("id"):
            voc_db.upsert_preset({"chemical_name": DDVP_NAME, "is_builtin": 1, **p})
            p = self._preset()
        PresetEditorDialog(self, preset=p, on_save=self.refresh,
                           incubator_id=self.incubator_id)

    def _export_csv(self):
        rows = voc_db.get_recent_readings(self.incubator_id, self._hours)
        if not rows:
            messagebox.showinfo("Export", "No readings to export.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Export VOC Readings", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"voc_inc{self.incubator_id}_{self._hours}h.csv")
        if not path:
            return
        p = self._preset()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "voc_ppm", "temp_c", "zone"])
            for r in rows:
                ppm = r.get("voc_ppm")
                _, zlbl, _ = voc_db.get_zone(ppm, p)
                w.writerow([r["timestamp"], ppm, r.get("temp_c"), zlbl])
        messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=self)

    def _export_chart(self):
        if not HAS_MPL or self._fig is None:
            messagebox.showinfo("Export", "No chart to export.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Save Chart", defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf")],
            initialfile="voc_trend.png")
        if path:
            self._fig.savefig(path, dpi=150, bbox_inches="tight",
                              facecolor=self._fig.get_facecolor())
            messagebox.showinfo("Saved", f"Chart saved to:\n{path}", parent=self)


# ══════════════════════════════════════════════════════════════════════════════
#  Presets manager (standalone dialog)
# ══════════════════════════════════════════════════════════════════════════════

class PresetsManagerDialog(ctk.CTkToplevel):
    """List + manage all chemical presets."""

    def __init__(self, master, on_save=None, incubator_id=None):
        super().__init__(master)
        self.on_save = on_save
        self.incubator_id = incubator_id
        self.title("Chemical Presets")
        self.geometry("620x500")
        self.grab_set()
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(12, 4))
        _lbl(hdr, "Chemical Presets", FONT_H, GOLD).pack(side="left")
        _btn(hdr, "+ Add Preset", lambda: self._edit(None),
             fg=DK_GOLD, hover=GOLD, tc="black", width=120).pack(side="right")

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=12, pady=4)
        self._load()

        _btn(self, "Close", self.destroy, width=100,
             fg=CARD2, hover=BORDER).pack(pady=8)

    def _load(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        for p in voc_db.get_presets():
            row = ctk.CTkFrame(self._scroll, fg_color=CARD, corner_radius=8)
            row.pack(fill="x", pady=3, padx=4)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="both", expand=True, padx=12, pady=8)

            name_row = ctk.CTkFrame(left, fg_color="transparent")
            name_row.pack(fill="x")
            _lbl(name_row, p["chemical_name"], FONT_B, GOLD).pack(side="left")
            if not p.get("confirmed"):
                _lbl(name_row, "  ⚠ unconfirmed", FONT_S, AMBER).pack(side="left")
            if p.get("is_builtin"):
                _lbl(name_row, "  [built-in]", FONT_S, SUBTEXT).pack(side="left")

            _lbl(left,
                 f"Safe: {p['low_warn_ppm']:.2f}–{p['high_warn_ppm']:.2f} ppm   "
                 f"Warn: <{p['low_warn_ppm']:.2f} or >{p['high_warn_ppm']:.2f}   "
                 f"Alert: <{p['low_alert_ppm']:.2f} or >{p['high_alert_ppm']:.2f}",
                 FONT_S, SUBTEXT).pack(anchor="w")

            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=10, pady=8)
            _btn(right, "Edit",
                 lambda pr=p: self._edit(pr),
                 width=70, height=26, fg=BORDER, hover=CARD2).pack(pady=2)
            if not p.get("is_builtin"):
                _btn(right, "Delete",
                     lambda pid=p["id"]: self._delete(pid),
                     width=70, height=26, fg="#4B0000", hover=RED).pack(pady=2)

    def _edit(self, preset):
        PresetEditorDialog(self, preset=preset, on_save=self._reload,
                           incubator_id=self.incubator_id)

    def _reload(self):
        self._load()
        if self.on_save:
            self.on_save()

    def _delete(self, preset_id: int):
        if messagebox.askyesno("Delete", "Delete this preset?", parent=self):
            ok = voc_db.delete_preset(preset_id)
            if ok:
                self._load()
            else:
                messagebox.showerror(
                    "Cannot Delete",
                    "This preset is used by one or more runs and cannot be deleted.",
                    parent=self)
