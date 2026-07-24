"""
email_reporter.py — Daily HTML email report for Bee Incubation Manager.

Scheduled to fire at 7 PM daily from a background thread in incubation_app.py.

SMTP settings stored in the DB settings table:
    smtp_host         (default: smtp.gmail.com)
    smtp_port         (default: 587)
    smtp_tls          (default: 1  — use STARTTLS)
    smtp_username     — your Gmail / SMTP account address
    smtp_password     — app password (Gmail: Settings > Security > App passwords)
    smtp_from         — display From address (falls back to username)
    email_recipients  — one address per line (or comma-separated)
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date

import incubation_db as db
import incubation_calc as calc
import inspection_db


# ── Colour palette (matches app dark theme) ───────────────────────────────────
_BG      = "#0F172A"
_CARD    = "#1E293B"
_CARD2   = "#0F172A"
_GOLD    = "#F59E0B"
_GREEN   = "#10B981"
_RED     = "#EF4444"
_ORANGE  = "#F97316"
_TEAL    = "#14B8A6"
_SUBTEXT = "#94A3B8"
_TEXT    = "#E2E8F0"


# ── Config helpers ─────────────────────────────────────────────────────────────

def get_recipients() -> list:
    """Parse the recipients setting into a clean list of email addresses."""
    raw = db.get_setting("email_recipients", "")
    addrs = [a.strip() for line in raw.splitlines() for a in line.split(",")]
    return [a for a in addrs if "@" in a]


def smtp_configured() -> bool:
    return bool(db.get_setting("smtp_host") and db.get_setting("smtp_username"))


def _parse_addr_list(raw: str) -> list:
    """Split a newline/comma separated address list. Accepts real email
    addresses AND carrier email-to-SMS gateway addresses (both contain '@')."""
    addrs = [a.strip() for line in (raw or "").splitlines() for a in line.split(",")]
    return [a for a in addrs if "@" in a]


def get_alert_recipients() -> list:
    """Who gets real-time alert notifications. Falls back to the daily-report
    recipients if no dedicated alert list is set."""
    lst = _parse_addr_list(db.get_setting("alert_recipients", ""))
    return lst or get_recipients()


def notifications_enabled() -> bool:
    return db.get_setting("alert_notify_enabled", "1") == "1"


def send_message(subject: str, body: str, recipients: list, html: str = None) -> str:
    """Send a plain-text (optionally multipart) message via the configured SMTP.
    Returns '' on success or an error string. Reused for alerts and test sends."""
    if not recipients:
        return "No recipients configured."
    cfg = _smtp_cfg()
    if not cfg["host"] or not cfg["username"]:
        return "SMTP not configured — set host and username in Settings."
    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = cfg["from"] or cfg["username"]
    msg["To"]      = ", ".join(recipients)
    try:
        if cfg["tls"]:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            smtp.ehlo(); smtp.starttls(); smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        smtp.login(cfg["username"], cfg["password"])
        smtp.sendmail(msg["From"], recipients, msg.as_string())
        smtp.quit()
        return ""
    except Exception as exc:
        return str(exc)


def dispatch_alerts() -> int:
    """Send one concise notification for any active, not-yet-notified alerts,
    then mark them notified so they never re-send. Short body so it fits an SMS.
    Returns the number of alerts included (0 if nothing sent)."""
    if not notifications_enabled():
        return 0
    alerts = db.get_unnotified_alerts()
    if not alerts:
        return 0
    recipients = get_alert_recipients()
    if not recipients or not smtp_configured():
        return 0
    n = len(alerts)
    subject = f"Bee Incubation: {n} alert{'s' if n != 1 else ''}"
    lines = []
    for a in alerts:
        inc = a.get("incubator_name")
        lines.append(f"- {('[' + inc + '] ') if inc else ''}{a['message']}")
    body = subject + "\n\n" + "\n".join(lines)
    if send_message(subject, body, recipients):
        return 0   # send failed — leave them un-notified to retry next cycle
    db.mark_alerts_notified([a["id"] for a in alerts])
    return n


def _smtp_cfg() -> dict:
    return {
        "host":     db.get_setting("smtp_host",     "smtp.gmail.com"),
        "port":     int(db.get_setting("smtp_port",  "587") or 587),
        "tls":      db.get_setting("smtp_tls",      "1") == "1",
        "username": db.get_setting("smtp_username", ""),
        "password": db.get_setting("smtp_password", ""),
        "from":     db.get_setting("smtp_from",     ""),
    }


# ── Stats helper ──────────────────────────────────────────────────────────────

def _temp_stats(readings: list) -> dict:
    temps = [r["temperature_c"] for r in readings if r.get("temperature_c") is not None]
    if not temps:
        return {"min": None, "max": None, "avg": None, "count": 0}
    return {
        "min":   round(min(temps), 1),
        "max":   round(max(temps), 1),
        "avg":   round(sum(temps) / len(temps), 1),
        "count": len(temps),
    }


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _td(text, color=_TEXT, bold=False, align="left", bg=None):
    s = f"padding:7px 12px;color:{color};text-align:{align};font-size:14px;"
    if bold:
        s += "font-weight:bold;"
    if bg:
        s += f"background:{bg};"
    return f"<td style='{s}'>{text}</td>"


def _insp_badge(status: str, record: dict | None) -> str:
    if status == "done":
        ts = (record.get("timestamp", "") or "")[11:16] if record else ""
        return f"<span style='color:{_GREEN};font-weight:bold;'>&#10003; Done {ts}</span>"
    if status == "missed":
        return f"<span style='color:{_RED};font-weight:bold;'>&#10007; Missed</span>"
    if status == "open":
        return f"<span style='color:{_ORANGE};'>! Open now</span>"
    return f"<span style='color:{_SUBTEXT};'>&#183; Pending</span>"


# ── Report builder ─────────────────────────────────────────────────────────────

def build_html_report(report_date: date = None) -> str:
    """Build the full HTML email body for the daily report."""
    if report_date is None:
        report_date = date.today()

    incubators  = db.get_incubators()                   # visible (active) only
    all_batches = db.get_batches(status="active")
    alerts_24h  = db.get_alerts_24h()

    date_str    = report_date.strftime("%A, %B %d, %Y").replace(" 0", " ")
    gen_time    = datetime.now().strftime("%H:%M")

    # Group alerts by incubator_id for fast lookup
    alerts_by_inc: dict = {}
    for a in alerts_24h:
        alerts_by_inc.setdefault(a.get("incubator_id"), []).append(a)

    # Events happening tomorrow (days_away == 1)
    tomorrow_events = [
        ev for ev in calc.get_all_events(all_batches, lookahead_days=2)
        if ev["days_away"] == 1
    ]

    # ── Outer shell ───────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bee Incubation Daily Report</title></head>
<body style="margin:0;padding:0;background:{_BG};font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:740px;margin:0 auto;padding:28px 20px;">

  <!-- ═══ Header ══════════════════════════════════════════════════════════ -->
  <div style="border-bottom:2px solid {_GOLD};padding-bottom:14px;margin-bottom:22px;">
    <div style="font-size:26px;font-weight:bold;color:{_GOLD};">&#x1F41D; Bee Incubation Daily Report</div>
    <div style="color:{_SUBTEXT};font-size:13px;margin-top:4px;">{date_str} &nbsp;&#183;&nbsp; Generated {gen_time}</div>
  </div>
"""

    # ── Per-incubator blocks ───────────────────────────────────────────────────
    for inc in incubators:
        iid      = inc["id"]
        mode_key = inc.get("temp_mode", "incubation")
        mode_cfg = calc.TEMP_MODES.get(mode_key, calc.TEMP_MODES["incubation"])
        t_min, t_max = calc.get_temp_range(inc)

        readings = db.get_readings_24h(iid)
        stats    = _temp_stats(readings)
        latest   = db.get_latest_reading(iid)

        # Current temp
        if latest and latest.get("temperature_c") is not None:
            cur_t   = latest["temperature_c"]
            cur_col = _GREEN if t_min <= cur_t <= t_max else _RED
            cur_str = f"{cur_t:.1f}&#176;C"
            hum_str = f"{latest.get('humidity_pct', 0):.0f}%"
        else:
            cur_t   = None
            cur_col = _SUBTEXT
            cur_str = "No reading"
            hum_str = "—"

        # Inspections
        insp_status = inspection_db.get_inspection_status(iid)
        insp_done   = inspection_db.get_today_inspections(iid)

        # Active batches for this incubator
        inc_batches = [b for b in all_batches if b.get("incubator_id") == iid]

        # Alerts for this incubator in last 24h
        inc_alerts = alerts_by_inc.get(iid, [])

        # ── Card header ───────────────────────────────────────────────────
        html += f"""
  <!-- ═══ Incubator: {inc['name']} ═════════════════════════════════════════ -->
  <div style="background:{_CARD};border-radius:10px;padding:20px;margin-bottom:16px;border-left:4px solid {_GOLD};">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;">
      <div>
        <div style="font-size:20px;font-weight:bold;color:{_GOLD};">{inc['name']}</div>
        <div style="color:{_SUBTEXT};font-size:13px;margin-top:3px;">
          {mode_cfg['label']} &nbsp;&#183;&nbsp; {mode_cfg['min']}&#8211;{mode_cfg['max']}&#176;C
          &nbsp;&#183;&nbsp; Capacity: {inc.get('capacity', 50)} trays
        </div>
      </div>
      <div style="text-align:right;">
        <div style="font-size:28px;font-weight:bold;color:{cur_col};">{cur_str}</div>
        <div style="color:{_SUBTEXT};font-size:13px;">&#128167; {hum_str}</div>
      </div>
    </div>
"""

        # ── 24-hour temperature summary table ─────────────────────────────
        stat_min  = f"{stats['min']}&#176;C" if stats['min'] is not None else "&#8212;"
        stat_max  = f"{stats['max']}&#176;C" if stats['max'] is not None else "&#8212;"
        stat_avg  = f"{stats['avg']}&#176;C" if stats['avg'] is not None else "&#8212;"
        stat_n    = str(stats['count'])

        # colour each stat — warn if min below or max above threshold
        min_col = (_RED if stats['min'] is not None and stats['min'] < t_min else _TEXT)
        max_col = (_RED if stats['max'] is not None and stats['max'] > t_max else _TEXT)

        html += f"""
    <!-- 24h temp stats -->
    <div style="margin-bottom:14px;">
      <div style="color:{_SUBTEXT};font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">24-Hour Temperature</div>
      <table style="width:100%;border-collapse:collapse;background:{_CARD2};border-radius:6px;overflow:hidden;">
        <thead>
          <tr style="border-bottom:1px solid #334155;">
            <th style="padding:8px 12px;color:{_SUBTEXT};text-align:left;font-size:12px;font-weight:normal;">Min</th>
            <th style="padding:8px 12px;color:{_SUBTEXT};text-align:left;font-size:12px;font-weight:normal;">Max</th>
            <th style="padding:8px 12px;color:{_SUBTEXT};text-align:left;font-size:12px;font-weight:normal;">Average</th>
            <th style="padding:8px 12px;color:{_SUBTEXT};text-align:left;font-size:12px;font-weight:normal;">Readings</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            {_td(stat_min, min_col, bold=(min_col==_RED))}
            {_td(stat_max, max_col, bold=(max_col==_RED))}
            {_td(stat_avg)}
            {_td(stat_n, _SUBTEXT)}
          </tr>
        </tbody>
      </table>
    </div>
"""

        # ── Inspections ───────────────────────────────────────────────────
        m_badge = _insp_badge(insp_status.get("morning","pending"), insp_done.get("morning"))
        e_badge = _insp_badge(insp_status.get("evening","pending"), insp_done.get("evening"))

        html += f"""
    <!-- Inspections -->
    <div style="margin-bottom:14px;">
      <div style="color:{_SUBTEXT};font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Today's Inspections</div>
      <table style="width:100%;border-collapse:collapse;background:{_CARD2};border-radius:6px;overflow:hidden;">
        <tr>
          <td style="padding:8px 12px;color:{_SUBTEXT};font-size:13px;width:50%;">
            &#9788; Morning (6&#8211;10 AM): {m_badge}
          </td>
          <td style="padding:8px 12px;color:{_SUBTEXT};font-size:13px;">
            &#9790; Evening (4&#8211;10 PM): {e_badge}
          </td>
        </tr>
      </table>
    </div>
"""

        # ── Active batches ─────────────────────────────────────────────────
        if inc_batches:
            html += f"""
    <!-- Active batches -->
    <div style="margin-bottom:14px;">
      <div style="color:{_SUBTEXT};font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Active Batches</div>
"""
            for batch in inc_batches:
                day_num   = calc.get_incubation_day(batch)
                day_label = f"Day {day_num} of incubation" if day_num is not None else ""
                bname     = batch.get("name") or f"Batch {batch['id']}"
                # Events on exactly today for this batch
                today_evs = [
                    ev for ev in calc.get_upcoming_events(batch, lookahead_days=1)
                    if ev["days_away"] == 0
                ]

                html += f"""
      <div style="background:{_CARD2};border-radius:6px;padding:10px 14px;margin:4px 0;">
        <span style="color:{_TEXT};font-weight:bold;font-size:14px;">{bname}</span>
        {"&nbsp;&nbsp;<span style='color:" + _ORANGE + ";font-size:13px;font-weight:bold;'>" + day_label + "</span>" if day_label else ""}
"""
                if today_evs:
                    for ev in today_evs:
                        html += f"""
        <div style="color:{_RED};font-size:13px;margin-top:4px;padding-left:8px;">
          &#9888; Today: {ev['label']} ({ev['date']})
        </div>"""
                html += "\n      </div>"
            html += "\n    </div>"

        # ── Temp alerts last 24h ───────────────────────────────────────────
        if inc_alerts:
            html += f"""
    <!-- Alerts -->
    <div style="margin-bottom:4px;">
      <div style="color:{_SUBTEXT};font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Temp Alerts — Last 24h</div>
"""
            for a in inc_alerts[:8]:
                ts  = (a.get("triggered_at") or "")[:16].replace("T", " ")
                ack = " <em style='color:{_SUBTEXT};'>(acknowledged)</em>" if a.get("acknowledged") else ""
                html += f"""
      <div style="background:#450A0A;border-radius:4px;padding:7px 12px;margin:3px 0;font-size:13px;color:{_RED};">
        {ts} &nbsp;&#8212;&nbsp; {a['message']}{ack}
      </div>"""
            html += "\n    </div>"

        html += "\n  </div>  <!-- end incubator card -->\n"

    # ── Tomorrow's calendar ────────────────────────────────────────────────────
    html += f"""
  <!-- ═══ Tomorrow's Calendar ════════════════════════════════════════════ -->
  <div style="background:{_CARD};border-radius:10px;padding:20px;margin-bottom:16px;border-left:4px solid {_TEAL};">
    <div style="font-size:16px;font-weight:bold;color:{_TEAL};margin-bottom:12px;">&#128197; Tomorrow's Calendar</div>
"""
    if tomorrow_events:
        for ev in tomorrow_events:
            dot_col = _RED if ev["urgent"] else _ORANGE
            html += f"""
    <div style="border-left:3px solid {dot_col};padding:8px 14px;margin:5px 0;background:{_CARD2};border-radius:0 6px 6px 0;">
      <span style="color:{_TEXT};font-weight:bold;font-size:14px;">{ev['label']}</span>
      <span style="color:{_SUBTEXT};font-size:13px;"> &nbsp;&#183;&nbsp; {ev['incubator_name']} / {ev['batch_name']}</span>
      <span style="color:{_SUBTEXT};font-size:12px;float:right;">{ev['date']}</span>
    </div>"""
    else:
        html += f"""
    <div style="color:{_SUBTEXT};font-size:14px;">No events scheduled for tomorrow.</div>"""
    html += "\n  </div>\n"

    # ── Footer ─────────────────────────────────────────────────────────────────
    html += f"""
  <div style="text-align:center;color:{_SUBTEXT};font-size:12px;margin-top:8px;padding-top:16px;border-top:1px solid #1E293B;">
    Bee Incubation Manager &nbsp;&#183;&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>

</div>
</body>
</html>"""
    return html


# ── Send ──────────────────────────────────────────────────────────────────────

def send_daily_report() -> str:
    """
    Build and send the daily email report.
    Returns empty string on success, or an error message string on failure.
    """
    recipients = get_recipients()
    if not recipients:
        return "No recipients configured — add emails in Settings → Email Reports."

    cfg = _smtp_cfg()
    if not cfg["host"] or not cfg["username"]:
        return "SMTP not configured — fill in host and username in Settings → Email Reports."

    html_body = build_html_report()
    today_str = date.today().strftime("%B %d, %Y").replace(" 0", " ")
    subject   = f"Bee Incubation Daily Report — {today_str}"

    msg             = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = cfg["from"] or cfg["username"]
    msg["To"]       = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if cfg["tls"]:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        smtp.login(cfg["username"], cfg["password"])
        smtp.sendmail(msg["From"], recipients, msg.as_string())
        smtp.quit()
        return ""
    except Exception as exc:
        return str(exc)
