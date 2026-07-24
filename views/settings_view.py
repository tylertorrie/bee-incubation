"""
views/settings_view.py  —  the Settings screen and its helpers.

Extracted from incubation_app.py as a mixin: a pure relocation, so every
method keeps its original `self.` references and the App class simply
inherits them. Covers Govee, the background poller, thresholds and mode
goals, data storage/backups, the QR server, email and Google Calendar.
"""
import os
import sys
import subprocess
import threading
import time
from datetime import date
from tkinter import filedialog, messagebox

import customtkinter as ctk

import incubation_db as db
import incubation_calc as calc
import govee_client as govee_mod
import sensibo_client as sensibo_mod
import gcal_sync
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
from views.dialogs import _VocDeviceManager, _WifiNetworkManager

from app_config import _NO_WINDOW


class SettingsViewMixin:
    """Settings view: builds the screen and handles every action on it."""

    def _build_settings_view(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._main, fg_color="transparent", corner_radius=0)

        hdr = self._screen_header(frame, "Settings",
                                  "Integrations & polling configuration")
        _btn_primary(hdr, "Save Settings", self._save_settings,
                     width=140).pack(side="right")

        scroll = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=4)

        def section(title):
            f = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=12,
                             border_width=1, border_color=BORDER2)
            f.pack(fill="x", padx=4, pady=(8, 4))
            _label(f, title, ("Segoe UI", 13, "bold"), GOLD).pack(
                anchor="w", padx=18, pady=(14, 4))
            g = ctk.CTkFrame(f, fg_color="transparent")
            g.pack(fill="x", padx=18, pady=(0, 14))
            g.columnconfigure(1, weight=1)
            return g

        # Govee
        gf = section("Govee API")
        self._set = {}
        r0 = _FormRow(gf, 0, "API Key", "Paste Govee API key here", 360)
        self._set["govee_api_key"] = r0
        _btn(gf, "Test Connection", self._test_govee, fg=BLUE, hover="#1D4ED8",
             text_color="white", width=150).grid(row=1, column=1, sticky="w", padx=4, pady=4)
        self._govee_status_lbl = _label(gf, "", FONT_S, SUBTEXT)
        self._govee_status_lbl.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        # Sensibo (manual AC control)
        sf = section("Sensibo API (AC Control)")
        _label(sf, "Used for manual AC on/off and target temperature buttons.\n"
                    "Set a per-incubator Sensibo Device ID in each incubator's setup.",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 6))
        r1 = _FormRow(sf, 1, "API Key", "Paste Sensibo API key here", 360)
        self._set["sensibo_api_key"] = r1
        _btn(sf, "Test Connection", self._test_sensibo, fg=BLUE, hover="#1D4ED8",
             text_color="white", width=150).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        self._sensibo_status_lbl = _label(sf, "", FONT_S, SUBTEXT)
        self._sensibo_status_lbl.grid(row=3, column=1, sticky="w", padx=4, pady=2)

        # Vapona Sensors (VOC device management)
        vf = section("Vapona Sensors")
        _label(vf, "Manage the Raspberry Pi VOC sensors. Each sensor reports a\n"
                   "stable hardware ID; assign it to an incubator and position\n"
                   "here (the app is authoritative — the Pi does not decide).",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=2, sticky="w",
                                     padx=4, pady=(0, 6))
        _vbtns = ctk.CTkFrame(vf, fg_color="transparent")
        _vbtns.grid(row=1, column=0, columnspan=2, sticky="w", padx=0, pady=4)
        _btn(_vbtns, "Manage Vapona Sensors", self._manage_voc_devices,
             fg=BLUE, hover="#1D4ED8", text_color="white", width=200).pack(
                 side="left", padx=4)
        _btn(_vbtns, "Sensor Wi-Fi Networks", self._manage_wifi_networks,
             fg=CARD2, hover=BORDER, width=180).pack(side="left", padx=4)
        self._voc_dev_status_lbl = _label(vf, "", FONT_S, SUBTEXT)
        self._voc_dev_status_lbl.grid(row=2, column=0, columnspan=2, sticky="w",
                                      padx=4, pady=2)
        self._refresh_voc_dev_status()

        # Google Calendar Sync
        gcf = section("Google Calendar Sync")
        _label(gcf, "Auto-push the incubation Calendar to Google Calendar.\n"
                    "Needs a one-time Google Cloud OAuth credentials file (Desktop app).",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 6))
        self._set["gcal_credentials_path"] = _FormRow(gcf, 1, "Credentials JSON", "path to client_secret.json", 320)
        _btn(gcf, "Browse", self._browse_gcal_creds, width=80, height=28,
             fg=BORDER, hover=CARD2).grid(row=1, column=2, padx=4, pady=4)
        self._set["gcal_calendar_id"] = _FormRow(gcf, 2, "Calendar ID", "primary", 320)
        self._set["gcal_enabled"] = _FormRow(gcf, 3, "Auto-sync on changes (1=yes, 0=no)", "0", 60)
        gc_btns = ctk.CTkFrame(gcf, fg_color="transparent")
        gc_btns.grid(row=4, column=0, columnspan=3, sticky="w", padx=4, pady=(6, 2))
        _btn(gc_btns, "Install Libraries", self._install_gcal_libs,
             fg=BORDER, hover=CARD2, width=150).pack(side="left", padx=(0, 6))
        _btn(gc_btns, "Connect / Authorize", self._gcal_connect,
             fg=GREEN, hover="#15803D", text_color="white", width=170).pack(side="left", padx=(0, 6))
        _btn(gc_btns, "Sync Now", lambda: self._gcal_sync(notify=True),
             fg=BLUE, hover="#1D4ED8", text_color="white", width=110).pack(side="left")
        self._gcal_status_lbl = _label(gcf, "", FONT_S, SUBTEXT)
        self._gcal_status_lbl.grid(row=5, column=0, columnspan=3, sticky="w", padx=4, pady=2)

        # Background Poller
        bf = section("Background Poller")
        _label(bf,
               "Run a lightweight background task that collects Govee readings\n"
               "even when this window is closed.  Enable it on one computer only\n"
               "— the one that stays on — to avoid duplicate writes.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 6))
        btn_row = ctk.CTkFrame(bf, fg_color="transparent")
        btn_row.pack(anchor="w", padx=14, pady=(0, 4))
        _btn(btn_row, "Enable on this computer",  self._enable_poller,
             fg=GREEN, hover="#15803D", text_color="white", width=200).pack(side="left", padx=(0, 8))
        _btn(btn_row, "Disable on this computer", self._disable_poller,
             fg=RED,   hover="#991B1B", text_color="white", width=200).pack(side="left")
        self._poller_status_lbl = _label(bf, "", FONT_S, SUBTEXT)
        self._poller_status_lbl.pack(anchor="w", padx=14, pady=(0, 8))
        self._refresh_poller_status()

        # Poll interval is fixed at 15 minutes (not user-configurable)
        pf = section("Polling & Thresholds")
        _label(pf, "Govee poll interval: every 15 minutes (fixed)",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=2,
                                     sticky="w", padx=4, pady=4)
        self._set["date_alert_lookahead"] = _FormRow(pf, 1, "Date Alert Lookahead (days)", "7", 100)
        self._set["temp_unit"] = _FormRow(pf, 2, "Temp Unit",
            widget=_combo(pf, ["C", "F"], 80))

        # Temperature Mode Goals — target temp/humidity per mode
        tmg = section("Temperature Mode Goals")
        _label(tmg,
               "Target temperature (°C) and humidity (%) for each mode. Shown on\n"
               "incubator tiles and drawn as dotted goal lines on the charts.",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 6))
        _label(tmg, "Mode",       FONT_S, SUBTEXT).grid(row=1, column=0, sticky="w", padx=4)
        _label(tmg, "Temp °C",    FONT_S, SUBTEXT).grid(row=1, column=1, sticky="w", padx=4)
        _label(tmg, "Humidity %", FONT_S, SUBTEXT).grid(row=1, column=2, sticky="w", padx=4)
        self._goal_entries = {}
        for _ri, _mk in enumerate(calc.ACTIVE_MODES, start=2):
            _gt, _gh = db.get_mode_goals(_mk)
            _label(tmg, calc.TEMP_MODES[_mk]["label"], FONT_B, TEXT).grid(
                row=_ri, column=0, sticky="w", padx=4, pady=3)
            _te = ctk.CTkEntry(tmg, width=80, fg_color=CARD2,
                               border_color=BORDER, text_color=TEXT)
            _te.grid(row=_ri, column=1, sticky="w", padx=4, pady=3)
            _te.insert(0, "" if _gt is None else f"{_gt:g}")
            _he = ctk.CTkEntry(tmg, width=80, fg_color=CARD2,
                               border_color=BORDER, text_color=TEXT)
            _he.grid(row=_ri, column=2, sticky="w", padx=4, pady=3)
            _he.insert(0, "" if _gh is None else f"{_gh:g}")
            self._goal_entries[_mk] = (_te, _he)

        # Development model (degree-days) — drives the projection on the detail view
        dm = section("Development Model (degree-days)")
        _label(dm,
               "Optional. Projects a batch's progress from accumulated temperature.\n"
               "Base = developmental threshold; Target = °C-days to emergence\n"
               "(calibrate the target from your own records). Leave target blank to hide.",
               FONT_S, SUBTEXT).grid(row=0, column=0, columnspan=2,
                                     sticky="w", padx=4, pady=(0, 6))
        self._set["dev_base_temp_c"] = _FormRow(dm, 1, "Base temp (°C)", "10", 100)
        self._set["dev_target_degree_days"] = _FormRow(dm, 2, "Target (°C-days)", "", 100)

        # Calculation
        cf = section("Weight Calculations")
        self._set["lbs_per_gal"] = _FormRow(cf, 0, "Lbs per Gallon (raw cells)", "2.2", 100)
        self._set["target_gals_per_tray"] = _FormRow(cf, 1, "Target Gals per Tray", "2.0", 100)

        # QR Server
        qf = section("QR Scan Server")
        self._set["qr_server_port"] = _FormRow(qf, 0, "Port", "5151", 100)
        self._set["qr_server_enabled"] = _FormRow(qf, 1, "Enabled (1=yes, 0=no)", "1", 60)
        self._qr_ip_lbl = _label(qf, "", FONT_S, BLUE)
        self._qr_ip_lbl.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        # Mobile app section
        mf = section("Mobile App")
        self._set["mobile_passcode"] = _FormRow(
            mf, 0, "Shared passcode", "", 200)
        _label(mf, "Set a passcode before sharing the app online (Tailscale).\n"
                   "Leave blank to disable the login (local network only).",
               FONT_S, SUBTEXT).grid(row=1, column=0, columnspan=2,
                                     sticky="w", padx=4, pady=(2, 4))

        # ── Data Storage ──────────────────────────────────────────────────────
        dsf = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=12,
                           border_width=1, border_color=BORDER2)
        dsf.pack(fill="x", padx=4, pady=(8, 4))
        _label(dsf, "Data Storage", ("Segoe UI", 13, "bold"), GOLD).pack(
            anchor="w", padx=18, pady=(14, 2))
        _label(dsf,
               "Move the database to your Google Drive folder so data syncs\n"
               "between computers automatically. The app restarts after saving.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 8))

        dsg = ctk.CTkFrame(dsf, fg_color=CARD2, corner_radius=8)
        dsg.pack(fill="x", padx=14, pady=(0, 14))

        # Current path row
        path_row = ctk.CTkFrame(dsg, fg_color="transparent")
        path_row.pack(fill="x", padx=12, pady=(10, 4))
        _label(path_row, "Current database:", FONT_S, SUBTEXT).pack(side="left")
        self._db_path_lbl = _label(path_row, db.DB_PATH, FONT_S, BLUE)
        self._db_path_lbl.pack(side="left", padx=(8, 0))

        # Backups status row — daily auto-snapshots in a "Backups" subfolder
        backup_row = ctk.CTkFrame(dsg, fg_color="transparent")
        backup_row.pack(fill="x", padx=12, pady=(0, 4))
        _label(backup_row, "Backups:", FONT_S, SUBTEXT).pack(side="left")

        def _backup_status_text():
            try:
                bk = db.list_backups()
                if not bk:
                    return "none yet (created automatically at startup)"
                newest = os.path.basename(bk[0]).replace("incubation-", "").replace(".db", "")
                return f"{len(bk)} daily snapshot(s), newest {newest}"
            except Exception:
                return "—"

        self._backup_lbl = _label(backup_row, _backup_status_text(), FONT_S, TEXT)
        self._backup_lbl.pack(side="left", padx=(8, 0))

        def _backup_now():
            self._run_db_backup()
            self._backup_lbl.configure(text=_backup_status_text())

        _btn(backup_row, "Back up now", _backup_now,
             fg=CARD2, hover=BORDER, width=110, height=24).pack(side="left", padx=12)

        # New path entry row
        new_row = ctk.CTkFrame(dsg, fg_color="transparent")
        new_row.pack(fill="x", padx=12, pady=(4, 10))
        _label(new_row, "Move database to:", FONT_S, SUBTEXT).pack(side="left")
        self._db_folder_entry = ctk.CTkEntry(
            new_row, placeholder_text="Paste or browse to a folder…",
            fg_color=CARD, border_color=BORDER, text_color=TEXT, width=320)
        self._db_folder_entry.pack(side="left", padx=(8, 6))
        _btn(new_row, "Browse", self._browse_db_folder,
             width=80, height=28, fg=BORDER, hover=CARD2).pack(side="left")

        # Status / hint
        self._db_move_status = _label(dsg, "", FONT_S, SUBTEXT)
        self._db_move_status.pack(anchor="w", padx=12, pady=(0, 4))

        # Action buttons
        act_row = ctk.CTkFrame(dsg, fg_color="transparent")
        act_row.pack(fill="x", padx=12, pady=(0, 10))
        _btn(act_row, "Move & Restart", self._move_database,
             fg=DK_GOLD, hover=GOLD, text_color="black",
             width=140, height=30).pack(side="left", padx=(0, 8))
        _btn(act_row, "Use Local File (default)", self._use_local_db,
             fg=BORDER, hover=CARD2, width=180, height=30).pack(side="left")

        # ── Email Reports ─────────────────────────────────────────────────
        ef = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=12,
                          border_width=1, border_color=BORDER2)
        ef.pack(fill="x", padx=4, pady=(8, 4))
        _label(ef, "Email Reports", ("Segoe UI", 13, "bold"), GOLD).pack(
            anchor="w", padx=18, pady=(14, 2))
        _label(ef,
               "A daily summary is sent at 7 PM with 24h temps, inspections, batch progress and "
               "tomorrow's calendar.\nFor Gmail: use an App Password (Google Account › Security › "
               "2-Step Verification › App passwords).",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 8))

        eg = ctk.CTkFrame(ef, fg_color=CARD2, corner_radius=8)
        eg.pack(fill="x", padx=14, pady=(0, 14))
        eg.columnconfigure(1, weight=1)

        self._set["smtp_host"]     = _FormRow(eg, 0, "SMTP Host",     "smtp.gmail.com", 220)
        self._set["smtp_port"]     = _FormRow(eg, 1, "SMTP Port",     "587",            80)
        self._set["smtp_tls"]      = _FormRow(eg, 2, "Use TLS (1/0)", "1",              60)
        self._set["smtp_username"] = _FormRow(eg, 3, "Username / Email", "you@gmail.com", 260)
        self._set["smtp_password"] = _FormRow(eg, 4, "Password / App Password", "••••••••", 260)
        self._set["smtp_from"]     = _FormRow(eg, 5, "From Address (optional)", "Incubation App <you@gmail.com>", 300)

        # Recipients box
        _label(eg, "Recipients\n(one per line)", FONT_S, SUBTEXT).grid(
            row=6, column=0, sticky="ne", padx=(14, 8), pady=8)
        self._email_recip_box = ctk.CTkTextbox(
            eg, height=90, fg_color=CARD, border_color=BORDER,
            text_color=TEXT, font=FONT_S, corner_radius=6)
        self._email_recip_box.grid(row=6, column=1, sticky="ew", padx=(0, 14), pady=8)

        # Test + status
        email_btn_row = ctk.CTkFrame(eg, fg_color="transparent")
        email_btn_row.grid(row=7, column=1, sticky="w", padx=(0, 14), pady=(0, 10))
        self._email_status_lbl = _label(email_btn_row, "", FONT_S, SUBTEXT)
        _btn(email_btn_row, "Send Test Email", self._send_test_email,
             fg=BLUE, hover="#1D4ED8", text_color="white",
             width=150, height=30).pack(side="left", padx=(0, 10))
        self._email_status_lbl.pack(side="left")

        # ── Alert Notifications (text / email) ────────────────────────────
        af = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=12,
                          border_width=1, border_color=BORDER2)
        af.pack(fill="x", padx=4, pady=(8, 4))
        _label(af, "Alert Notifications", ("Segoe UI", 13, "bold"), GOLD).pack(
            anchor="w", padx=18, pady=(14, 2))
        _label(af,
               "Get a text or email the moment an alert fires (temperature out of "
               "range, a Vapona sensor offline, etc.). Uses the SMTP settings above.\n"
               "For a TEXT, add your carrier's email-to-SMS address, e.g. "
               "5551234567@vtext.com (Verizon), @txt.att.net (AT&T), "
               "@tmomail.net (T-Mobile). Leave blank to reuse the report recipients.",
               FONT_S, SUBTEXT).pack(anchor="w", padx=14, pady=(0, 8))

        ag = ctk.CTkFrame(af, fg_color=CARD2, corner_radius=8)
        ag.pack(fill="x", padx=14, pady=(0, 14))
        ag.columnconfigure(1, weight=1)

        self._set["alert_notify_enabled"] = _FormRow(ag, 0, "Enabled (1=yes, 0=no)", "1", 60)

        _label(ag, "Send to\n(email or SMS\ngateway, one\nper line)", FONT_S, SUBTEXT).grid(
            row=1, column=0, sticky="ne", padx=(14, 8), pady=8)
        self._alert_recip_box = ctk.CTkTextbox(
            ag, height=80, fg_color=CARD, border_color=BORDER,
            text_color=TEXT, font=FONT_S, corner_radius=6)
        self._alert_recip_box.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=8)

        alert_btn_row = ctk.CTkFrame(ag, fg_color="transparent")
        alert_btn_row.grid(row=2, column=1, sticky="w", padx=(0, 14), pady=(0, 10))
        self._alert_status_lbl = _label(alert_btn_row, "", FONT_S, SUBTEXT)
        _btn(alert_btn_row, "Send Test Alert", self._send_test_alert,
             fg=BLUE, hover="#1D4ED8", text_color="white",
             width=150, height=30).pack(side="left", padx=(0, 10))
        self._alert_status_lbl.pack(side="left")

        return frame

    def _refresh_settings(self):
        keys = ["govee_api_key", "sensibo_api_key", "date_alert_lookahead",
                "temp_unit", "lbs_per_gal", "target_gals_per_tray",
                "qr_server_port", "qr_server_enabled", "mobile_passcode",
                "smtp_host", "smtp_port", "smtp_tls",
                "smtp_username", "smtp_password", "smtp_from",
                "gcal_credentials_path", "gcal_calendar_id", "gcal_enabled",
                "dev_base_temp_c", "dev_target_degree_days", "alert_notify_enabled"]
        for k in keys:
            if k in self._set:
                self._set[k].set(db.get_setting(k))
        self._qr_ip_lbl.configure(
            text=f"Phone scan URL: http://{qr_server.get_local_ip()}:{self._qr_port}/tray/<id>")
        # Recipients text box
        recip_val = db.get_setting("email_recipients", "")
        self._email_recip_box.delete("1.0", "end")
        if recip_val:
            self._email_recip_box.insert("1.0", recip_val)
        # Alert-notification recipients
        alert_val = db.get_setting("alert_recipients", "")
        self._alert_recip_box.delete("1.0", "end")
        if alert_val:
            self._alert_recip_box.insert("1.0", alert_val)
        # Reload per-mode goals (in case another computer changed them)
        for _mk, (_te, _he) in getattr(self, "_goal_entries", {}).items():
            _gt, _gh = db.get_mode_goals(_mk)
            _te.delete(0, "end"); _te.insert(0, "" if _gt is None else f"{_gt:g}")
            _he.delete(0, "end"); _he.insert(0, "" if _gh is None else f"{_gh:g}")

    def _save_settings(self):
        keys = ["govee_api_key", "sensibo_api_key", "date_alert_lookahead",
                "temp_unit", "lbs_per_gal", "target_gals_per_tray",
                "qr_server_port", "qr_server_enabled", "mobile_passcode",
                "smtp_host", "smtp_port", "smtp_tls",
                "smtp_username", "smtp_password", "smtp_from",
                "gcal_credentials_path", "gcal_calendar_id", "gcal_enabled",
                "dev_base_temp_c", "dev_target_degree_days", "alert_notify_enabled"]
        for k in keys:
            if k in self._set:
                db.set_setting(k, self._set[k].get())
        # Save recipients
        recip_text = self._email_recip_box.get("1.0", "end").strip()
        db.set_setting("email_recipients", recip_text)
        db.set_setting("alert_recipients",
                       self._alert_recip_box.get("1.0", "end").strip())
        # Per-mode temperature/humidity goals
        for _mk, (_te, _he) in getattr(self, "_goal_entries", {}).items():
            db.set_mode_goals(_mk, _te.get().strip(), _he.get().strip())
        # Update govee/sensibo keys live
        self._govee.set_api_key(db.get_setting("govee_api_key"))
        self._sensibo.set_api_key(db.get_setting("sensibo_api_key"))
        messagebox.showinfo("Settings", "Settings saved.", parent=self)

    # ── Data storage helpers ──────────────────────────────────────────────────

    def _browse_db_folder(self):
        folder = filedialog.askdirectory(
            title="Choose a folder for the database "
                  "(e.g. your Google Drive folder)",
            mustexist=True,
        )
        if folder:
            self._db_folder_entry.delete(0, "end")
            self._db_folder_entry.insert(0, folder)
            self._db_move_status.configure(
                text=f"Will save to: {folder}\\incubation.db",
                text_color=SUBTEXT)

    def _move_database(self):
        import shutil
        folder = self._db_folder_entry.get().strip()
        if not folder:
            self._db_move_status.configure(
                text="Please browse to or paste a folder path first.",
                text_color=ORANGE)
            return
        if not os.path.isdir(folder):
            self._db_move_status.configure(
                text="That folder doesn't exist. Create it first or pick a different one.",
                text_color=RED)
            return

        new_path = os.path.join(folder, "incubation.db")
        same_file = (os.path.exists(new_path) and
                     os.path.abspath(new_path) == os.path.abspath(db.DB_PATH))

        # If a database is already in the target folder (e.g. a shared Google
        # Drive folder another computer set up), let the user JOIN it instead of
        # overwriting — so a new computer never clobbers the shared data.
        if os.path.exists(new_path) and not same_file:
            choice = messagebox.askyesnocancel(
                "Database already exists there",
                f"A database already exists at:\n{new_path}\n\n"
                "Yes — replace it with THIS computer's current data\n"
                "No — keep the existing (shared) database and just use it here\n"
                "Cancel — do nothing",
                parent=self)
            if choice is None:          # Cancel
                return
            if choice:                  # Yes — overwrite with our data
                try:
                    shutil.copy2(db.DB_PATH, new_path)
                except Exception as exc:
                    self._db_move_status.configure(
                        text=f"Copy failed: {exc}", text_color=RED)
                    return
            # No — leave the existing file untouched and just adopt its path
        elif not same_file:
            # Empty target folder — copy our current data into it
            try:
                shutil.copy2(db.DB_PATH, new_path)
            except Exception as exc:
                self._db_move_status.configure(
                    text=f"Copy failed: {exc}", text_color=RED)
                return

        # Save new path to config so next launch uses it
        db.save_config({"db_path": new_path})

        messagebox.showinfo(
            "Done — please restart",
            f"The app will now use:\n{new_path}\n\n"
            "Close and reopen the app to start using this location.\n\n"
            "Tip: on another computer, install the app, then go to\n"
            "Settings ▸ Data Storage, browse to the same Google Drive folder,\n"
            "click 'Move & Restart', and choose 'No' to join the shared data.",
            parent=self)

    def _use_local_db(self):
        """Reset to default local file (remove any configured path)."""
        db.save_config({"db_path": ""})
        messagebox.showinfo(
            "Done — please restart",
            "Database location reset to the default (next to the app files).\n"
            "Restart the app for the change to take effect.",
            parent=self)

    # ── Background poller ──────────────────────────────────────────────────────

    _TASK_NAME   = "BeeIncubationPoller"
    _POLLER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "govee_poller.py")

    def _poller_installed(self) -> bool:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run")
            winreg.QueryValueEx(key, self._TASK_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False

    def _refresh_voc_dev_status(self):
        try:
            devs = voc_db.get_devices()
        except Exception as exc:
            self._voc_dev_status_lbl.configure(
                text=f"(unavailable: {exc})", text_color=SUBTEXT)
            return
        total = len(devs)
        unassigned = sum(1 for d in devs if d.get("incubator_id") is None)
        if total == 0:
            self._voc_dev_status_lbl.configure(
                text="No sensors have reported yet.", text_color=SUBTEXT)
        else:
            msg = f"{total} sensor(s) known"
            if unassigned:
                msg += f"  •  {unassigned} unassigned"
            self._voc_dev_status_lbl.configure(
                text=msg, text_color=(GOLD if unassigned else GREEN))

    def _manage_voc_devices(self):
        _VocDeviceManager(self, on_close=self._refresh_voc_dev_status)

    def _manage_wifi_networks(self):
        _WifiNetworkManager(self)

    def _refresh_poller_status(self):
        import json, socket
        installed = self._poller_installed()
        # Check lock file for active machine info
        lock_file = os.path.join(os.path.dirname(db.DB_PATH), "govee_poller.lock")
        lock_info = ""
        try:
            with open(lock_file, encoding="utf-8") as f:
                lock = json.load(f)
            lock_info = f"  |  Active on: {lock.get('machine')}  (last seen {lock.get('last_seen', '')[:19]})"
        except Exception:
            pass
        if installed:
            self._poller_status_lbl.configure(
                text=f"Enabled on this computer ({socket.gethostname()}){lock_info}",
                text_color=GREEN)
        else:
            self._poller_status_lbl.configure(
                text=f"Not enabled on this computer{lock_info}",
                text_color=SUBTEXT)

    def _kill_poller_processes(self):
        subprocess.run(
            ["wmic", "process", "where", "commandline like '%govee_poller%'", "delete"],
            capture_output=True, creationflags=_NO_WINDOW
        )

    def _enable_poller(self):
        import winreg
        self._kill_poller_processes()  # stop any existing instances first
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        cmd = f'"{pythonw}" "{self._POLLER_SCRIPT}"'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, self._TASK_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
        except Exception as exc:
            messagebox.showerror("Background Poller",
                f"Could not register startup entry:\n{exc}", parent=self)
            return
        # Start it immediately too
        subprocess.Popen([pythonw, self._POLLER_SCRIPT], creationflags=_NO_WINDOW)
        self._refresh_poller_status()
        messagebox.showinfo("Background Poller",
            "Poller enabled and started.\n\n"
            "It will run automatically each time you log in to Windows.\n"
            "Check govee_poller.log (next to incubation.db) for activity.",
            parent=self)

    def _disable_poller(self):
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, self._TASK_NAME)
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass
        except Exception as exc:
            messagebox.showerror("Background Poller",
                f"Could not remove startup entry:\n{exc}", parent=self)
            return
        # Kill all running govee_poller.py processes
        self._kill_poller_processes()
        self._refresh_poller_status()
        messagebox.showinfo("Background Poller",
            "Poller disabled and stopped on this computer.",
            parent=self)

    def _test_govee(self):
        key = self._set["govee_api_key"].get()
        if not key:
            self._govee_status_lbl.configure(text="Enter an API key first.", text_color=ORANGE)
            return
        self._govee_status_lbl.configure(text="Connecting…", text_color=SUBTEXT)
        self.update()

        tmp     = govee_mod.GoveeClient(api_key=key)
        devices = tmp.get_all_devices()

        if not devices:
            self._govee_status_lbl.configure(
                text=f"Connection failed: {tmp.status_label()}", text_color=RED)
            return

        db.set_setting("govee_api_key", key)
        self._govee.set_api_key(key)

        sensors = [d for d in devices if d.get("is_sensor")]
        others  = [d for d in devices if not d.get("is_sensor")]

        self._govee_status_lbl.configure(
            text=f"Connected — {len(sensors)} sensor(s), {len(others)} other device(s). "
                 f"See device list below.", text_color=GREEN)

        # Open a popup showing all devices with copy-ready IDs
        self._show_device_list(devices)

    def _show_device_list(self, devices: list):
        """
        Popup listing every Govee device found, clearly separated into
        Temperature Sensors and Other Devices.  Each row has copy buttons
        for Device ID and SKU so they can be pasted straight into Incubator settings.
        """
        win = ctk.CTkToplevel(self)
        win.title("Govee Devices Found")
        win.geometry("700x520")
        win.grab_set()

        _label(win, "Govee Devices", FONT_H, GOLD).pack(padx=16, pady=(14, 2), anchor="w")
        _label(win,
               "Sensors are highlighted. Copy the Device ID and SKU into each Incubator's settings.",
               FONT_S, SUBTEXT).pack(padx=16, anchor="w")

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=8)

        sensors = [d for d in devices if d.get("is_sensor")]
        others  = [d for d in devices if not d.get("is_sensor")]

        def device_row(parent, dev: dict, highlight: bool):
            bg  = CARD2 if highlight else CARD
            row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=8,
                               border_width=2 if highlight else 0,
                               border_color=GOLD if highlight else BORDER)
            row.pack(fill="x", pady=3, padx=4)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="both", expand=True, padx=12, pady=8)

            name     = dev.get("deviceName") or dev.get("device", "Unknown")
            sku      = dev.get("sku") or dev.get("model", "")
            dev_id   = dev.get("device", "")
            api_ver  = dev.get("api_version", "v2")
            tag      = "SENSOR" if highlight else "device"
            tag_col  = GREEN if highlight else SUBTEXT

            top_row = ctk.CTkFrame(left, fg_color="transparent")
            top_row.pack(fill="x")
            _label(top_row, name, FONT_B, GOLD if highlight else TEXT).pack(side="left")
            _label(top_row, f"  [{tag}]", FONT_S, tag_col).pack(side="left")
            _label(top_row, f"  {api_ver}", FONT_S, SUBTEXT).pack(side="right")

            id_row = ctk.CTkFrame(left, fg_color="transparent")
            id_row.pack(fill="x", pady=(2, 0))
            _label(id_row, f"Device ID:  {dev_id}", ("Courier New", 10), TEXT).pack(side="left")

            sku_row = ctk.CTkFrame(left, fg_color="transparent")
            sku_row.pack(fill="x")
            _label(sku_row, f"SKU/Model:  {sku}", ("Courier New", 10), TEXT).pack(side="left")

            # Copy buttons
            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=10, pady=8)

            def _copy(text, btn):
                self.clipboard_clear()
                self.clipboard_append(text)
                orig = btn.cget("text")
                btn.configure(text="Copied!")
                self.after(1500, lambda b=btn, t=orig: b.configure(text=t))

            btn_id  = _btn(right, "Copy ID",  None, width=82, height=26, fg=BORDER, hover=CARD)
            btn_id.configure(command=lambda t=dev_id, b=btn_id: _copy(t, b))
            btn_id.pack(pady=2)

            btn_sku = _btn(right, "Copy SKU", None, width=82, height=26, fg=BORDER, hover=CARD)
            btn_sku.configure(command=lambda t=sku, b=btn_sku: _copy(t, b))
            btn_sku.pack(pady=2)

        # ── Sensors section ──
        if sensors:
            _label(scroll, f"Temperature / Humidity Sensors  ({len(sensors)})",
                   FONT_B, GREEN).pack(anchor="w", padx=4, pady=(6, 2))
            for d in sensors:
                device_row(scroll, d, highlight=True)
        else:
            _label(scroll, "No temperature sensors found via API.",
                   FONT_B, ORANGE).pack(anchor="w", padx=4, pady=6)
            _label(scroll,
                   "If your sensors connect via a gateway, make sure the gateway\n"
                   "is online and its firmware is up to date in the Govee app.",
                   FONT_S, SUBTEXT).pack(anchor="w", padx=4)

        # ── Other devices section ──
        if others:
            _label(scroll, f"Other Devices  ({len(others)})",
                   FONT_B, SUBTEXT).pack(anchor="w", padx=4, pady=(14, 2))
            for d in others:
                device_row(scroll, d, highlight=False)

        _btn(win, "Close", win.destroy, width=100,
             fg=CARD2, hover=BORDER).pack(pady=10)

    def _browse_gcal_creds(self):
        path = filedialog.askopenfilename(
            title="Select Google OAuth credentials JSON",
            filetypes=[("JSON file", "*.json")])
        if path:
            self._set["gcal_credentials_path"].set(path)

    def _install_gcal_libs(self):
        self._gcal_status_lbl.configure(text="Installing libraries…", text_color=SUBTEXT)
        self.update()

        def _work():
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     "google-api-python-client", "google-auth-oauthlib",
                     "google-auth-httplib2"],
                    capture_output=True, text=True,
                    creationflags=_NO_WINDOW)
                ok = proc.returncode == 0
                msg = ("Libraries installed. Restart, then click Connect."
                       if ok else f"Install failed:\n{proc.stderr[-300:]}")
            except Exception as exc:
                ok, msg = False, f"Install error: {exc}"
            self.after(0, lambda: self._gcal_status_lbl.configure(
                text=msg, text_color=(GREEN if ok else RED)))
        threading.Thread(target=_work, daemon=True).start()

    def _gcal_connect(self):
        # Persist the path/calendar so the sync uses the latest values
        db.set_setting("gcal_credentials_path", self._set["gcal_credentials_path"].get())
        db.set_setting("gcal_calendar_id", self._set["gcal_calendar_id"].get() or "primary")
        if not gcal_sync.available():
            self._gcal_status_lbl.configure(
                text="Install libraries first, then restart.", text_color=ORANGE)
            return
        self._gcal_status_lbl.configure(
            text="Opening browser for authorization…", text_color=SUBTEXT)
        self._gcal_sync(interactive=True, notify=True)

    def _test_sensibo(self):
        key = self._set["sensibo_api_key"].get()
        if not key:
            self._sensibo_status_lbl.configure(text="Enter an API key first.", text_color=ORANGE)
            return
        self._sensibo_status_lbl.configure(text="Connecting…", text_color=SUBTEXT)
        self.update()

        tmp     = sensibo_mod.SensiboClient(api_key=key)
        devices = tmp.list_devices()

        if not devices:
            self._sensibo_status_lbl.configure(
                text=f"Connection failed: {tmp.status_label()}", text_color=RED)
            return

        db.set_setting("sensibo_api_key", key)
        self._sensibo.set_api_key(key)

        self._sensibo_status_lbl.configure(
            text=f"Connected — {len(devices)} AC pod(s). See device list below.",
            text_color=GREEN)
        self._show_sensibo_device_list(devices)

    def _show_sensibo_device_list(self, devices: list):
        """Popup listing every Sensibo pod with a copy-ready Device ID."""
        win = ctk.CTkToplevel(self)
        win.title("Sensibo Devices Found")
        win.geometry("560x420")
        win.grab_set()

        _label(win, "Sensibo Devices", FONT_H, GOLD).pack(padx=16, pady=(14, 2), anchor="w")
        _label(win, "Copy the Device ID into each Incubator's setup.",
               FONT_S, SUBTEXT).pack(padx=16, anchor="w")

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=8)

        def _copy(text, btn):
            self.clipboard_clear()
            self.clipboard_append(text)
            orig = btn.cget("text")
            btn.configure(text="Copied!")
            self.after(1500, lambda b=btn, t=orig: b.configure(text=t))

        for d in devices:
            dev_id = d.get("id", "")
            room   = (d.get("room") or {}).get("name", "Unnamed")
            row = ctk.CTkFrame(scroll, fg_color=CARD2, corner_radius=8)
            row.pack(fill="x", pady=3, padx=4)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="both", expand=True, padx=12, pady=8)
            _label(left, room, FONT_B, GOLD).pack(anchor="w")
            _label(left, f"Device ID:  {dev_id}", ("Courier New", 10), TEXT).pack(anchor="w")
            btn_id = _btn(row, "Copy ID", None, width=82, height=26, fg=BORDER, hover=CARD)
            btn_id.configure(command=lambda t=dev_id, b=btn_id: _copy(t, b))
            btn_id.pack(side="right", padx=10, pady=8)

        if not devices:
            _label(scroll, "No Sensibo AC pods found on this account.",
                   FONT_B, ORANGE).pack(anchor="w", padx=4, pady=6)

        _btn(win, "Close", win.destroy, width=100,
             fg=CARD2, hover=BORDER).pack(pady=10)

    # ══════════════════════════════════════════════════════════════════════════
    #  OPENERS
    # ══════════════════════════════════════════════════════════════════════════

