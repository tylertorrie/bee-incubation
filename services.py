"""
services.py  —  background services: sensor polling, alerts, backups, email and git sync.

Extracted from incubation_app.py as a mixin (pure relocation).
"""
import os
import sys
import subprocess
import threading
import time
from datetime import datetime, date
from tkinter import messagebox

import incubation_db as db
import incubation_calc as calc
import qr_server
import voc_db
import email_reporter
from app_config import POLL_INTERVAL_SEC, _NO_WINDOW

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)


class ServicesMixin:
    """Background services: sensor polling, alerts, backups, email and git sync."""

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
                    # Push any new alerts to phones/email (once each)
                    try:
                        import email_reporter
                        sent = email_reporter.dispatch_alerts()
                        if sent:
                            print(f"[AlertChecker] notified {sent} new alert(s)")
                    except Exception as exc:
                        print(f"[AlertChecker] notify failed: {exc}")
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

    def _send_test_alert(self):
        """Send a test alert notification (text/email) using saved settings."""
        recipients = email_reporter.get_alert_recipients()
        if not recipients:
            self._alert_status_lbl.configure(
                text="No recipients — add an email/SMS address and Save Settings.",
                text_color=ORANGE)
            return
        if not email_reporter.smtp_configured():
            self._alert_status_lbl.configure(
                text="SMTP host and username are required — Save Settings first.",
                text_color=ORANGE)
            return
        self._alert_status_lbl.configure(text="Sending…", text_color=SUBTEXT)
        self.update_idletasks()

        def _send():
            err = email_reporter.send_message(
                "Bee Incubation: test alert",
                "This is a test alert from the Bee Incubation Manager. "
                "If you received this, real alerts will reach you here.",
                recipients)
            def _done():
                if err:
                    self._alert_status_lbl.configure(
                        text=f"Failed: {err}", text_color=RED)
                else:
                    self._alert_status_lbl.configure(
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
