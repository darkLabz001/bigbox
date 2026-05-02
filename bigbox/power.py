"""Battery / UPS HAT detection.

Pi 4 has no built-in battery, so this only returns data when the user
has a UPS HAT or PiSugar attached. Tried in order:

1. ``/sys/class/power_supply/*`` — standard kernel interface populated
   by mainline drivers (axp20x, max17040, etc.). Most accurate when
   present.
2. PiSugar ``http://localhost:8421/api/battery`` — used by the
   ``pisugar-server`` daemon shipped with the PiSugar 2/3 boards.
3. MAX17048 fuel gauge at I²C 0x36 — what most Geekworm / Waveshare
   UPS HATs ship with. Reads SOC (0x04) and VCELL (0x02) directly.

If none are present, :func:`battery` returns ``None`` and the status
bar indicator gracefully omits itself. Negative results are cached for
a minute so we don't probe I²C every frame on a battery-less device.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BatteryInfo:
    percent: int            # 0..100
    voltage: float = 0.0    # volts; 0 if unknown
    charging: bool = False
    source: str = ""        # "sysfs/<name>" / "pisugar" / "i2c/max17048"


_lock = threading.Lock()
_cached: Optional[BatteryInfo] = None
_cache_ts: float = 0.0
_CACHE_SECONDS = 5.0
_NEGATIVE_CACHE_SECONDS = 60.0
_last_negative_ts: float = 0.0


def _read_sysfs() -> Optional[BatteryInfo]:
    base = Path("/sys/class/power_supply")
    if not base.is_dir():
        return None
    for entry in base.iterdir():
        cap_file = entry / "capacity"
        if not cap_file.exists():
            continue
        try:
            pct = int(cap_file.read_text().strip())
        except Exception:
            continue
        voltage = 0.0
        v_file = entry / "voltage_now"
        if v_file.exists():
            try:
                voltage = int(v_file.read_text().strip()) / 1_000_000.0
            except Exception:
                pass
        charging = False
        s_file = entry / "status"
        if s_file.exists():
            try:
                charging = s_file.read_text().strip().lower() == "charging"
            except Exception:
                pass
        return BatteryInfo(percent=pct, voltage=voltage,
                           charging=charging, source=f"sysfs/{entry.name}")
    return None


def _read_pisugar() -> Optional[BatteryInfo]:
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(
                "http://localhost:8421/api/battery", timeout=0.5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return BatteryInfo(
        percent=int(data.get("battery_p", 0)),
        voltage=float(data.get("battery_v", 0.0)),
        charging=bool(data.get("battery_charging", 0)),
        source="pisugar",
    )


def _read_i2c_max17048() -> Optional[BatteryInfo]:
    try:
        import smbus2  # type: ignore
    except Exception:
        return None
    try:
        bus = smbus2.SMBus(1)
        try:
            # SOC register 0x04 — high byte = whole percent, big-endian.
            soc = bus.read_word_data(0x36, 0x04)
            pct = max(0, min(100, ((soc & 0xFF) << 8 | (soc >> 8)) >> 8))
            # VCELL register 0x02 — 12-bit, 78.125 µV per LSB.
            vcell = bus.read_word_data(0x36, 0x02)
            vcell_be = (vcell & 0xFF) << 8 | (vcell >> 8)
            voltage = (vcell_be >> 4) * 78.125 / 1_000_000
        finally:
            bus.close()
        return BatteryInfo(percent=pct, voltage=voltage,
                           source="i2c/max17048")
    except Exception:
        return None


def battery() -> Optional[BatteryInfo]:
    """Current battery state, or None if no source detected. Cached
    5s on success, 60s on miss."""
    global _cached, _cache_ts, _last_negative_ts
    now = time.monotonic()
    with _lock:
        if _cached is not None and now - _cache_ts < _CACHE_SECONDS:
            return _cached
        if _cached is None and now - _last_negative_ts < _NEGATIVE_CACHE_SECONDS:
            return None

    for fn in (_read_sysfs, _read_pisugar, _read_i2c_max17048):
        try:
            info = fn()
        except Exception:
            info = None
        if info:
            with _lock:
                _cached = info
                _cache_ts = now
            return info

    with _lock:
        _cached = None
        _last_negative_ts = now
    return None
