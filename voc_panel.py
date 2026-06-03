"""
voc_panel.py  —  VOC / Vapona monitor dashboard panel.

Embeds into the incubator detail window as a tab.

Requires: matplotlib  (pip install matplotlib)
"""
import csv
import json
import os
from datetime import datetime
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

import voc_db

# ── Optional matplotlib ───────────────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── Colours (match main app palette) ─────────────────────────────────────────
GOLD    = "#FFD700"
DK_GOLD = "#B8860B"
GREEN   = "#10B981"
AMBER   = "#F59E0B"
RED     = "#EF4444"
BLUE    = "#3B82F6"
TEAL    = "#06B6D4"
CARD    = "#1F2937"
CARD2   = "#263347"
BORDER  = "#374151"
TEXT    = "#F3F4F6"
SUBTEXT = "#9CA3AF"
SIDEBAR = "#111827"
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

        f = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
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

    def __init__(self, master, preset: dict = None, on_save=None):
        super().__init__(master)
        self.preset  = preset or {}
        self.on_save = on_save
        self.title("Edit Chemical Preset" if preset else "New Chemical Preset")
        self.geometry("460x500")
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

        # Range hint
        hint = ctk.CTkFrame(f, fg_color=CARD2, corner_radius=8)
        hint.grid(row=7, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        _lbl(hint,
             "Required order:  low_alert < low_warn < high_warn < high_alert",
             FONT_S, SUBTEXT).pack(padx=10, pady=6)

        # Reset builtin
        if self.preset.get("is_builtin"):
            _btn(f, "Reset to Defaults", self._reset, fg=BORDER, hover=CARD2,
                 width=160).grid(row=8, column=1, sticky="w", padx=6, pady=4)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=12)
        _btn(btns, "Save", self._save, fg=DK_GOLD, hover=GOLD,
             tc="black", width=130).pack(side="right", padx=4)
        _btn(btns, "Cancel", self.destroy, width=100).pack(side="right")

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
    """
    Full VOC monitoring dashboard for one incubator.
    Drop into any CTkTabview tab or CTkFrame container.
    """

    def __init__(self, master, incubator_id: int, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.incubator_id = incubator_id
        self._active_run  = None
        self._shown_run   = None     # may be a historical run
        self._fig         = None
        self._canvas      = None
        self._after_id    = None

        voc_db.ensure_sensor_positions(incubator_id)
        self._build()
        self.refresh()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # Run bar (top)
        self._run_bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=8)
        self._run_bar.pack(fill="x", padx=8, pady=(8, 4))
        self._run_info_lbl = _lbl(self._run_bar, "No active run", FONT_B, SUBTEXT)
        self._run_info_lbl.pack(side="left", padx=12, pady=6)
        self._unconf_lbl = _lbl(self._run_bar, "", FONT_S, AMBER)
        self._unconf_lbl.pack(side="left", padx=4)

        rb = ctk.CTkFrame(self._run_bar, fg_color="transparent")
        rb.pack(side="right", padx=8, pady=6)
        _btn(rb, "New Run",  self._new_run,  fg=DK_GOLD, hover=GOLD, tc="black",
             width=90, height=28).pack(side="right", padx=3)
        self._end_btn = _btn(rb, "End Run", self._end_run,
                             fg=BORDER, hover=CARD2, width=85, height=28)
        self._end_btn.pack(side="right", padx=3)
        _btn(rb, "Presets", self._open_presets, fg=BORDER, hover=CARD2,
             width=80, height=28).pack(side="right", padx=3)

        # Summary cards
        self._cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._cards_frame.pack(fill="x", padx=8, pady=4)

        self._card_front = self._make_card("Front Sensor",  BLUE)
        self._card_back  = self._make_card("Back Sensor",   TEAL)
        self._card_delta = self._make_card("Front/Back Δ",  SUBTEXT)
        self._card_temp  = self._make_card("Temperature",   ORANGE)
        self._card_day   = self._make_card("Run Day",       GOLD)

        for card in (self._card_front, self._card_back,
                     self._card_delta, self._card_temp, self._card_day):
            card.pack(side="left", expand=True, fill="x", padx=4)

        # Chart area
        self._chart_outer = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        self._chart_outer.pack(fill="both", expand=True, padx=8, pady=4)

        if HAS_MPL:
            self._init_chart()
        else:
            _lbl(self._chart_outer,
                 "Install matplotlib for trend charts:\n"
                 "pip install matplotlib",
                 FONT_B, SUBTEXT).pack(expand=True, pady=40)

        # Bottom bar
        bb = ctk.CTkFrame(self, fg_color="transparent")
        bb.pack(fill="x", padx=8, pady=(4, 8))

        # History selector
        _lbl(bb, "View run:", FONT_S, SUBTEXT).pack(side="left", padx=(4, 4))
        self._history_cb = ctk.CTkComboBox(
            bb, values=["Active run"], width=200,
            fg_color=CARD, border_color=BORDER, text_color=TEXT,
            command=self._on_history_select)
        self._history_cb.set("Active run")
        self._history_cb.pack(side="left", padx=4)

        _btn(bb, "Export CSV",   self._export_csv,   fg=BORDER, hover=CARD2,
             width=110, height=28).pack(side="right", padx=4)
        _btn(bb, "Save Chart",   self._export_chart, fg=BORDER, hover=CARD2,
             width=110, height=28).pack(side="right", padx=4)
        _btn(bb, "Refresh Now",  self.refresh,       fg=BORDER, hover=CARD2,
             width=110, height=28).pack(side="right", padx=4)

    def _make_card(self, title: str, accent: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self._cards_frame, fg_color=CARD,
                            corner_radius=10, border_width=1, border_color=BORDER)
        _lbl(card, title, FONT_S, accent).pack(pady=(6, 0))
        card._val_lbl   = _lbl(card, "—", FONT_M, TEXT)
        card._val_lbl.pack()
        card._badge_lbl = _lbl(card, "", FONT_S, SUBTEXT)
        card._badge_lbl.pack(pady=(0, 6))
        return card

    def _update_card(self, card, value_text: str,
                     badge_text: str, badge_color: str):
        card._val_lbl.configure(text=value_text)
        card._badge_lbl.configure(text=badge_text, text_color=badge_color)

    # ── Chart initialisation ──────────────────────────────────────────────────

    def _init_chart(self):
        self._fig = Figure(figsize=(6, 2.8), facecolor="#111827")
        self._ax  = self._fig.add_subplot(111)
        self._fig.subplots_adjust(left=0.08, right=0.97, top=0.92, bottom=0.14)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self._chart_outer)
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass

        self._active_run = voc_db.get_active_run(self.incubator_id)
        if self._shown_run is None or (
                self._shown_run.get("status") == "active"):
            self._shown_run = self._active_run

        self._update_run_bar()
        self._update_history_dropdown()
        self._update_cards()
        if HAS_MPL:
            self._update_chart()

        self._after_id = self.after(REFRESH_MS, self.refresh)

    def destroy(self):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        super().destroy()

    # ── Run bar ───────────────────────────────────────────────────────────────

    def _update_run_bar(self):
        run = self._active_run
        if run:
            start   = (run.get("start_time") or "")[:10]
            days    = self._run_day(run)
            txt     = f"Active: {run['chemical_name']}  |  Started: {start}  |  Day {days}"
            self._run_info_lbl.configure(text=txt, text_color=GOLD)
            self._end_btn.configure(state="normal")
            snap = voc_db.run_snapshot(run)
            preset = voc_db.get_preset(run.get("preset_id") or 0) or {}
            if not preset.get("confirmed"):
                self._unconf_lbl.configure(
                    text="⚠ Preset not confirmed")
            else:
                self._unconf_lbl.configure(text="")
        else:
            self._run_info_lbl.configure(
                text="No active run — click New Run to start", text_color=SUBTEXT)
            self._end_btn.configure(state="disabled")
            self._unconf_lbl.configure(text="")

    @staticmethod
    def _run_day(run: dict) -> int:
        try:
            start = datetime.fromisoformat(run["start_time"])
            return max(1, (datetime.now() - start).days + 1)
        except Exception:
            return 1

    # ── Summary cards ─────────────────────────────────────────────────────────

    def _update_cards(self):
        readings = voc_db.get_latest_readings(self.incubator_id)
        run      = self._active_run
        preset   = voc_db.run_snapshot(run) if run else {}

        # Front
        fr = readings.get("front")
        if fr and fr.get("voc_ppm") is not None:
            ppm = fr["voc_ppm"]
            _, zlbl, zcol = voc_db.get_zone(ppm, preset)
            self._update_card(self._card_front, f"{ppm:.3f}", zlbl, zcol)
        else:
            self._update_card(self._card_front, "—", "No data", SUBTEXT)

        # Back
        bk = readings.get("back")
        if bk and bk.get("voc_ppm") is not None:
            ppm = bk["voc_ppm"]
            _, zlbl, zcol = voc_db.get_zone(ppm, preset)
            self._update_card(self._card_back, f"{ppm:.3f}", zlbl, zcol)
        else:
            self._update_card(self._card_back, "—", "No data", SUBTEXT)

        # Delta
        if (fr and fr.get("voc_ppm") is not None and
                bk and bk.get("voc_ppm") is not None):
            delta = abs(fr["voc_ppm"] - bk["voc_ppm"])
            dcol  = RED if delta > 0.10 else (AMBER if delta > 0.05 else GREEN)
            self._update_card(self._card_delta, f"{delta:.3f}",
                              "High Δ — check placement" if delta > 0.10 else "OK", dcol)
        else:
            self._update_card(self._card_delta, "—", "", SUBTEXT)

        # Temperature (use whichever sensor has it)
        temp_c = None
        for r in (fr, bk):
            if r and r.get("temp_c") is not None:
                temp_c = r["temp_c"]
                break
        if temp_c is not None:
            self._update_card(self._card_temp, f"{temp_c:.1f}°C", "", TEXT)
        else:
            self._update_card(self._card_temp, "—", "No data", SUBTEXT)

        # Run day
        if run:
            day = self._run_day(run)
            self._update_card(self._card_day, str(day), run["chemical_name"][:16], GOLD)
        else:
            self._update_card(self._card_day, "—", "No active run", SUBTEXT)

    # ── Trend chart ───────────────────────────────────────────────────────────

    def _update_chart(self):
        if not HAS_MPL:
            return
        run = self._shown_run
        if not run:
            self._draw_empty_chart("No active run")
            return
        readings = voc_db.get_run_readings(run["id"])
        if not readings:
            self._draw_empty_chart("No readings yet for this run")
            return

        # Split by position
        front = [(r["timestamp"], r["voc_ppm"]) for r in readings
                 if r["position"] == "front" and r["voc_ppm"] is not None]
        back  = [(r["timestamp"], r["voc_ppm"]) for r in readings
                 if r["position"] == "back"  and r["voc_ppm"] is not None]

        try:
            start_dt = datetime.fromisoformat(run["start_time"])
        except Exception:
            start_dt = datetime.now()

        def to_days(ts_str):
            try:
                return (datetime.fromisoformat(ts_str) - start_dt
                        ).total_seconds() / 86400
            except Exception:
                return 0

        fx = [to_days(t) for t, _ in front]
        fy = [v for _, v in front]
        bx = [to_days(t) for t, _ in back]
        by = [v for _, v in back]

        snap = voc_db.run_snapshot(run)
        la   = snap.get("low_alert_ppm",  0.20)
        lw   = snap.get("low_warn_ppm",   0.25)
        hw   = snap.get("high_warn_ppm",  0.60)
        ha   = snap.get("high_alert_ppm", 0.70)

        ax = self._ax
        ax.clear()
        ax.set_facecolor("#111827")
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
        ax.tick_params(colors=SUBTEXT, labelsize=8)
        ax.set_xlabel("Day of run", color=SUBTEXT, fontsize=8)
        ax.set_ylabel("VOC (ppm)", color=SUBTEXT, fontsize=8)
        title = f"{run['chemical_name']}  —  "
        title += "Active run" if run["status"] == "active" else \
                 f"Ended {(run.get('end_time') or '')[:10]}"
        ax.set_title(title, color=GOLD, fontsize=9, pad=4)

        # Zone bands — determine y extent
        all_y  = fy + by
        y_max  = max(max(all_y) * 1.15, ha * 1.2, 0.80) if all_y else ha * 1.2
        y_min  = 0.0

        ax.axhspan(y_min, la,    facecolor="#EF4444", alpha=0.12, zorder=0)
        ax.axhspan(la,    lw,    facecolor="#F59E0B", alpha=0.12, zorder=0)
        ax.axhspan(lw,    hw,    facecolor="#10B981", alpha=0.14, zorder=0)
        ax.axhspan(hw,    ha,    facecolor="#F59E0B", alpha=0.12, zorder=0)
        ax.axhspan(ha,    y_max, facecolor="#EF4444", alpha=0.12, zorder=0)

        # Zone boundary lines
        for val, col in [(la, "#EF4444"), (lw, "#F59E0B"),
                         (hw, "#F59E0B"), (ha, "#EF4444")]:
            ax.axhline(val, color=col, linewidth=0.8, linestyle="--", alpha=0.6)

        # Data lines
        if fx:
            ax.plot(fx, fy, color=BLUE,  linewidth=1.6,
                    label="Front", marker=".", markersize=3, zorder=3)
        if bx:
            ax.plot(bx, by, color=TEAL,  linewidth=1.6,
                    label="Back",  marker=".", markersize=3, zorder=3)

        # Legend with zone labels
        legend_labels = [
            (f"Safe: {lw:.2f}–{hw:.2f} ppm",     "#10B981"),
            (f"Warn: {la:.2f}–{lw:.2f} / {hw:.2f}–{ha:.2f}", "#F59E0B"),
            (f"Alert: <{la:.2f} or >{ha:.2f}",    "#EF4444"),
        ]
        if fx or bx:
            ax.legend(fontsize=7, facecolor="#1F2937",
                      edgecolor=BORDER, labelcolor=TEXT,
                      loc="upper left")

        ax.set_ylim(y_min, y_max)
        ax.grid(axis="y", color=BORDER, linewidth=0.4, alpha=0.5)
        self._fig.canvas.draw_idle()

    def _draw_empty_chart(self, msg: str):
        ax = self._ax
        ax.clear()
        ax.set_facecolor("#111827")
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER)
        ax.text(0.5, 0.5, msg, transform=ax.transAxes,
                ha="center", va="center", color=SUBTEXT, fontsize=10)
        self._fig.canvas.draw_idle()

    # ── History dropdown ──────────────────────────────────────────────────────

    def _update_history_dropdown(self):
        runs = voc_db.get_runs(self.incubator_id)
        self._run_list = runs
        labels = []
        for r in runs:
            start = (r.get("start_time") or "")[:10]
            tag   = "▶" if r["status"] == "active" else "■"
            labels.append(f"{tag} {r['chemical_name']} ({start})")
        if not labels:
            labels = ["No runs yet"]
        self._history_cb.configure(values=labels)
        if self._shown_run:
            for i, r in enumerate(runs):
                if r["id"] == self._shown_run.get("id"):
                    self._history_cb.set(labels[i])
                    break

    def _on_history_select(self, choice):
        idx = self._history_cb.cget("values").index(choice)
        if 0 <= idx < len(self._run_list):
            self._shown_run = self._run_list[idx]
            if HAS_MPL:
                self._update_chart()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _new_run(self):
        NewRunDialog(self, self.incubator_id,
                     on_save=lambda _: self.refresh())

    def _end_run(self):
        run = self._active_run
        if not run:
            return
        if messagebox.askyesno("End Run",
                               f"End run '{run['chemical_name']}'?",
                               parent=self):
            voc_db.end_run(run["id"])
            self.refresh()

    def _open_presets(self):
        PresetsManagerDialog(self, on_save=self.refresh)

    def _export_csv(self):
        run = self._shown_run
        if not run:
            messagebox.showinfo("Export", "Select a run first.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Export Readings",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"voc_run_{run['id']}_{run['chemical_name'].replace(' ','_')}.csv",
        )
        if not path:
            return
        readings = voc_db.get_run_readings(run["id"])
        snap     = voc_db.run_snapshot(run)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "position", "voc_ppm", "temp_c", "zone"])
            for r in readings:
                ppm = r.get("voc_ppm")
                _, zlbl, _ = voc_db.get_zone(ppm, snap)
                w.writerow([r["timestamp"], r["position"],
                             ppm, r.get("temp_c"), zlbl])
        messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=self)

    def _export_chart(self):
        if not HAS_MPL or self._fig is None:
            messagebox.showinfo("Export", "No chart to export.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Save Chart",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf")],
            initialfile="voc_trend_chart.png",
        )
        if path:
            self._fig.savefig(path, dpi=150, bbox_inches="tight",
                              facecolor=self._fig.get_facecolor())
            messagebox.showinfo("Saved", f"Chart saved to:\n{path}", parent=self)


# ══════════════════════════════════════════════════════════════════════════════
#  Presets manager (standalone dialog)
# ══════════════════════════════════════════════════════════════════════════════

class PresetsManagerDialog(ctk.CTkToplevel):
    """List + manage all chemical presets."""

    def __init__(self, master, on_save=None):
        super().__init__(master)
        self.on_save = on_save
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
        PresetEditorDialog(self, preset=preset, on_save=self._reload)

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
