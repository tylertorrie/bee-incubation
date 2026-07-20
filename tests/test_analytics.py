"""Tests for the analytics helpers in incubation_calc."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import incubation_calc as calc


def _readings(temps, start=None, step_min=30):
    """Build evenly spaced readings from a list of temperatures."""
    start = start or datetime(2026, 7, 1, 0, 0, 0)
    out = []
    for i, t in enumerate(temps):
        ts = (start + timedelta(minutes=step_min * i)).isoformat()
        out.append({"timestamp": ts, "temperature_c": t, "humidity_pct": 60})
    return out


def test_summarize_empty():
    s = calc.summarize_readings([])
    assert s["count"] == 0 and s["avg_temp"] is None and s["in_range_pct"] is None


def test_summarize_avg_and_extremes():
    s = calc.summarize_readings(_readings([20, 30, 40]))
    assert s["avg_temp"] == 30.0
    assert s["min_temp"] == 20.0 and s["max_temp"] == 40.0
    assert s["avg_humidity"] == 60


def test_summarize_time_in_range_full():
    # All readings at 30°C, band 25–35 -> 100% in range
    s = calc.summarize_readings(_readings([30, 30, 30, 30]), t_min=25, t_max=35)
    assert s["in_range_pct"] == 100
    assert s["hours_total"] > 0


def test_summarize_time_in_range_partial():
    # Half in band, half out (each interval's midpoint decides membership)
    s = calc.summarize_readings(_readings([30, 30, 50, 50]), t_min=25, t_max=35)
    # 3 intervals: [30,30] in, [30,50] mid=40 out, [50,50] out -> ~1/3 in range
    assert 30 <= s["in_range_pct"] <= 40


def test_summarize_ignores_large_gaps():
    r = _readings([30, 30])                 # 30 min apart -> counted
    # add a reading 5 hours later (gap > max_gap_h) -> that interval skipped
    r.append({"timestamp": (datetime(2026, 7, 1, 6, 0, 0)).isoformat(),
              "temperature_c": 30, "humidity_pct": 60})
    s = calc.summarize_readings(r, t_min=25, t_max=35, max_gap_h=1.0)
    assert round(s["hours_total"], 1) == 0.5   # only the first 30-min interval


def test_degree_days_above_base():
    # 24 h at 20°C with base 10 -> 10 degree-days per day * 1 day = 10
    start = datetime(2026, 7, 1, 0, 0, 0)
    r = [{"timestamp": start.isoformat(), "temperature_c": 20, "humidity_pct": 50},
         {"timestamp": (start + timedelta(hours=1)).isoformat(),
          "temperature_c": 20, "humidity_pct": 50}]
    # 1 hour at (20-10)=10 above base -> 10 * (1/24) ≈ 0.42 dd
    assert calc.accumulate_degree_days(r, base_c=10.0, max_gap_h=2.0) == 0.42


def test_degree_days_never_negative():
    r = _readings([5, 5, 5], step_min=30)   # below base
    assert calc.accumulate_degree_days(r, base_c=10.0) == 0.0


def test_project_completion():
    pct, days = calc.project_completion(accumulated_dd=50, target_dd=100,
                                        elapsed_days=5)
    assert pct == 50
    assert days == 5.0                      # 50 dd in 5 days -> 10/day -> 5 more
    # No target -> nothing
    assert calc.project_completion(50, 0, 5) == (None, None)
    # No elapsed time -> pct but no estimate
    assert calc.project_completion(0, 100, 0) == (0, None)
