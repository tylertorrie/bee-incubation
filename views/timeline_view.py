"""
views/timeline_view.py  —  the Timeline/calendar screen, ICS export and Google Calendar sync.

Extracted from incubation_app.py as a mixin (pure relocation).
"""
import json
import os
import threading
from datetime import datetime, date
from tkinter import filedialog, messagebox

import customtkinter as ctk

import incubation_db as db
import gcal_sync
from incubation_calc import _parse_date_loose

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)


class TimelineViewMixin:
    """The timeline/calendar screen, ics export and google calendar sync."""

    def _inc_color_map(self) -> dict:
        """Stable {incubator_id: color} mapping by display order."""
        incs = db.get_incubators(include_hidden=False)
        return {i["id"]: self._INC_PALETTE[idx % len(self._INC_PALETTE)]
                for idx, i in enumerate(incs)}

    def _build_timeline_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = self._screen_header(frame, "Calendar",
                                  "Incubation schedule & milestones")
        _btn_primary(hdr, "+ Schedule Incubator", self._schedule_incubator,
                     width=170).pack(side="right")
        _btn_secondary(hdr, "Export to Calendar", self._export_calendar,
                       width=150).pack(side="right", padx=8)
        _btn_secondary(hdr, "Today", self._cal_today,
                       width=75).pack(side="right")

        # Centered month navigation: ‹  Month Year  ›
        monthbar = ctk.CTkFrame(frame, fg_color="transparent")
        monthbar.pack(fill="x", pady=(2, 8))
        nav = ctk.CTkFrame(monthbar, fg_color="transparent")
        nav.pack()  # no fill → stays centered horizontally
        _btn(nav, "‹", self._cal_prev, width=40, height=40, fg=CARD, hover=CARD2,
             text_color=GOLD).pack(side="left", padx=4)
        self._tl_month_lbl = _label(nav, "", ("Segoe UI", 28, "bold"), GOLD)
        self._tl_month_lbl.pack(side="left", padx=20)
        _btn(nav, "›", self._cal_next, width=40, height=40, fg=CARD, hover=CARD2,
             text_color=GOLD).pack(side="left", padx=4)

        _t = date.today()
        self._cal_year, self._cal_month = _t.year, _t.month

        # Plain (non-scrolling) container so the whole month fits on one page
        self._tl_scroll = ctk.CTkFrame(frame, fg_color="transparent", corner_radius=0)
        self._tl_scroll.pack(fill="both", expand=True, padx=12, pady=4)
        return frame

    def _inc_start_date(self, inc_id: int):
        """Start date of an incubator's current incubation = the most common
        start date among its active trays. None if no active trays."""
        from collections import Counter
        counts = Counter()
        for t in db.get_trays(incubator_id=inc_id, status="active"):
            d = _parse_date_loose(t.get("in_date"))
            if d:
                counts[d] += 1
        return counts.most_common(1)[0][0] if counts else None

    def _incubation_events(self) -> list:
        """All milestone events across incubators with a start date.
        Each: {date, label, inc, color, day}. Start date prefers the explicit
        Edit-Setup value, falling back to the active-tray-derived date."""
        from datetime import timedelta
        cmap = self._inc_color_map()
        out = []
        for inc in db.get_incubators(include_hidden=False):
            raw = (inc.get("incubation_start") or "").strip()
            if raw == "none":
                continue  # schedule explicitly removed — don't auto-derive
            start = _parse_date_loose(raw) or self._inc_start_date(inc["id"])
            if not start:
                continue
            inc_color = cmap.get(inc["id"], BLUE)
            for day, label, color in self._INC_MILESTONES:
                out.append({
                    "date":  start + timedelta(days=day - 1),
                    "label": label, "inc": inc["name"], "inc_id": inc["id"],
                    "color": inc_color, "day": day,
                })
        return out

    def _cal_prev(self):
        self._cal_month -= 1
        if self._cal_month < 1:
            self._cal_month, self._cal_year = 12, self._cal_year - 1
        self._refresh_timeline()

    def _cal_next(self):
        self._cal_month += 1
        if self._cal_month > 12:
            self._cal_month, self._cal_year = 1, self._cal_year + 1
        self._refresh_timeline()

    def _cal_today(self):
        t = date.today()
        self._cal_year, self._cal_month = t.year, t.month
        self._refresh_timeline()

    def _refresh_timeline(self):
        import calendar as _cal
        container = self._tl_scroll
        for w in container.winfo_children():
            w.destroy()

        y, m = self._cal_year, self._cal_month
        self._tl_month_lbl.configure(text=f"{_cal.month_name[m]} {y}")

        # Events keyed by exact date; collect incubators present for the legend
        evs = self._incubation_events()
        by_date = {}
        inc_legend = {}   # incubator name → color
        for ev in evs:
            by_date.setdefault(ev["date"], []).append(ev)
            inc_legend.setdefault(ev["inc"], ev["color"])

        today = date.today()

        # Outer card wraps the whole calendar for a cleaner framed look
        cal_card = ctk.CTkFrame(container, fg_color=PANEL, corner_radius=14,
                                border_width=1, border_color=BORDER2)
        cal_card.pack(fill="both", expand=True, padx=4, pady=(2, 6))

        grid = ctk.CTkFrame(cal_card, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=8, pady=8)

        # Weekday header row (fixed height); the 6 week rows share the rest equally
        weekdays = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        grid.rowconfigure(0, weight=0)
        for c, wd in enumerate(weekdays):
            grid.columnconfigure(c, weight=1, uniform="cal")
            _label(grid, wd, ("Segoe UI", 10, "bold"), SUBTEXT).grid(
                row=0, column=c, sticky="ew", padx=2, pady=(0, 4))

        # Render the month's natural number of weeks (usually 5) so rows stay
        # tall enough to read — adjacent-month days fill out the first/last weeks.
        weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(y, m)

        for r, week in enumerate(weeks, start=1):
            grid.rowconfigure(r, weight=1, uniform="calrow")
            for c, d in enumerate(week):
                in_month = (d.month == m)
                is_today = (d == today)
                cell_bg = "#1F2529" if is_today else \
                          ("#101827" if not in_month else NESTED)
                cell = ctk.CTkFrame(
                    grid, fg_color=cell_bg, corner_radius=8,
                    border_width=2 if is_today else 0,
                    border_color=GOLD if is_today else BORDER)
                cell.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
                cell.grid_propagate(False)   # share grid space equally; content won't resize it

                # Day number — today gets a filled gold badge
                num_row = ctk.CTkFrame(cell, fg_color="transparent")
                num_row.pack(fill="x", padx=5, pady=(2, 0))
                if is_today:
                    ctk.CTkLabel(num_row, text=str(d.day), width=20, height=20,
                                 corner_radius=10, fg_color=GOLD, text_color="black",
                                 font=("Segoe UI", 10, "bold")).pack(side="left")
                else:
                    daycol = TEXT if in_month else "#4B5563"
                    _label(num_row, str(d.day), ("Segoe UI", 11, "bold"), daycol).pack(side="left")

                for ev in by_date.get(d, []):
                    chip = ctk.CTkFrame(cell, fg_color=ev["color"], corner_radius=4)
                    chip.pack(fill="x", padx=4, pady=1)
                    ctk.CTkLabel(
                        chip, text=f"{ev['inc']} · {ev['label']}",
                        font=("Segoe UI", 9, "bold"), text_color="white",
                        anchor="w", justify="left", wraplength=150).pack(
                        fill="x", padx=5, pady=1)

        # Legend — incubators (color-coded) + milestone day reference
        legend = ctk.CTkFrame(container, fg_color="transparent")
        legend.pack(fill="x", padx=6, pady=(4, 0))
        _label(legend, "Incubators:", FONT_B, SUBTEXT).pack(side="left", padx=(2, 10))
        for name, color in inc_legend.items():
            chip = ctk.CTkFrame(legend, fg_color=color, corner_radius=5)
            chip.pack(side="left", padx=4, pady=2)
            ctk.CTkLabel(chip, text=f" {name} ",
                         font=("Segoe UI", 10, "bold"), text_color="white").pack(padx=5, pady=2)

        days_ref = "   ".join(f"{lbl} (Day {day})" for day, lbl, _ in self._INC_MILESTONES)
        _label(container, "Milestones:  " + days_ref, FONT_S, SUBTEXT).pack(
            anchor="w", padx=8, pady=(2, 6))

    def _export_calendar(self):
        """Write all incubation milestones to an .ics file that imports straight
        into Google Calendar (or any calendar app)."""
        import uuid
        from datetime import timedelta
        events = self._incubation_events()
        if not events:
            messagebox.showinfo("Export", "No incubation milestones to export.\n"
                "Set an Incubation Start date for at least one incubator.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Export incubation calendar",
            defaultextension=".ics", filetypes=[("Calendar file", "*.ics")],
            initialfile="incubation_timeline.ics")
        if not path:
            return
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
                 "PRODID:-//Bee Incubation Manager//Timeline//EN", "CALSCALE:GREGORIAN"]
        for ev in events:
            d0 = ev["date"]
            d1 = d0 + timedelta(days=1)   # all-day event: DTEND is exclusive next day
            summary = f"{ev['inc']} — {ev['label']} (Day {ev['day']})"
            lines += [
                "BEGIN:VEVENT",
                f"UID:{uuid.uuid4()}@bee-incubation",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{d0.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{d1.strftime('%Y%m%d')}",
                f"SUMMARY:{summary}",
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        try:
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("\n".join(lines))
        except OSError as exc:
            messagebox.showerror("Export", f"Could not save file:\n{exc}", parent=self)
            return
        messagebox.showinfo("Export complete",
            f"Saved {len(events)} events to:\n{path}\n\n"
            "To add them to Google Calendar:\n"
            "1. Open Google Calendar (calendar.google.com)\n"
            "2. Settings ⚙ → Import & export → Import\n"
            "3. Choose this .ics file and pick a calendar.",
            parent=self)

    # ── Google Calendar sync ────────────────────────────────────────────────

    def _gcal_token_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcal_token.json")

    def _gcal_enabled(self) -> bool:
        return (db.get_setting("gcal_enabled", "0") == "1"
                and bool(db.get_setting("gcal_credentials_path")))

    def _gcal_sync(self, interactive: bool = False, notify: bool = False):
        """Push every incubator's milestones to Google Calendar (create/update),
        deleting events for incubators without a schedule. Runs in a thread."""
        if not gcal_sync.available():
            if notify or interactive:
                messagebox.showwarning("Google Calendar",
                    "Google libraries aren't installed yet.\n"
                    "Settings → Google Calendar Sync → Install Libraries.", parent=self)
            return
        creds = db.get_setting("gcal_credentials_path")
        cal   = db.get_setting("gcal_calendar_id", "primary") or "primary"
        if not creds:
            if notify or interactive:
                messagebox.showwarning("Google Calendar",
                    "Set the OAuth credentials JSON path in Settings first.", parent=self)
            return
        gc = gcal_sync.GoogleCalendar(creds, self._gcal_token_path(), cal)

        def _work():
            from datetime import timedelta
            if not gc.connect(interactive=interactive):
                if notify or interactive:
                    self.after(0, lambda: messagebox.showerror(
                        "Google Calendar", gc.error or "Connection failed.", parent=self))
                return
            n = 0
            for inc in db.get_incubators(include_hidden=False):
                raw = (inc.get("incubation_start") or "").strip()
                if raw == "none":
                    # Explicitly removed — delete any events we created before
                    for day, _label, _ in self._INC_MILESTONES:
                        gc.delete(gcal_sync.make_event_id(inc["id"], day))
                    continue
                start = _parse_date_loose(raw) or self._inc_start_date(inc["id"])
                if not start:
                    continue  # never scheduled — skip entirely (no wasted calls)
                for day, label, _ in self._INC_MILESTONES:
                    gc.upsert(gcal_sync.make_event_id(inc["id"], day),
                              f"{inc['name']} — {label} (Day {day})",
                              start + timedelta(days=day - 1))
                    n += 1
            if notify or interactive:
                self.after(0, lambda: messagebox.showinfo(
                    "Google Calendar", f"Synced {n} milestone events.", parent=self))

        threading.Thread(target=_work, daemon=True).start()

    def _schedule_incubator(self):
        """Schedule, edit, or remove an incubation. Enter only the Expected
        Release date — the start and all milestones are back-calculated.
        Selecting an already-scheduled incubator prefills its current date."""
        from datetime import timedelta
        rel_day = next((day for day, lbl, _ in self._INC_MILESTONES
                        if lbl == "Expected Release"), 23)

        incs = db.get_incubators(include_hidden=True)
        if not incs:
            messagebox.showinfo("Schedule", "Add an incubator first.", parent=self)
            return
        name_map  = {i["name"]: i["id"] for i in incs}
        start_map = {i["id"]: (i.get("incubation_start") or "").strip() for i in incs}

        win = ctk.CTkToplevel(self)
        win.title("Schedule Incubator")
        win.geometry("440x400")
        win.grab_set()
        _label(win, "Schedule Incubator", FONT_H, GOLD).pack(padx=16, pady=(14, 2))
        _label(win, "Enter the Expected Release date. The start date and all\n"
                    "milestones are calculated automatically. Pick an incubator\n"
                    "that's already scheduled to edit or remove it.",
               FONT_S, SUBTEXT).pack(padx=16)

        frm = ctk.CTkFrame(win, fg_color="transparent")
        frm.pack(fill="x", padx=24, pady=12)
        frm.columnconfigure(1, weight=1)
        _label(frm, "Incubator", FONT_S, SUBTEXT).grid(row=0, column=0, sticky="w", pady=6)
        inc_cb = _combo(frm, list(name_map.keys()), 210)
        inc_cb.grid(row=0, column=1, sticky="w", padx=8, pady=6)
        _label(frm, "Expected Release", FONT_S, SUBTEXT).grid(row=1, column=0, sticky="w", pady=6)
        date_e = ctk.CTkEntry(frm, placeholder_text="YYYY-MM-DD", width=210,
                              fg_color=CARD2, border_color=BORDER, text_color=TEXT)
        date_e.grid(row=1, column=1, sticky="w", padx=8, pady=6)

        preview = _label(win, "", FONT_S, SUBTEXT)
        preview.pack(padx=16, pady=(2, 4))

        def _update_preview(*_):
            d = _parse_date_loose(date_e.get())
            if not d:
                preview.configure(text="")
                return
            start = d - timedelta(days=rel_day - 1)
            lines = [f"{lbl}:  {(start + timedelta(days=day - 1)).strftime('%b %d, %Y')}"
                     for day, lbl, _ in self._INC_MILESTONES]
            preview.configure(text="\n".join(lines), justify="left")

        def _prefill(*_):
            """When the selected incubator changes, fill in its current release date."""
            iid = name_map.get(inc_cb.get())
            raw = start_map.get(iid, "")
            d = _parse_date_loose(raw) if raw and raw != "none" else None
            date_e.delete(0, "end")
            if d:
                date_e.insert(0, (d + timedelta(days=rel_day - 1)).strftime("%Y-%m-%d"))
            _update_preview()

        date_e.bind("<KeyRelease>", _update_preview)
        inc_cb.configure(command=lambda _v: _prefill())
        inc_cb.set(incs[0]["name"])
        _prefill()

        def _save():
            d = _parse_date_loose(date_e.get())
            if not d:
                messagebox.showerror("Schedule", "Enter a valid date (YYYY-MM-DD).", parent=win)
                return
            iid = name_map.get(inc_cb.get())
            if not iid:
                messagebox.showerror("Schedule", "Pick an incubator.", parent=win)
                return
            start = d - timedelta(days=rel_day - 1)
            db.set_incubator_incubation_start(iid, start.isoformat())
            self._cal_year, self._cal_month = d.year, d.month  # jump to release month
            win.destroy()
            self._refresh_timeline()
            if self._gcal_enabled():
                self._gcal_sync(interactive=False)

        def _remove():
            iid = name_map.get(inc_cb.get())
            if not iid:
                return
            if not messagebox.askyesno("Remove schedule",
                    f"Remove the incubation schedule for {inc_cb.get()}?", parent=win):
                return
            db.set_incubator_incubation_start(iid, "none")  # explicit cleared marker
            win.destroy()
            self._refresh_timeline()
            if self._gcal_enabled():
                self._gcal_sync(interactive=False)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=(8, 6))
        _btn(btns, "Save Schedule", _save, fg=DK_GOLD, hover=GOLD,
             text_color="black", width=150).pack(side="left", padx=4)
        _btn(btns, "Remove", _remove, fg=RED, hover="#991B1B",
             text_color="white", width=100).pack(side="left", padx=4)
        _btn(btns, "Cancel", win.destroy, fg=CARD2, hover=BORDER, width=90).pack(side="left", padx=4)

    # ══════════════════════════════════════════════════════════════════════════
    #  SETTINGS VIEW
    # ══════════════════════════════════════════════════════════════════════════

