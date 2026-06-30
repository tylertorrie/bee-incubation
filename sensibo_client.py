"""
sensibo_client.py  —  Sensibo API for manual AC control

Manual control only (no automatic/closed-loop thermostat behavior).
Docs: https://support.sensibo.com/api/

  GET  /api/v2/users/me/pods                      — list devices ("pods")
  GET  /api/v2/pods/{id}/acStates?days=1           — most recent AC state
  POST /api/v2/pods/{id}/acStates                  — set full AC state

API key: Sensibo app → account settings → API key page.
"""
import requests
from typing import Optional

_BASE = "https://home.sensibo.com/api/v2"
REQUEST_TIMEOUT = 12  # seconds

# Common Sensibo fan levels. Exact set varies per AC model; if a unit rejects
# one it returns an error which is surfaced to the user.
FAN_LEVELS = ["auto", "low", "medium", "high"]

# The AC units operate in Fahrenheit. Target temps are entered and sent in °F.
TEMP_UNIT  = "F"
MIN_TEMP_F = 62
MAX_TEMP_F = 86


def parse_device_ids(raw: str) -> list:
    """Split a stored device-id field into a clean list.

    An incubator may have more than one AC unit; their IDs are stored
    comma- (or whitespace-) separated in a single field and controlled
    together. Returns [] when nothing is configured.
    """
    if not raw:
        return []
    parts = str(raw).replace(";", ",").replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


class SensiboClient:
    """Thin wrapper for manual Sensibo AC control. No background polling."""

    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or "").strip()
        self._error  = ""
        self._power  = {}   # device_id -> last known on/off (bool)
        self._state  = {}   # device_id -> full acState dict

    def set_api_key(self, key: str):
        self.api_key = (key or "").strip()

    # ── Power-state cache (so a single toggle knows which way to flip) ─────────

    def get_cached_state(self, device_ids) -> dict:
        """Return the cached acState for the first device in the group, or {}."""
        if isinstance(device_ids, str):
            device_ids = parse_device_ids(device_ids)
        for did in device_ids:
            if did in self._state:
                return self._state[did]
        return {}

    def fetch_state(self, device_ids) -> dict:
        """Return acState, querying the hardware if not cached."""
        cached = self.get_cached_state(device_ids)
        if cached:
            return cached
        if isinstance(device_ids, str):
            device_ids = parse_device_ids(device_ids)
        if not device_ids:
            return {}
        return self.get_ac_state(device_ids[0])

    def get_cached_power(self, device_ids):
        """Last known on/off for a device or group. True/False, or None if
        not yet known. For a group, returns True if ANY unit is known-on."""
        if isinstance(device_ids, str):
            device_ids = parse_device_ids(device_ids)
        vals = [self._power.get(d) for d in device_ids]
        known = [v for v in vals if v is not None]
        if not known:
            return None
        return any(known)

    def resolve_power(self, device_ids):
        """Return current on/off, querying the hardware if not cached.
        Used right before a toggle so we flip the correct direction."""
        cached = self.get_cached_power(device_ids)
        if cached is not None:
            return cached
        st = self.fetch_state(device_ids)
        return bool(st.get("on")) if st else False

    def status_label(self) -> str:
        if not self.api_key:
            return "No API key"
        return f"Error: {self._error}" if self._error else "Ready"

    # ── Devices ──────────────────────────────────────────────────────────────

    def list_devices(self) -> list:
        """Return list of pod dicts: id, room name, current acState."""
        if not self.api_key:
            return []
        try:
            resp = requests.get(
                f"{_BASE}/users/me/pods",
                params={"apiKey": self.api_key, "fields": "id,room,acState"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                self._error = ""
                return resp.json().get("result", [])
            self._error = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            self._error = str(exc)
        return []

    # ── AC state ─────────────────────────────────────────────────────────────

    def get_ac_state(self, device_id: str) -> dict:
        """Return the most recent acState dict for one device, or {}."""
        if not self.api_key or not device_id:
            return {}
        try:
            resp = requests.get(
                f"{_BASE}/pods/{device_id}/acStates",
                params={"apiKey": self.api_key, "limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                results = resp.json().get("result", [])
                if results:
                    self._error = ""
                    state = results[0].get("acState", {})
                    self._state[device_id] = state
                    if "on" in state:
                        self._power[device_id] = bool(state["on"])
                    return state
            else:
                self._error = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            self._error = str(exc)
        return {}

    def set_ac_state(self, device_id: str, on: Optional[bool] = None,
                      target_temp: Optional[int] = None,
                      mode: Optional[str] = None,
                      fan_level: Optional[str] = None) -> bool:
        """
        Push a new AC state. Reads the current state first and only changes
        the fields provided, so e.g. setting a temp doesn't accidentally
        turn the unit on/off.
        Returns True on success.
        """
        if not self.api_key or not device_id:
            return False
        state = self.get_ac_state(device_id)
        if not state:
            # No known state yet — start from a sane default (units operate in °F)
            state = {"on": False, "mode": mode or "cool",
                      "targetTemperature": target_temp or 72,
                      "temperatureUnit": TEMP_UNIT}
        if on is not None:
            state["on"] = on
        if mode is not None:
            state["mode"] = mode
        if target_temp is not None:
            # AC units expect Fahrenheit — set the unit alongside the value
            state["targetTemperature"] = target_temp
            state["temperatureUnit"]   = TEMP_UNIT
        if fan_level is not None:
            state["fanLevel"] = fan_level

        try:
            resp = requests.post(
                f"{_BASE}/pods/{device_id}/acStates",
                params={"apiKey": self.api_key},
                json={"acState": state},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                self._error = ""
                self._state[device_id] = state
                if "on" in state:
                    self._power[device_id] = bool(state["on"])
                return True
            self._error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as exc:
            self._error = str(exc)
        return False

    def set_ac_state_many(self, device_ids, on: Optional[bool] = None,
                          target_temp: Optional[int] = None,
                          mode: Optional[str] = None,
                          fan_level: Optional[str] = None) -> bool:
        """Apply the same AC change to several devices (controlled together).

        Accepts a list of device IDs or a raw comma-separated string.
        Returns True only if EVERY device succeeded; self._error names the
        first failure.
        """
        if isinstance(device_ids, str):
            device_ids = parse_device_ids(device_ids)
        if not device_ids:
            self._error = "No device configured"
            return False
        all_ok = True
        for did in device_ids:
            if not self.set_ac_state(did, on=on, target_temp=target_temp,
                                     mode=mode, fan_level=fan_level):
                all_ok = False
        return all_ok

    def turn_on(self, device_id: str) -> bool:
        return self.set_ac_state(device_id, on=True)

    def turn_off(self, device_id: str) -> bool:
        return self.set_ac_state(device_id, on=False)

    def set_target_temp(self, device_id: str, temp_c: int) -> bool:
        return self.set_ac_state(device_id, target_temp=temp_c)
