"""
views/sensibo_controls.py  —  Sensibo air-conditioner controls used by the incubator screens.

Extracted from incubation_app.py as a mixin (pure relocation).
"""
import threading
from tkinter import messagebox

import customtkinter as ctk

import incubation_db as db
import sensibo_client as sensibo_mod

from ui_theme import (
    GOLD, DK_GOLD, GREEN, GREEN_LT, TEAL, ORANGE, RED, RED_LT, BLUE, LINK,
    BG, BARBG, SIDEBAR, RIGHTPANE, CARD, PANEL, NESTED, CARD2,
    BORDER, BORDER2, SUBBORDER, TEXT, TEXT2, SUBTEXT, FAINT,
    FONT_H, FONT_B, FONT_S, MODE_COLORS, MODE_BADGE_BG,
    _treeview_style, _label, _btn, _btn_primary, _btn_secondary,
    _entry, _combo, _mix, _poll_age, _FormRow,
)


class SensiboControlMixin:
    """Sensibo air-conditioner controls used by the incubator screens."""

    def _sensibo_update_buttons(self, iid: int, device_id: str):
        """Patch just the AC button labels/colors on the card for this incubator."""
        widgets = self._card_widgets.get(iid, {})
        pwr = widgets.get("ac_power")
        tmp = widgets.get("ac_temp")
        fan = widgets.get("ac_fan")
        if pwr and pwr.winfo_exists():
            lbl, fg, tc = self._ac_toggle_style(device_id)
            pwr.configure(text=lbl, fg_color=fg, text_color=tc)
        if tmp and tmp.winfo_exists():
            tmp.configure(text=self._ac_temp_label(device_id))
        if fan and fan.winfo_exists():
            fan.configure(text=self._ac_fan_label(device_id))

    def _sensibo_run(self, iid: int, device_id: str, fn, on_error=None):
        """Run fn() in a background thread; patch AC buttons when done.

        fn must be a zero-argument callable (use a lambda to capture kwargs).
        """
        import threading
        widgets = self._card_widgets.get(iid, {})
        for key in ("ac_power", "ac_temp", "ac_fan"):
            w = widgets.get(key)
            if w and w.winfo_exists():
                w.configure(state="disabled")

        def _work():
            ok = fn()
            def _done():
                for key in ("ac_power", "ac_temp", "ac_fan"):
                    w = widgets.get(key)
                    if w and w.winfo_exists():
                        w.configure(state="normal")
                if not ok and on_error:
                    messagebox.showerror("Sensibo", on_error(), parent=self)
                self._sensibo_update_buttons(iid, device_id)
            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _sensibo_set_power(self, iid: int, device_id: str, on: bool):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        self._sensibo_run(iid, device_id,
            lambda: self._sensibo.set_ac_state_many(device_id, on=on),
            on_error=lambda: f"Could not reach the AC unit(s):\n{self._sensibo.status_label()}")

    def _sensibo_toggle_power(self, iid: int, device_id: str):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        target = not self._sensibo.resolve_power(device_id)
        self._sensibo_run(iid, device_id,
            lambda: self._sensibo.set_ac_state_many(device_id, on=target),
            on_error=lambda: f"Could not reach the AC unit(s):\n{self._sensibo.status_label()}")

    def _ac_toggle_style(self, device_id: str):
        """Return (label, fg, text_color) for an AC power toggle button."""
        power = self._sensibo.get_cached_power(device_id)
        if power is True:
            return "● On", "#243A34", GREEN_LT   # spec AC-on fill
        if power is False:
            return "● Off", "#3A2129", RED_LT     # spec AM/PM-pending-like red
        return "⏻ Power", CARD2, TEXT

    def _ac_temp_label(self, device_id: str) -> str:
        st = self._sensibo.get_cached_state(device_id)
        t = st.get("targetTemperature")
        return f"{t}°F" if t is not None else "Set Temp"

    def _ac_fan_label(self, device_id: str) -> str:
        st = self._sensibo.get_cached_state(device_id)
        f = st.get("fanLevel")
        return f.capitalize() if f else "Fan"

    def _sensibo_prompt_temp(self, iid: int, device_id: str, name: str):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        lo, hi = sensibo_mod.MIN_TEMP_F, sensibo_mod.MAX_TEMP_F
        dlg = ctk.CTkInputDialog(
            title="Set AC Target Temp",
            text=f"Target temperature (°F) for {name}.\n\n"
                 f"Minimum {lo}°F · Maximum {hi}°F")
        raw = dlg.get_input()
        if raw is None or not raw.strip():
            return
        try:
            temp_f = int(round(float(raw.strip())))
        except ValueError:
            messagebox.showerror("Sensibo", "Enter a numeric temperature.", parent=self)
            return
        if not (lo <= temp_f <= hi):
            messagebox.showerror("Sensibo",
                f"Temperature must be between {lo}°F and {hi}°F.", parent=self)
            return
        self._sensibo_run(iid, device_id,
            lambda: self._sensibo.set_ac_state_many(device_id, on=True, target_temp=temp_f),
            on_error=lambda: f"Could not reach the AC unit(s):\n{self._sensibo.status_label()}")

    def _sensibo_prompt_fan(self, iid: int, device_id: str, name: str):
        if not db.get_setting("sensibo_api_key"):
            messagebox.showwarning("Sensibo", "Set a Sensibo API key in Settings first.", parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Set Fan Speed")
        win.geometry("260x320")
        win.grab_set()
        _label(win, f"Fan speed — {name}", FONT_B, GOLD).pack(padx=16, pady=(14, 8))

        def _apply(level):
            win.destroy()
            self._sensibo_run(iid, device_id,
                lambda l=level: self._sensibo.set_ac_state_many(device_id, fan_level=l),
                on_error=lambda: f"Could not set fan speed:\n{self._sensibo.status_label()}")

        for lvl in sensibo_mod.FAN_LEVELS:
            _btn(win, lvl.capitalize(), lambda l=lvl: _apply(l),
                 width=180, height=32, fg=BORDER, hover=CARD2).pack(pady=3)
        _btn(win, "Cancel", win.destroy, width=180, height=28,
             fg=CARD2, hover=BORDER).pack(pady=(8, 4))

