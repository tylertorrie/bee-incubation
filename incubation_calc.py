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


def get_all_events(batches: list, lookahead_days: int = 30) -> list:
    events = []
    for batch in batches:
        events.extend(get_upcoming_events(batch, lookahead_days))
    return sorted(events, key=lambda x: x["days_away"])


# ── Temperature mode presets ──────────────────────────────────────────────────

TEMP_MODES = {
    "cool_storage": {"label": "Cool Storage",  "min":  0.0, "max": 12.0},
    "incubation":   {"label": "Incubation",     "min": 25.0, "max": 35.0},
    "holding":      {"label": "Holding Temp",   "min": 10.0, "max": 18.0},
}

# Reverse lookup: display label → mode key
_MODE_BY_LABEL = {v["label"]: k for k, v in TEMP_MODES.items()}


def get_temp_range(incubator: dict) -> tuple:
    """Return (min_c, max_c) for the incubator's current temp_mode."""
    mode = incubator.get("temp_mode") or "incubation"
    cfg  = TEMP_MODES.get(mode, TEMP_MODES["incubation"])
    return cfg["min"], cfg["max"]


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
    h_min = float(incubator.get("humidity_min") or 55.0)
    h_max = float(incubator.get("humidity_max") or 75.0)

    if temp_c < t_min:
        problems.append(f"{name}: Temp {temp_c:.1f}°C below minimum {t_min}°C")
    elif temp_c > t_max:
        problems.append(f"{name}: Temp {temp_c:.1f}°C above maximum {t_max}°C")

    if humidity < h_min:
        problems.append(f"{name}: Humidity {humidity:.0f}% below minimum {h_min:.0f}%")
    elif humidity > h_max:
        problems.append(f"{name}: Humidity {humidity:.0f}% above maximum {h_max:.0f}%")

    return problems


# ── Unit conversion ───────────────────────────────────────────────────────────

def c_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


def f_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 1)


def format_temp(temp_c: float, unit: str = "C") -> str:
    if unit == "F":
        return f"{c_to_f(temp_c):.1f}°F"
    return f"{temp_c:.1f}°C"
