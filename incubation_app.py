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
import math
import subprocess
import threading
import time
from datetime import datetime, date

from tkinter import ttk, filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
import govee_client as govee_mod
import sensibo_client as sensibo_mod
import gcal_sync
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

from app_config import (APP_VERSION, POLL_INTERVAL_SEC, _NO_WINDOW,
                        _git_revision, app_version_string)


# ── Theme & shared widget helpers (moved to ui_theme.py) ─────────────
from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)


# Tray date helpers now live in incubation_calc (kept unqualified so all
# existing call sites keep working).
from incubation_calc import _parse_date_loose, cool_down_days




# ── Dialogs (moved to views/dialogs.py) ──────────────────────────────
from views.dialogs import (
    IncubatorDialog, BatchDialog, SampleDialog, TrayDialog, QRDialog,
    AlertsDialog, _VocDeviceManager, _WifiNetworkManager,
)
from views.settings_view import SettingsViewMixin
from views.detail_view import DetailViewMixin
from views.trays_view import TraysViewMixin
from views.samples_view import SamplesViewMixin
from views.analytics_view import AnalyticsViewMixin
from views.timeline_view import TimelineViewMixin
from views.dashboard_view import DashboardViewMixin
from views.sensibo_controls import SensiboControlMixin
from services import ServicesMixin

# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN APP
# ═══════════════════════════════════════════════════════════════════════════════

class IncubationApp(SettingsViewMixin, DetailViewMixin, TraysViewMixin,
                    SamplesViewMixin, AnalyticsViewMixin, TimelineViewMixin,
                    DashboardViewMixin, SensiboControlMixin, ServicesMixin,
                    ctk.CTk):

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
        # Sensibo client (manual AC control only, no background polling)
        self._sensibo = sensibo_mod.SensiboClient(
            api_key=db.get_setting("sensibo_api_key"),
        )
        self._card_widgets: dict = {}  # incubator_id → {temp, hum, dot, ts} labels
        self._detail_inc: dict = {}   # incubator being shown in detail view

        # QR server port
        self._qr_port = int(db.get_setting("qr_server_port", "5151"))

        # Build UI — order matters for pack(): the title bar (top) plus the
        # sidebar (left) and the status bar (bottom, full width) must be
        # reserved BEFORE the expanding main area.
        self._build_titlebar()
        self._build_sidebar()
        self._build_status_bar()
        self._build_main()

        # Build all views (hidden until selected)
        self._views = {}
        self._views["dashboard"]    = self._build_dashboard()
        self._views["incubators"]   = self._build_incubators_view()
        self._views["samples"]      = self._build_samples_view()
        self._views["trays"]        = self._build_trays_view()
        self._views["analytics"]    = self._build_analytics_view()
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

        # Pulsing alert dots on dashboard cards
        self._pulse_on = True
        self._pulse_dots()

        # Watch for code updates (incl. git auto-sync pulls) and offer 1-click reload
        self._start_code_watcher()
        self.bind_all("<F5>", lambda e: self._restart_app())
        self.bind_all("<Control-r>", lambda e: self._restart_app())

    # ── Layout ────────────────────────────────────────────────────────────────

    def _load_icon(self, key: str, color: str, size: int = 18):
        """Load a tinted nav icon PNG as a CTkImage (cached)."""
        cache = getattr(self, "_icon_cache", None)
        if cache is None:
            cache = self._icon_cache = {}
        ck = (key, color, size)
        if ck in cache:
            return cache[ck]
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "assets", "icons", f"{key}_{color}.png")
        img = None
        if os.path.exists(path):
            try:
                from PIL import Image as _PImg
                raw = _PImg.open(path)
                img = ctk.CTkImage(light_image=raw, dark_image=raw, size=(size, size))
            except Exception:
                img = None
        cache[ck] = img
        return img

    def _build_titlebar(self):
        """40px app title bar across the very top (spec: #0B1220 + hairline)."""
        tb = ctk.CTkFrame(self, fg_color=BARBG, height=40, corner_radius=0)
        tb.pack(side="top", fill="x")
        tb.pack_propagate(False)
        _label(tb, "🐝", ("Segoe UI", 13), "#F5B52B").pack(side="left", padx=(14, 6))
        _label(tb, "Bee Incubation Manager", ("Segoe UI", 12, "bold"),
               TEXT2).pack(side="left")
        # Mock window-control circles (right)
        for _ in range(3):
            ctk.CTkLabel(tb, text="●", font=("Segoe UI", 11),
                         text_color="#334155").pack(side="right", padx=(0, 6))
        # bottom hairline
        ctk.CTkFrame(self, fg_color=SUBBORDER, height=1, corner_radius=0).pack(
            side="top", fill="x")

    def _screen_header(self, parent, title: str, subtitle: str):
        """Standard top bar: 20px gold title + muted subtitle, actions right.
        Returns the header frame — callers pack action buttons side='right'."""
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=26, pady=(20, 14))
        left = ctk.CTkFrame(hdr, fg_color="transparent")
        left.pack(side="left")
        _label(left, title, ("Segoe UI", 20, "bold"), GOLD).pack(anchor="w")
        _label(left, subtitle, ("Segoe UI", 11), FAINT).pack(anchor="w")
        return hdr

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR, width=208, corner_radius=0,
                          border_width=0)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        # Brand block — rounded tile containing the logo, centered
        tile = ctk.CTkFrame(sb, fg_color=CARD, corner_radius=16, width=62, height=62)
        tile.pack(pady=(22, 8))
        tile.pack_propagate(False)
        _logo_png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        _placed = False
        if os.path.exists(_logo_png):
            try:
                from PIL import Image as _PILImage
                _raw = _PILImage.open(_logo_png).convert("RGBA")
                _data = _raw.getdata()
                _raw.putdata([(r, g, b, 0) if r > 230 and g > 230 and b > 230
                              else (r, g, b, a) for r, g, b, a in _data])
                _ctk_img = ctk.CTkImage(light_image=_raw, dark_image=_raw, size=(38, 38))
                ctk.CTkLabel(tile, image=_ctk_img, text="").pack(expand=True)
                _placed = True
            except Exception:
                _placed = False
        if not _placed:
            _label(tile, "🐝", ("Segoe UI", 26), "#F5B52B").pack(expand=True)

        _label(sb, "Incubation", ("Segoe UI", 15, "bold"), GOLD).pack(anchor="center")
        _label(sb, "Bee Manager", FONT_S, SUBTEXT).pack(anchor="center", pady=(1, 1))
        _label(sb, app_version_string(), ("Segoe UI", 9), FAINT).pack(anchor="center")
        ctk.CTkFrame(sb, fg_color=SUBBORDER, height=1).pack(fill="x", padx=14, pady=(12, 10))

        nav_items = [
            ("Dashboard",     "dashboard"),
            ("Incubators",    "incubators"),
            ("Samples",       "samples"),
            ("Trays",         "trays"),
            ("Analytics",     "analytics"),
            ("Calendar",      "timeline"),
            ("Inspections",   "inspections"),
            ("Settings",      "settings"),
        ]
        self._nav_btns = {}
        self._nav_icons = {}   # key -> {"grey": CTkImage, "gold": CTkImage}
        for label, key in nav_items:
            g = self._load_icon(key, "grey")
            gold = self._load_icon(key, "gold")
            self._nav_icons[key] = {"grey": g, "gold": gold}
            _cmd = self._open_incubators if key == "incubators" \
                else (lambda k=key: self.show_view(k))
            btn = ctk.CTkButton(
                sb, text="  " + label, anchor="w", height=40,
                image=g, compound="left",
                fg_color="transparent", hover_color="#1A2436",
                text_color=SUBTEXT, font=("Segoe UI", 12), corner_radius=9,
                command=_cmd
            )
            btn.pack(fill="x", padx=10, pady=2)
            self._nav_btns[key] = btn

        # Spacer
        ctk.CTkFrame(sb, fg_color="transparent").pack(fill="y", expand=True)

        # Alert button pinned to the bottom — red-tinted
        self._alert_btn = ctk.CTkButton(
            sb, text="  Alerts  0", height=40, anchor="w",
            image=self._load_icon("alerts", "red"), compound="left",
            fg_color="#2A1E20", hover_color="#3A2224",
            text_color=RED_LT, font=("Segoe UI", 12, "bold"), corner_radius=10,
            border_width=1, border_color="#5A2A2C",
            command=self._open_alerts
        )
        self._alert_btn.pack(fill="x", padx=10, pady=(0, 16))

    def _build_main(self):
        self._main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._main.pack(side="left", fill="both", expand=True)

    def _build_status_bar(self):
        sb = ctk.CTkFrame(self, fg_color=BARBG, height=32, corner_radius=0,
                          border_width=0)
        sb.pack(side="bottom", fill="x")
        sb.pack_propagate(False)
        # top hairline divider
        ctk.CTkFrame(sb, fg_color=SUBBORDER, height=1).pack(fill="x", side="top")
        self._status_govee = _label(sb, "Govee: —", FONT_S, FAINT)
        self._status_govee.pack(side="left", padx=(24, 16))
        self._status_qr = _label(sb, "QR: —", FONT_S, FAINT)
        self._status_qr.pack(side="left", padx=16)
        self._status_sensibo = _label(sb, "Sensibo: —", FONT_S, FAINT)
        self._status_sensibo.pack(side="left", padx=16)
        self._status_time = _label(sb, "", FONT_S, SUBTEXT)
        self._status_time.pack(side="right", padx=24)
        self._status_refresh = _label(sb, "", FONT_S, FAINT)
        self._status_refresh.pack(side="right", padx=8)

        # Reload button — hidden until a code update is detected on disk.
        self._reload_btn = ctk.CTkButton(
            sb, text="🔄 Update ready — Reload", height=22, width=190,
            corner_radius=6, font=("Segoe UI", 10, "bold"),
            fg_color=DK_GOLD, hover_color=GOLD, text_color="black",
            command=self._restart_app)
        # not packed yet; shown by the watcher when files change

    # ── Navigation ────────────────────────────────────────────────────────────

    def _after_inc_delete(self):
        """After deleting an incubator, forget the stale selection and leave."""
        self._detail_inc = {}
        self.show_view("dashboard")

    def _toast(self, text: str, color: str = None, ms: int = 3500):
        """Brief, self-dismissing confirmation banner overlaid on the window.
        Parented to the app (not a view frame) so it survives view rebuilds."""
        try:
            old = getattr(self, "_toast_win", None)
            if old is not None and old.winfo_exists():
                old.destroy()
        except Exception:
            pass
        try:
            tw = ctk.CTkFrame(self, fg_color=(color or "#1B3A2A"),
                              corner_radius=10, border_width=1, border_color=GREEN)
            _label(tw, text, ("Segoe UI", 12, "bold"), "#EAF7EF").pack(
                padx=16, pady=8)
            tw.place(relx=0.5, rely=0.045, anchor="n")
            tw.lift()
            self._toast_win = tw
            self.after(ms, lambda w=tw: w.winfo_exists() and w.destroy())
        except Exception:
            pass

    def _manual_poll_incubator(self, inc: dict, btn=None):
        """Force an immediate Govee reading for one incubator (background thread)."""
        if not (inc.get("govee_device_id") and inc.get("govee_sku")):
            messagebox.showinfo("Pull Reading",
                "No Govee device is configured for this incubator.\n"
                "Set the Govee Device ID and SKU in Edit Setup first.", parent=self)
            return
        if btn is not None:
            btn.configure(text="Polling…", state="disabled")
        iid = inc["id"]

        def _work():
            try:
                temp_c, humidity = self._govee.poll_incubator(inc)
                err = None
            except Exception as exc:
                temp_c = humidity = None
                err = str(exc)

            def _done():
                try:
                    if btn is not None and btn.winfo_exists():
                        btn.configure(text="⟳ Pull Reading", state="normal")
                    if temp_c is not None and humidity is not None:
                        tc = govee_mod.to_celsius(temp_c)
                        db.save_reading(iid, tc, humidity)
                        # Keep the in-memory cache fresh so the detail view and
                        # tiles agree with what we just stored.
                        self._govee._last[iid] = {
                            "temp_c": tc, "humidity": humidity,
                            "timestamp": datetime.now().isoformat()}
                        self._refresh_alert_badge()
                        # Visible confirmation that survives the view rebuild
                        unit = (db.get_setting("temp_unit", "C") or "C").upper()
                        shown = tc if unit.startswith("C") else round(tc * 9 / 5 + 32, 1)
                        self._toast(f"✓ {inc.get('name', 'Incubator')} updated:  "
                                    f"{shown:g}°{unit[:1]}   {humidity:g}% RH   "
                                    f"{datetime.now():%H:%M}")
                        # Rebuild the detail view (safely) to show the new reading
                        if self._current_view == "inc_detail":
                            self._refresh_inc_detail()
                    else:
                        messagebox.showwarning("Pull Reading",
                            f"Couldn't read the sensor right now.\n"
                            f"{err or self._govee.status_label()}", parent=self)
                except Exception as exc:
                    messagebox.showerror("Pull Reading",
                        f"Error updating after poll:\n{exc}", parent=self)
            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _open_incubators(self):
        """Incubators tab opens the per-unit detail view (matches the README)."""
        if not self._detail_inc:
            incs = db.get_incubators(include_hidden=True)
            if incs:
                self._detail_inc = incs[0]
            else:
                self._open_incubator_dialog()   # no units yet → add one
                return
        self.show_view("inc_detail")

    def show_view(self, name: str):
        for v in self._views.values():
            v.pack_forget()
        # The detail view lives under the "Incubators" nav item — highlight it there
        _hl = "incubators" if name == "inc_detail" else name
        if True:
            for k, btn in self._nav_btns.items():
                active = (k == _hl)
                _ico = self._nav_icons.get(k, {})
                btn.configure(
                    fg_color="#25281E" if active else "transparent",   # spec exact
                    hover_color="#2B2C1D" if active else "#1A2436",
                    text_color=GOLD if active else SUBTEXT,
                    image=_ico.get("gold" if active else "grey"),
                    font=("Segoe UI", 12, "bold") if active else ("Segoe UI", 12))
        view = self._views[name]
        view.pack(fill="both", expand=True)
        self._current_view = name
        getattr(self, f"_refresh_{name}")()

    # ══════════════════════════════════════════════════════════════════════════
    #  DASHBOARD
    # ══════════════════════════════════════════════════════════════════════════

    def _open_incubator_dialog(self, inc: dict = None):
        IncubatorDialog(self, inc, on_save=lambda: self._refresh_current())

    # ══════════════════════════════════════════════════════════════════════════
    #  INCUBATOR DETAIL VIEW
    # ══════════════════════════════════════════════════════════════════════════

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

        hdr = self._screen_header(frame, "Inspections",
                                  "Full inspection log across all units")

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

    def _make_tree(self, parent, columns: tuple) -> ttk.Treeview:
        frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER2)
        tree = ttk.Treeview(frame, columns=columns, show="headings",
                            style="Dark.Treeview")
        # Zebra striping + status row tints (applied via _apply_zebra / on insert)
        tree.tag_configure("evenrow", background=PANEL)
        tree.tag_configure("oddrow",  background="#18222F")
        tree.tag_configure("released", foreground=GREEN_LT)
        tree.tag_configure("cooled",   foreground="#7DB0FF")
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

    def _apply_zebra(self, tree, status_col: int = None):
        """Alternate row backgrounds; if status_col given, tint Released/Cooled
        rows by status instead of the zebra background."""
        for i, iid in enumerate(tree.get_children()):
            tags = ["evenrow" if i % 2 == 0 else "oddrow"]
            if status_col is not None:
                vals = tree.item(iid, "values")
                if status_col < len(vals):
                    sv = str(vals[status_col]).strip().lower()
                    if sv == "released":
                        tags.append("released")
                    elif sv == "cooled":
                        tags.append("cooled")
            tree.item(iid, tags=tags)

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


# ═══════════════════════════════════════════════════════════════════════════════
#   ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = IncubationApp()
    app.mainloop()
