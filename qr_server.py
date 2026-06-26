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
    from flask import (Flask, request, jsonify, render_template_string,
                       session, redirect)
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
.fld input[type=number],.fld input[type=text],.fld input[type=search],.fld textarea{
        width:100%;background:#374151;
        border:1px solid #4b5563;border-radius:8px;color:#F3F4F6;padding:12px;font-size:1.05rem}
.fld input:focus,.fld textarea:focus{outline:2px solid #FBBF24;border-color:#FBBF24}
.trow{display:flex;justify-content:space-between;align-items:center;background:#1F2937;
      border:1px solid #263347;border-radius:10px;padding:12px;margin-bottom:8px;
      text-decoration:none;color:inherit}
.trow .tn{font-weight:700;color:#F3F4F6}
.trow .ts{font-size:.8rem;color:#9CA3AF;margin-top:2px}
.trow .tg{color:#FBBF24;font-weight:700;white-space:nowrap;margin-left:10px}
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


def _dashboard_card_html(i: dict) -> str:
    iid = i["id"]
    return (
        '<div class="card">'
        f'<a href="/m/incubator/{iid}" style="text-decoration:none;color:inherit;display:block">'
        f'<div class="cn">{i["name"]}</div>'
        '<div class="metrics">'
        f'<div class="metric"><div class="ml">Temp</div>'
        f'<div class="mv" style="color:{i["temp_color"]}">{i["temp"]}</div></div>'
        f'<div class="metric"><div class="ml">Humidity</div>'
        f'<div class="mv">{i["humidity"]}</div></div>'
        '</div>'
        f'<div class="meta" style="color:{i["poll_color"]}">● Last polled: {i["last_polled"]}</div>'
        f'<div class="meta">{i["trays"]} / {i["capacity"]} trays</div>'
        '</a>'
        '<div class="pills">'
        + _pill_html("AM", "🌅", i["morning_done"], href=f"/m/inspect/{iid}")
        + _pill_html("PM", "🌙", i["evening_done"], href=f"/m/inspect/{iid}")
        + '</div></div>'
    )


def _dashboard_body() -> str:
    """Server-rendered dashboard (one round trip), then JS refreshes in place."""
    data  = _dashboard_data()
    cards = "".join(_dashboard_card_html(i) for i in data["incubators"]) \
            or '<div class="loading">No incubators.</div>'
    return (
        '<div class="topbar"><h1>🐝 Incubators</h1><span class="upd" id="upd"></span></div>'
        f'<div class="wrap"><div id="cards">{cards}</div></div>'
        '<script>'
        'async function load(){'
        ' try{'
        '  const r = await fetch("/api/dashboard", {cache:"no-store"});'
        '  const d = await r.json();'
        '  const c = document.getElementById("cards");'
        '  if(!d.incubators.length){ c.innerHTML = "<div class=\\"loading\\">No incubators.</div>"; return; }'
        '  c.innerHTML = "";'
        '  d.incubators.forEach(function(i){'
        '    const card = document.createElement("div"); card.className = "card";'
        '    card.innerHTML ='
        '      "<a href=\\"/m/incubator/"+i.id+"\\" style=\\"text-decoration:none;color:inherit;display:block\\">"+'
        '      "<div class=\\"cn\\">"+i.name+"</div>"+'
        '      "<div class=\\"metrics\\">"+'
        '        "<div class=\\"metric\\"><div class=\\"ml\\">Temp</div><div class=\\"mv\\" style=\\"color:"+i.temp_color+"\\">"+i.temp+"</div></div>"+'
        '        "<div class=\\"metric\\"><div class=\\"ml\\">Humidity</div><div class=\\"mv\\">"+i.humidity+"</div></div>"+'
        '      "</div>"+'
        '      "<div class=\\"meta\\" style=\\"color:"+i.poll_color+"\\">\\u25CF Last polled: "+i.last_polled+"</div>"+'
        '      "<div class=\\"meta\\">"+i.trays+" / "+i.capacity+" trays</div></a>"+'
        '      "<div class=\\"pills\\">"+'
        '        "<a class=\\"pill "+(i.morning_done?"g":"r")+"\\" href=\\"/m/inspect/"+i.id+"\\" style=\\"text-decoration:none\\">\\uD83C\\uDF05 AM "+(i.morning_done?"\\u2713":"\\u2022")+"</a>"+'
        '        "<a class=\\"pill "+(i.evening_done?"g":"r")+"\\" href=\\"/m/inspect/"+i.id+"\\" style=\\"text-decoration:none\\">\\uD83C\\uDF19 PM "+(i.evening_done?"\\u2713":"\\u2022")+"</a>"+'
        '      "</div>";'
        '    c.appendChild(card);'
        '  });'
        '  document.getElementById("upd").textContent = "Updated " + new Date().toLocaleTimeString();'
        ' }catch(e){}'
        '}'
        'setInterval(load, 20000);'
        '</script>'
    )


def _svg_chart(readings: list, unit: str, t_min, t_max) -> str:
    """Self-contained inline SVG temp+humidity chart (no JS library)."""
    from datetime import datetime
    W, H = 360, 210
    padL, padR, padT, padB = 6, 6, 10, 4
    plotW, plotH = W - padL - padR, H - padT - padB

    pts = []
    for r in readings:
        try:
            t = datetime.fromisoformat(r["timestamp"])
        except Exception:
            continue
        tc = r.get("temperature_c")
        if tc is not None and unit == "F":
            tc = tc * 9 / 5 + 32
        pts.append((t, tc, r.get("humidity_pct")))

    if len(pts) < 2:
        return ('<div class="meta" style="text-align:center;padding:30px">'
                'Not enough readings in this range yet.</div>')

    t0, t1 = pts[0][0].timestamp(), pts[-1][0].timestamp()
    span = (t1 - t0) or 1
    tvals = [p[1] for p in pts if p[1] is not None]
    hvals = [p[2] for p in pts if p[2] is not None]
    if not tvals:
        return ('<div class="meta" style="text-align:center;padding:30px">'
                'No temperature data in this range.</div>')

    tlo, thi = min(tvals), max(tvals)
    band_lo = band_hi = None
    if t_min is not None:
        lo, hi = (t_min, t_max)
        if unit == "F":
            lo, hi = lo * 9 / 5 + 32, hi * 9 / 5 + 32
        band_lo, band_hi = lo, hi
        tlo, thi = min(tlo, lo), max(thi, hi)
    if thi == tlo:
        thi = tlo + 1
    pad = (thi - tlo) * 0.12; tlo -= pad; thi += pad
    hlo = min(hvals) if hvals else 0
    hhi = max(hvals) if hvals else 100
    if hhi == hlo:
        hhi = hlo + 1
    hpad = (hhi - hlo) * 0.12; hlo -= hpad; hhi += hpad

    def X(t):  return padL + (t.timestamp() - t0) / span * plotW
    def Yt(v): return padT + plotH - (v - tlo) / (thi - tlo) * plotH
    def Yh(v): return padT + plotH - (v - hlo) / (hhi - hlo) * plotH

    band = ""
    if band_lo is not None:
        y1, y2 = Yt(band_hi), Yt(band_lo)
        band = (f'<rect x="{padL}" y="{y1:.1f}" width="{plotW}" '
                f'height="{max(0,y2-y1):.1f}" fill="#EF4444" opacity="0.10"/>')

    temp_pts = " ".join(f"{X(t):.1f},{Yt(v):.1f}" for t, v, _ in pts if v is not None)
    hum_pts  = " ".join(f"{X(t):.1f},{Yh(v):.1f}" for t, _, v in pts if v is not None)

    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="none" '
        f'style="background:#111827;border-radius:10px">'
        + band +
        f'<polyline points="{hum_pts}" fill="none" stroke="#60A5FA" '
        f'stroke-width="1.4" stroke-dasharray="3,3" opacity="0.85"/>'
        f'<polyline points="{temp_pts}" fill="none" stroke="#FFD700" stroke-width="2"/>'
        f'<text x="{padL+2}" y="{padT+10}" fill="#FFD700" font-size="11">{thi:.0f}°{unit}</text>'
        f'<text x="{padL+2}" y="{padT+plotH-2}" fill="#FFD700" font-size="11">{tlo:.0f}°{unit}</text>'
        f'<text x="{W-padR-2}" y="{padT+10}" fill="#60A5FA" font-size="11" text-anchor="end">{hhi:.0f}%</text>'
        f'<text x="{W-padR-2}" y="{padT+plotH-2}" fill="#60A5FA" font-size="11" text-anchor="end">{hlo:.0f}%</text>'
        '</svg>'
    )


def _incubator_detail_body(inc_id: int, hours: int) -> str:
    try:
        import inspection_db as idb
    except Exception:
        idb = None
    try:
        import incubation_calc as calc
    except Exception:
        calc = None

    inc = next((i for i in db.get_incubators(include_hidden=True)
                if i["id"] == inc_id), None)
    if not inc:
        return ('<div class="topbar"><h1>Incubator</h1></div><div class="wrap">'
                '<div class="card"><div class="soon">Not found.</div></div></div>')

    unit = db.get_setting("temp_unit", "C")
    row  = db.get_latest_reading(inc_id)
    temp_c = row["temperature_c"] if row else None
    hum    = row["humidity_pct"]  if row else None
    ts     = row["timestamp"]     if row else None
    t_min, t_max = (calc.get_temp_range(inc) if calc else (None, None))

    if temp_c is None:
        temp_str, temp_col = "—", "#F3F4F6"
    else:
        temp_str = calc.format_temp(temp_c, unit) if calc else f"{temp_c:.1f}°{unit}"
        temp_col = ("#22C55E" if (t_min is not None and t_min <= temp_c <= t_max)
                    else ("#EF4444" if t_min is not None else "#F3F4F6"))
    hum_str = f"{hum:.0f}%" if hum is not None else "—"
    poll_txt, poll_col = _mobile_poll_age(ts)

    readings = db.get_readings_hours(inc_id, hours)
    chart    = _svg_chart(readings, unit, t_min, t_max)

    ranges = [("1H", 1), ("6H", 6), ("24H", 24), ("7D", 24 * 7), ("30D", 24 * 30)]
    rbtns = "".join(
        f'<a href="/m/incubator/{inc_id}?h={h}" '
        f'style="flex:1;text-align:center;padding:8px 4px;border-radius:8px;'
        f'text-decoration:none;font-weight:700;font-size:.9rem;'
        f'background:{"#D97706" if h==hours else "#263347"};'
        f'color:{"#111" if h==hours else "#9CA3AF"}">{lbl}</a>'
        for lbl, h in ranges)

    stats = db.get_tray_stats(incubator_id=inc_id, status="active")
    am = pm = False
    if idb:
        st = idb.get_inspection_status(inc_id)
        am = st.get("morning") == "done"
        pm = st.get("evening") == "done"

    return (
        '<div class="topbar">'
        f'<h1>{inc["name"]}</h1>'
        '<a href="/" style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Home</a>'
        '</div><div class="wrap">'
        '<div class="card">'
        '<div class="metrics">'
        f'<div class="metric"><div class="ml">Temp</div>'
        f'<div class="mv" style="color:{temp_col}">{temp_str}</div></div>'
        f'<div class="metric"><div class="ml">Humidity</div>'
        f'<div class="mv">{hum_str}</div></div>'
        '</div>'
        f'<div class="meta" style="color:{poll_col}">● Last polled: {poll_txt}</div>'
        '<div class="bp">'
        + _pill_html("AM", "🌅", am, href=f"/m/inspect/{inc_id}")
        + _pill_html("PM", "🌙", pm, href=f"/m/inspect/{inc_id}") +
        '</div>'
        '</div>'
        '<div class="card">'
        f'<div style="display:flex;gap:8px;margin-bottom:10px">{rbtns}</div>'
        f'{chart}'
        '<div style="display:flex;justify-content:space-between;margin-top:6px">'
        '<span class="meta" style="color:#FFD700">— Temp</span>'
        '<span class="meta" style="color:#60A5FA">- - Humidity</span>'
        '</div>'
        '</div>'
        f'<a class="ibtn" href="/m/trays/{inc_id}">📦 {stats["count"]} active trays ·'
        f' {stats["total_gals"]:.1f} gal ›</a>'
        f'<a class="ibtn" style="background:#15803D" href="/m/inspections/{inc_id}">'
        '🔍 Inspections & reports ›</a>'
        f'<a class="ibtn" style="background:#1D4ED8" href="/m/inspect/{inc_id}">'
        '+ Record inspection</a>'
        '</div>'
    )


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


def _pill_html(label: str, icon: str, done: bool, href: str = None) -> str:
    cls = "g" if done else "r"
    sym = "✓" if done else "•"
    inner = f'{icon} {label} {sym}'
    if href:
        return f'<a class="pill {cls}" href="{href}" style="text-decoration:none">{inner}</a>'
    return f'<span class="pill {cls}">{inner}</span>'


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

    tray_link_html = actions_html = ''
    if actions:
        import inspection_db as idb
        iid  = r.get("incubator_id")
        rid  = r.get("id")
        n_ti = idb.count_tray_inspections(rid)
        tray_link_html = (
            f'<a class="ibtn" style="margin-top:10px;background:#7C3AED" '
            f'href="/m/inspection/{rid}">🐝 Tray inspections ({n_ti}) ›</a>'
        )
        actions_html = (
            '<div style="display:flex;gap:8px;margin-top:8px">'
            f'<a class="ibtn" style="flex:1;margin-top:0;padding:9px" '
            f'href="/m/inspect/{iid}/edit/{rid}">✎ Edit</a>'
            f'<form method="POST" action="/m/inspection/{rid}/delete" style="flex:1" '
            f'onsubmit="return confirm(\'Delete this inspection and its tray inspections?\')">'
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
        + temp_html + flag_html + notes_html + tray_link_html + actions_html +
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

    new_id = idb.save_inspection(data)

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
    return new_id


# ── Tray inspections (per-tray detail under an inspection) ─────────────────────

def _tray_insp_card(ti: dict, master_inc_id=None) -> str:
    """One saved tray-inspection card with edit/delete."""
    loc = " / ".join(x for x in (ti.get("stack_position"), ti.get("depth_position")) if x) or "—"
    cells = ti.get("cells_opened")
    cells_txt = f"{cells} cells opened" if cells is not None else ""
    stage = ti.get("dev_stage") or "—"
    notes = (ti.get("notes") or "").strip()
    notes_html = (f'<div class="meta" style="color:#CBD5E1;margin-top:4px">“{notes}”</div>'
                  if notes else "")
    return (
        '<div class="card" style="padding:12px">'
        f'<div style="font-weight:700;color:#FFD700">Tray {ti.get("tray_number") or "—"}</div>'
        f'<div class="meta" style="margin-top:4px">📍 {loc}</div>'
        f'<div class="meta">🧬 {stage}</div>'
        + (f'<div class="meta">🥚 {cells_txt}</div>' if cells_txt else "")
        + notes_html +
        '<div style="display:flex;gap:8px;margin-top:10px">'
        f'<a class="ibtn" style="flex:1;margin-top:0;padding:9px" '
        f'href="/m/tray-inspection/{ti["id"]}/edit">✎ Edit</a>'
        f'<form method="POST" action="/m/tray-inspection/{ti["id"]}/delete" style="flex:1" '
        f'onsubmit="return confirm(\'Delete this tray inspection?\')">'
        f'<button type="submit" style="width:100%;background:#7F1D1D;color:#fff;'
        f'border:none;border-radius:10px;padding:9px;font-weight:700">🗑 Delete</button>'
        '</form></div>'
        '</div>'
    )


def _inspection_report_body(insp_id: int, saved: bool = False) -> str:
    """Master inspection report: summary + its tray inspections + add actions."""
    import inspection_db as idb
    from datetime import datetime
    insp = idb.get_inspection_by_id(insp_id)
    if not insp:
        return ('<div class="topbar"><h1>Inspection</h1></div><div class="wrap">'
                '<div class="card"><div class="soon">Not found.</div></div></div>')
    inc = next((i for i in db.get_incubators(include_hidden=True)
                if i["id"] == insp["incubator_id"]), None)
    inc_name = inc["name"] if inc else "—"
    try:
        when = datetime.fromisoformat(insp["timestamp"]).strftime("%a %b %d  ·  %I:%M %p")
    except Exception:
        when = (insp.get("timestamp") or "")[:16]
    period = _PERIOD_LABEL.get(insp.get("period"), "Manual entry")

    tis = idb.get_tray_inspections(insp_id)
    parts = [
        '<div class="topbar">'
        f'<h1>🔍 {inc_name}</h1>'
        f'<a href="/m/inspections/{insp["incubator_id"]}" '
        'style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Back</a>'
        '</div><div class="wrap">'
    ]
    if saved:
        parts.append('<div class="banner">✓ Saved</div>')
    parts.append(
        '<div class="card">'
        f'<div class="meta">{when}</div>'
        f'<div class="period">● {period}</div>'
        '</div>'
        '<div class="ml" style="margin:16px 4px 8px">Tray inspections</div>'
        '<a class="ibtn" style="background:#7C3AED" '
        f'href="/m/scan?next=/m/inspection/{insp_id}/tray-form">📷 Scan a tray to inspect</a>'
        f'<form method="GET" action="/m/inspection/{insp_id}/tray-form" class="fld" '
        'style="margin-top:10px;margin-bottom:14px">'
        '<div style="display:flex;gap:8px">'
        '<input type="text" name="q" placeholder="or enter tray # (e.g. 123)" '
        'autocapitalize="off" autocorrect="off" spellcheck="false" autocomplete="off" '
        'style="flex:1">'
        '<button class="savebtn" style="width:auto;margin-top:0;padding:12px 18px" '
        'type="submit">Go</button></div></form>'
    )
    if tis:
        for ti in tis:
            parts.append(_tray_insp_card(ti))
    else:
        parts.append('<div class="card"><div class="soon">'
                     'No tray inspections yet. Scan or enter a tray above.</div></div>')
    parts.append('</div>')
    return "".join(parts)


def _tray_insp_form_body(insp_id: int, tray: dict, existing: dict = None) -> str:
    """Form to add/edit a tray inspection (location, cells, stage, notes)."""
    import inspection_db as idb
    is_edit = existing is not None
    action  = (f'/m/tray-inspection/{existing["id"]}/edit' if is_edit
               else f'/m/inspection/{insp_id}/tray-form?tray={tray["id"]}')
    title   = "✎ Edit Tray Inspection" if is_edit else "🐝 Tray Inspection"
    btn     = "💾  Update" if is_edit else "💾  Save tray inspection"

    cur = existing or {}
    def _sel(name, options, current):
        opts = '<option value="">—</option>'
        for o in options:
            s = " selected" if o == current else ""
            opts += f'<option value="{o}"{s}>{o}</option>'
        return (f'<select name="{name}" style="width:100%;background:#374151;'
                f'border:1px solid #4b5563;border-radius:8px;color:#F3F4F6;'
                f'padding:12px;font-size:1.05rem">{opts}</select>')

    cells_val = cur.get("cells_opened")
    cells_val = "" if cells_val is None else cells_val
    notes_val = cur.get("notes") or ""

    return (
        f'<div class="topbar"><h1>{title}</h1></div><div class="wrap">'
        '<div class="card">'
        f'<div class="cn">Tray {tray.get("tray_number") or "—"}</div>'
        f'<div class="meta">{tray.get("sample_name") or "—"} · '
        f'{tray.get("incubator_name") or "—"}</div>'
        '</div>'
        f'<form method="POST" action="{action}">'
        '<div class="card">'
        '<div class="fld"><label>Stack position</label>'
        + _sel("stack_position", idb.STACK_POSITIONS, cur.get("stack_position")) + '</div>'
        '<div class="fld"><label>Depth in unit</label>'
        + _sel("depth_position", idb.DEPTH_POSITIONS, cur.get("depth_position")) + '</div>'
        '</div>'
        '<div class="card">'
        '<div class="fld"><label>Developmental stage</label>'
        + _sel("dev_stage", idb.DEV_STAGES, cur.get("dev_stage")) + '</div>'
        '<div class="fld"><label>Bee cells opened (count)</label>'
        f'<input type="number" name="cells_opened" inputmode="numeric" '
        f'placeholder="e.g. 12" value="{cells_val}"></div>'
        '</div>'
        '<div class="card">'
        '<div class="fld"><label>Notes</label>'
        f'<textarea name="notes" placeholder="Optional notes…">{notes_val}</textarea></div>'
        '</div>'
        f'<button class="savebtn" type="submit">{btn}</button>'
        '</form>'
        '</div>'
    )


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


def _trays_home_body(notfound: str = None) -> str:
    """Trays home: search box + per-incubator active-tray summary cards."""
    parts = ['<div class="topbar"><h1>📦 Trays</h1></div><div class="wrap">']
    if notfound:
        parts.append('<div class="card"><div class="meta" style="color:#EF4444">'
                     f'No tray found for “{notfound}”.</div></div>')
    parts.append(
        '<a class="ibtn" style="background:#7C3AED;margin:0 0 14px;padding:14px;'
        'font-size:1.05rem" href="/m/scan">📷  Scan a tray QR code</a>'
    )
    parts.append(
        '<form method="GET" action="/m/tray-lookup" class="fld" style="margin-bottom:16px">'
        '<label>Or find a tray by number</label>'
        '<div style="display:flex;gap:8px">'
        '<input type="text" name="q" placeholder="e.g. 123 or Tray0123" '
        'autocapitalize="off" autocorrect="off" spellcheck="false" '
        'autocomplete="off" style="flex:1">'
        '<button class="savebtn" style="width:auto;margin-top:0;padding:12px 18px" '
        'type="submit">Go</button>'
        '</div></form>'
    )
    for inc in db.get_incubators():
        st = db.get_tray_stats(incubator_id=inc["id"], status="active")
        parts.append(
            f'<a href="/m/trays/{inc["id"]}" style="text-decoration:none;color:inherit">'
            '<div class="card">'
            f'<div class="cn">{inc["name"]}</div>'
            f'<div class="meta" style="margin-top:6px">📦 {st["count"]} active trays '
            f'· {st["total_gals"]:.1f} gal — tap to view ›</div>'
            '</div></a>'
        )
    parts.append('</div>')
    return "".join(parts)


_SCAN_TEMPLATE = """
<div class="topbar"><h1>📷 Scan Tray</h1>
<a href="/m/trays" style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Back</a></div>
<div class="wrap">
  <div id="fallback" class="card" style="display:none">
    <div class="meta" style="color:#FBBF24;font-size:.95rem;line-height:1.5">
      📷 In-app scanning needs a secure (HTTPS) connection, which isn't set up yet.
      <br><br>
      For now you can:<br>
      • Use your phone's <b>built-in camera app</b> to scan the tray's QR code — it opens the tray directly.<br>
      • Or go back and <b>search by tray number</b>.
    </div>
    <a class="ibtn" href="/m/trays">‹ Back to Trays</a>
  </div>
  <div id="scanwrap" style="display:none">
    <div class="card" style="padding:10px">
      <div id="reader" style="width:100%"></div>
    </div>
    <div class="meta" id="scanmsg" style="text-align:center">Point the camera at a tray QR code…</div>
  </div>
</div>
<script>
(function(){
  var NEXT = __NEXT__;
  if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    document.getElementById('fallback').style.display = 'block';
    return;
  }
  document.getElementById('scanwrap').style.display = 'block';
  var s = document.createElement('script');
  s.src = 'https://unpkg.com/html5-qrcode';
  s.onload = function(){
    var qr = new Html5Qrcode("reader");
    var done = false;
    function onScan(text){
      if (done) return;
      done = true;
      document.getElementById('scanmsg').textContent = 'Found code — opening…';
      var dest;
      var m = text.match(/\\/tray\\/(\\d+)/);
      if (NEXT){
        var sep = NEXT.indexOf('?') >= 0 ? '&' : '?';
        dest = m ? (NEXT + sep + 'tray=' + m[1])
                 : (NEXT + sep + 'q=' + encodeURIComponent(text.trim()));
      } else {
        dest = m ? ('/tray/' + m[1])
                 : ('/m/tray-lookup?q=' + encodeURIComponent(text.trim()));
      }
      qr.stop().then(function(){ window.location = dest; })
                .catch(function(){ window.location = dest; });
    }
    qr.start({facingMode:"environment"}, {fps:10, qrbox:240}, onScan, function(){})
      .catch(function(e){
        document.getElementById('scanmsg').textContent = 'Could not start camera: ' + e;
      });
  };
  s.onerror = function(){
    document.getElementById('scanmsg').textContent =
      'Could not load the scanner (no internet?). Use your phone camera app or search instead.';
  };
  document.body.appendChild(s);
})();
</script>
"""


def _scan_body(nxt: str = "") -> str:
    import json
    if not (nxt and nxt.startswith("/")):
        nxt = ""
    return _SCAN_TEMPLATE.replace("__NEXT__", json.dumps(nxt))


def _tray_results_body(query: str, matches: list) -> str:
    """List of trays matching a search (when more than one matches)."""
    parts = ['<div class="topbar"><h1>📦 Search</h1>'
             '<a href="/m/trays" style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Back</a>'
             '</div><div class="wrap">'
             f'<div class="meta" style="margin:0 4px 10px">{len(matches)} matches for “{query}”</div>']
    for t in matches:
        tn  = t.get("tray_number") or "—"
        sm  = t.get("sample_name") or "—"
        inc = t.get("incubator_name") or "—"
        stt = t.get("status") or "active"
        parts.append(
            f'<a class="trow" href="/tray/{t["id"]}">'
            f'<div><div class="tn">{tn}</div><div class="ts">{sm} · {inc}</div></div>'
            f'<div class="tg" style="color:#9CA3AF">{stt}</div></a>'
        )
    parts.append('</div>')
    return "".join(parts)


def _incubator_trays_body(inc_id: int) -> str:
    """Active trays in one incubator, with a live filter box."""
    inc = next((i for i in db.get_incubators(include_hidden=True)
                if i["id"] == inc_id), None)
    if not inc:
        return ('<div class="topbar"><h1>Trays</h1></div><div class="wrap">'
                '<div class="card"><div class="soon">Incubator not found.</div>'
                '</div></div>')

    trays = db.get_trays(incubator_id=inc_id, status="active")
    parts = [
        '<div class="topbar">'
        f'<h1>📦 {inc["name"]}</h1>'
        '<a href="/m/trays" style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Back</a>'
        '</div><div class="wrap">'
        '<div class="fld" style="margin-bottom:12px">'
        '<input type="search" id="q" placeholder="Filter by tray # or sample…" '
        'autocapitalize="off" autocorrect="off" spellcheck="false" '
        'autocomplete="off" oninput="filt()"></div>'
        f'<div class="meta" id="cnt" style="margin:0 4px 10px">{len(trays)} active trays</div>'
        '<div id="list">'
    ]
    if trays:
        for t in trays:
            tn  = t.get("tray_number") or "—"
            sm  = t.get("sample_name") or "—"
            vol = t.get("volume_gal")
            vtxt = f"{vol:.1f} gal" if vol is not None else "—"
            key = (str(tn) + " " + str(sm)).lower().replace('"', "")
            parts.append(
                f'<a class="trow" href="/tray/{t["id"]}" data-s="{key}">'
                f'<div><div class="tn">{tn}</div><div class="ts">{sm}</div></div>'
                f'<div class="tg">{vtxt}</div></a>'
            )
    else:
        parts.append('<div class="card"><div class="soon">No active trays.</div></div>')
    parts.append('</div></div>')
    parts.append(
        '<script>'
        'function filt(){'
        'var q=document.getElementById("q").value.toLowerCase();'
        'var n=0;'
        'document.querySelectorAll(".trow").forEach(function(e){'
        'var m=e.dataset.s.indexOf(q)>=0;e.style.display=m?"":"none";if(m)n++;});'
        'document.getElementById("cnt").textContent=n+" active trays";'
        '}'
        '</script>'
    )
    return "".join(parts)


# ── Auth (shared passcode) ────────────────────────────────────────────────────

# Paths reachable without logging in (login page, health, machine/ESP32 endpoints)
_AUTH_EXEMPT = ("/m/login", "/health", "/reading", "/api/readings", "/api/status")

# Simple in-memory brute-force throttle: ip -> [fail_count, locked_until_ts]
_auth_fails: dict = {}


def _passcode() -> str:
    """The shared mobile passcode, or '' if auth is disabled."""
    return (db.get_setting("mobile_passcode", "") or "").strip()


def _flask_secret() -> str:
    """Persistent secret for signing session cookies (so logins survive restarts)."""
    sec = db.get_setting("flask_secret", "")
    if not sec:
        import secrets
        sec = secrets.token_hex(32)
        db.set_setting("flask_secret", sec)
    return sec


def _login_body(error: str = "") -> str:
    err_html = (f'<div class="meta" style="color:#EF4444;margin-bottom:10px">{error}</div>'
                if error else '')
    return (
        '<div class="topbar"><h1>🐝 Bee Incubation</h1></div>'
        '<div class="wrap" style="padding-top:40px">'
        '<div class="card">'
        '<div class="cn" style="margin-bottom:12px">Enter passcode</div>'
        + err_html +
        '<form method="POST" action="/m/login">'
        '<div class="fld">'
        '<input type="password" name="passcode" placeholder="Passcode" '
        'autocomplete="current-password" autofocus></div>'
        '<button class="savebtn" type="submit">Unlock</button>'
        '</form>'
        '</div></div>'
    )


# ── Flask app ─────────────────────────────────────────────────────────────────

_flask_app: Optional[object] = None
_on_update: Optional[Callable] = None
_running = False


def _make_flask_app():
    from datetime import timedelta
    app = Flask(__name__)
    app.logger.disabled = True
    app.secret_key = _flask_secret()
    app.permanent_session_lifetime = timedelta(days=30)

    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    # ── gzip responses (big win for large pages over the Funnel relay) ─────────
    @app.after_request
    def _gzip(resp):
        try:
            if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
                return resp
            ct = resp.content_type or ""
            if not (ct.startswith("text/") or "json" in ct or "javascript" in ct):
                return resp
            if resp.direct_passthrough:
                return resp
            data = resp.get_data()
            if len(data) < 600:
                return resp
            import gzip as _gz
            comp = _gz.compress(data, 6)
            resp.set_data(comp)
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"]   = str(len(comp))
            resp.headers["Vary"]             = "Accept-Encoding"
        except Exception:
            pass
        return resp

    # ── Auth gate (active only when a passcode is set) ─────────────────────────
    @app.before_request
    def _require_passcode():
        code = _passcode()
        if not code:
            return  # auth disabled — open on LAN as before
        p = request.path or "/"
        if p == "/m/login" or any(p == e or p.startswith(e) for e in _AUTH_EXEMPT):
            return
        if session.get("authed"):
            return
        if p.startswith("/api/"):
            return jsonify({"error": "auth required"}), 401
        return redirect("/m/login")

    @app.route("/m/login", methods=["GET", "POST"])
    def mobile_login():
        import time
        ip   = request.remote_addr or "?"
        now  = time.time()
        fails, locked_until = _auth_fails.get(ip, [0, 0])

        if request.method == "POST":
            if now < locked_until:
                return _mobile_page("Login",
                    _login_body("Too many attempts — wait a minute and try again."),
                    active="home")
            entered = (request.form.get("passcode") or "").strip()
            if entered and entered == _passcode():
                session.permanent = True
                session["authed"] = True
                _auth_fails.pop(ip, None)
                return redirect("/")
            fails += 1
            locked_until = now + 60 if fails >= 5 else 0
            _auth_fails[ip] = [fails, locked_until]
            return _mobile_page("Login",
                _login_body("Incorrect passcode."), active="home")

        if session.get("authed"):
            return redirect("/")
        return _mobile_page("Login", _login_body(), active="home")

    @app.route("/m/logout")
    def mobile_logout():
        session.clear()
        return redirect("/m/login")

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
        return _mobile_page("Bee Incubators", _dashboard_body(), active="home")

    @app.route("/api/dashboard")
    def mobile_dashboard_data():
        return jsonify(_dashboard_data())

    @app.route("/m/incubator/<int:inc_id>")
    def mobile_incubator_detail(inc_id):
        try:
            hours = int(request.args.get("h", 24))
        except (TypeError, ValueError):
            hours = 24
        if hours not in (1, 6, 24, 24 * 7, 24 * 30):
            hours = 24
        return _mobile_page("Incubator",
                            _incubator_detail_body(inc_id, hours),
                            active="home")

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
        new_id = _save_mobile_inspection(inc_id, request.form)
        if _on_update:
            _on_update(None)
        if new_id:
            # Land on the report page so trays can be inspected under it
            return redirect(f"/m/inspection/{new_id}?saved=1")
        return redirect(f"/m/inspections/{inc_id}?saved=1")

    @app.route("/m/inspection/<int:insp_id>")
    def mobile_inspection_report(insp_id):
        saved = request.args.get("saved")
        return _mobile_page("Inspection",
                            _inspection_report_body(insp_id, saved=bool(saved)),
                            active="inspect")

    @app.route("/m/inspection/<int:insp_id>/tray-form", methods=["GET"])
    def mobile_tray_insp_form(insp_id):
        import inspection_db as idb
        if not idb.get_inspection_by_id(insp_id):
            return "<h2 style='color:red;padding:20px;font-family:sans-serif'>Inspection not found</h2>", 404
        # Resolve the tray from ?tray=<id> or ?q=<text>
        tid = request.args.get("tray", type=int)
        tray = None
        if tid:
            tray = db.get_tray_by_id(tid)
        else:
            q = (request.args.get("q") or "").strip()
            matches = db.find_trays(q) if q else []
            if len(matches) == 1:
                tray = matches[0]
            elif len(matches) > 1:
                # Let the user pick which tray, then come back here
                body = ['<div class="topbar"><h1>Pick a tray</h1>'
                        f'<a href="/m/inspection/{insp_id}" '
                        'style="color:#9CA3AF;text-decoration:none;font-size:.9rem">‹ Back</a>'
                        '</div><div class="wrap">'
                        f'<div class="meta" style="margin:0 4px 10px">{len(matches)} matches</div>']
                for m in matches:
                    body.append(
                        f'<a class="trow" href="/m/inspection/{insp_id}/tray-form?tray={m["id"]}">'
                        f'<div><div class="tn">{m.get("tray_number")}</div>'
                        f'<div class="ts">{m.get("sample_name") or "—"}</div></div></a>')
                body.append('</div>')
                return _mobile_page("Pick a tray", "".join(body), active="inspect")
        if not tray:
            return _mobile_page("Inspection",
                                _inspection_report_body(insp_id), active="inspect")
        return _mobile_page("Tray Inspection",
                            _tray_insp_form_body(insp_id, tray), active="inspect")

    @app.route("/m/inspection/<int:insp_id>/tray-form", methods=["POST"])
    def mobile_tray_insp_save(insp_id):
        from flask import redirect
        import inspection_db as idb
        insp = idb.get_inspection_by_id(insp_id)
        tid  = request.args.get("tray", type=int)
        tray = db.get_tray_by_id(tid) if tid else None
        if not insp or not tray:
            return redirect(f"/m/inspection/{insp_id}")
        cells = (request.form.get("cells_opened") or "").strip()
        try:
            cells = int(cells) if cells else None
        except ValueError:
            cells = None
        idb.add_tray_inspection({
            "inspection_id":  insp_id,
            "tray_id":        tray["id"],
            "tray_number":    tray.get("tray_number"),
            "incubator_id":   insp.get("incubator_id"),
            "stack_position": (request.form.get("stack_position") or "").strip() or None,
            "depth_position": (request.form.get("depth_position") or "").strip() or None,
            "cells_opened":   cells,
            "dev_stage":      (request.form.get("dev_stage") or "").strip() or None,
            "notes":          (request.form.get("notes") or "").strip(),
        })
        if _on_update:
            _on_update(None)
        return redirect(f"/m/inspection/{insp_id}?saved=1")

    @app.route("/m/tray-inspection/<int:ti_id>/edit", methods=["GET", "POST"])
    def mobile_tray_insp_edit(ti_id):
        from flask import redirect
        import inspection_db as idb
        ti = idb.get_tray_inspection_by_id(ti_id)
        if not ti:
            return redirect("/m/inspections")
        if request.method == "POST":
            cells = (request.form.get("cells_opened") or "").strip()
            try:
                cells = int(cells) if cells else None
            except ValueError:
                cells = None
            idb.update_tray_inspection(ti_id, {
                "stack_position": (request.form.get("stack_position") or "").strip() or None,
                "depth_position": (request.form.get("depth_position") or "").strip() or None,
                "cells_opened":   cells,
                "dev_stage":      (request.form.get("dev_stage") or "").strip() or None,
                "notes":          (request.form.get("notes") or "").strip(),
            })
            if _on_update:
                _on_update(None)
            return redirect(f"/m/inspection/{ti['inspection_id']}?saved=1")
        tray = db.get_tray_by_id(ti["tray_id"]) if ti.get("tray_id") else None
        if not tray:
            tray = {"tray_number": ti.get("tray_number"), "id": ti.get("tray_id")}
        return _mobile_page("Edit Tray Inspection",
                            _tray_insp_form_body(ti["inspection_id"], tray, existing=ti),
                            active="inspect")

    @app.route("/m/tray-inspection/<int:ti_id>/delete", methods=["POST"])
    def mobile_tray_insp_delete(ti_id):
        from flask import redirect
        import inspection_db as idb
        ti = idb.get_tray_inspection_by_id(ti_id)
        insp_id = ti["inspection_id"] if ti else None
        if ti:
            idb.delete_tray_inspection(ti_id)
        if _on_update:
            _on_update(None)
        return redirect(f"/m/inspection/{insp_id}" if insp_id else "/m/inspections")

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
        notfound = request.args.get("notfound")
        return _mobile_page("Trays", _trays_home_body(notfound=notfound),
                            active="trays")

    @app.route("/m/trays/<int:inc_id>")
    def mobile_incubator_trays(inc_id):
        return _mobile_page("Trays", _incubator_trays_body(inc_id),
                            active="trays")

    @app.route("/m/scan")
    def mobile_scan():
        nxt = (request.args.get("next") or "").strip()
        return _mobile_page("Scan Tray", _scan_body(nxt), active="trays")

    @app.route("/m/tray-lookup")
    def mobile_tray_lookup():
        from flask import redirect
        from urllib.parse import quote
        q = (request.args.get("q") or "").strip()
        if not q:
            return redirect("/m/trays")
        matches = db.find_trays(q)
        if len(matches) == 1:
            return redirect(f"/tray/{matches[0]['id']}")
        if len(matches) > 1:
            return _mobile_page("Search", _tray_results_body(q, matches), active="trays")
        return redirect(f"/m/trays?notfound={quote(q)}")

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
