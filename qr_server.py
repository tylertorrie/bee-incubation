"""
qr_server.py  —  Tiny Flask server that lets a phone camera update tray data.

Flow:
  1. Desktop generates QR code containing http://<LAN-IP>:<port>/tray/<id>
  2. Worker scans QR with phone → browser opens the URL
  3. Mobile-friendly form appears with current tray data pre-filled
  4. Worker edits fields (weight, live count, status, etc.) and taps Save
  5. POST /tray/<id>/update → server writes to SQLite → callback fires
  6. Desktop GUI refreshes the tray in real time

To start:  qr_server.start(port=5151, on_update=callback_fn)
"""
import socket
import threading
from typing import Callable, Optional

try:
    from flask import Flask, request, jsonify, render_template_string
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

import incubation_db as db

# ── Mobile template ───────────────────────────────────────────────────────────

_TRAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tray {{ tray.tray_number }}</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#111827;color:#f3f4f6;font-family:system-ui,sans-serif;padding:16px}
    h2{color:#fbbf24;margin-bottom:4px;font-size:1.3rem}
    .sub{color:#9ca3af;font-size:.85rem;margin-bottom:16px}
    .card{background:#1f2937;border-radius:10px;padding:12px;margin-bottom:12px}
    .card-title{color:#6b7280;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
    .row{display:flex;gap:8px;margin-bottom:10px}
    .field{flex:1}
    label{display:block;color:#9ca3af;font-size:.78rem;margin-bottom:4px}
    input,select{width:100%;background:#374151;border:1px solid #4b5563;border-radius:6px;
                 color:#f3f4f6;padding:8px 10px;font-size:1rem}
    input:focus,select:focus{outline:2px solid #fbbf24;border-color:#fbbf24}
    .btn{display:block;width:100%;background:#d97706;color:#111;font-weight:700;
         font-size:1.05rem;padding:13px;border:none;border-radius:8px;cursor:pointer;margin-top:4px}
    .btn:active{background:#b45309}
    .toast{display:none;background:#065f46;color:#d1fae5;border-radius:8px;
           padding:12px;text-align:center;margin-top:10px;font-weight:600}
    .info-row{display:flex;justify-content:space-between;margin-bottom:4px;font-size:.9rem}
    .info-val{color:#fbbf24}
  </style>
</head>
<body>
  <h2>Tray {{ tray.tray_number }}</h2>
  <div class="sub">{{ tray.incubator_name or "No incubator" }} &nbsp;·&nbsp; {{ tray.sample_name or "No sample" }}</div>

  <div class="card">
    <div class="card-title">Current Info</div>
    <div class="info-row"><span>Status</span><span class="info-val">{{ tray.status or "active" }}</span></div>
    <div class="info-row"><span>In Date</span><span class="info-val">{{ tray.in_date or "—" }}</span></div>
    <div class="info-row"><span>Out Date</span><span class="info-val">{{ tray.out_date or "—" }}</span></div>
  </div>

  <form id="f">
    <div class="card">
      <div class="card-title">Update Measurements</div>
      <div class="row">
        <div class="field"><label>Weight (lbs)</label>
          <input type="number" step="0.01" name="weight_lbs" value="{{ tray.weight_lbs or '' }}" inputmode="decimal"></div>
        <div class="field"><label>Volume (gal)</label>
          <input type="number" step="0.01" name="volume_gal" value="{{ tray.volume_gal or '' }}" inputmode="decimal"></div>
      </div>
      <div class="row">
        <div class="field"><label>Live Count</label>
          <input type="number" name="live_count" value="{{ tray.live_count or '' }}" inputmode="numeric"></div>
        <div class="field"><label>Parasite (%)</label>
          <input type="number" step="0.1" name="parasite_level_pct" value="{{ tray.parasite_level_pct or '' }}" inputmode="decimal"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Status</div>
      <select name="status">
        <option value="active"   {% if tray.status=='active'   %}selected{% endif %}>Active (in incubator)</option>
        <option value="cooled"   {% if tray.status=='cooled'   %}selected{% endif %}>Cooled</option>
        <option value="released" {% if tray.status=='released' %}selected{% endif %}>Released</option>
        <option value="removed"  {% if tray.status=='removed'  %}selected{% endif %}>Removed</option>
      </select>
      <div style="margin-top:10px">
        <label>Notes</label>
        <input type="text" name="notes" value="{{ tray.notes or '' }}" placeholder="Optional notes…">
      </div>
    </div>

    <button class="btn" type="submit">💾  Save Changes</button>
    <div class="toast" id="ok">✓ Saved successfully</div>
  </form>

  <script>
    document.getElementById('f').onsubmit = async function(e) {
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {};
      fd.forEach((v,k) => { if (v !== '') body[k] = v; });
      const r = await fetch('/tray/{{ tray.id }}/update',
                            {method:'POST', headers:{'Content-Type':'application/json'},
                             body:JSON.stringify(body)});
      if (r.ok) {
        const t = document.getElementById('ok');
        t.style.display = 'block';
        setTimeout(() => t.style.display='none', 3000);
      }
    };
  </script>
</body>
</html>"""


# ── Mobile web app (PWA) ──────────────────────────────────────────────────────

_MOBILE_CSS = """
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:#0F172A;color:#F3F4F6;font-family:system-ui,-apple-system,sans-serif;
     padding:0 0 76px 0;min-height:100vh}
.topbar{position:sticky;top:0;z-index:5;background:#111827;padding:14px 16px;
        border-bottom:1px solid #1f2937;display:flex;align-items:center;justify-content:space-between}
.topbar h1{font-size:1.15rem;color:#FFD700}
.topbar .upd{font-size:.7rem;color:#6B7280}
.wrap{padding:12px}
.card{background:#1F2937;border-radius:14px;padding:14px;margin-bottom:12px;
      border:1px solid #263347}
.cn{font-size:1.15rem;font-weight:700;color:#FFD700;margin-bottom:10px}
.metrics{display:flex;gap:10px;margin-bottom:10px}
.metric{flex:1;background:#263347;border-radius:10px;padding:10px;text-align:center}
.ml{font-size:.7rem;color:#9CA3AF;text-transform:uppercase;letter-spacing:.04em}
.mv{font-size:1.6rem;font-weight:800;margin-top:2px}
.meta{font-size:.85rem;color:#9CA3AF;margin-top:4px}
.pills{display:flex;gap:8px;margin-top:12px}
.pill{flex:1;text-align:center;padding:9px 0;border-radius:14px;font-weight:700;
      font-size:.95rem;color:#fff}
.pill.g{background:#15803D}
.pill.r{background:#B91C1C}
.soon{color:#9CA3AF;text-align:center;padding:30px 10px;font-size:1rem}
.loading{color:#6B7280;text-align:center;padding:40px}
.nav{position:fixed;bottom:0;left:0;right:0;background:#111827;border-top:1px solid #1f2937;
     display:flex;padding:6px 0 calc(6px + env(safe-area-inset-bottom))}
.nav a{flex:1;text-align:center;color:#6B7280;text-decoration:none;font-size:.72rem;padding:6px 0}
.nav a .ic{display:block;font-size:1.3rem;margin-bottom:2px}
.nav a.active{color:#FFD700}
.banner{background:#065F46;color:#D1FAE5;border-radius:10px;padding:12px;
        text-align:center;font-weight:700;margin-bottom:12px}
.period{display:inline-block;background:#78350F;color:#FBBF24;padding:4px 10px;
        border-radius:8px;font-size:.78rem;font-weight:700;margin-top:6px}
.gv{font-size:.9rem;color:#9CA3AF;margin-top:6px}
.fld{margin-bottom:14px}
.fld label{display:block;color:#9CA3AF;font-size:.82rem;margin-bottom:6px}
.fld input[type=number],.fld textarea{width:100%;background:#374151;
        border:1px solid #4b5563;border-radius:8px;color:#F3F4F6;padding:12px;font-size:1.05rem}
.fld input:focus,.fld textarea:focus{outline:2px solid #FBBF24;border-color:#FBBF24}
.fld textarea{min-height:72px;resize:vertical}
.chk{display:flex;align-items:center;justify-content:space-between;padding:12px;
     background:#263347;border-radius:10px;margin-bottom:8px}
.chk span{font-size:.95rem}
.chk input{width:26px;height:26px;accent-color:#16A34A}
.savebtn{display:block;width:100%;background:#D97706;color:#111;font-weight:800;
         font-size:1.1rem;padding:15px;border:none;border-radius:10px;margin-top:6px}
.savebtn:active{background:#B45309}
.ibtn{display:block;background:#1D4ED8;color:#fff;text-align:center;text-decoration:none;
      padding:12px;border-radius:10px;font-weight:700;margin-top:10px}
.donebtn{display:block;background:#15803D;color:#fff;text-align:center;text-decoration:none;
      padding:12px;border-radius:10px;font-weight:700;margin-top:10px}
.bp{display:flex;gap:8px;margin-top:10px}
.bp .pill{flex:1}
"""


def _nav_html(active: str) -> str:
    def item(key, href, icon, label):
        cls = "active" if key == active else ""
        return f'<a class="{cls}" href="{href}"><span class="ic">{icon}</span>{label}</a>'
    return ('<div class="nav">'
            + item("home",    "/",             "🏠", "Dashboard")
            + item("inspect", "/m/inspections", "🔍", "Inspect")
            + item("trays",   "/m/trays",       "📦", "Trays")
            + '</div>')


def _mobile_page(title: str, body: str, active: str = "home") -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
        "<meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='mobile-web-app-capable' content='yes'>"
        "<meta name='theme-color' content='#111827'>"
        f"<title>{title}</title><style>{_MOBILE_CSS}</style></head>"
        f"<body>{body}{_nav_html(active)}</body></html>"
    )


def _mobile_poll_age(ts: Optional[str]) -> tuple:
    """(text, color) for how long ago — colors scaled to the 15-min poll cadence."""
    from datetime import datetime
    if not ts:
        return "Never polled", "#9CA3AF"
    try:
        then    = datetime.fromisoformat(ts)
        minutes = (datetime.now() - then).total_seconds() / 60
    except Exception:
        return "Unknown", "#9CA3AF"
    if minutes < 1:
        text = "Just now"
    elif minutes < 60:
        text = f"{int(minutes)} min ago"
    elif minutes < 120:
        text = "1 hr ago"
    else:
        text = f"{int(minutes // 60)} hrs ago"
    cycle = 15  # minutes
    if minutes <= cycle * 1.5 + 1:
        color = "#22C55E"
    elif minutes <= cycle * 3 + 2:
        color = "#F59E0B"
    else:
        color = "#EF4444"
    return text, color


def _dashboard_data() -> dict:
    """Build the incubator list the mobile dashboard renders."""
    try:
        import inspection_db as idb
    except Exception:
        idb = None
    try:
        import incubation_calc as calc
    except Exception:
        calc = None

    unit = db.get_setting("temp_unit", "C")
    incs = []
    for inc in db.get_incubators():
        row    = db.get_latest_reading(inc["id"])
        temp_c = row["temperature_c"] if row else None
        hum    = row["humidity_pct"]  if row else None
        ts     = row["timestamp"]     if row else None

        t_min, t_max = (calc.get_temp_range(inc) if calc else (None, None))
        if temp_c is None:
            temp_str, temp_col = "—", "#F3F4F6"
        else:
            temp_str = calc.format_temp(temp_c, unit) if calc else f"{temp_c:.1f}°{unit}"
            if t_min is None:
                temp_col = "#F3F4F6"
            else:
                temp_col = "#22C55E" if (t_min <= temp_c <= t_max) else "#EF4444"

        poll_txt, poll_col = _mobile_poll_age(ts)
        stats = db.get_tray_stats(incubator_id=inc["id"], status="active")
        insp  = (idb.get_inspection_status(inc["id"]) if idb
                 else {"morning": "pending", "evening": "pending"})

        incs.append({
            "id":           inc["id"],
            "name":         inc["name"],
            "temp":         temp_str,
            "temp_color":   temp_col,
            "humidity":     f"{hum:.0f}%" if hum is not None else "—",
            "last_polled":  poll_txt,
            "poll_color":   poll_col,
            "trays":        stats["count"],
            "capacity":     inc.get("capacity") or 0,
            "morning_done": insp.get("morning") == "done",
            "evening_done": insp.get("evening") == "done",
        })
    return {"incubators": incs, "unit": unit}


_DASHBOARD_BODY = """
<div class="topbar"><h1>🐝 Incubators</h1><span class="upd" id="upd"></span></div>
<div class="wrap"><div id="cards"><div class="loading">Loading…</div></div></div>
<script>
async function load(){
  try{
    const r = await fetch('/api/dashboard', {cache:'no-store'});
    const d = await r.json();
    const c = document.getElementById('cards');
    if(!d.incubators.length){ c.innerHTML = '<div class="loading">No incubators.</div>'; return; }
    c.innerHTML = '';
    d.incubators.forEach(function(i){
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML =
        '<div class="cn">'+i.name+'</div>'+
        '<div class="metrics">'+
          '<div class="metric"><div class="ml">Temp</div><div class="mv" style="color:'+i.temp_color+'">'+i.temp+'</div></div>'+
          '<div class="metric"><div class="ml">Humidity</div><div class="mv">'+i.humidity+'</div></div>'+
        '</div>'+
        '<div class="meta" style="color:'+i.poll_color+'">● Last polled: '+i.last_polled+'</div>'+
        '<div class="meta">'+i.trays+' / '+i.capacity+' trays</div>'+
        '<div class="pills">'+
          '<span class="pill '+(i.morning_done?'g':'r')+'">🌅 AM '+(i.morning_done?'✓':'•')+'</span>'+
          '<span class="pill '+(i.evening_done?'g':'r')+'">🌙 PM '+(i.evening_done?'✓':'•')+'</span>'+
        '</div>';
      c.appendChild(card);
    });
    document.getElementById('upd').textContent = 'Updated ' + new Date().toLocaleTimeString();
  }catch(e){
    document.getElementById('cards').innerHTML = '<div class="loading">Connection lost — retrying…</div>';
  }
}
load();
setInterval(load, 20000);
</script>
"""


_PERIOD_LABEL = {
    "morning": "Morning (6–10 am)",
    "evening": "Evening (4–10 pm)",
    "manual":  "Manual entry",
}

# Checklist items: (key, label, default_checked)
_CHECKLIST = [
    ("heat_pumps_ok",      "Heat pumps on & working",  True),
    ("fans_ok",            "Fans on & working",        True),
    ("black_lights_ok",    "All black lights working", True),
    ("bees_emerging",      "Bees emerging",            False),
    ("parasites_emerging", "Parasites emerging",       False),
]


def _pill_html(label: str, icon: str, done: bool) -> str:
    cls = "g" if done else "r"
    sym = "✓" if done else "•"
    return f'<span class="pill {cls}">{icon} {label} {sym}</span>'


def _inspection_record_html(r: dict, actions: bool = False) -> str:
    """Compact card for one completed inspection."""
    from datetime import datetime
    period = r.get("period") or "manual"
    icon   = {"morning": "🌅", "evening": "🌙"}.get(period, "📝")
    when   = r.get("timestamp") or ""
    try:
        when = datetime.fromisoformat(when).strftime("%b %d  %I:%M %p")
    except Exception:
        when = when[:16]

    # Temperature line
    thermo = r.get("thermometer_temp_c")
    govee  = r.get("govee_temp_c")
    if thermo is not None:
        temp_col = "#EF4444" if r.get("temp_alert") else "#F3F4F6"
        gtxt = f"  (Govee {govee:.1f})" if govee is not None else ""
        temp_html = (f'<div class="meta" style="color:{temp_col}">'
                     f'🌡 Thermo {thermo:.1f}°C{gtxt}</div>')
    else:
        temp_html = ''

    # Issue flags
    flags = []
    if not r.get("heat_pumps_ok"):   flags.append("⚠ Heat pumps")
    if not r.get("fans_ok"):         flags.append("⚠ Fans")
    if not r.get("black_lights_ok"): flags.append("⚠ Black lights")
    if r.get("bees_emerging"):       flags.append("🐝 Bees emerging")
    if r.get("parasites_emerging"):  flags.append("⚠ Parasites")
    if r.get("temp_alert"):          flags.append("🌡 Temp alert")
    flag_html = ''
    if flags:
        chips = "".join(
            f'<span style="display:inline-block;background:#7F1D1D;color:#FEE2E2;'
            f'border-radius:6px;padding:2px 8px;font-size:.75rem;margin:3px 4px 0 0">{f}</span>'
            for f in flags)
        flag_html = f'<div style="margin-top:6px">{chips}</div>'

    notes = (r.get("notes") or "").strip()
    notes_html = (f'<div class="meta" style="margin-top:6px;color:#CBD5E1">“{notes}”</div>'
                  if notes else '')

    actions_html = ''
    if actions:
        iid  = r.get("incubator_id")
        rid  = r.get("id")
        actions_html = (
            '<div style="display:flex;gap:8px;margin-top:10px">'
            f'<a class="ibtn" style="flex:1;margin-top:0;padding:9px" '
            f'href="/m/inspect/{iid}/edit/{rid}">✎ Edit</a>'
            f'<form method="POST" action="/m/inspection/{rid}/delete" style="flex:1" '
            f'onsubmit="return confirm(\'Delete this inspection?\')">'
            f'<button type="submit" style="width:100%;background:#7F1D1D;color:#fff;'
            f'border:none;border-radius:10px;padding:9px;font-weight:700">🗑 Delete</button>'
            '</form>'
            '</div>'
        )

    return (
        '<div class="card" style="padding:12px">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
        f'<span style="font-weight:700;color:#F3F4F6">{icon} {r.get("incubator_name") or "—"}</span>'
        f'<span class="meta">{when}</span></div>'
        + temp_html + flag_html + notes_html + actions_html +
        '</div>'
    )


def _inspections_list_body(saved_name: str = None) -> str:
    """Inspections home: one card per incubator linking to its own history."""
    import inspection_db as idb
    parts = ['<div class="topbar"><h1>🔍 Inspections</h1></div><div class="wrap">']
    if saved_name:
        parts.append(f'<div class="banner">✓ Inspection saved for {saved_name}</div>')

    for inc in db.get_incubators():
        st = idb.get_inspection_status(inc["id"])
        am = st.get("morning") == "done"
        pm = st.get("evening") == "done"
        count = len(idb.get_inspections(incubator_id=inc["id"]))
        parts.append(
            f'<a href="/m/inspections/{inc["id"]}" style="text-decoration:none;color:inherit">'
            '<div class="card">'
            f'<div class="cn">{inc["name"]}</div>'
            '<div class="bp">'
            + _pill_html("AM", "🌅", am) + _pill_html("PM", "🌙", pm) +
            '</div>'
            f'<div class="meta" style="margin-top:10px">📋 {count} report(s) — tap to view ›</div>'
            '</div></a>'
        )
    parts.append('</div>')
    return "".join(parts)


def _incubator_inspections_body(inc_id: int, saved: str = None) -> str:
    """Per-incubator page: status, record button, and that incubator's reports."""
    import inspection_db as idb
    inc = next((i for i in db.get_incubators(include_hidden=True)
                if i["id"] == inc_id), None)
    if not inc:
        return ('<div class="topbar"><h1>Inspections</h1></div>'
                '<div class="wrap"><div class="card"><div class="soon">'
                'Incubator not found.</div></div></div>')

    st = idb.get_inspection_status(inc_id)
    am = st.get("morning") == "done"
    pm = st.get("evening") == "done"

    parts = [
        '<div class="topbar">'
        f'<h1>🔍 {inc["name"]}</h1>'
        '<a href="/m/inspections" style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Back</a>'
        '</div><div class="wrap">'
    ]
    if saved:
        parts.append(f'<div class="banner">✓ Saved</div>')

    parts.append(
        '<div class="card">'
        '<div class="bp">'
        + _pill_html("AM", "🌅", am) + _pill_html("PM", "🌙", pm) +
        '</div>'
        f'<a class="ibtn" href="/m/inspect/{inc_id}">+ Record inspection</a>'
        '</div>'
    )

    reports = idb.get_inspections(incubator_id=inc_id)
    parts.append('<div class="ml" style="margin:18px 4px 8px">Reports</div>')
    if reports:
        for r in reports:
            parts.append(_inspection_record_html(r, actions=True))
    else:
        parts.append('<div class="card"><div class="soon">'
                     'No inspections recorded yet.</div></div>')
    parts.append('</div>')
    return "".join(parts)


def _inspection_form_body(inc: dict, govee_temp, period: str,
                          existing: dict = None) -> str:
    from datetime import datetime
    is_edit = existing is not None
    per_label = _PERIOD_LABEL.get(period, "Manual entry")

    if is_edit:
        action    = f'/m/inspect/{inc["id"]}/edit/{existing["id"]}'
        title     = "✎ Edit Inspection"
        ts        = existing.get("timestamp") or ""
        try:
            when_str = datetime.fromisoformat(ts).strftime("%a %b %d  ·  %I:%M %p")
        except Exception:
            when_str = ts[:16]
        thermo_val = existing.get("thermometer_temp_c")
        thermo_val = "" if thermo_val is None else f"{thermo_val}"
        notes_val  = (existing.get("notes") or "")
        btn_txt    = "💾  Update Inspection"
    else:
        action     = f'/m/inspect/{inc["id"]}'
        title      = "🔍 Inspect"
        when_str   = datetime.now().strftime("%a %b %d  ·  %I:%M %p")
        thermo_val = ""
        notes_val  = ""
        btn_txt    = "💾  Save Inspection"

    govee_txt = (f"Govee reading: {govee_temp:.1f} °C" if govee_temp is not None
                 else "Govee reading: none available")

    checks = []
    for key, label, default in _CHECKLIST:
        if is_edit:
            checked = "checked" if existing.get(key) else ""
        else:
            checked = "checked" if default else ""
        checks.append(
            f'<label class="chk"><span>{label}</span>'
            f'<input type="checkbox" name="{key}" {checked}></label>'
        )

    return (
        f'<div class="topbar"><h1>{title}</h1></div><div class="wrap">'
        '<div class="card">'
        f'<div class="cn">{inc["name"]}</div>'
        f'<div class="meta">{when_str}</div>'
        f'<div class="period">● {per_label}</div>'
        f'<div class="gv">{govee_txt}</div>'
        '</div>'
        f'<form method="POST" action="{action}">'
        '<div class="card">'
        '<div class="fld"><label>Thermometer reading (°C)</label>'
        f'<input type="number" step="0.1" name="thermometer_temp_c" '
        f'inputmode="decimal" placeholder="e.g. 27.5" value="{thermo_val}"></div>'
        '</div>'
        '<div class="card">'
        '<div class="ml" style="margin-bottom:8px">Checklist</div>'
        + "".join(checks) +
        '</div>'
        '<div class="card">'
        '<div class="fld"><label>Notes</label>'
        f'<textarea name="notes" placeholder="Optional notes…">{notes_val}</textarea></div>'
        '</div>'
        f'<button class="savebtn" type="submit">{btn_txt}</button>'
        '</form>'
        '</div>'
    )


def _save_mobile_inspection(inc_id: int, form) -> Optional[str]:
    """Save an inspection from mobile form data. Returns incubator name on success."""
    import inspection_db as idb
    inc = next((i for i in db.get_incubators(include_hidden=True)
                if i["id"] == inc_id), None)
    if not inc:
        return None

    row        = db.get_latest_reading(inc_id)
    govee_temp = row["temperature_c"] if row else None

    thermo_c = None
    raw = (form.get("thermometer_temp_c") or "").strip()
    if raw:
        try:
            thermo_c = float(raw)
        except ValueError:
            thermo_c = None

    temp_diff = temp_alert = None
    if thermo_c is not None and govee_temp is not None:
        temp_diff  = abs(thermo_c - govee_temp)
        temp_alert = temp_diff > idb.TEMP_ALERT_THRESHOLD

    data = {
        "incubator_id":       inc_id,
        "period":             idb.get_current_period(),
        "thermometer_temp_c": thermo_c,
        "govee_temp_c":       govee_temp,
        "temp_diff_c":        temp_diff,
        "temp_alert":         bool(temp_alert),
        "notes":              (form.get("notes") or "").strip(),
    }
    # Checkboxes only appear in the form when checked
    for key, _label, _default in _CHECKLIST:
        data[key] = key in form

    idb.save_inspection(data)

    if temp_alert:
        try:
            db.add_alert(
                "inspection_temp",
                (f"Inspection temp alert — {inc['name']}: "
                 f"Thermometer {thermo_c:.1f}°C vs Govee {govee_temp:.1f}°C "
                 f"(Δ {temp_diff:.1f}°C)"),
                severity="warning", incubator_id=inc_id,
            )
        except Exception:
            pass
    return inc["name"]


def _update_mobile_inspection(insp_id: int, form) -> Optional[int]:
    """Update an existing inspection from mobile form data. Returns incubator_id."""
    import inspection_db as idb
    existing = idb.get_inspection_by_id(insp_id)
    if not existing:
        return None

    govee_temp = existing.get("govee_temp_c")
    thermo_c = None
    raw = (form.get("thermometer_temp_c") or "").strip()
    if raw:
        try:
            thermo_c = float(raw)
        except ValueError:
            thermo_c = None

    temp_diff = temp_alert = None
    if thermo_c is not None and govee_temp is not None:
        temp_diff  = abs(thermo_c - govee_temp)
        temp_alert = temp_diff > idb.TEMP_ALERT_THRESHOLD

    data = {
        "thermometer_temp_c": thermo_c,
        "govee_temp_c":       govee_temp,
        "temp_diff_c":        temp_diff,
        "temp_alert":         bool(temp_alert),
        "notes":              (form.get("notes") or "").strip(),
    }
    for key, _label, _default in _CHECKLIST:
        data[key] = key in form

    idb.update_inspection(insp_id, data)
    return existing.get("incubator_id")


# ── Flask app ─────────────────────────────────────────────────────────────────

_flask_app: Optional[object] = None
_on_update: Optional[Callable] = None
_running = False


def _make_flask_app():
    app = Flask(__name__)
    app.logger.disabled = True

    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    @app.route("/tray/<int:tray_id>")
    def tray_page(tray_id):
        tray = db.get_tray_by_id(tray_id)
        if not tray:
            return "<h2 style='color:red;font-family:sans-serif;padding:20px'>Tray not found</h2>", 404
        return render_template_string(_TRAY_HTML, tray=tray)

    @app.route("/tray/<int:tray_id>/update", methods=["POST"])
    def tray_update(tray_id):
        data = request.get_json(silent=True) or {}
        tray = db.get_tray_by_id(tray_id)
        if not tray:
            return jsonify({"error": "not found"}), 404
        for field, cast in [
            ("weight_lbs",       float),
            ("volume_gal",       float),
            ("parasite_level_pct", float),
            ("live_count",       int),
            ("status",           str),
            ("notes",            str),
        ]:
            if field in data and data[field] != "":
                try:
                    tray[field] = cast(data[field])
                except (ValueError, TypeError):
                    pass
        db.upsert_tray(tray)
        if _on_update:
            _on_update(tray_id)
        return jsonify({"ok": True})

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # ── Mobile web app (PWA) ──────────────────────────────────────────────────
    @app.route("/")
    def mobile_home():
        return _mobile_page("Bee Incubators", _DASHBOARD_BODY, active="home")

    @app.route("/api/dashboard")
    def mobile_dashboard_data():
        return jsonify(_dashboard_data())

    @app.route("/m/inspections")
    def mobile_inspections():
        saved = request.args.get("saved")
        return _mobile_page("Inspections",
                            _inspections_list_body(saved_name=saved),
                            active="inspect")

    @app.route("/m/inspections/<int:inc_id>")
    def mobile_incubator_inspections(inc_id):
        saved = request.args.get("saved")
        return _mobile_page("Inspections",
                            _incubator_inspections_body(inc_id, saved=saved),
                            active="inspect")

    @app.route("/m/inspect/<int:inc_id>", methods=["GET"])
    def mobile_inspect_form(inc_id):
        import inspection_db as idb
        inc = next((i for i in db.get_incubators(include_hidden=True)
                    if i["id"] == inc_id), None)
        if not inc:
            return "<h2 style='color:red;padding:20px;font-family:sans-serif'>Incubator not found</h2>", 404
        row    = db.get_latest_reading(inc_id)
        govee  = row["temperature_c"] if row else None
        period = idb.get_current_period()
        return _mobile_page(f"Inspect {inc['name']}",
                            _inspection_form_body(inc, govee, period),
                            active="inspect")

    @app.route("/m/inspect/<int:inc_id>", methods=["POST"])
    def mobile_inspect_save(inc_id):
        from flask import redirect
        _save_mobile_inspection(inc_id, request.form)
        if _on_update:
            _on_update(None)
        return redirect(f"/m/inspections/{inc_id}?saved=1")

    @app.route("/m/inspect/<int:inc_id>/edit/<int:insp_id>", methods=["GET"])
    def mobile_inspect_edit_form(inc_id, insp_id):
        import inspection_db as idb
        inc = next((i for i in db.get_incubators(include_hidden=True)
                    if i["id"] == inc_id), None)
        existing = idb.get_inspection_by_id(insp_id)
        if not inc or not existing:
            return "<h2 style='color:red;padding:20px;font-family:sans-serif'>Not found</h2>", 404
        return _mobile_page(f"Edit — {inc['name']}",
                            _inspection_form_body(
                                inc, existing.get("govee_temp_c"),
                                existing.get("period", "manual"),
                                existing=existing),
                            active="inspect")

    @app.route("/m/inspect/<int:inc_id>/edit/<int:insp_id>", methods=["POST"])
    def mobile_inspect_edit_save(inc_id, insp_id):
        from flask import redirect
        _update_mobile_inspection(insp_id, request.form)
        if _on_update:
            _on_update(None)
        return redirect(f"/m/inspections/{inc_id}?saved=1")

    @app.route("/m/inspection/<int:insp_id>/delete", methods=["POST"])
    def mobile_inspection_delete(insp_id):
        from flask import redirect
        import inspection_db as idb
        existing = idb.get_inspection_by_id(insp_id)
        inc_id = existing.get("incubator_id") if existing else None
        if existing:
            idb.delete_inspection(insp_id)
        if _on_update:
            _on_update(None)
        return redirect(f"/m/inspections/{inc_id}" if inc_id else "/m/inspections")

    @app.route("/m/trays")
    def mobile_trays():
        body = ('<div class="topbar"><h1>📦 Trays</h1></div>'
                '<div class="wrap"><div class="card">'
                '<div class="soon">Coming in the next update.</div>'
                '</div></div>')
        return _mobile_page("Trays", body, active="trays")

    # ── VOC / ESP32 endpoints ─────────────────────────────────────────────────
    # ESP32 posts here every N minutes:
    #   POST /reading
    #   Body: { "incubator_id": 1, "position": "front",
    #            "voc_ppm": 0.42, "temp_c": 27.3, "timestamp": "..." }

    try:
        import voc_db as _vdb
        _voc_available = True
    except ImportError:
        _voc_available = False

    @app.route("/reading", methods=["POST"])
    def esp32_reading():
        if not _voc_available:
            return jsonify({"error": "voc_db not available"}), 503
        data = request.get_json(silent=True) or {}
        inc_id   = data.get("incubator_id")
        position = data.get("position", "front")
        voc_ppm  = data.get("voc_ppm")
        temp_c   = data.get("temp_c")
        if inc_id is None or voc_ppm is None:
            return jsonify({"error": "incubator_id and voc_ppm required"}), 400
        try:
            inc_id  = int(inc_id)
            voc_ppm = float(voc_ppm)
            temp_c  = float(temp_c) if temp_c is not None else None
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

        run = _vdb.get_active_run(inc_id)
        run_id = run["id"] if run else None
        _vdb.save_reading(inc_id, run_id, position, voc_ppm, temp_c)

        # Check thresholds and log alert if out of range
        if run:
            snap = _vdb.run_snapshot(run)
            zone_key, zone_lbl, _ = _vdb.get_zone(voc_ppm, snap)
            if zone_key not in ("ok", "no_data"):
                msg = (f"{zone_lbl}: {position} sensor "
                       f"{voc_ppm:.3f} ppm in {run['chemical_name']}")
                _vdb.log_alert_event(inc_id, run_id, position, voc_ppm, zone_key, msg)

        if _on_update:
            _on_update(None)
        return jsonify({"ok": True, "run_id": run_id})

    @app.route("/api/readings")
    def api_readings():
        if not _voc_available:
            return jsonify({"error": "voc_db not available"}), 503
        inc_id = request.args.get("incubator_id", type=int)
        run_id = request.args.get("run_id",       type=int)
        hours  = request.args.get("hours",         type=int, default=24)
        if not inc_id:
            return jsonify({"error": "incubator_id required"}), 400
        if run_id:
            rows = _vdb.get_run_readings(run_id)
        else:
            rows = _vdb.get_recent_readings(inc_id, hours)
        return jsonify(rows)

    @app.route("/api/status")
    def api_status():
        if not _voc_available:
            return jsonify({"error": "voc_db not available"}), 503
        inc_id = request.args.get("incubator_id", type=int)
        if not inc_id:
            return jsonify({"error": "incubator_id required"}), 400
        latest = _vdb.get_latest_readings(inc_id)
        run    = _vdb.get_active_run(inc_id)
        snap   = _vdb.run_snapshot(run) if run else {}
        result = {"incubator_id": inc_id, "run": run}
        for pos, row in latest.items():
            if row and row.get("voc_ppm") is not None:
                ppm = row["voc_ppm"]
                zone_key, zone_lbl, _ = _vdb.get_zone(ppm, snap)
                result[pos] = {
                    "voc_ppm":   ppm,
                    "temp_c":    row.get("temp_c"),
                    "timestamp": row.get("timestamp"),
                    "zone":      zone_key,
                    "zone_label": zone_lbl,
                }
            else:
                result[pos] = None
        if (latest.get("front") and latest["front"].get("voc_ppm") is not None and
                latest.get("back") and latest["back"].get("voc_ppm") is not None):
            result["delta_ppm"] = abs(
                latest["front"]["voc_ppm"] - latest["back"]["voc_ppm"])
        return jsonify(result)

    return app


# ── Public start/stop ─────────────────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None


def start(port: int = 5151, on_update: Callable = None):
    """Start the QR server in a background daemon thread."""
    global _flask_app, _on_update, _running, _server_thread
    if not HAS_FLASK:
        print("[QRServer] Flask not installed — QR server disabled.")
        return
    if _running:
        return
    _on_update = on_update
    _flask_app = _make_flask_app()
    _running = True

    def run():
        _flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    _server_thread = threading.Thread(target=run, daemon=True, name="QRServer")
    _server_thread.start()
    print(f"[QRServer] Listening on {get_local_ip()}:{port}")


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def tray_url(tray_id: int, port: int = 5151) -> str:
    return f"http://{get_local_ip()}:{port}/tray/{tray_id}"


def available() -> bool:
    return HAS_FLASK
