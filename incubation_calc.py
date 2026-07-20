"""
incubation_calc.py  —  Weight calculations, date projections, threshold checks
"""
from datetime import datetime
from typing import Optional


# ── Weight / Volume calculations ──────────────────────────────────────────────

def calc_raw_weight_per_tray(live_pct: float,
                              target_gals: float = 2.0,
                              lbs_per_gal: float = 2.2) -> float:
    """
    How many lbs of raw (ungraded) bee cells are needed to fill one tray
    with `target_gals` gallons of LIVE bees?

    live_pct   : fraction 0–1 (e.g. 0.82 for 82 % live on x-ray)
    target_gals: gallons of live bees desired per tray (default 2)
    lbs_per_gal: weight per gallon of raw cells (configurable, default 2.2 lbs/gal)
    """
    if live_pct <= 0:
        return 0.0
    raw_gals_needed = target_gals / live_pct
    return round(raw_gals_needed * lbs_per_gal, 3)


def calc_sample_summary(total_volume_gal: float,
                         live_pct: float,
                         target_gals_per_tray: float = 2.0,
                         lbs_per_gal: float = 2.2) -> dict:
    """
    Given a sample's total raw volume and live %, calculate:
      - live_gals_total     : total live-bee gallons in the sample
      - tray_count_exact    : how many trays the sample can fill (float)
      - tray_count          : floor of above
      - raw_gals_per_tray   : raw gallons to load per tray
      - raw_lbs_per_tray    : raw weight (lbs) to load per tray
    """
    if not total_volume_gal or not live_pct or live_pct <= 0:
        return {
            "live_gals_total": 0.0, "tray_count_exact": 0.0, "tray_count": 0,
            "raw_gals_per_tray": 0.0, "raw_lbs_per_tray": 0.0,
        }
    live_gals = total_volume_gal * live_pct
    tray_count_exact = live_gals / target_gals_per_tray
    tray_count = int(tray_count_exact)
    raw_gals_per_tray = target_gals_per_tray / live_pct
    return {
        "live_gals_total":   round(live_gals, 2),
        "tray_count_exact":  round(tray_count_exact, 2),
        "tray_count":        tray_count,
        "raw_gals_per_tray": round(raw_gals_per_tray, 3),
        "raw_lbs_per_tray":  round(raw_gals_per_tray * lbs_per_gal, 3),
    }


# ── Date helpers ──────────────────────────────────────────────────────────────

def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO or common date strings, return datetime or None."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(date_str[:10], fmt[:10])
        except ValueError:
            continue
    return None


def days_from_now(date_str: Optional[str]) -> Optional[int]:
    """Return signed integer days from today (negative = past)."""
    d = parse_date(date_str)
    if d is None:
        return None
    return (d.date() - datetime.now().date()).days


def format_days(days: Optional[int]) -> str:
    """Human-readable string: 'Today', '3 days', 'Yesterday', '5 days ago'."""
    if days is None:
        return "—"
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    if days == -1:
        return "Yesterday"
    if days > 0:
        return f"in {days}d"
    return f"{abs(days)}d ago"


# ── Event extraction ──────────────────────────────────────────────────────────

BATCH_EVENT_FIELDS = [
    ("vapona_in",            "Vapona In"),
    ("vapona_out",           "Vapona Out"),
    ("air_out",              "Air Out"),
    ("male_10pct_emergence", "10% Male Emergence"),
    ("earliest_cool",        "Earliest Cool"),
    ("estimated_release",    "Est. Release"),
    ("latest_release",       "Latest Release"),
]


def get_upcoming_events(batch: dict, lookahead_days: int = 30) -> list:
    """
    Return events from a batch that are within `lookahead_days` days.
    Each item:
        {label, date, days_away, urgent, batch_name, batch_id, incubator_name}
    """
    events = []
    for field, label in BATCH_EVENT_FIELDS:
        val = batch.get(field)
        if not val:
            continue
        days = days_from_now(val)
        if days is not None and -1 <= days <= lookahead_days:
            events.append({
                "label":          label,
                "date":           val[:10],
                "days_away":      days,
                "urgent":         days <= 1,
                "batch_name":     batch.get("name") or "—",
                "batch_id":       batch.get("id"),
                "incubator_name": batch.get("incubator_name") or "—",
            })
    return sorted(events, key=lambda x: x["days_away"])


def get_incubation_day(batch: dict) -> int | None:
    """
    Return how many days into incubation this batch is (Day 1 = start date).
    Returns None if start_date is not set.
    """
    start = parse_date(batch.get("start_date"))
    if start is None:
        return None
    return (datetime.now().date() - start.date()).days + 1


def get_all_events(batches: list, lookahead_days: int = 30) -> list:
    events = []
    for batch in batches:
        events.extend(get_upcoming_events(batch, lookahead_days))
    return sorted(events, key=lambda x: x["days_away"])


# ── Temperature mode presets ──────────────────────────────────────────────────

TEMP_MODES = {
    "off":          {"label": "Off",            "min": None, "max": None,
                     "goal_temp": None, "goal_humidity": None},
    "cool_storage": {"label": "Cool Storage",  "min":  0.0, "max": 12.0,
                     "goal_temp":  4.0, "goal_humidity": 50.0},
    "incubation":   {"label": "Incubation",     "min": 25.0, "max": 35.0,
                     "goal_temp": 30.0, "goal_humidity": 65.0},
    "holding":      {"label": "Holding Temp",   "min": 10.0, "max": 18.0,
                     "goal_temp": 14.0, "goal_humidity": 60.0},
}

# Reverse lookup: display label → mode key
_MODE_BY_LABEL = {v["label"]: k for k, v in TEMP_MODES.items()}

# Mode keys that count as "active" (shown on the dashboard, inspections needed).
ACTIVE_MODES = [k for k in TEMP_MODES if k != "off"]


def is_off(incubator: dict) -> bool:
    """True when the incubator is turned off (temp_mode == 'off')."""
    return (incubator.get("temp_mode") or "incubation") == "off"


def get_temp_range(incubator: dict) -> tuple:
    """Return (min_c, max_c) for the incubator's current temp_mode, or (None, None) if Off."""
    mode = incubator.get("temp_mode") or "incubation"
    cfg  = TEMP_MODES.get(mode, TEMP_MODES["incubation"])
    return cfg["min"], cfg["max"]


def get_mode_goal_defaults(mode: str) -> tuple:
    """Return the built-in (goal_temp_c, goal_humidity_pct) for a mode, or (None, None)."""
    cfg = TEMP_MODES.get(mode, {})
    return cfg.get("goal_temp"), cfg.get("goal_humidity")


# ── Threshold checks ──────────────────────────────────────────────────────────

def check_temp_humidity(incubator: dict,
                         temp_c: float,
                         humidity: float) -> list:
    """
    Returns list of problem strings if reading is out of range.
    Empty list = all OK.
    """
    problems = []
    name = incubator.get("name", f"Incubator {incubator.get('id', '?')}")

    t_min, t_max = get_temp_range(incubator)

    if t_min is None:  # Off mode — no alerts
        return problems

    if temp_c < t_min:
        problems.append(f"{name}: Temp {temp_c:.1f}°C below minimum {t_min}°C")
    elif temp_c > t_max:
        problems.append(f"{name}: Temp {temp_c:.1f}°C above maximum {t_max}°C")

    return problems


# ── Analytics ─────────────────────────────────────────────────────────────────

def summarize_readings(readings, t_min=None, t_max=None, max_gap_h: float = 1.0) -> dict:
    """Summarise a list of temp/humidity readings for one incubator.

    `readings`: dicts with 'timestamp' (ISO), 'temperature_c', 'humidity_pct'.
    Returns averages/extremes, degree-hours (trapezoidal integral of temperature
    over time, °C·h), and — when a [t_min, t_max] band is given — the % of time
    the temperature stayed in range. Gaps longer than `max_gap_h` hours (sensor
    outages) are not counted toward time totals so they don't skew the result.
    """
    pts = []
    for r in readings:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except Exception:
            continue
        pts.append((ts, r.get("temperature_c"), r.get("humidity_pct")))
    pts.sort(key=lambda p: p[0])

    temps = [t for _, t, _ in pts if t is not None]
    hums  = [h for _, _, h in pts if h is not None]
    out = {
        "count":         len(pts),
        "avg_temp":      round(sum(temps) / len(temps), 1) if temps else None,
        "min_temp":      round(min(temps), 1) if temps else None,
        "max_temp":      round(max(temps), 1) if temps else None,
        "avg_humidity":  round(sum(hums) / len(hums)) if hums else None,
        "degree_hours":  0.0,
        "hours_total":   0.0,
        "hours_in_range": 0.0,
        "in_range_pct":  None,
    }
    for (t1, tmp1, _), (t2, tmp2, _) in zip(pts, pts[1:]):
        dh = (t2 - t1).total_seconds() / 3600.0
        if dh <= 0 or dh > max_gap_h or tmp1 is None or tmp2 is None:
            continue
        avg_t = (tmp1 + tmp2) / 2.0
        out["hours_total"]  += dh
        out["degree_hours"] += avg_t * dh
        if t_min is not None and t_max is not None and t_min <= avg_t <= t_max:
            out["hours_in_range"] += dh
    out["degree_hours"] = round(out["degree_hours"], 1)
    out["hours_total"]  = round(out["hours_total"], 1)
    out["hours_in_range"] = round(out["hours_in_range"], 1)
    if out["hours_total"] > 0 and t_min is not None:
        out["in_range_pct"] = round(out["hours_in_range"] / out["hours_total"] * 100)
    return out


def accumulate_degree_days(readings, base_c: float = 10.0,
                           max_gap_h: float = 1.0) -> float:
    """Accumulated degree-days above `base_c` from time-series readings.

    Degree-days = integral of max(0, temp − base) over time, expressed in days.
    A standard, transparent way to relate temperature exposure to insect
    development. `base_c` (developmental threshold) is configurable per operation
    — this function makes no species assumption.
    """
    pts = []
    for r in readings:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except Exception:
            continue
        t = r.get("temperature_c")
        if t is not None:
            pts.append((ts, t))
    pts.sort(key=lambda p: p[0])

    dd = 0.0
    for (t1, tmp1), (t2, tmp2) in zip(pts, pts[1:]):
        dh = (t2 - t1).total_seconds() / 3600.0
        if dh <= 0 or dh > max_gap_h:
            continue
        avg_above = max(0.0, (tmp1 + tmp2) / 2.0 - base_c)
        dd += avg_above * (dh / 24.0)
    return round(dd, 2)


def project_completion(accumulated_dd: float, target_dd: float,
                       elapsed_days: float):
    """Project progress toward a degree-day target.

    Returns (pct_complete, projected_days_remaining). days_remaining is None when
    it can't be estimated yet (no elapsed time / no accumulation / no target).
    The projection extrapolates the average accumulation rate so far.
    """
    if not target_dd or target_dd <= 0:
        return None, None
    pct = round(accumulated_dd / target_dd * 100)
    if elapsed_days <= 0 or accumulated_dd <= 0:
        return pct, None
    rate = accumulated_dd / elapsed_days            # degree-days per day
    if rate <= 0:
        return pct, None
    remaining = max(0.0, target_dd - accumulated_dd)
    return pct, round(remaining / rate, 1)


# ── Unit conversion ───────────────────────────────────────────────────────────

def c_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


def f_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 1)


def format_temp(temp_c: float, unit: str = "C") -> str:
    if unit == "F":
        return f"{c_to_f(temp_c):.1f}°F"
    return f"{temp_c:.1f}°C"


# ── Tray date helpers (moved from incubation_app.py) ─────────────────────────

def _parse_date_loose(s):
    """Parse a date string in common formats (ISO or M/D/Y). Returns date or None."""
    if not s:
        return None
    s = str(s).strip()
    token = s.replace("T", " ").split()[0] if s else s   # the date portion
    for cand in (token, s):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def cool_down_days(tray: dict):
    """Days a tray has been / was cooled. None if not applicable."""
    cd = _parse_date_loose(tray.get("cool_date"))
    if not cd:
        return None
    status = tray.get("status")
    if status == "cooled":
        end = datetime.now().date()
    elif status == "released":
        end = _parse_date_loose(tray.get("out_date")) or datetime.now().date()
    else:
        return None
    return max((end - cd).days, 0)

