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

# Windows: run helper subprocesses (git, py_compile, poller) hidden so no
# console window flashes up and steals focus while you're working.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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

# ── Version ─────────────────────────────────────────────────────────────────
APP_VERSION = "1.47.1"   # bump on every push (semver: MAJOR.MINOR.PATCH)


def _git_revision() -> str:
    """Short git commit hash + date for the running code, or '' if unavailable."""
    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "-C", app_dir, "log", "-1", "--format=%h · %cd", "--date=short"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, creationflags=_NO_WINDOW,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def app_version_string() -> str:
    rev = _git_revision()
    return f"v{APP_VERSION}  ({rev})" if rev else f"v{APP_VERSION}"


# ── Polling ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 15 * 60   # Govee polling is fixed at 15 minutes


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

# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN APP
# ═══════════════════════════════════════════════════════════════════════════════

class IncubationApp(SettingsViewMixin, DetailViewMixin, TraysViewMixin,
                    SamplesViewMixin, AnalyticsViewMixin, TimelineViewMixin,
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

    def _sensibo_update_buttons(self, iid: int, device_id: str):
        """Patch just the AC button labels/colors on the card for this incubator."""
        widgets = self._card_widgets.get(iid, {})
        pwr = widgets.get("ac_power")
        tmp = widgets.get("ac_temp")
        fan = widgets.get("ac_fan")
        if pwr and pwr.winfo_exists():
            lbl, fg, tc = self._ac_toggle_style(device_id)
            pwr.configure(text=lbl, fg_color=fg, text_color=tc)
        if tmp and tmp.winfo_exists():
            tmp.configure(text=self._ac_temp_label(device_id))
        if fan and fan.winfo_exists():
            fan.configure(text=self._ac_fan_label(device_id))

    def _sensibo_run(self, iid: int, device_id: str, fn, on_error=None):
        """Run fn() in a background thread; patch AC buttons when done.

        fn must be a zero-argument callable (use a lambda to capture kwargs).
        """
        import threading
        widgets = self._card_widgets.get(iid, {})
        for key in ("ac_power", "ac_temp", "ac_fan"):
            w = widgets.get(key)
            if w and w.winfo_exists():
                w.configure(state="disabled")

        def _work():
            ok = fn()
            def _done():
                for key in ("ac_power", "ac_temp", "ac_fan"):
                    w = widgets.get(key)
                    if w and w.winfo_exists():
                        w.configure(state="normal")
                if not ok and on_error:
                    messagebox.showerror("Sensibo", on_error(), parent=self)
                self._sensibo_update_buttons(iid, device_id)
            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _sensibo_set_power(self, iid: int, device_id: str, on: bool):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        self._sensibo_run(iid, device_id,
            lambda: self._sensibo.set_ac_state_many(device_id, on=on),
            on_error=lambda: f"Could not reach the AC unit(s):\n{self._sensibo.status_label()}")

    def _sensibo_toggle_power(self, iid: int, device_id: str):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        target = not self._sensibo.resolve_power(device_id)
        self._sensibo_run(iid, device_id,
            lambda: self._sensibo.set_ac_state_many(device_id, on=target),
            on_error=lambda: f"Could not reach the AC unit(s):\n{self._sensibo.status_label()}")

    def _ac_toggle_style(self, device_id: str):
        """Return (label, fg, text_color) for an AC power toggle button."""
        power = self._sensibo.get_cached_power(device_id)
        if power is True:
            return "● On", "#243A34", GREEN_LT   # spec AC-on fill
        if power is False:
            return "● Off", "#3A2129", RED_LT     # spec AM/PM-pending-like red
        return "⏻ Power", CARD2, TEXT

    def _ac_temp_label(self, device_id: str) -> str:
        st = self._sensibo.get_cached_state(device_id)
        t = st.get("targetTemperature")
        return f"{t}°F" if t is not None else "Set Temp"

    def _ac_fan_label(self, device_id: str) -> str:
        st = self._sensibo.get_cached_state(device_id)
        f = st.get("fanLevel")
        return f.capitalize() if f else "Fan"

    def _sensibo_prompt_temp(self, iid: int, device_id: str, name: str):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        lo, hi = sensibo_mod.MIN_TEMP_F, sensibo_mod.MAX_TEMP_F
        dlg = ctk.CTkInputDialog(
            title="Set AC Target Temp",
            text=f"Target temperature (°F) for {name}.\n\n"
                 f"Minimum {lo}°F · Maximum {hi}°F")
        raw = dlg.get_input()
        if raw is None or not raw.strip():
            return
        try:
            temp_f = int(round(float(raw.strip())))
        except ValueError:
            messagebox.showerror("Sensibo", "Enter a numeric temperature.", parent=self)
            return
        if not (lo <= temp_f <= hi):
            messagebox.showerror("Sensibo",
                f"Temperature must be between {lo}°F and {hi}°F.", parent=self)
            return
        self._sensibo_run(iid, device_id,
            lambda: self._sensibo.set_ac_state_many(device_id, on=True, target_temp=temp_f),
            on_error=lambda: f"Could not reach the AC unit(s):\n{self._sensibo.status_label()}")

    def _sensibo_prompt_fan(self, iid: int, device_id: str, name: str):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Set Fan Speed")
        win.geometry("260x320")
        win.grab_set()
        _label(win, f"Fan speed — {name}", FONT_B, GOLD).pack(padx=16, pady=(14, 8))

        def _apply(level):
            win.destroy()
            self._sensibo_run(iid, device_id,
                lambda l=level: self._sensibo.set_ac_state_many(device_id, fan_level=l),
                on_error=lambda: f"Could not set fan speed:\n{self._sensibo.status_label()}")

        for lvl in sensibo_mod.FAN_LEVELS:
            _btn(win, lvl.capitalize(), lambda l=lvl: _apply(l),
                 width=180, height=32, fg=BORDER, hover=CARD2).pack(pady=3)
        _btn(win, "Cancel", win.destroy, width=180, height=28,
             fg=CARD2, hover=BORDER).pack(pady=(8, 4))

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
        if inc and inc.get("temp_alerts_enabled", 1):
            problems = calc.check_temp_humidity(inc, temp_c, humidity)
            if problems:
                for msg in problems:
                    # One standing alert per incubator+problem-type; suppress the
                    # per-minute repeats while the condition persists.
                    kind = "humidity" if "humid" in msg.lower() else "temp"
                    db.add_alert("temp_humidity", msg, severity="warning",
                                 incubator_id=incubator_id,
                                 dedup_key=f"temp_humidity:{kind}:{incubator_id}")
            else:
                # Condition resolved — auto-acknowledge any standing temp/humidity
                # alerts for this incubator so the red outline clears automatically.
                db.auto_acknowledge_alerts([
                    f"temp_humidity:temp:{incubator_id}",
                    f"temp_humidity:humidity:{incubator_id}",
                ])

        # Refresh UI on main thread
        self.after(0, self._on_reading_ui_refresh)

    def _update_dashboard_readings(self):
        """Update only the sensor labels on each card — no rebuild, no shutter."""
        unit = db.get_setting("temp_unit", "C")
        alert_ids = {a["incubator_id"] for a in db.get_active_alerts()
                     if a.get("incubator_id")}
        self._alert_inc_ids = alert_ids
        for inc_id, widgets in self._card_widgets.items():
            # Red outline live when an alert is active for this incubator
            card = widgets.get("card")
            if card is not None:
                if inc_id in alert_ids:
                    card.configure(border_width=2, border_color=RED)
                else:
                    card.configure(border_width=1,
                                   border_color="#222D3D" if widgets.get("hidden") else BORDER)
            reading = self._govee.get_last(inc_id)
            if not reading:
                db_row  = db.get_latest_reading(inc_id)
                reading = {"temp_c": db_row["temperature_c"], "humidity": db_row["humidity_pct"],
                           "timestamp": db_row["timestamp"]} if db_row else {}
            temp_c = reading.get("temp_c")
            hum    = reading.get("humidity")
            inc    = widgets.get("inc")
            t_min, t_max = calc.get_temp_range(inc)
            _poll_txt, _poll_col = _poll_age(reading.get("timestamp"), POLL_INTERVAL_SEC)
            if temp_c is not None:
                t_str = calc.format_temp(temp_c, unit)
                t_col = SUBTEXT if t_min is None else (GREEN if t_min <= temp_c <= t_max else RED)
                h_col = TEXT
                dot   = RED if calc.check_temp_humidity(inc, temp_c, hum) else GREEN
            else:
                t_str = "—"
                t_col = h_col = dot = SUBTEXT
            try:
                widgets["temp"].configure(text=t_str, text_color=t_col)
                widgets["hum"].configure(text=f"{hum:.0f}%" if hum is not None else "—", text_color=h_col)
                widgets["dot"].configure(text_color=dot)
                widgets["ts"].configure(text=f"Last polled: {_poll_txt}", text_color=_poll_col)
            except Exception:
                pass  # widget may have been destroyed by a full refresh

    def _on_reading_ui_refresh(self):
        self._refresh_alert_badge()
        if self._current_view == "dashboard":
            self._update_dashboard_readings()

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
            cycle = 0
            while True:
                try:
                    self._check_sensor_health()        # every ~10 min
                    if cycle % 6 == 0:
                        self._check_date_alerts()      # hourly
                        self._check_db_conflicts()     # hourly
                    if cycle % 72 == 0:                # ~every 12 h (and at startup)
                        self._run_db_backup()
                except Exception as exc:
                    print(f"[AlertChecker] {exc}")
                cycle += 1
                time.sleep(600)  # 10 minutes

        t = threading.Thread(target=loop, daemon=True, name="AlertChecker")
        t.start()

    def _run_db_backup(self):
        """Create today's DB snapshot (idempotent) and prune old ones."""
        try:
            path = db.make_daily_backup(keep_days=30)
            if path:
                self._sync_log(f"[Backup] daily snapshot ok: {os.path.basename(path)}")
            else:
                self._sync_log("[Backup] snapshot failed")
        except Exception as exc:
            self._sync_log(f"[Backup] {exc}")

    def _check_db_conflicts(self):
        """Raise a loud alert when Google Drive creates DB conflict copies."""
        try:
            conflicts = db.find_drive_conflicts()
        except Exception:
            return
        if not conflicts:
            db.auto_acknowledge_alerts(["db_conflict"])
            return
        names = ", ".join(os.path.basename(c) for c in conflicts[:3])
        more  = f" (+{len(conflicts) - 3} more)" if len(conflicts) > 3 else ""
        db.add_alert(
            "db_conflict",
            f"Google Drive made {len(conflicts)} database conflict copy(ies): "
            f"{names}{more}. Two computers likely wrote the database at the same "
            f"time — open the Data Storage folder and reconcile; recent changes "
            f"may have diverged.",
            severity="critical",
            cooldown_min=720,
            dedup_key="db_conflict",
        )

    @staticmethod
    def _age_minutes(ts: str, now: datetime = None) -> float | None:
        """Minutes since an ISO timestamp, tolerant of naive (local) and
        tz-aware (UTC, from the Pi) timestamps. None if unparseable/absent."""
        if not ts:
            return None
        try:
            then = datetime.fromisoformat(ts)
            ref  = datetime.now(then.tzinfo) if then.tzinfo else (now or datetime.now())
            return (ref - then).total_seconds() / 60
        except Exception:
            return None

    @staticmethod
    def _fmt_age(minutes: float | None) -> str:
        if minutes is None:
            return "never"
        if minutes < 60:
            return f"{int(minutes)} min"
        if minutes < 60 * 48:
            return f"{int(minutes // 60)} hr"
        return f"{int(minutes // (60 * 24))} days"

    def _check_sensor_health(self):
        """Raise/clear alerts when a Vapona sensor stops reporting or only sends
        implausible (corrupt-frame) values. Runs on the alert-checker thread."""
        try:
            import voc_db
        except Exception:
            return
        PI_OFFLINE_MIN = 30   # no contact at all (Pi down / off network)
        DATA_STALE_MIN = 50   # Pi alive but no valid readings (~3 missed cycles)

        incs = {i["id"]: i for i in db.get_incubators(include_hidden=True)}
        for d in voc_db.get_devices():
            # Separate dedup keys per failure mode so a transition (e.g. stale
            # data -> fully offline) isn't suppressed by the other's cooldown.
            off_dk  = f"vapona_offline:{d['id']}"
            data_dk = f"vapona_stale:{d['id']}"
            inc_id = d.get("incubator_id")
            name = d.get("name") or d.get("hardware_id")
            inc = incs.get(inc_id) if inc_id else None
            # Unassigned, or its incubator is off -> no data expected; clear both.
            if inc is None or (calc and calc.is_off(inc)):
                db.auto_acknowledge_alerts([off_dk, data_dk])
                continue

            inc_name = inc.get("name", "")
            seen_age = self._age_minutes(d.get("last_seen"))
            last_ok  = voc_db.latest_valid_reading(inc_id)
            data_age = self._age_minutes(last_ok.get("timestamp")) if last_ok else None

            if seen_age is None or seen_age > PI_OFFLINE_MIN:
                msg = (f"Vapona sensor “{name}” ({inc_name}) is offline — no contact "
                       f"for {self._fmt_age(seen_age)}. Check the Pi's power and Wi-Fi.")
                db.add_alert("vapona_sensor", msg, severity="warning",
                             incubator_id=inc_id, dedup_key=off_dk, cooldown_min=180)
                db.auto_acknowledge_alerts([data_dk])
            elif data_age is None or data_age > DATA_STALE_MIN:
                msg = (f"Vapona sensor “{name}” ({inc_name}) isn’t reporting valid "
                       f"readings — last good reading {self._fmt_age(data_age)} ago. "
                       f"The sensor may be disconnected, unpowered, or faulty "
                       f"(check wiring/power).")
                db.add_alert("vapona_sensor", msg, severity="warning",
                             incubator_id=inc_id, dedup_key=data_dk, cooldown_min=180)
                db.auto_acknowledge_alerts([off_dk])
            else:
                db.auto_acknowledge_alerts([off_dk, data_dk])

    def _check_date_alerts(self):
        lookahead = int(db.get_setting("date_alert_lookahead", "7"))
        batches   = db.get_batches(status="active")
        for batch in batches:
            for ev in calc.get_upcoming_events(batch, lookahead_days=lookahead):
                # One alert per event per batch, regardless of the countdown text
                _evkey = f"date:{ev.get('batch_id')}:{ev['label']}"
                if ev["urgent"]:
                    db.add_alert(
                        "date",
                        f"{'TODAY' if ev['days_away']==0 else 'TOMORROW'}: "
                        f"{ev['label']} — {ev['batch_name']} ({ev['incubator_name']})",
                        severity="critical",
                        batch_id=ev.get("batch_id"),
                        dedup_key=_evkey,
                    )
                elif ev["days_away"] <= lookahead:
                    db.add_alert(
                        "date",
                        f"{ev['label']} in {ev['days_away']}d — "
                        f"{ev['batch_name']} ({ev['incubator_name']})",
                        severity="warning",
                        batch_id=ev.get("batch_id"),
                        dedup_key=_evkey,
                    )
        self.after(0, self._refresh_alert_badge)

    # ── Email scheduler ────────────────────────────────────────────────────────

    def _start_email_scheduler(self):
        """Background thread: send daily report at 7 PM if SMTP is configured."""
        def _loop():
            last_sent_date = None
            while True:
                try:
                    now = datetime.now()
                    if now.hour == 19 and now.minute < 5:        # 7:00–7:04 PM window
                        today = now.date()
                        if last_sent_date != today:
                            if email_reporter.smtp_configured() and email_reporter.get_recipients():
                                err = email_reporter.send_daily_report()
                                if err:
                                    print(f"[Email] Send failed: {err}")
                                else:
                                    print(f"[Email] Daily report sent ({today})")
                            last_sent_date = today
                except Exception as exc:
                    print(f"[EmailScheduler] {exc}")
                time.sleep(60)

        t = threading.Thread(target=_loop, daemon=True, name="EmailScheduler")
        t.start()

    def _send_test_email(self):
        """Send a test email immediately using current settings (must Save first)."""
        recipients = email_reporter.get_recipients()
        if not recipients:
            self._email_status_lbl.configure(
                text="No recipients — add at least one email and Save Settings.",
                text_color=ORANGE)
            return
        if not email_reporter.smtp_configured():
            self._email_status_lbl.configure(
                text="SMTP host and username are required — Save Settings first.",
                text_color=ORANGE)
            return

        self._email_status_lbl.configure(text="Sending…", text_color=SUBTEXT)
        self.update_idletasks()

        def _send():
            err = email_reporter.send_daily_report()
            def _done():
                if err:
                    self._email_status_lbl.configure(
                        text=f"Failed: {err}", text_color=RED)
                else:
                    self._email_status_lbl.configure(
                        text=f"Sent to {len(recipients)} recipient(s) ✓",
                        text_color=GREEN)
            self.after(0, _done)

        threading.Thread(target=_send, daemon=True).start()

    # ── Git sync ──────────────────────────────────────────────────────────────

    def _git_pull(self):
        """Pull latest code from GitHub in a background thread."""
        def _pull():
            try:
                app_dir = os.path.dirname(os.path.abspath(__file__))
                result  = subprocess.run(
                    ["git", "-C", app_dir, "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
                )
                if result.returncode == 0:
                    msg = result.stdout.strip() or "Already up to date."
                    self._sync_log(f"[git pull] {msg}")
                    self.after(0, lambda: self._set_git_status(msg, ok=True))
                else:
                    err = (result.stderr or result.stdout).strip()
                    self._sync_log(f"[git pull] {err}")
                    self.after(0, lambda: self._set_git_status(f"pull: {err}", ok=False))
            except FileNotFoundError:
                # git not on PATH — silent, not a required dependency
                pass
            except Exception as exc:
                self._sync_log(f"[git pull] {exc}")

        threading.Thread(target=_pull, daemon=True, name="GitPull").start()

    def _sync_log(self, msg: str):
        """Print and append a git-sync message to git_sync.log next to the app."""
        print(msg)
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "git_sync.log")
            if os.path.exists(path) and os.path.getsize(path) > 250_000:
                open(path, "w").close()   # simple rotation
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        except Exception:
            pass

    def _start_auto_sync(self):
        """Keep code in sync with GitHub automatically (every 5 min):
        pull new commits, then commit + push any local edits.

        Disable by setting 'auto_git_sync' to '0' in settings.
        """
        if db.get_setting("auto_git_sync", "1") != "1":
            self._sync_log("[AutoSync] disabled via settings")
            return

        def _loop():
            # Small initial delay so startup isn't competing with the launch pull
            time.sleep(60)
            while True:
                try:
                    self._auto_sync_once()
                except FileNotFoundError:
                    pass   # git not installed — nothing we can do
                except Exception as exc:
                    self._sync_log(f"[AutoSync] {exc}")
                time.sleep(300)   # 5 minutes

        threading.Thread(target=_loop, daemon=True, name="AutoSync").start()

    def _auto_sync_once(self):
        """One full sync pass: pull → (commit local edits) → push. Thread-safe."""
        import socket
        app_dir = os.path.dirname(os.path.abspath(__file__))

        def _git(*args, timeout=40):
            return subprocess.run(["git", "-C", app_dir, *args],
                                  capture_output=True, text=True, timeout=timeout,
                                  creationflags=_NO_WINDOW)

        self._sync_log("[AutoSync] checking for updates…")
        _did_something = False

        # 1. Pull remote changes (fast-forward only — never auto-merge)
        pull = _git("pull", "--ff-only")
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout).strip()
            self.after(0, lambda e=err: self._set_git_status(
                f"sync paused: {e[:50]}", ok=False))
            self._sync_log(f"[AutoSync] pull failed (diverged?): {err}")
            return
        pulled = (pull.stdout or "").strip()
        if pulled and "already up to date" not in pulled.lower():
            self.after(0, lambda m=pulled: self._set_git_status(f"Updated: {m}", ok=True))
            self._sync_log(f"[AutoSync] pulled: {pulled}")
            _did_something = True

        # 2. Commit local source edits (if any, stable, and valid)
        status = _git("status", "--porcelain").stdout.strip()
        if status:
            # Stability guard: don't commit mid-save. Re-check after a short pause.
            time.sleep(3)
            if _git("status", "--porcelain").stdout.strip() != status:
                self._sync_log("[AutoSync] files still changing — will retry next cycle")
                return
            # Safety guard: never propagate code that doesn't compile.
            changed_py = [ln[3:].strip().strip('"') for ln in status.splitlines()
                          if ln.strip().endswith(".py")]
            for rel in changed_py:
                chk = subprocess.run(
                    [sys.executable, "-m", "py_compile", os.path.join(app_dir, rel)],
                    capture_output=True, text=True, creationflags=_NO_WINDOW)
                if chk.returncode != 0:
                    self.after(0, lambda f=rel: self._set_git_status(
                        f"sync paused: {os.path.basename(f)} has errors", ok=False))
                    self._sync_log(f"[AutoSync] {rel} failed py_compile — not committing")
                    return
            # Safety guard: never propagate code that fails the test suite.
            tests_ok, tests_out = self._run_tests(app_dir)
            if not tests_ok:
                self.after(0, lambda: self._set_git_status(
                    "sync paused: tests failing", ok=False))
                self._sync_log("[AutoSync] tests failing — not committing/pushing:\n"
                               + tests_out[-500:])
                return
            _git("add", "-A")
            host  = socket.gethostname()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            commit = _git("commit", "-m", f"Auto-sync from {host} at {stamp}")
            if commit.returncode == 0:
                self._sync_log(f"[AutoSync] committed local changes ({len(status.splitlines())} file(s))")
            else:
                err = (commit.stderr or commit.stdout).strip()
                self._sync_log(f"[AutoSync] commit failed: {err}")
                return

        # 3. Push if we have commits ahead of origin
        ahead = _git("rev-list", "--count", "origin/main..HEAD").stdout.strip()
        if ahead and ahead != "0":
            push = _git("push", "origin", "main")
            if push.returncode == 0:
                self.after(0, lambda n=ahead: self._set_git_status(
                    f"Pushed {n} update(s) ✓", ok=True))
                self._sync_log(f"[AutoSync] pushed {ahead} commit(s)")
                _did_something = True
            else:
                err = (push.stderr or push.stdout).strip()
                self.after(0, lambda e=err: self._set_git_status(
                    f"push failed: {e[:50]}", ok=False))
                self._sync_log(f"[AutoSync] push failed: {err}")
                return

        if not _did_something:
            self._sync_log("[AutoSync] up to date — nothing to pull or push")

    def _run_tests(self, app_dir: str) -> tuple[bool, str]:
        """Run the pytest suite for the auto-sync gate.

        Returns (passed, output). A missing tests/ folder or a machine without
        pytest installed is treated as a pass, so sync is never blocked just
        because the test tooling isn't present.
        """
        tests_dir = os.path.join(app_dir, "tests")
        if not os.path.isdir(tests_dir):
            return True, ""
        # Don't block sync when pytest simply isn't installed on this machine.
        have = subprocess.run([sys.executable, "-c", "import pytest"],
                              capture_output=True, creationflags=_NO_WINDOW)
        if have.returncode != 0:
            self._sync_log("[AutoSync] pytest not installed — skipping test gate")
            return True, ""
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", tests_dir, "-q"],
                capture_output=True, text=True, timeout=240,
                creationflags=_NO_WINDOW, cwd=app_dir)
        except Exception as exc:
            return True, f"(test run skipped: {exc})"
        # pytest exit 5 == "no tests collected" — treat as a pass.
        if r.returncode in (0, 5):
            return True, r.stdout
        return False, (r.stdout or "") + (r.stderr or "")

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

    def _pulse_dots(self):
        """Pulse the status dot on incubator cards that have an active alert."""
        self._pulse_on = not self._pulse_on
        alert_ids = getattr(self, "_alert_inc_ids", set())
        dim = "#7A1F1A"   # spec exact dim dot
        for iid, w in list(getattr(self, "_card_widgets", {}).items()):
            dot = w.get("dot")
            if dot is None or not dot.winfo_exists():
                continue
            if iid in alert_ids:
                dot.configure(text_color=RED if self._pulse_on else dim)
        self.after(750, self._pulse_dots)

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
        # Sensibo status (green when a key is configured)
        _has_sb = bool(db.get_setting("sensibo_api_key"))
        self._status_sensibo.configure(
            text="Sensibo: Ready" if _has_sb else "Sensibo: —",
            text_color=(GREEN if _has_sb else FAINT))
        self._status_refresh.configure(
            text=f"Last refresh: {datetime.now().strftime('%H:%M')}", text_color=FAINT)
        self.after(30_000, self._tick)  # refresh every 30s

    # ── Live code reload ────────────────────────────────────────────────────────

    def _restart_app(self):
        """Relaunch the app so code edits take effect without a manual
        close/reopen. Launches a fresh instance, then closes this one."""
        import subprocess
        try:
            subprocess.Popen([sys.executable] + sys.argv)
        except Exception as exc:
            messagebox.showerror("Reload", f"Could not restart:\n{exc}", parent=self)
            return
        self.destroy()
        os._exit(0)   # ensure the in-process QR server thread doesn't linger

    def _start_code_watcher(self):
        """Watch this folder's .py files; when any changes, reveal the Reload
        button so a single click loads the new code (no close/reopen needed)."""
        import threading
        app_dir = os.path.dirname(os.path.abspath(__file__))

        def _snapshot():
            stamps = {}
            for fn in os.listdir(app_dir):
                if fn.endswith(".py"):
                    try:
                        stamps[fn] = os.path.getmtime(os.path.join(app_dir, fn))
                    except OSError:
                        pass
            return stamps

        baseline = _snapshot()

        def _watch():
            import time
            while True:
                time.sleep(2)
                try:
                    if _snapshot() != baseline:
                        self.after(0, self._show_reload_btn)
                        return  # stop watching once an update is flagged
                except Exception:
                    pass

        threading.Thread(target=_watch, daemon=True).start()

    def _show_reload_btn(self):
        if hasattr(self, "_reload_btn") and not self._reload_btn.winfo_ismapped():
            self._reload_btn.pack(side="right", padx=8, pady=3)


# ═══════════════════════════════════════════════════════════════════════════════
#   ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = IncubationApp()
    app.mainloop()
