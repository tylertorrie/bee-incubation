"""Structural tests for the split-out view modules.

These guard the refactor itself: that every module still imports, that the App
class actually inherits each mixin, and that incubation_app.py keeps its entry
point (a real bug once caught here — the `if __name__ == "__main__"` block was
accidentally swept into services.py, leaving the app unable to launch).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIXINS = [
    ("views.dialogs",          None),
    ("views.settings_view",    "SettingsViewMixin"),
    ("views.detail_view",      "DetailViewMixin"),
    ("views.trays_view",       "TraysViewMixin"),
    ("views.samples_view",     "SamplesViewMixin"),
    ("views.analytics_view",   "AnalyticsViewMixin"),
    ("views.timeline_view",    "TimelineViewMixin"),
    ("views.dashboard_view",   "DashboardViewMixin"),
    ("views.sensibo_controls", "SensiboControlMixin"),
    ("services",               "ServicesMixin"),
]


@pytest.mark.parametrize("module_name,cls_name", MIXINS)
def test_module_imports_and_defines_mixin(module_name, cls_name):
    mod = __import__(module_name, fromlist=["*"])
    if cls_name:
        assert hasattr(mod, cls_name), f"{module_name} is missing {cls_name}"


def test_app_inherits_every_mixin():
    import incubation_app as ia
    bases = {c.__name__ for c in ia.IncubationApp.__mro__}
    for _mod, cls_name in MIXINS:
        if cls_name:
            assert cls_name in bases, f"IncubationApp does not inherit {cls_name}"


def test_app_still_has_entry_point():
    """incubation_app.py must remain runnable as the program entry point."""
    src = open(os.path.join(REPO, "incubation_app.py"), encoding="utf-8").read()
    assert '__name__ == "__main__"' in src, "entry point block is missing"
    assert "app.mainloop()" in src, "mainloop call is missing"


def test_shared_constants_live_in_app_config():
    import app_config
    assert isinstance(app_config.POLL_INTERVAL_SEC, int)
    assert app_config.APP_VERSION
    assert isinstance(app_config.app_version_string(), str)


def test_theme_helpers_available():
    import ui_theme
    for name in ("GOLD", "CARD", "TEXT", "FONT_B", "_label", "_btn", "_FormRow"):
        assert hasattr(ui_theme, name), f"ui_theme missing {name}"
