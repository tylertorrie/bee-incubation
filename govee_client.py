"""
govee_client.py  —  Govee API polling for temperature / humidity

Two APIs are supported and tried in order:

  1. Platform API v2  (openapi.api.govee.com)  ← gateway sensors live here
     POST /router/api/v1/user/devices   — lists ALL devices incl. sensors
     POST /router/api/v1/device/state   — reads temp/humidity

  2. Legacy API v1  (developer-api.govee.com)  ← lights/plugs only fallback
     GET  /v1/devices
     GET  /v1/devices/state

API key: Govee app → Profile → About Us → Apply for API Key

Temperature is reported as integer in 0.01 °C units  (2730 → 27.30 °C)
Humidity    is reported as integer in 0.01 %   units  (6200 → 62.00 %)
"""
import threading
import time
import uuid
import requests
from datetime import datetime
from typing import Callable, Optional

# ── API endpoints ─────────────────────────────────────────────────────────────
_V2_BASE = "https://openapi.api.govee.com/router/api/v1"
_V1_BASE = "https://developer-api.govee.com/v1"
REQUEST_TIMEOUT = 12  # seconds

# SKU prefixes that are temperature/humidity sensors
_SENSOR_SKU_PREFIXES = (
    "H5074", "H5075", "H5076", "H5100", "H5101", "H5102", "H5103",
    "H5104", "H5105", "H5106", "H5174", "H5175", "H5176", "H5177",
    "H5178", "H5179", "H5182", "H5183", "H5184", "H5185", "H5056",
    "H5057", "H5051", "H5052",
)


def _is_sensor(sku: str) -> bool:
    return any(sku.upper().startswith(p) for p in _SENSOR_SKU_PREFIXES)


def _raw_to_value(raw) -> float | None:
    """Convert Govee integer reading to real value (divide by 100 if > 100)."""
    if raw is None:
        return None
    if not isinstance(raw, (int, float)):
        return None
    return raw / 100.0 if raw > 100 else float(raw)


def to_celsius(sensor_temp) -> float | None:
    """Convert a sensor temperature reading to °C for storage.

    The Govee sensors report in the unit set in 'govee_sensor_unit' (default
    'F'). We convert based on that unit — NOT on the value — so cold incubators
    near 50°F don't flip between converted/unconverted (which caused a sawtooth).
    """
    if sensor_temp is None:
        return None
    try:
        import incubation_db as _db
        unit = (_db.get_setting("govee_sensor_unit", "F") or "F").upper()
    except Exception:
        unit = "F"
    if unit.startswith("F"):
        return round((sensor_temp - 32) * 5 / 9, 2)
    return round(float(sensor_temp), 2)


# ═════════════════════════════════════════════════════════════════════════════

class GoveeClient:
    """
    Background-polling Govee API client.  Tries Platform API v2 first
    (which exposes gateway-connected sensors), falls back to v1.

    Usage:
        client = GoveeClient(api_key="xxx")
        client.start_polling(
            incubators_fn=db.get_incubators,
            on_reading=lambda inc_id, temp_c, hum: ...
        )
    """

    def __init__(self, api_key: str = "", poll_interval_sec: int = 60):
        self.api_key          = api_key.strip()
        self.poll_interval_sec = poll_interval_sec
        self._thread: Optional[threading.Thread] = None
        self._running  = False
        self._last: dict = {}   # incubator_id → {temp_c, humidity, timestamp}
        self._error    = ""
        self.connected = False

    def set_api_key(self, key: str):
        self.api_key = key.strip()

    # ── Headers ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Govee-API-Key":  self.api_key,
            "Content-Type":   "application/json",
        }

    def _req_id(self) -> str:
        return str(uuid.uuid4())

    # ══════════════════════════════════════════════════════════════════════════
    #  Platform API v2  —  includes sensors & gateway devices
    # ══════════════════════════════════════════════════════════════════════════

    def get_devices_v2(self) -> list:
        """
        List ALL devices on the account via Platform API v2.
        Returns list of dicts, each with: device, sku, deviceName, capabilities.
        """
        if not self.api_key:
            return []
        try:
            resp = requests.get(
                f"{_V2_BASE}/user/devices",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200:
                    self.connected = True
                    return data.get("data", [])
                self._error = data.get("message", f"v2 code {data.get('code')}")
            else:
                self._error = f"v2 HTTP {resp.status_code}"
        except requests.RequestException as exc:
            self._error = str(exc)
        return []

    def get_device_state_v2(self, device_id: str, sku: str) -> dict:
        """
        Fetch current state for one device via Platform API v2.
        Returns the 'payload' dict (contains 'capabilities' list), or {}.
        """
        if not self.api_key:
            return {}
        try:
            resp = requests.post(
                f"{_V2_BASE}/device/state",
                headers=self._headers(),
                json={
                    "requestId": self._req_id(),
                    "payload":   {"sku": sku, "device": device_id},
                },
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200:
                    return data.get("payload", {})
                self._error = data.get("message", f"v2 state code {data.get('code')}")
            else:
                self._error = f"v2 state HTTP {resp.status_code}"
        except requests.RequestException as exc:
            self._error = str(exc)
        return {}

    @staticmethod
    def _parse_v2_state(payload: dict):
        """
        Extract (temp_c, humidity) from a v2 capability payload.
        Looks for instances: sensorTemperature, sensorHumidity,
        temperature, humidity (various naming conventions across models).
        """
        temp_c   = None
        humidity = None
        for cap in payload.get("capabilities", []):
            instance = (cap.get("instance") or "").lower()
            val      = (cap.get("state") or {}).get("value")
            if val is None:
                continue
            if "temperature" in instance:
                temp_c   = _raw_to_value(val)
            elif "humidity" in instance:
                humidity = _raw_to_value(val)
        return temp_c, humidity

    # ══════════════════════════════════════════════════════════════════════════
    #  Legacy API v1  —  controllable devices (lights, plugs) only
    # ══════════════════════════════════════════════════════════════════════════

    def get_devices_v1(self) -> list:
        """List devices via legacy v1 API (controllable devices only)."""
        if not self.api_key:
            return []
        try:
            resp = requests.get(
                f"{_V1_BASE}/devices",
                headers={"Govee-API-Key": self.api_key},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                self.connected = True
                return resp.json().get("data", {}).get("devices", [])
            self._error = f"v1 HTTP {resp.status_code}"
        except requests.RequestException as exc:
            self._error = str(exc)
        return []

    def get_device_state_v1(self, device_id: str, sku: str) -> dict:
        """Fetch state via legacy v1 API."""
        if not self.api_key:
            return {}
        try:
            resp = requests.get(
                f"{_V1_BASE}/devices/state",
                headers={"Govee-API-Key": self.api_key},
                params={"device": device_id, "model": sku},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {})
        except requests.RequestException as exc:
            self._error = str(exc)
        return {}

    @staticmethod
    def _parse_v1_state(state: dict):
        """Extract (temp_c, humidity) from v1 properties list."""
        temp_c   = None
        humidity = None
        for prop in state.get("properties", []):
            if "temperature" in prop:
                temp_c   = _raw_to_value(prop["temperature"])
            if "humidity" in prop:
                humidity = _raw_to_value(prop["humidity"])
        return temp_c, humidity

    # ══════════════════════════════════════════════════════════════════════════
    #  Combined helpers
    # ══════════════════════════════════════════════════════════════════════════

    def get_all_devices(self) -> list:
        """
        Return all devices from v2 (sensors + lights), supplemented by
        any v1-only devices not already present.
        Each device dict is normalised to:
            device, sku, deviceName, is_sensor, api_version
        """
        v2_devices = self.get_devices_v2()
        seen_ids   = {d.get("device") for d in v2_devices}

        # Tag each v2 device
        result = []
        for d in v2_devices:
            d["api_version"] = "v2"
            d["is_sensor"]   = _is_sensor(d.get("sku", "")) or _has_sensor_caps(d)
            result.append(d)

        # Supplement with v1 devices that v2 missed (rare)
        for d in self.get_devices_v1():
            did = d.get("device")
            if did not in seen_ids:
                d["api_version"] = "v1"
                d["is_sensor"]   = False
                result.append(d)

        return result

    # ── Single incubator poll ─────────────────────────────────────────────────

    def poll_incubator(self, incubator: dict):
        """
        Poll one incubator's Govee sensor.
        Tries v2 first (works for gateway/sensor devices), falls back to v1.
        Returns (temp_c, humidity) or (None, None).
        """
        device_id = (incubator.get("govee_device_id") or "").strip()
        sku       = (incubator.get("govee_sku")       or "").strip()
        if not device_id or not sku:
            return None, None

        # Try v2 first
        payload = self.get_device_state_v2(device_id, sku)
        if payload:
            temp_c, humidity = self._parse_v2_state(payload)
            if temp_c is not None and humidity is not None:
                return temp_c, humidity

        # Fall back to v1
        state = self.get_device_state_v1(device_id, sku)
        if state:
            return self._parse_v1_state(state)

        return None, None

    # ── Background polling thread ─────────────────────────────────────────────

    def start_polling(self,
                      incubators_fn: Callable,
                      on_reading: Callable):
        """
        Spin up a daemon thread that polls each incubator's Govee sensor
        every `poll_interval_sec` seconds.

        incubators_fn() → list of incubator dicts (called fresh each cycle)
        on_reading(incubator_id, temp_c, humidity) → called on each reading
        """
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            args=(incubators_fn, on_reading),
            daemon=True,
            name="GoveePoller",
        )
        self._thread.start()

    def stop_polling(self):
        self._running = False

    def _loop(self, incubators_fn: Callable, on_reading: Callable):
        while self._running:
            try:
                for inc in incubators_fn():
                    if not self._running:
                        break
                    temp_c, humidity = self.poll_incubator(inc)
                    if temp_c is not None and humidity is not None:
                        self.connected = True
                        temp_c = to_celsius(temp_c)  # convert by unit, not value
                        self._last[inc["id"]] = {
                            "temp_c":    temp_c,
                            "humidity":  humidity,
                            "timestamp": datetime.now().isoformat(),
                        }
                        on_reading(inc["id"], temp_c, humidity)
            except Exception as exc:
                self._error = str(exc)
            for _ in range(self.poll_interval_sec):
                if not self._running:
                    break
                time.sleep(1)

    # ── Cache / status ────────────────────────────────────────────────────────

    def get_last(self, incubator_id: int) -> dict:
        """Return latest cached reading for an incubator, or {}."""
        return self._last.get(incubator_id, {})

    def status_label(self) -> str:
        if not self.api_key:
            return "No API key"
        if self.connected:
            return "Connected"
        return f"Error: {self._error}" if self._error else "Connecting…"


# ── Module-level helper ───────────────────────────────────────────────────────

def _has_sensor_caps(device: dict) -> bool:
    """Return True if the device's capability list mentions temperature/humidity."""
    for cap in device.get("capabilities", []):
        inst = (cap.get("instance") or "").lower()
        if "temperature" in inst or "humidity" in inst:
            return True
    return False
