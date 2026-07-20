"""
views/trays_view.py  —  the Trays screen and every tray operation.

Extracted from incubation_app.py as a mixin (pure relocation): the tray table
with paging/sorting/selection, tray history, CSV import + templates for trays
and releases, and the bulk status / move / delete operations.
"""
import csv
import math
import os
from datetime import datetime, date
from tkinter import filedialog, messagebox

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
from incubation_calc import _parse_date_loose, cool_down_days

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)
from views.dialogs import QRDialog


class TraysViewMixin:
    """Trays screen: table, paging, selection, history, CSV and bulk actions."""

    def _build_trays_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = self._screen_header(frame, "Trays",
                                  "Tray inventory & release tracking")
        _btn_primary(hdr, "+ Add Tray", lambda: self._open_tray_dialog(),
                     width=110).pack(side="right")
        _btn(hdr, "🗑 Delete All", self._delete_all_trays,
             fg="#3A1C1E", hover="#4A2225", text_color=RED_LT,
             height=34, width=110, border_width=1,
             border_color="#5A2A2C").pack(side="right", padx=8)
        _btn_secondary(hdr, "Set Status →", self._bulk_set_status,
                       width=110).pack(side="right", padx=(8, 0))
        self._bulk_status = _combo(hdr, [lbl for _v, lbl in db.TRAY_STATUS_OPTIONS], 120)
        self._bulk_status.set("Released")
        self._bulk_status.pack(side="right", padx=6)
        # Bulk-move selected trays to another incubator (cool-days carry over
        # only when source and destination are in the same temp mode).
        _btn_secondary(hdr, "Move →", self._bulk_move_trays,
                       width=80).pack(side="right", padx=(8, 0))
        _move_incs = db.get_incubators()
        self._bulk_move_map = {i["name"]: i["id"] for i in _move_incs}
        self._bulk_move_dest = _combo(hdr, list(self._bulk_move_map.keys()), 140)
        if _move_incs:
            self._bulk_move_dest.set(_move_incs[0]["name"])
        self._bulk_move_dest.pack(side="right", padx=6)
        _btn_secondary(hdr, "QR Code", self._show_selected_qr,
                       width=90).pack(side="right", padx=6)
        _btn_secondary(hdr, "History", self._show_tray_history,
                       width=90).pack(side="right", padx=6)
        _btn_secondary(hdr, "Release CSV",
                       lambda: self._release_csv_import(on_complete=self._refresh_trays),
                       width=110).pack(side="right", padx=6)
        _btn_secondary(hdr, "⬇ Release Template", lambda: self._release_csv_template(),
                       width=150).pack(side="right")
        _btn_secondary(hdr, "Import CSV",
                       lambda: self._tray_csv_import(on_complete=self._refresh_trays),
                       width=105).pack(side="right", padx=6)
        _btn_secondary(hdr, "⬇ Template", lambda: self._tray_csv_template(),
                       width=110).pack(side="right")

        # Filter bar
        fbar = ctk.CTkFrame(frame, fg_color=PANEL, corner_radius=10,
                            border_width=1, border_color=BORDER2)
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

        # Year filter — based on each tray's Start Date (defaults to current year)
        _tray_yrs = self._tray_years()
        self._flt_year = _combo(fbar, ["All Years"] + _tray_yrs, 120)
        _cur_year = str(datetime.now().year)
        self._flt_year.set(_cur_year if _cur_year in _tray_yrs else "All Years")
        self._flt_year.pack(side="left", padx=6, pady=6)
        self._flt_year.configure(command=lambda _: self._refresh_trays())

        # Sample look-up — show only trays of the chosen sample
        self._flt_sample_map = {"All Samples": None}
        self._flt_sample_map.update({s["name"]: s["id"] for s in db.get_samples()})
        self._flt_sample = _combo(fbar, list(self._flt_sample_map.keys()), 200)
        self._flt_sample.set("All Samples")
        self._flt_sample.pack(side="left", padx=6, pady=6)
        self._flt_sample.configure(command=lambda _: self._refresh_trays())

        # Cool-down summary for the currently-shown trays
        self._cooldown_lbl = _label(fbar, "", FONT_S, TEAL)
        self._cooldown_lbl.pack(side="right", padx=12, pady=6)

        # Label-grid table with pagination (fast even at thousands of rows)
        tbl = ctk.CTkFrame(frame, fg_color=PANEL, corner_radius=12,
                           border_width=1, border_color=BORDER2)
        tbl.pack(fill="both", expand=True, padx=12, pady=(4, 2))
        self._tray_scroll = ctk.CTkScrollableFrame(tbl, fg_color="transparent")
        self._tray_scroll.pack(fill="both", expand=True, padx=2, pady=2)

        pg = ctk.CTkFrame(frame, fg_color="transparent")
        pg.pack(fill="x", padx=14, pady=(0, 4))
        self._tray_prev_btn = _btn_secondary(pg, "‹ Prev", self._tray_prev_page, width=80)
        self._tray_prev_btn.pack(side="left")
        self._tray_page_lbl = _label(pg, "", FONT_S, SUBTEXT)
        self._tray_page_lbl.pack(side="left", padx=10)
        self._tray_next_btn = _btn_secondary(pg, "Next ›", self._tray_next_page, width=80)
        self._tray_next_btn.pack(side="left")
        _btn_secondary(pg, "☑ Select All", self._tray_select_all,
                       width=110).pack(side="left", padx=(14, 0))
        _btn_secondary(pg, "Clear", self._tray_clear_sel,
                       width=70).pack(side="left", padx=6)
        self._tray_sel_lbl = _label(pg, "", FONT_S, TEAL)
        self._tray_sel_lbl.pack(side="right")

        self._tray_sort_col = 0
        self._tray_sort_asc = True
        self._tray_page = 0
        self._tray_sel = set()
        self._tray_anchor = None
        self._tray_all = []
        self._tray_row_frames = {}
        return frame

    # (label, weight, align, kind)
    _TRAY_COLS = [
        ("Tray #", 2, "w", "link"), ("Sample", 3, "w", "text"),
        ("Incubator", 2, "w", "muted"), ("Weight Kg", 1, "e", "num"),
        ("Live/Kg", 1, "e", "num"), ("Parasite", 1, "e", "num"),
        ("Chalk", 1, "e", "num"), ("Start", 2, "w", "text"),
        ("Release", 2, "w", "text"), ("Cool", 1, "e", "num"),
        ("Status", 2, "c", "badge"),
    ]
    _TRAY_STATUS_COLOR = {"active": BLUE, "cooled": "#06B6D4",
                          "released": GREEN, "removed": FAINT}
    _TRAY_PAGE_SIZE = 150

    def _sort_tray_tree(self, col_idx: int):
        if self._tray_sort_col == col_idx:
            self._tray_sort_asc = not self._tray_sort_asc
        else:
            self._tray_sort_col = col_idx
            self._tray_sort_asc = True
        self._tray_sort_and_render()

    def _tray_prev_page(self):
        if self._tray_page > 0:
            self._tray_page -= 1
            self._tray_render_page()

    def _tray_next_page(self):
        import math as _m
        pages = max(1, _m.ceil(len(self._tray_all) / self._TRAY_PAGE_SIZE))
        if self._tray_page < pages - 1:
            self._tray_page += 1
            self._tray_render_page()

    def _tray_sort_and_render(self):
        sc = self._tray_sort_col

        def _key(row):
            v = row["cells"][sc]
            if v is None or v == "—":
                return (2, 0.0, "")
            try:
                return (0, float(str(v).replace("%", "").replace(",", "").replace("d", "")), "")
            except (ValueError, AttributeError):
                return (1, 0.0, str(v).lower())
        self._tray_all.sort(key=lambda r: str(r["cells"][0]).lower())
        self._tray_all.sort(key=_key, reverse=not self._tray_sort_asc)
        self._tray_page = 0
        self._tray_render_page()

    def _tray_toggle_sel(self, tid: int):
        if tid in self._tray_sel:
            self._tray_sel.discard(tid)
        else:
            self._tray_sel.add(tid)
        rf = self._tray_row_frames.get(tid)
        if rf and rf.winfo_exists():
            rf.configure(fg_color="#26374F" if tid in self._tray_sel else rf._base_bg)
        self._update_tray_sel_lbl()

    def _update_tray_sel_lbl(self):
        n = len(self._tray_sel)
        shown = len(getattr(self, "_tray_all", []))
        self._tray_sel_lbl.configure(
            text=f"{n} selected  (of {shown} shown)" if n else "")

    def _tray_click(self, event, tid: int):
        """Row click. Plain click toggles one row and sets the range anchor;
        Shift+click selects the whole range between the anchor and this row (in
        the current sorted order, across pages)."""
        shift = bool(event.state & 0x0001)  # Shift key mask
        anchor = getattr(self, "_tray_anchor", None)
        ids = [r["id"] for r in self._tray_all]
        if shift and anchor in ids and tid in ids:
            i0, i1 = ids.index(anchor), ids.index(tid)
            lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
            for rid in ids[lo:hi + 1]:
                self._tray_sel.add(rid)
            for rid, rf in self._tray_row_frames.items():
                if rf.winfo_exists():
                    rf.configure(fg_color="#26374F" if rid in self._tray_sel
                                 else rf._base_bg)
            self._update_tray_sel_lbl()
        else:
            self._tray_toggle_sel(tid)
            self._tray_anchor = tid

    def _tray_select_all(self):
        """Select every tray in the current filtered view (all pages)."""
        for row in self._tray_all:
            self._tray_sel.add(row["id"])
        # Re-color the rows visible on the current page
        for tid, rf in self._tray_row_frames.items():
            if rf.winfo_exists():
                rf.configure(fg_color="#26374F")
        self._update_tray_sel_lbl()

    def _tray_clear_sel(self):
        """Clear the current selection."""
        self._tray_sel.clear()
        self._tray_anchor = None
        for tid, rf in self._tray_row_frames.items():
            if rf.winfo_exists():
                rf.configure(fg_color=rf._base_bg)
        self._update_tray_sel_lbl()

    def _tray_render_page(self):
        import math as _m
        scroll = self._tray_scroll
        for w in scroll.winfo_children():
            w.destroy()
        self._tray_row_frames = {}

        total = len(self._tray_all)
        pages = max(1, _m.ceil(total / self._TRAY_PAGE_SIZE))
        self._tray_page = max(0, min(self._tray_page, pages - 1))
        start = self._tray_page * self._TRAY_PAGE_SIZE
        page_rows = self._tray_all[start:start + self._TRAY_PAGE_SIZE]

        # Header
        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 2))
        for ci, (lbl, wt, al, kind) in enumerate(self._TRAY_COLS):
            hdr.columnconfigure(ci, weight=wt, uniform="tray")
            arrow = (" ↑" if self._tray_sort_asc else " ↓") if ci == self._tray_sort_col else ""
            ctk.CTkButton(
                hdr, text=lbl + arrow, height=28, corner_radius=6,
                anchor="w" if al == "w" else ("center" if al == "c" else "e"),
                fg_color="transparent", hover_color=CARD2, text_color=GOLD,
                font=("Segoe UI", 11, "bold"),
                command=lambda c=ci: self._sort_tray_tree(c)
            ).grid(row=0, column=ci, sticky="ew", padx=1)
        ctk.CTkFrame(scroll, fg_color=BORDER2, height=1).pack(fill="x")

        for i, row in enumerate(page_rows):
            base_bg = PANEL if i % 2 == 0 else "#18222F"
            sel = row["id"] in self._tray_sel
            rf = ctk.CTkFrame(scroll, fg_color="#26374F" if sel else base_bg,
                              corner_radius=0)
            rf._base_bg = base_bg
            rf.pack(fill="x")
            self._tray_row_frames[row["id"]] = rf
            for ci, (lbl, wt, al, kind) in enumerate(self._TRAY_COLS):
                rf.columnconfigure(ci, weight=wt, uniform="tray")
                v = row["cells"][ci]
                if kind == "badge":
                    scol = self._TRAY_STATUS_COLOR.get(row["status"], SUBTEXT)
                    ctk.CTkLabel(rf, text=f" {v} ", font=("Segoe UI", 10, "bold"),
                                 fg_color=_mix(scol, base_bg, 0.18), text_color=scol,
                                 corner_radius=6).grid(row=0, column=ci, padx=8, pady=5)
                else:
                    col = LINK if kind == "link" else (
                        SUBTEXT if kind == "muted" else
                        ("#E5E7EB" if al == "e" else "#CBD5E1"))
                    _label(rf, str(v), ("Segoe UI", 11), col,
                           anchor="w" if al == "w" else "e"
                           ).grid(row=0, column=ci, sticky="ew", padx=8, pady=5)
            rf.bind("<Button-1>", lambda e, t=row["id"]: self._tray_click(e, t))
            rf.bind("<Double-1>", lambda e, t=row["id"]: self._open_tray_by_id(t))
            for ch in rf.winfo_children():
                ch.bind("<Button-1>", lambda e, t=row["id"]: self._tray_click(e, t))
                ch.bind("<Double-1>", lambda e, t=row["id"]: self._open_tray_by_id(t))

        self._tray_page_lbl.configure(text=f"Page {self._tray_page + 1} of {pages}  ·  {total} trays")
        self._tray_prev_btn.configure(state="normal" if self._tray_page > 0 else "disabled")
        self._tray_next_btn.configure(state="normal" if self._tray_page < pages - 1 else "disabled")

    def _open_tray_by_id(self, tid: int):
        tray = db.get_tray_by_id(tid)
        if tray:
            self._open_tray_dialog(tray=tray)

    def _tray_years(self) -> list:
        """Distinct years present in tray Start Dates, newest first (as strings)."""
        years = set()
        for t in db.get_trays():
            d = _parse_date_loose(t.get("in_date"))
            if d:
                years.add(d.year)
        return [str(y) for y in sorted(years, reverse=True)]

    def _refresh_trays(self):
        inc_id = self._flt_inc_map.get(self._flt_inc.get())
        _flt = self._flt_status.get()
        status = None if _flt == "All" else db.tray_status_value(_flt)

        sample_id = None
        _fs = getattr(self, "_flt_sample", None)
        if _fs is not None:
            sample_id = self._flt_sample_map.get(_fs.get())

        trays = db.get_trays(incubator_id=inc_id, sample_id=sample_id, status=status)

        # Year filter — keep trays whose Start Date falls in the chosen year
        yr = getattr(self, "_flt_year", None)
        yr = yr.get() if yr else "All Years"
        if yr and yr != "All Years":
            trays = [t for t in trays
                     if (lambda d: d is not None and str(d.year) == yr)(
                         _parse_date_loose(t.get("in_date")))]

        self._tray_sel = set()
        self._tray_anchor = None
        self._tray_all = [{
            "id": t["id"], "status": t.get("status") or "active",
            "cells": [
                t["tray_number"],
                t.get("sample_name") or "—",
                t.get("incubator_name") or "—",
                f"{t['sample_kg_per_2gal']:.2f}" if t.get("sample_kg_per_2gal") else "—",
                f"{t['sample_live_per_kg']:,.0f}" if t.get("sample_live_per_kg") else "—",
                f"{t['sample_parasites']:.1f}%" if t.get("sample_parasites") is not None else "—",
                f"{t['sample_chalkbrood']:.1f}%" if t.get("sample_chalkbrood") is not None else "—",
                t.get("in_date") or "—",
                t.get("out_date") or "—",
                (lambda d: f"{d}d" if d is not None else "—")(cool_down_days(t)),
                db.tray_status_label(t.get("status") or "active"),
            ]} for t in trays]
        self._tray_sort_and_render()

        # Cool-down report: average over shown trays that have a cool-down value
        _durs = [d for d in (cool_down_days(t) for t in trays) if d is not None]
        if _durs:
            self._cooldown_lbl.configure(
                text=f"Cool-down: avg {sum(_durs)/len(_durs):.1f}d  "
                     f"(min {min(_durs)} · max {max(_durs)}, n={len(_durs)})")
        else:
            self._cooldown_lbl.configure(text="")

    def _show_selected_qr(self):
        if not self._tray_sel:
            messagebox.showinfo("QR Code", "Click a tray to select it first.")
            return
        tray = db.get_tray_by_id(next(iter(self._tray_sel)))
        if tray:
            QRDialog(self, tray, port=self._qr_port)

    def _show_tray_history(self):
        """Show every season's record for the selected tray number."""
        if not self._tray_sel:
            messagebox.showinfo("History", "Click a tray to select it first.", parent=self)
            return
        tray = db.get_tray_by_id(next(iter(self._tray_sel)))
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
        self._apply_zebra(tree, status_col=0)

    # ══════════════════════════════════════════════════════════════════════════
    #  TIMELINE VIEW
    # ══════════════════════════════════════════════════════════════════════════

    # Incubation milestone days (offset from start, Day 1 = start) → (label, color)
    # Matches the developmental-stage timeline and the field spreadsheet.
    _INC_MILESTONES = [
        (1,  "Incubation Start",     "#10B981"),
        (7,  "Vapona In",            "#8B5CF6"),
        (13, "Vapona Out",           "#D946EF"),
        (14, "Earliest We Can Cool", "#06B6D4"),
        (18, "10% Male Emergence",   "#6366F1"),
        (23, "Expected Release",     "#F59E0B"),
        (37, "Latest Release",       "#EF4444"),
    ]

    # Distinct colors assigned per incubator, drawn from the dashboard theme
    # (BLUE / GREEN / ORANGE / TEAL / RED / dark-gold) plus a couple of
    # complementary accents — all readable with white chip text.
    _INC_PALETTE = [
        BLUE, GREEN, ORANGE, "#8B5CF6", TEAL,
        DK_GOLD, "#EC4899", RED, "#0EA5E9", "#A16207",
    ]

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
        if not self._tray_sel:
            messagebox.showinfo("Set Status",
                "Click one or more trays to select them first.",
                parent=self)
            return

        new_status = db.tray_status_value(self._bulk_status.get())
        tray_ids   = [int(t) for t in self._tray_sel]

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

    def _bulk_move_trays(self):
        """Reassign all selected trays to another incubator, applying the
        cool-day carry-over rules (same mode carries over, different mode adopts
        the destination's status, Off freezes)."""
        if not self._tray_sel:
            messagebox.showinfo("Move Trays",
                "Click one or more trays to select them first.", parent=self)
            return
        dest_name = self._bulk_move_dest.get()
        dest_id   = self._bulk_move_map.get(dest_name)
        if not dest_id:
            messagebox.showinfo("Move Trays",
                "Pick a destination incubator first.", parent=self)
            return
        tray_ids = [int(t) for t in self._tray_sel]
        dest = next((i for i in db.get_incubators(include_hidden=True)
                     if i["id"] == dest_id), None)
        dest_mode  = (dest.get("temp_mode") if dest else "") or "incubation"
        mode_label = calc.TEMP_MODES.get(dest_mode, {}).get("label", dest_mode)
        if not messagebox.askyesno(
            "Move Trays",
            f"Move {len(tray_ids)} selected tray(s) to {dest_name} "
            f"({mode_label})?\n\n"
            "• Trays from an incubator in the SAME mode keep their cool-day count.\n"
            "• Trays from a different mode take on this incubator's status "
            "(cool-days start fresh or clear).\n"
            "• If this incubator is Off, trays keep their current status.",
            parent=self):
            return
        moved = db.move_trays(tray_ids, dest_id)
        self._tray_sel.clear()
        self._refresh_trays()
        self._refresh_alert_badge()
        messagebox.showinfo("Trays Moved",
            f"Moved {moved} tray(s) to {dest_name}.", parent=self)

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

