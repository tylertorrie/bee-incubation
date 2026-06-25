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
        body = ('<div class="topbar"><h1>🔍 Inspections</h1></div>'
                '<div class="wrap"><div class="card">'
                '<div class="soon">Coming in the next update.</div>'
                '</div></div>')
        return _mobile_page("Inspections", body, active="inspect")

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
