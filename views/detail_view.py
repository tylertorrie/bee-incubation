"""
views/detail_view.py  —  the single-incubator detail screen.

Extracted from incubation_app.py as a mixin (pure relocation): the readings
chart with its range selector and dotted goal lines, temp-mode controls, the
analytics summary, and the Inspections / Batches / Trays / VOC tabs.
"""
import time
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
from voc_panel import VOCPanel
from inspection_form import InspectionsLogPanel, make_status_badges

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)
from views.dialogs import IncubatorDialog, QRDialog

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


class DetailViewMixin:
    """Incubator detail screen: chart, controls and per-incubator tabs."""

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

        # ── Screen header (title + subtitle, actions on the right) ────────────
        self._detail_hdr = self._screen_header(
            frame, "Incubators", "Per-unit detail & inspection history")

        # ── Selector row (card) — exact prototype values ───────────────────────
        topbar = ctk.CTkFrame(frame, fg_color="#151E2E", corner_radius=12,
                              border_width=1, border_color="#232F42")
        topbar.pack(fill="x", padx=16, pady=(2, 6))

        _nav_list = db.get_incubators()  # visible incubators, in display order
        _cur_idx  = next((i for i, x in enumerate(_nav_list)
                          if x["id"] == fresh["id"]), 0)

        def _go_to(idx: int):
            if _nav_list:
                self._show_inc_detail(_nav_list[idx % len(_nav_list)])

        ctk.CTkButton(
            topbar, text="‹", command=lambda: _go_to(_cur_idx - 1),
            width=28, height=28, corner_radius=7,
            fg_color="#1F2937", hover_color="#28374D", text_color="#CBD5E1",
            font=("Segoe UI", 13),
        ).pack(side="left", padx=(16, 10), pady=12)
        _label(topbar, fresh["name"], ("Segoe UI", 15, "bold"), "#F3F4F6").pack(side="left")
        ctk.CTkButton(
            topbar, text="›", command=lambda: _go_to(_cur_idx + 1),
            width=28, height=28, corner_radius=7,
            fg_color="#1F2937", hover_color="#28374D", text_color="#CBD5E1",
            font=("Segoe UI", 13),
        ).pack(side="left", padx=10, pady=12)

        ctk.CTkFrame(topbar, fg_color="#263347", width=1, height=22).pack(
            side="left", padx=4)

        reading = self._govee.get_last(fresh["id"])
        if not reading:
            db_row  = db.get_latest_reading(fresh["id"])
            reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"]} if db_row else {}

        if reading.get("temp_c") is not None:
            t_min, t_max = calc.get_temp_range(fresh)
            _gt, _ = db.get_mode_goals(fresh.get("temp_mode", "incubation"))
            _tc = reading["temp_c"]
            if _gt is not None:
                _d = abs(_tc - _gt)
                t_col = GREEN if _d <= 1 else (ORANGE if _d <= 3 else RED)
            elif t_min is not None:
                t_col = GREEN if t_min <= _tc <= t_max else RED
            else:
                t_col = "#6B7280"
            _label(topbar, calc.format_temp(_tc, unit),
                   ("Segoe UI", 20, "bold"), t_col).pack(side="left", padx=(14, 2))
            _label(topbar, f"{reading['humidity']:.0f}% RH",
                   ("Segoe UI", 20, "bold"), GOLD).pack(side="left", padx=(2, 0))

        # AM/PM pills (pill shape, translucent fills, no border — spec exact)
        make_status_badges(
            topbar, fresh["id"], style="pill",
            on_click=lambda p, i=fresh: self._open_inspection_form(i)).pack(
            side="right", padx=(0, 14), pady=8)

        # Actions live in the screen header (right side)
        _hdr = self._detail_hdr
        _btn_primary(_hdr, "Inspect Now",
                     lambda i=fresh: self._open_inspection_form(i),
                     width=110).pack(side="right", padx=(6, 0))
        _btn_secondary(_hdr, "⚙ Edit Setup",
             lambda i=fresh: IncubatorDialog(
                 self, i,
                 on_save=lambda: self._refresh_current(),
                 on_delete=self._after_inc_delete),
             width=110).pack(side="right", padx=6)
        _pull_btn = _btn_secondary(_hdr, "⟳ Pull Reading", None, width=130)
        _pull_btn.configure(command=lambda i=fresh, b=_pull_btn: self._manual_poll_incubator(i, b))
        _pull_btn.pack(side="right", padx=6)
        _btn_secondary(_hdr, "+ Add",
                       lambda: self._open_incubator_dialog(),
                       width=80).pack(side="right", padx=6)

        # ── Mode + AC row (card) — exact prototype values ──────────────────────
        ctrl = ctk.CTkFrame(frame, fg_color="#151E2E", corner_radius=12,
                            border_width=1, border_color="#232F42")
        ctrl.pack(fill="x", padx=16, pady=(0, 6))

        _label(ctrl, "Temp Mode", ("Segoe UI", 11, "bold"), "#6B7280").pack(
            side="left", padx=(16, 16), pady=12)

        _det_mode_key = fresh.get("temp_mode", "incubation")

        def _set_detail_mode(key, iid=fresh["id"]):
            prev = next((x for x in db.get_incubators(include_hidden=True)
                         if x["id"] == iid), {}).get("temp_mode", "incubation")
            db.set_incubator_temp_mode(iid, key)
            self._sync_trays_to_mode(iid, key, prev)
            self._refresh_current()

        # Segmented control — no wrapping panel (spec: bare row of buttons, gap 5px)
        _seg = ctk.CTkFrame(ctrl, fg_color="transparent")
        _seg.pack(side="left", pady=8)
        for _mkey, _mlabel in (("off", "Off"), ("cool_storage", "Cool"),
                               ("incubation", "Inc"), ("holding", "Hold")):
            _act = (_mkey == _det_mode_key)
            _mc  = MODE_COLORS.get(_mkey, BLUE)
            _txt_col = GOLD if (_act and _mkey == "holding") else (_mc if _act else "#94A3B8")
            ctk.CTkButton(
                _seg, text=_mlabel, width=0, height=28, corner_radius=6,
                fg_color=MODE_BADGE_BG.get(_mkey, "#1B2536") if _act else "#1B2536",
                hover_color="#243044", text_color=_txt_col,
                font=("Segoe UI", 11, "bold" if _act else "normal"),
                border_width=1, border_color=(_mc if _act else "#2A3648"),
                command=lambda k=_mkey: _set_detail_mode(k),
            ).pack(side="left", padx=(0, 5))

        # Goal · range text
        _gt2, _ = db.get_mode_goals(_det_mode_key)
        _rmin, _rmax = calc.get_temp_range(fresh)
        _grp = []
        if _gt2 is not None:
            _grp.append(f"Goal {calc.format_temp(_gt2, unit)}")
        if _rmin is not None:
            _grp.append(f"range {_rmin}-{_rmax}°C")
        _label(ctrl, "  ·  ".join(_grp) if _grp else "No target set",
               ("Segoe UI", 11), "#6B7280").pack(side="left", padx=(14, 0), pady=10)

        # ── AC controls (right side): Power · Set Temp · Fan ───────────────────
        # Spec base style (all three identical when idle): #202B3D bg, #374151
        # border, #CBD5E1 text. We additionally tint Power green/red when the
        # AC's on/off state is known (implementation guide: toggle buttons
        # restyle on state) — the prototype's static mock doesn't show this
        # since it has no live device to read from.
        _dev = (fresh.get("sensibo_device_id") or "").strip()

        def _no_ac(*_):
            messagebox.showinfo("AC Control",
                "No Sensibo device is configured for this incubator.\n"
                "Add its Sensibo Device ID in Edit Setup.", parent=self)

        _ac_base = dict(fg_color="#202B3D", hover_color="#28374D",
                        text_color="#CBD5E1", border_width=1, border_color="#374151")

        if _dev:
            _pl, _pf, _ptc = self._ac_toggle_style(_dev)
            _fan_cmd  = lambda i=fresh["id"], d=_dev, n=fresh["name"]: self._sensibo_prompt_fan(i, d, n)
            _temp_cmd = lambda i=fresh["id"], d=_dev, n=fresh["name"]: self._sensibo_prompt_temp(i, d, n)
            _pow_cmd  = lambda i=fresh["id"], d=_dev: self._sensibo_toggle_power(i, d)
            _fan_txt  = self._ac_fan_label(_dev)
            _temp_txt = self._ac_temp_label(_dev)
        else:
            _pl, _pf, _ptc = "Power", "#202B3D", "#CBD5E1"
            _fan_cmd = _temp_cmd = _pow_cmd = _no_ac
            _fan_txt, _temp_txt = "Fan", "Set Temp"

        ctk.CTkButton(ctrl, text=_fan_txt, width=88, height=32, corner_radius=7,
                      font=("Segoe UI", 11),
                      command=_fan_cmd, **_ac_base).pack(side="right", padx=(4, 16), pady=8)
        ctk.CTkButton(ctrl, text=_temp_txt, width=96, height=32, corner_radius=7,
                      font=("Segoe UI", 11), command=_temp_cmd, **_ac_base
                      ).pack(side="right", padx=4, pady=8)
        ctk.CTkButton(ctrl, text=_pl, width=84, height=32, corner_radius=7,
                      fg_color=_pf, hover_color="#28374D", text_color=_ptc,
                      font=("Segoe UI", 11), border_width=1,
                      border_color=("#374151" if _pf == "#202B3D" else _pf),
                      command=_pow_cmd
                      ).pack(side="right", padx=4, pady=8)

        # ── Scrollable body ───────────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(frame, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True)

        # ── Temperature / Humidity Chart with time-range selector ────────────
        chart_frame = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=12,
                                   border_width=1, border_color=BORDER2)
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

        chart_title = _label(chart_hdr, "Last 24H", ("Segoe UI", 12.5, "bold"), GOLD)
        chart_title.pack(side="left")

        # Canvas holder — we replace its contents when the range changes
        chart_canvas_frame = ctk.CTkFrame(chart_frame, fg_color="transparent")
        chart_canvas_frame.pack(fill="x", padx=8, pady=(0, 4))

        # Analytics line for the selected range: avg temp / % time in range / degree-hours
        chart_stats = _label(chart_frame, "", FONT_S, SUBTEXT)
        chart_stats.pack(anchor="w", padx=14, pady=(0, 8))

        def _draw_chart(hours: float, label: str):
            for w in chart_canvas_frame.winfo_children():
                w.destroy()
            chart_title.configure(text=f"Last {label}")
            chart_stats.configure(text="")

            # Highlight active button (spec: active gold, others #202B3D)
            for lbl, btn in _range_btns.items():
                _on = (lbl == label)
                btn.configure(fg_color=GOLD if _on else CARD2,
                              text_color="#1A1206" if _on else SUBTEXT)

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

            # Analytics summary for this range
            _tmn, _tmx = calc.get_temp_range(fresh)
            _sm = calc.summarize_readings(readings, _tmn, _tmx)
            _bits = []
            if _sm["avg_temp"] is not None:
                _bits.append(f"Avg {calc.format_temp(_sm['avg_temp'], unit)}")
            if _sm["in_range_pct"] is not None:
                _bits.append(f"In range {_sm['in_range_pct']}%")
            if _sm["degree_hours"]:
                _bits.append(f"{_sm['degree_hours']:.0f} °C·h")
            chart_stats.configure(text="    ·    ".join(_bits))

            try:
                # Build series, breaking the line (NaN) across gaps in the data
                # so missing stretches don't get a misleading straight diagonal.
                _GAP_SEC = 2700  # 45 min (poll interval is 15 min)
                timestamps, temps, hums = [], [], []
                _prev_ts = None
                for r in readings:
                    ts = datetime.fromisoformat(r["timestamp"])
                    if _prev_ts is not None and (ts - _prev_ts).total_seconds() > _GAP_SEC:
                        timestamps.append(_prev_ts + (ts - _prev_ts) / 2)
                        temps.append(float("nan")); hums.append(float("nan"))
                    tv = r["temperature_c"]
                    timestamps.append(ts)
                    temps.append(calc.c_to_f(tv) if unit == "F" and tv is not None else tv)
                    hums.append(r["humidity_pct"])
                    _prev_ts = ts
                temp_lbl = f"Temp (°{unit})"

                fig = Figure(figsize=(10, 2.5), facecolor="#151E2E")
                ax1 = fig.add_subplot(111)
                ax2 = ax1.twinx()

                ax1.plot(timestamps, temps, color="#FFD700", linewidth=2)
                ax2.plot(timestamps, hums,  color="#3B82F6", linewidth=2,
                         linestyle=(0, (4, 3)))

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
                    ax2.axhline(goal_h, color="#3B82F6", linewidth=1.1, linestyle=":")

                for ax in (ax1, ax2):
                    ax.set_facecolor("#151E2E")
                    ax.tick_params(colors="#9CA3AF", labelsize=8)
                    for spine in ax.spines.values():
                        spine.set_edgecolor("#374151")

                ax1.set_ylabel(temp_lbl, color="#FFD700", fontsize=8)
                ax2.set_ylabel("Humidity %", color="#3B82F6", fontsize=8)

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

                # Legend row below the chart — exact spec: swatch + label, 10.5px muted
                leg_row = ctk.CTkFrame(chart_canvas_frame, fg_color="transparent")
                leg_row.pack(fill="x", pady=(6, 0))
                for _color, _text in (("#FFD700", "Temp °C"), ("#3B82F6", "Humidity %")):
                    _item = ctk.CTkFrame(leg_row, fg_color="transparent")
                    _item.pack(side="left", padx=(0, 16))
                    ctk.CTkFrame(_item, fg_color=_color, width=10, height=2
                                ).pack(side="left", pady=1)
                    _label(_item, " " + _text, ("Segoe UI", 10.5), "#6B7280").pack(side="left")
            except Exception as exc:
                _label(chart_canvas_frame, f"Chart error: {exc}", FONT_S, RED).pack(pady=10)

        for range_label, range_hours in _RANGES:
            rl, rh = range_label, range_hours
            b = ctk.CTkButton(
                btn_row, text=rl, width=52, height=26,
                fg_color=GOLD if rl == "24H" else CARD2,
                hover_color=BORDER,
                text_color="#1A1206" if rl == "24H" else SUBTEXT,
                corner_radius=6, font=("Segoe UI", 10, "bold"),
                command=lambda lbl=rl, hrs=rh: _draw_chart(hrs, lbl),
            )
            b.pack(side="left", padx=2)
            _range_btns[rl] = b

        # Defer chart so the screen appears instantly, then render after
        frame.after(50, lambda: _draw_chart(24, "24H"))

        # ── Development projection (degree-days) — only when a target is set ───
        _target_dd = db.get_setting("dev_target_degree_days", "")
        _act_batch = next(iter(db.get_batches(incubator_id=fresh["id"],
                                              status="active")), None)
        if _target_dd and _act_batch and _act_batch.get("start_date"):
            try:
                _base    = float(db.get_setting("dev_base_temp_c", "10") or 10)
                _tgt     = float(_target_dd)
                _elapsed = calc.get_incubation_day(_act_batch) or 1
                _rd      = db.get_readings_hours(fresh["id"], int(_elapsed * 24) + 24)
                _dd      = calc.accumulate_degree_days(_rd, base_c=_base)
                _pct, _days_left = calc.project_completion(_dd, _tgt, _elapsed)
                _txt = f"Development: {_dd:.0f} / {_tgt:.0f} °C-days  ({_pct}%)"
                if _days_left is not None:
                    _txt += f"   ·   ~{_days_left:.0f} day(s) to target"
                _dev_card = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10)
                _dev_card.pack(fill="x", padx=16, pady=(0, 8))
                _label(_dev_card, "🐝  " + _txt, FONT_B, GOLD).pack(
                    anchor="w", padx=14, pady=10)
                _label(_dev_card,
                       f"Above {_base:g}°C base · day {_elapsed} · projection from "
                       f"average accumulation rate (calibrate the target in Settings).",
                       FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 10))
            except Exception:
                pass

        # ── Tabs: Inspections / Batches / Trays / VOC ─────────────────────────
        # Tabs are built lazily — content is only created the first time each tab is selected.
        # CTkSegmentedButton always draws as ONE fused strip with no real gaps
        # between segments, which doesn't match the prototype's 4 separate
        # rounded pill buttons. So: use CTkTabview purely as the content
        # switcher (hide its built-in button row) and drive it with our own
        # row of individually-styled pill buttons instead.
        tabs = ctk.CTkTabview(
            body, fg_color="transparent", corner_radius=0, border_width=0,
        )
        # NOTE: .add() re-grids (re-shows) the built-in segmented button when
        # adding the FIRST tab — so grid_forget() must happen AFTER all tabs
        # are added, not before, or the built-in strip reappears alongside
        # our custom pill row (the duplicate tab-row bug).
        _tab_names = ("Inspections", "Batches", "Trays", "Vapona Monitor")
        for _tn in _tab_names:
            tabs.add(_tn)
        tabs._segmented_button.grid_forget()   # hide the fused built-in strip

        # Custom pill row — spec exact: gap 6px, padding 8px 16px, radius 7px,
        # active border #3B82F6 / bg #22314A / text #93C5FD, inactive #2A3648 / #1B2536 / #9CA3AF
        pill_row = ctk.CTkFrame(body, fg_color="transparent")
        pill_row.pack(pady=(4, 10))
        _pill_btns: dict = {}

        def _select_tab(name):
            tabs.set(name)
            for tn, btn in _pill_btns.items():
                _act = (tn == name)
                btn.configure(
                    fg_color="#22314A" if _act else "#1B2536",
                    hover_color="#28395A" if _act else CARD2,
                    text_color="#93C5FD" if _act else "#9CA3AF",
                    border_color="#3B82F6" if _act else "#2A3648",
                    font=("Segoe UI", 11.5, "bold" if _act else "normal"))
            frame._active_tab = name
            if name not in _tab_built:
                _tab_built.add(name)
                _tab_builders[name]()

        for _tn in _tab_names:
            _b = ctk.CTkButton(
                pill_row, text=_tn, height=32, corner_radius=7,
                fg_color="#1B2536", hover_color=CARD2, text_color="#9CA3AF",
                border_width=1, border_color="#2A3648",
                font=("Segoe UI", 11.5), command=lambda n=_tn: _select_tab(n),
            )
            _b.pack(side="left", padx=3)
            _pill_btns[_tn] = _b

        _tab_built: set = set()

        def _build_inspections_tab():
            it = tabs.tab("Inspections")
            InspectionsLogPanel(it, fixed_incubator_id=fresh["id"]).pack(fill="both", expand=True)

        def _build_batches_tab():
            bt = tabs.tab("Batches")
            _bhdr = ctk.CTkFrame(bt, fg_color="transparent")
            _bhdr.pack(fill="x", padx=8, pady=(8, 2))
            _btn_secondary(_bhdr, "+ New Batch",
                lambda i=fresh["id"]: self._open_batch_dialog(incubator_id=i),
                width=120).pack(side="right")
            bscroll = ctk.CTkScrollableFrame(bt, fg_color="transparent")
            bscroll.pack(fill="both", expand=True)
            batches = db.get_batches(incubator_id=fresh["id"])
            if batches:
                for batch in batches:
                    bf = ctk.CTkFrame(bscroll, fg_color=CARD2, corner_radius=8)
                    bf.pack(fill="x", pady=4, padx=4)
                    _brow = ctk.CTkFrame(bf, fg_color="transparent")
                    _brow.pack(fill="x", padx=10, pady=(8, 0))
                    bname = batch.get("name") or f"Batch {batch['id']}"
                    _label(_brow, bname, FONT_B, GOLD).pack(side="left")
                    _btn_secondary(_brow, "Edit",
                        lambda b=batch: self._open_batch_dialog(batch=b),
                        width=64).pack(side="right")
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
                                         fg_color="#2A1E20", hover_color="#3A2224",
                                         text_color="#FF6A57", state="normal")
                else:
                    delete_btn.configure(text="Delete Selected",
                                         fg_color="#2A1E20", hover_color="#3A2224",
                                         text_color="#FF6A57", state="disabled")

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
            _btn_secondary(tray_hdr, "⬇ Template",
                 lambda: self._tray_csv_template(default_incubator_name=fresh["name"]),
                 width=110).pack(side="right", padx=(4, 0))
            _btn(tray_hdr, "Import CSV",
                 lambda: self._tray_csv_import(default_incubator_id=fresh["id"],
                                               on_complete=_refresh_tray_list),
                 width=100, height=28, fg=TEAL, hover="#0D9488",
                 text_color="white").pack(side="right", padx=4)
            _btn(tray_hdr, "Release CSV",
                 lambda: self._release_csv_import(on_complete=_refresh_tray_list),
                 width=110, height=28, fg="#7C3AED", hover="#6D28D9",
                 text_color="white").pack(side="right", padx=4)
            _btn_secondary(tray_hdr, "⬇ Release Template", self._release_csv_template,
                 width=150).pack(side="right", padx=(0, 2))
            delete_btn = ctk.CTkButton(tray_hdr, text="Delete Selected", width=130, height=28,
                fg_color="#2A1E20", hover_color="#3A2224", text_color="#FF6A57",
                corner_radius=8, font=("Segoe UI", 11, "bold"),
                border_width=1, border_color="#5A2A2C",
                state="disabled", command=_delete_selected)
            delete_btn.pack(side="right", padx=4)
            _btn_secondary(tray_hdr, "Select All", _select_all_toggle,
                 width=90).pack(side="right", padx=(0, 2))
            _refresh_tray_list()

        def _build_voc_tab():
            vt = tabs.tab("Vapona Monitor")
            VOCPanel(vt, incubator_id=fresh["id"]).pack(fill="both", expand=True)

        # Register all tab names first (required before .tab() can be called)
        # Map tab name → builder function (tabs were already added above,
        # alongside the custom pill row that drives _select_tab)
        _tab_builders = {
            "Inspections": _build_inspections_tab,
            "Batches":     _build_batches_tab,
            "Trays":       _build_trays_tab,
            "Vapona Monitor": _build_voc_tab,
        }

        # Also pack the content area itself now that tabs/pills exist
        tabs.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # Restore the previously active tab (or default to Inspections)
        restore_tab = _prev_tab if _prev_tab in _tab_builders else "Inspections"
        _select_tab(restore_tab)

    def _open_inc_detail_window(self, inc: dict):
        """Open a detail window with Vapona Monitor, Inspections, Batches and Trays tabs."""
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

        # ── Vapona Monitor tab (first — most used) ───────────────────────────────
        vt = tabs.add("Vapona Monitor")
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

