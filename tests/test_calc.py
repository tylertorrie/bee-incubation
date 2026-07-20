"""Tests for incubation_calc — pure logic, no database or GUI."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import incubation_calc as calc


# ── Weight / volume calculations ─────────────────────────────────────────────

def test_raw_weight_per_tray_basic():
    # 2 gal live at 100% live, 2.2 lbs/gal -> 4.4 lbs raw
    assert calc.calc_raw_weight_per_tray(1.0, 2.0, 2.2) == 4.4


def test_raw_weight_per_tray_partial_live():
    # 50% live -> need twice the raw cells
    assert calc.calc_raw_weight_per_tray(0.5, 2.0, 2.2) == 8.8


def test_raw_weight_per_tray_zero_live_is_safe():
    assert calc.calc_raw_weight_per_tray(0.0) == 0.0


def test_sample_summary_zero_inputs():
    s = calc.calc_sample_summary(0, 0)
    assert s["tray_count"] == 0 and s["live_gals_total"] == 0.0


def test_sample_summary_counts_trays():
    # 10 gal @ 80% live = 8 live gal / 2 per tray = 4 trays
    s = calc.calc_sample_summary(10.0, 0.8, target_gals_per_tray=2.0)
    assert s["tray_count"] == 4
    assert s["live_gals_total"] == 8.0


# ── Dates ────────────────────────────────────────────────────────────────────

def test_parse_date_formats():
    assert calc.parse_date("2026-07-19").year == 2026
    assert calc.parse_date("07/19/2026").month == 7
    assert calc.parse_date(None) is None
    assert calc.parse_date("not-a-date") is None


def test_days_from_now_and_format():
    today = datetime.now().strftime("%Y-%m-%d")
    assert calc.days_from_now(today) == 0
    assert calc.format_days(0) == "Today"
    assert calc.format_days(1) == "Tomorrow"
    assert calc.format_days(-1) == "Yesterday"
    assert calc.format_days(None) == "—"


def test_incubation_day():
    start = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
    # Day 1 = start date, so 4 days ago -> day 5
    assert calc.get_incubation_day({"start_date": start}) == 5
    assert calc.get_incubation_day({"start_date": None}) is None


# ── Temp modes / goals / ranges ──────────────────────────────────────────────

def test_temp_modes_have_required_keys():
    for key, cfg in calc.TEMP_MODES.items():
        for field in ("label", "min", "max", "goal_temp", "goal_humidity"):
            assert field in cfg, f"{key} missing {field}"


def test_off_mode_is_off_and_has_no_range():
    assert calc.is_off({"temp_mode": "off"}) is True
    assert calc.is_off({"temp_mode": "incubation"}) is False
    assert calc.is_off({}) is False           # default is incubation, not off
    assert calc.get_temp_range({"temp_mode": "off"}) == (None, None)


def test_active_modes_excludes_off():
    assert "off" not in calc.ACTIVE_MODES
    assert set(calc.ACTIVE_MODES) == {"cool_storage", "incubation", "holding"}


def test_goal_defaults():
    assert calc.get_mode_goal_defaults("incubation") == (30.0, 65.0)
    assert calc.get_mode_goal_defaults("off") == (None, None)


def test_check_temp_humidity():
    inc = {"name": "T", "temp_mode": "incubation"}   # range 25–35
    assert calc.check_temp_humidity(inc, 30.0, 60) == []       # in range
    assert calc.check_temp_humidity(inc, 40.0, 60)             # too hot -> problem
    assert calc.check_temp_humidity(inc, 10.0, 60)             # too cold -> problem
    # Off mode never alerts
    assert calc.check_temp_humidity({"temp_mode": "off"}, 99.0, 5) == []


# ── Unit conversion ──────────────────────────────────────────────────────────

def test_unit_conversion_roundtrip():
    assert calc.c_to_f(0) == 32.0
    assert calc.c_to_f(100) == 212.0
    assert calc.f_to_c(32) == 0.0
    assert calc.format_temp(25.0, "C") == "25.0°C"
    assert calc.format_temp(0.0, "F") == "32.0°F"
