"""
views/analytics_view.py  —  the Analytics screen: KPI cards, bar chart, temp stability, cycle stats.

Extracted from incubation_app.py as a mixin (pure relocation).
"""
import math
from datetime import datetime

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

try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


class AnalyticsViewMixin:
    """The analytics screen: kpi cards, bar chart, temp stability, cycle stats."""

    def _build_analytics_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)
        hdr = self._screen_header(frame, "Analytics",
                                  "Sample & incubation performance")
        _btn_secondary(hdr, "Refresh", self._refresh_analytics,
                       width=100).pack(side="right")
        _yrs = self._tray_years()
        self._an_year = _combo(hdr, ["All Years"] + _yrs, 120)
        _cur = str(datetime.now().year)
        self._an_year.set(_cur if _cur in _yrs else "All Years")
        self._an_year.pack(side="right", padx=8)
        self._an_year.configure(command=lambda _: self._refresh_analytics())
        self._an_body = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        self._an_body.pack(fill="both", expand=True, padx=12, pady=4)
        return frame

    def _refresh_analytics(self):
        body = self._an_body
        for w in body.winfo_children():
            w.destroy()

        yr = self._an_year.get() if getattr(self, "_an_year", None) else "All Years"
        year = None if yr == "All Years" else int(yr)

        samples = db.get_samples()
        if year is not None:
            ids = db.current_year_sample_ids(year)
            samples = [s for s in samples if s["id"] in ids]
        actual = db.get_tray_counts_by_sample(statuses=None)

        self._an_kpi_cards(body, samples, actual)
        self._an_bar_chart(body, "Live bees / kg by sample (high → low)",
                           samples, "live_bees_per_kg", "{:,.0f}", "#FFD700")
        self._an_bar_chart(body, "Parasite % by sample (high → low)",
                           samples, "parasites", "{:.1f}%", "#EF4444")
        self._an_bar_chart(body, "Chalkbrood % by sample (high → low)",
                           samples, "chalkbrood", "{:.1f}%", "#F59E0B")
        self._an_temp_stability(body)
        self._an_cycle_stats(body, year)

    def _an_card(self, parent, title):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12,
                            border_width=1, border_color=BORDER2)
        card.pack(fill="x", padx=4, pady=7)
        _label(card, title, ("Segoe UI", 12, "bold"), GOLD).pack(
            anchor="w", padx=16, pady=(12, 2))
        return card

    def _an_kpi_cards(self, parent, samples, actual):
        def avg(key):
            vals = [s[key] for s in samples if isinstance(s.get(key), (int, float))]
            return sum(vals) / len(vals) if vals else None

        a_live, a_par, a_chalk = avg("live_bees_per_kg"), avg("parasites"), avg("chalkbrood")
        exp = sum(s["total_trays"] for s in samples if isinstance(s.get("total_trays"), (int, float)))
        act = sum(actual.get(s["id"], 0) for s in samples)
        cards = [
            ("Samples", str(len(samples))),
            ("Avg live bees/kg", f"{a_live:,.0f}" if a_live is not None else "—"),
            ("Avg parasite %", f"{a_par:.1f}%" if a_par is not None else "—"),
            ("Avg chalkbrood %", f"{a_chalk:.1f}%" if a_chalk is not None else "—"),
            ("Trays exp / actual", f"{math.ceil(exp)} / {act}"),
        ]
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(4, 8))
        for i in range(len(cards)):
            row.columnconfigure(i, weight=1, uniform="an_kpi")
        for i, (lbl, val) in enumerate(cards):
            c = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER2)
            c.grid(row=0, column=i, sticky="ew", padx=5)
            _label(c, val, ("Segoe UI", 22, "bold"), GOLD).pack(
                anchor="w", padx=16, pady=(14, 0))
            _label(c, lbl, ("Segoe UI", 11), SUBTEXT).pack(
                anchor="w", padx=16, pady=(0, 14))

    def _an_bar_chart(self, parent, title, samples, key, fmt, color, top=15):
        card = self._an_card(parent, title)
        data = [(s["name"], s[key]) for s in samples
                if isinstance(s.get(key), (int, float))]
        if not data:
            _label(card, "No data for this selection.", FONT_S, SUBTEXT).pack(
                padx=14, pady=(0, 12))
            return
        data.sort(key=lambda x: x[1], reverse=True)
        data = data[:top]
        if not HAS_MPL:
            for nm, v in data:
                _label(card, f"{nm}: {fmt.format(v)}", FONT_S, TEXT).pack(
                    anchor="w", padx=18, pady=1)
            ctk.CTkFrame(card, fg_color="transparent", height=8).pack()
            return
        names = [d[0] for d in data][::-1]
        vals  = [d[1] for d in data][::-1]
        fig = Figure(figsize=(9, max(2.0, 0.34 * len(names) + 0.5)), facecolor=PANEL)
        ax = fig.add_subplot(111)
        ax.set_facecolor(PANEL)
        bars = ax.barh(names, vals, color=color)
        ax.tick_params(colors="#9CA3AF", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER2)
        xmax = max(vals) if vals else 0
        ax.set_xlim(0, xmax * 1.15 if xmax else 1)
        for b, v in zip(bars, vals):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2,
                    " " + fmt.format(v), va="center", color="#F3F4F6", fontsize=7)
        fig.tight_layout(pad=1.0)
        cv = FigureCanvasTkAgg(fig, master=card)
        cv.draw()
        cv.get_tk_widget().pack(fill="x", padx=8, pady=(0, 8))

    def _an_temp_stability(self, parent):
        card = self._an_card(parent, "Incubator temperature stability (last 30 days)")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=(0, 12))
        heads = ["Incubator", "Readings", "% in range", "Avg temp"]
        for ci, h in enumerate(heads):
            grid.columnconfigure(ci, weight=1)
            _label(grid, h, FONT_S, SUBTEXT).grid(row=0, column=ci, sticky="w", padx=6, pady=2)
        unit = db.get_setting("temp_unit", "C")
        r = 1
        for inc in db.get_incubators(include_hidden=False):
            readings = db.get_readings_hours(inc["id"], 24 * 30)
            temps = [x["temperature_c"] for x in readings if x["temperature_c"] is not None]
            t_min, t_max = calc.get_temp_range(inc)
            if not temps or t_min is None:
                pct_txt, avg_txt = "—", "—"
                pct_col = SUBTEXT
            else:
                pct = 100 * sum(1 for t in temps if t_min <= t <= t_max) / len(temps)
                avg = sum(temps) / len(temps)
                pct_txt = f"{pct:.0f}%"
                pct_col = GREEN if pct >= 90 else (ORANGE if pct >= 70 else RED)
                avg_txt = calc.format_temp(avg, unit)
            _label(grid, inc["name"], FONT_B, TEXT).grid(row=r, column=0, sticky="w", padx=6, pady=2)
            _label(grid, str(len(temps)), FONT_S, SUBTEXT).grid(row=r, column=1, sticky="w", padx=6, pady=2)
            _label(grid, pct_txt, FONT_B, pct_col).grid(row=r, column=2, sticky="w", padx=6, pady=2)
            _label(grid, avg_txt, FONT_S, TEXT).grid(row=r, column=3, sticky="w", padx=6, pady=2)
            r += 1

    def _an_cycle_stats(self, parent, year):
        card = self._an_card(parent, "Cycle & cool-down stats")
        trays = db.get_trays()
        if year is not None:
            trays = [t for t in trays
                     if (lambda d: d is not None and d.year == year)(
                         _parse_date_loose(t.get("in_date")))]
        cool = [d for d in (cool_down_days(t) for t in trays) if d is not None]
        incub = []
        for t in trays:
            ind = _parse_date_loose(t.get("in_date"))
            outd = _parse_date_loose(t.get("out_date"))
            if ind and outd and outd >= ind:
                incub.append((outd - ind).days)
        lines = []
        lines.append(f"Trays in selection: {len(trays)}")
        if cool:
            lines.append(f"Cool-down days — avg {sum(cool)/len(cool):.1f}, "
                         f"min {min(cool)}, max {max(cool)}  (n={len(cool)})")
        else:
            lines.append("Cool-down days — no cooled/released trays yet")
        if incub:
            lines.append(f"Incubation length (start→release) — avg {sum(incub)/len(incub):.1f} days  "
                         f"(n={len(incub)})")
        for ln in lines:
            _label(card, ln, FONT_B, TEXT).pack(anchor="w", padx=18, pady=2)
        ctk.CTkFrame(card, fg_color="transparent", height=6).pack()

    # ══════════════════════════════════════════════════════════════════════════
    #  TRAYS VIEW
    # ══════════════════════════════════════════════════════════════════════════

