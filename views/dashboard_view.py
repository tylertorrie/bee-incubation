"""
views/dashboard_view.py  —  the Dashboard and Incubators screens.

Extracted from incubation_app.py as a mixin (pure relocation).
"""
import time

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
from inspection_form import make_status_badges
from app_config import POLL_INTERVAL_SEC

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)


class DashboardViewMixin:
    """The dashboard and incubators screens."""

    def _build_dashboard(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = self._screen_header(frame, "Dashboard",
                                  "Live monitoring · incubator status & controls")
        _btn_primary(hdr, "+ Incubator", lambda: self._open_incubator_dialog(),
                     width=130).pack(side="right")
        _btn_secondary(hdr, "Refresh", self._refresh_dashboard,
                       width=100).pack(side="right", padx=8)

        # "Show hidden" toggle — only visible when hidden incubators exist
        self._dash_show_hidden = ctk.BooleanVar(value=False)
        self._dash_hidden_btn  = ctk.CTkButton(
            hdr, text="", width=150, height=28,
            fg_color=CARD2, hover_color=BORDER, text_color=SUBTEXT,
            corner_radius=6, font=FONT_S,
            command=self._toggle_dash_hidden)
        # packed conditionally in _refresh_dashboard

        # Body: card area (left, expands) + incubator mode panel (right, flush)
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="both", expand=True)

        # Pack the right-hand mode panel FIRST so it reserves the right strip;
        # the card area then expands into all the remaining width.
        mode_col = ctk.CTkFrame(body, fg_color=RIGHTPANE, width=300, corner_radius=0)
        mode_col.pack(side="right", fill="y")
        mode_col.pack_propagate(False)
        _label(mode_col, "Incubator Modes", ("Segoe UI", 13, "bold"), GOLD).pack(
            anchor="w", padx=16, pady=(16, 0))
        _label(mode_col, "Set operating mode per unit", ("Segoe UI", 11), FAINT).pack(
            anchor="w", padx=16, pady=(0, 8))
        # Fixed (non-scrolling) — rows share the height so all units fit
        self._dash_mode_panel = ctk.CTkFrame(mode_col, fg_color="transparent")
        self._dash_mode_panel.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._dash_scroll = ctk.CTkScrollableFrame(
            body, fg_color="transparent", corner_radius=0)
        self._dash_scroll.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=4)

        frame._card_container = self._dash_scroll
        return frame

    def _toggle_dash_hidden(self):
        self._dash_show_hidden.set(not self._dash_show_hidden.get())
        self._refresh_dashboard()

    def _refresh_mode_panel(self):
        """Right-hand dashboard panel: each incubator with a temp-mode selector."""
        panel = getattr(self, "_dash_mode_panel", None)
        if panel is None:
            return
        for w in panel.winfo_children():
            w.destroy()
        panel.columnconfigure(0, weight=1)
        incs = db.get_incubators(include_hidden=True)
        if not incs:
            _label(panel, "No incubators yet.", FONT_S, SUBTEXT).pack(pady=10)
            return
        # Spec: Off / Cool / Inc / Hold short segments, active = mode color
        _SEGS = [("off", "Off"), ("cool_storage", "Cool"),
                 ("incubation", "Inc"), ("holding", "Hold")]
        for _ri, inc in enumerate(incs):
            panel.rowconfigure(_ri, weight=1, uniform="moderow")
            row = ctk.CTkFrame(panel, fg_color=NESTED, corner_radius=10,
                               border_width=1, border_color=BORDER2)
            row.grid(row=_ri, column=0, sticky="nsew", pady=3, padx=2)
            head = ctk.CTkFrame(row, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 4))
            _label(head, inc["name"], ("Segoe UI", 12, "bold"),
                   "#E5E7EB").pack(side="left")
            _btn(head, "✎", lambda i=inc: self._open_incubator_dialog(i),
                 width=28, height=22, fg="transparent",
                 hover=CARD2, text_color=SUBTEXT).pack(side="right")
            segrow = ctk.CTkFrame(row, fg_color="transparent")
            segrow.pack(fill="x", padx=10, pady=(0, 9))
            cur = inc.get("temp_mode", "incubation")
            for ci in range(4):
                segrow.columnconfigure(ci, weight=1, uniform="seg")
            for ci, (key, short) in enumerate(_SEGS):
                active = (key == cur)
                mcol   = MODE_COLORS[key]
                ctk.CTkButton(
                    segrow, text=short, height=28, width=40, corner_radius=6,
                    font=("Segoe UI", 10, "bold" if active else "normal"),
                    fg_color=_mix(mcol, NESTED, 0.22) if active else CARD,
                    hover_color=_mix(mcol, NESTED, 0.30) if active else CARD2,
                    text_color=mcol if active else "#94A3B8",
                    border_width=1,
                    border_color=mcol if active else "#2A3648",
                    command=lambda k=key, i=inc["id"]: self._on_dash_mode_key(k, i),
                ).grid(row=0, column=ci, sticky="ew", padx=2)

    def _on_dash_mode(self, label: str, inc_id: int):
        self._on_dash_mode_key(calc._MODE_BY_LABEL.get(label, "incubation"), inc_id)

    def _on_dash_mode_key(self, key: str, inc_id: int):
        prev = next((x for x in db.get_incubators(include_hidden=True)
                     if x["id"] == inc_id), {}).get("temp_mode", "incubation")
        if key == prev:
            return
        db.set_incubator_temp_mode(inc_id, key)
        self._sync_trays_to_mode(inc_id, key, prev)   # cool-down prompt if relevant
        self.after(50, self._refresh_dashboard)        # deferred so the widget isn't
                                                       # destroyed mid-callback

    def _refresh_dashboard(self):
        self._refresh_mode_panel()
        self._card_widgets.clear()
        container = self._dash_scroll
        for w in container.winfo_children():
            w.destroy()

        # Incubators with an active alert → their cards get a red outline
        self._alert_inc_ids = {a["incubator_id"] for a in db.get_active_alerts()
                               if a.get("incubator_id")}

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
        # Total capacity = every incubator (even off/hidden), so the denominator
        # stays constant regardless of which incubators are turned on.
        total_capacity = sum((i.get("capacity") or 0) for i in all_inc)
        fill_pct       = round(tray_count / total_capacity * 100) if total_capacity else 0
        fill_col       = GREEN if fill_pct < 80 else (ORANGE if fill_pct < 95 else RED)

        _n_alerts = len(db.get_active_alerts())
        summary_f = ctk.CTkFrame(container, fg_color="transparent")
        summary_f.pack(fill="x", pady=(0, 14), padx=4)
        metrics = [
            ("Active Incubators", str(len(incubators)),               GOLD),
            ("Trays",             f"{tray_count} / {total_capacity}", GOLD),
            ("Capacity",          f"{fill_pct}% full",                fill_col),
            ("Total Gals",        f"{total_gals:.1f}",                GOLD),
            ("Active Alerts",     str(_n_alerts),  RED if _n_alerts else GOLD),
        ]
        for i in range(len(metrics)):
            summary_f.columnconfigure(i, weight=1, uniform="metric")
        for i, (txt, val, col) in enumerate(metrics):
            mc = ctk.CTkFrame(summary_f, fg_color=PANEL, corner_radius=12,
                              border_width=1, border_color=BORDER2)
            mc.grid(row=0, column=i, sticky="ew", padx=6)
            _label(mc, val, ("Segoe UI", 22, "bold"), col).pack(anchor="w", padx=16, pady=(14, 0))
            _label(mc, txt, ("Segoe UI", 11), SUBTEXT).pack(anchor="w", padx=16, pady=(0, 14))

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
                # available card width = window − nav sidebar − mode panel
                w    = self.winfo_width() - 380
                cols = 4 if w >= 1400 else (3 if w >= 1000 else (2 if w >= 600 else 1))
                if getattr(grid, "_last_cols", None) != cols:
                    grid._last_cols = cols
                    _build_grid(cols)
            grid._resize_job = self.after(200, _do)

        self.bind("<Configure>", _on_resize, add=True)
        _on_resize()

    def _make_inc_card(self, parent, inc: dict) -> ctk.CTkFrame:
        is_hidden = bool(inc.get("is_hidden"))
        card_bg   = "#161E2C" if is_hidden else CARD
        title_col = SUBTEXT  if is_hidden else TEXT
        bdr_col   = "#222D3D" if is_hidden else "#263347"

        # Red outline when this incubator has an active alert
        has_alert  = inc["id"] in getattr(self, "_alert_inc_ids", set())
        bdr_width  = 2 if has_alert else 1
        if has_alert:
            bdr_col = RED

        card = ctk.CTkFrame(parent, fg_color=card_bg, corner_radius=11,
                            border_width=bdr_width, border_color=bdr_col)

        # ── Readings ──
        reading = self._govee.get_last(inc["id"])
        if not reading:
            db_row  = db.get_latest_reading(inc["id"])
            reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"],
                       "timestamp": db_row["timestamp"]} if db_row else {}
        temp_c = reading.get("temp_c")
        hum    = reading.get("humidity")

        t_min, t_max   = calc.get_temp_range(inc)
        unit           = db.get_setting("temp_unit", "C")
        goal_t, goal_h = db.get_mode_goals(inc.get("temp_mode", "incubation"))
        if temp_c is not None:
            t_str = calc.format_temp(temp_c, unit)
            # Temp color rule: vs goal (≤1°=green, ≤3°=orange, else red); else range
            if goal_t is not None:
                _d = abs(temp_c - goal_t)
                t_col = GREEN if _d <= 1 else (ORANGE if _d <= 3 else RED)
            elif t_min is not None:
                t_col = GREEN if t_min <= temp_c <= t_max else RED
            else:
                t_col = SUBTEXT
            h_col = GOLD
            problems  = calc.check_temp_humidity(inc, temp_c, hum)
            dot_color = RED if problems else GREEN
        else:
            t_str = "—"
            t_col = h_col = dot_color = SUBTEXT

        # ── Header: name + chips + status dot ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        name_txt = f"{inc['name']}  (hidden)" if is_hidden else inc["name"]
        _label(hdr, name_txt, ("Segoe UI", 13, "bold"), title_col).pack(side="left")
        mode_key = inc.get("temp_mode", "incubation")
        mode_cfg = calc.TEMP_MODES.get(mode_key, calc.TEMP_MODES["incubation"])
        _mcol = MODE_COLORS.get(mode_key, BLUE)
        ctk.CTkLabel(hdr, text=mode_cfg["label"], font=("Segoe UI", 9, "bold"),
                     fg_color=MODE_BADGE_BG.get(mode_key, _mix(_mcol, card_bg, 0.20)),
                     text_color=_mcol,
                     corner_radius=6, height=20, padx=8).pack(side="left", padx=(8, 0))
        if has_alert:
            _n = sum(1 for a in db.get_active_alerts()
                     if a.get("incubator_id") == inc["id"])
            ctk.CTkLabel(hdr, text=f"{_n} Alert{'s' if _n != 1 else ''}",
                         font=("Segoe UI", 9, "bold"),
                         fg_color=_mix(RED, card_bg, 0.16), text_color=RED_LT,
                         corner_radius=6, height=20, padx=8).pack(side="left", padx=(6, 0))
        lbl_dot = _label(hdr, "●", ("Segoe UI", 18), dot_color)
        lbl_dot.pack(side="right")
        # Subtle bell toggle for temp alerts (spec has no header toggle — keep it minimal)
        alerts_on = bool(inc.get("temp_alerts_enabled", 1))
        ctk.CTkButton(
            hdr, text="🔔" if alerts_on else "🔕", width=26, height=22,
            fg_color="transparent", hover_color=CARD2,
            text_color=SUBTEXT if alerts_on else "#6B7280",
            font=("Segoe UI", 12), corner_radius=6,
            command=lambda i=inc: self._toggle_temp_alerts(i)
        ).pack(side="right", padx=(0, 4))

        # ── Large temp / humidity tiles (with per-mode goals) ──
        sensor_row = ctk.CTkFrame(card, fg_color="transparent")
        sensor_row.pack(fill="x", padx=12, pady=(0, 8))
        sensor_row.columnconfigure(0, weight=1)
        sensor_row.columnconfigure(1, weight=1)

        tf = ctk.CTkFrame(sensor_row, fg_color=CARD2, corner_radius=8,
                          border_width=1, border_color="#2A3648")
        tf.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        _label(tf, "TEMP", ("Segoe UI", 9, "bold"), SUBTEXT).pack(pady=(8, 0))
        lbl_temp = _label(tf, t_str if temp_c is not None else "—", ("Segoe UI", 20, "bold"), t_col)
        lbl_temp.pack(pady=(2, 0))
        _label(tf, f"Goal {calc.format_temp(goal_t, unit)}" if goal_t is not None else "—",
               ("Segoe UI", 9), FAINT).pack(pady=(0, 8))

        hf = ctk.CTkFrame(sensor_row, fg_color=CARD2, corner_radius=8,
                          border_width=1, border_color="#2A3648")
        hf.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        _label(hf, "HUMIDITY", ("Segoe UI", 9, "bold"), SUBTEXT).pack(pady=(8, 0))
        lbl_hum = _label(hf, f"{hum:.0f}%" if hum is not None else "—", ("Segoe UI", 20, "bold"), h_col)
        lbl_hum.pack(pady=(2, 0))
        _label(hf, f"Goal {goal_h:.0f}%" if goal_h is not None else "—",
               ("Segoe UI", 9), FAINT).pack(pady=(0, 8))

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
                                          "dot": lbl_dot, "ts": lbl_ts, "inc": inc,
                                          "card": card, "hidden": is_hidden}
        batches = db.get_batches(incubator_id=inc["id"], status="active")
        events  = calc.get_all_events(batches, lookahead_days=14)
        if events:
            ev   = events[0]
            ecol = RED if ev["urgent"] else (ORANGE if ev["days_away"] <= 5 else TEXT)
            _label(left, f"→ {ev['label']}: {calc.format_days(ev['days_away'])}", FONT_S, ecol).pack(anchor="w")
        else:
            _label(left, "No upcoming events", FONT_S, SUBTEXT).pack(anchor="w")

        # Right: tray count — aggregate query, no full row fetch
        right = ctk.CTkFrame(bottom, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        _ts    = db.get_tray_stats(incubator_id=inc["id"], status=db.IN_INCUBATOR_STATUSES)
        capacity   = inc.get("capacity") or 50
        fill_pct   = round(_ts["count"] / capacity * 100) if capacity else 0
        _label(right, f"{_ts['count']} / {capacity} trays",
               ("Segoe UI", 10, "bold"), TEXT2).pack(anchor="e")

        # ── Capacity bar (spec: 6px track, gold fill) ──
        cap_bar = ctk.CTkProgressBar(
            card, height=6, corner_radius=5,
            fg_color=CARD2, progress_color=GOLD)
        cap_bar.set(min(fill_pct / 100, 1.0))
        cap_bar.pack(fill="x", padx=14, pady=(2, 0))
        cap_sub = ctk.CTkFrame(card, fg_color="transparent")
        cap_sub.pack(fill="x", padx=14, pady=(1, 6))
        _label(cap_sub, f"{fill_pct}% filled", ("Segoe UI", 9), FAINT).pack(side="left")
        _label(cap_sub, f"{_ts['total_gals']:.1f} gal", ("Segoe UI", 9), FAINT).pack(side="right")

        # ── Sensibo AC manual controls (only when a device ID is configured) ──
        if inc.get("sensibo_device_id"):
            ac_row = ctk.CTkFrame(card, fg_color="transparent")
            ac_row.pack(fill="x", padx=12, pady=(2, 6))
            _label(ac_row, "AC:", FONT_S, SUBTEXT).pack(side="left", padx=(2, 6))
            _t_lbl, _t_fg, _t_tc = self._ac_toggle_style(inc["sensibo_device_id"])
            _ac_power_btn = ctk.CTkButton(
                ac_row, text=_t_lbl, width=82, height=28, corner_radius=14,
                fg_color=_t_fg, hover_color=BORDER, text_color=_t_tc,
                font=("Segoe UI", 11, "bold"),
                command=lambda i=inc["id"], d=inc["sensibo_device_id"]: self._sensibo_toggle_power(i, d),
            )
            _ac_power_btn.pack(side="left", padx=2)
            _ac_temp_btn = _btn(ac_row, self._ac_temp_label(inc["sensibo_device_id"]),
                 lambda i=inc["id"], d=inc["sensibo_device_id"], n=inc["name"]: self._sensibo_prompt_temp(i, d, n),
                 width=78, height=28, fg=CARD2, hover=BORDER)
            _ac_temp_btn.pack(side="left", padx=2)
            _ac_fan_btn = _btn(ac_row, self._ac_fan_label(inc["sensibo_device_id"]),
                 lambda i=inc["id"], d=inc["sensibo_device_id"], n=inc["name"]: self._sensibo_prompt_fan(i, d, n),
                 width=78, height=28, fg=CARD2, hover=BORDER)
            _ac_fan_btn.pack(side="left", padx=2)
            # Store refs so Sensibo handlers can update just these without full refresh
            self._card_widgets[inc["id"]]["ac_power"] = _ac_power_btn
            self._card_widgets[inc["id"]]["ac_temp"]  = _ac_temp_btn
            self._card_widgets[inc["id"]]["ac_fan"]   = _ac_fan_btn

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

        hdr = self._screen_header(frame, "Incubators",
                                  "Per-unit detail & inspection history")
        _btn_primary(hdr, "+ Add Incubator", lambda: self._open_incubator_dialog(),
                     width=150).pack(side="right")

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
            row_bg    = "#141C2B" if hidden else PANEL
            bdr_col   = "#1E2938" if hidden else BORDER2
            name_col  = SUBTEXT  if hidden else GOLD

            row = ctk.CTkFrame(container, fg_color=row_bg, corner_radius=12,
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

            # Sensibo AC manual control (only if a device ID is configured)
            if inc.get("sensibo_device_id"):
                ac_row = ctk.CTkFrame(left, fg_color="transparent")
                ac_row.pack(anchor="w", pady=(6, 0))
                _label(ac_row, "AC:", FONT_S, SUBTEXT).pack(side="left", padx=(0, 6))
                _it_lbl, _it_fg, _it_tc = self._ac_toggle_style(inc["sensibo_device_id"])
                ctk.CTkButton(
                    ac_row, text=_it_lbl, width=80, height=24, corner_radius=12,
                    fg_color=_it_fg, hover_color=BORDER, text_color=_it_tc,
                    font=("Segoe UI", 10, "bold"),
                    command=lambda i=inc["id"], d=inc["sensibo_device_id"]: self._sensibo_toggle_power(i, d),
                ).pack(side="left", padx=2)
                _btn(ac_row, self._ac_temp_label(inc["sensibo_device_id"]),
                     lambda i=inc["id"], d=inc["sensibo_device_id"], n=inc["name"]: self._sensibo_prompt_temp(i, d, n),
                     width=78, height=24, fg=CARD2, hover=BORDER).pack(side="left", padx=2)
                _btn(ac_row, self._ac_fan_label(inc["sensibo_device_id"]),
                     lambda i=inc["id"], d=inc["sensibo_device_id"], n=inc["name"]: self._sensibo_prompt_fan(i, d, n),
                     width=78, height=24, fg=CARD2, hover=BORDER).pack(side="left", padx=2)

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

