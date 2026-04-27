"""GPS reader for the Quectel LC86L (and other NMEA serial dongles).

Auto-probes /dev/ttyUSB* /dev/ttyACM* /dev/ttyAMA*, tries 9600 then 115200,
runs a background thread parsing $G[PN]GGA / $G[PN]RMC. Snapshot the most
recent fix with GPSReader.latest(); thread-safe.

Designed to fail soft: if the dongle is unplugged, latest() just keeps
returning a no-fix sentinel. Wardrive UI uses that to render a
"GPS NOT FOUND" or "WAITING FOR FIX" state.
"""
from __future__ import annotations

import glob
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import serial  # pyserial
    _HAS_SERIAL = True
except Exception:
    _HAS_SERIAL = False


@dataclass
class GPSFix:
    has_fix: bool = False
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0       # altitude above mean sea level
    hdop: float = 99.9       # horizontal dilution of precision
    sats: int = 0
    speed_kmh: float = 0.0
    heading_deg: float = 0.0
    timestamp_iso: str = ""  # UTC, "YYYY-MM-DD HH:MM:SS"
    device_path: str = ""    # which /dev node we're reading from

    @property
    def accuracy_m(self) -> float:
        """Rough accuracy estimate from HDOP — WiGLE wants meters."""
        # Standard rule-of-thumb: HDOP * UERE (5m typical for consumer GPS)
        return self.hdop * 5.0


_NMEA_PREFIXES = ("$GPGGA", "$GNGGA", "$GLGGA",
                  "$GPRMC", "$GNRMC", "$GLRMC")


def _parse_nmea_lat_lon(coord: str, hemi: str) -> float:
    """NMEA: ddmm.mmmm or dddmm.mmmm. Returns signed decimal degrees."""
    if not coord or not hemi:
        return 0.0
    try:
        # Find the dot to know how many digits are degrees
        dot = coord.index(".")
        # minutes is always 2 digits before the dot
        deg = int(coord[: dot - 2])
        minutes = float(coord[dot - 2:])
        val = deg + minutes / 60.0
        if hemi in ("S", "W"):
            val = -val
        return val
    except (ValueError, IndexError):
        return 0.0


def _parse_gga(parts: list[str], fix: GPSFix) -> None:
    # $GPGGA,hhmmss.ss,lat,N/S,lon,E/W,quality,sats,hdop,alt,M,...
    if len(parts) < 11:
        return
    try:
        quality = int(parts[6] or "0")
    except ValueError:
        quality = 0
    fix.has_fix = quality > 0
    if not fix.has_fix:
        return
    fix.lat = _parse_nmea_lat_lon(parts[2], parts[3])
    fix.lon = _parse_nmea_lat_lon(parts[4], parts[5])
    try:
        fix.sats = int(parts[7] or "0")
    except ValueError:
        pass
    try:
        fix.hdop = float(parts[8] or "99.9")
    except ValueError:
        pass
    try:
        fix.alt_m = float(parts[9] or "0")
    except ValueError:
        pass


def _parse_rmc(parts: list[str], fix: GPSFix) -> None:
    # $GPRMC,hhmmss.ss,A/V,lat,N/S,lon,E/W,speed_kn,heading,ddmmyy,...
    if len(parts) < 10:
        return
    status = parts[2]
    if status != "A":
        return  # void
    fix.lat = _parse_nmea_lat_lon(parts[3], parts[4])
    fix.lon = _parse_nmea_lat_lon(parts[5], parts[6])
    try:
        speed_kn = float(parts[7] or "0")
        fix.speed_kmh = speed_kn * 1.852
    except ValueError:
        pass
    try:
        fix.heading_deg = float(parts[8] or "0")
    except ValueError:
        pass
    # Build ISO timestamp from time + date
    ts = parts[1]
    date = parts[9]
    if len(ts) >= 6 and len(date) >= 6:
        try:
            hh, mm, ss = ts[0:2], ts[2:4], ts[4:6]
            dd, mo, yy = date[0:2], date[2:4], date[4:6]
            fix.timestamp_iso = f"20{yy}-{mo}-{dd} {hh}:{mm}:{ss}"
        except Exception:
            pass


def _parse_line(line: str, fix: GPSFix) -> None:
    if not line.startswith("$"):
        return
    # Strip optional checksum
    body = line.split("*", 1)[0]
    parts = body.split(",")
    head = parts[0]
    if head[3:] == "GGA":
        _parse_gga(parts, fix)
    elif head[3:] == "RMC":
        _parse_rmc(parts, fix)


def _candidate_devices() -> list[str]:
    paths: list[str] = []
    for pat in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*"):
        paths.extend(sorted(glob.glob(pat)))
    return paths


class GPSReader:
    """Background NMEA reader. Thread-safe latest() snapshot."""

    BAUDS = (9600, 115200, 38400)  # LC86L typically 115200 from cold

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fix = GPSFix()
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional["serial.Serial"] = None  # type: ignore[name-defined]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    def latest(self) -> GPSFix:
        with self._lock:
            # Return a copy so callers can mutate freely.
            return GPSFix(**self._fix.__dict__)

    def _open_first_working(self) -> Optional["serial.Serial"]:  # type: ignore[name-defined]
        if not _HAS_SERIAL:
            return None
        for path in _candidate_devices():
            for baud in self.BAUDS:
                try:
                    s = serial.Serial(path, baudrate=baud, timeout=1.0)
                except Exception:
                    continue
                # Probe: read a few lines, look for any NMEA sentence.
                try:
                    deadline = time.time() + 2.0
                    found = False
                    while time.time() < deadline:
                        line = s.readline().decode("ascii", errors="ignore").strip()
                        if line.startswith(_NMEA_PREFIXES):
                            found = True
                            break
                    if found:
                        with self._lock:
                            self._fix.device_path = f"{path}@{baud}"
                        return s
                    s.close()
                except Exception:
                    try:
                        s.close()
                    except Exception:
                        pass
                    continue
        return None

    def _loop(self) -> None:
        while not self._stop:
            self._serial = self._open_first_working()
            if not self._serial:
                # No device found — back off and keep probing.
                time.sleep(2.0)
                continue
            try:
                while not self._stop:
                    raw = self._serial.readline()
                    if not raw:
                        continue
                    try:
                        line = raw.decode("ascii", errors="ignore").strip()
                    except Exception:
                        continue
                    if not line.startswith(_NMEA_PREFIXES):
                        continue
                    with self._lock:
                        # Update in place so partial info from GGA gets
                        # combined with timestamp from RMC etc.
                        _parse_line(line, self._fix)
            except Exception:
                # Device unplugged or read error — drop and re-probe.
                pass
            finally:
                try:
                    if self._serial:
                        self._serial.close()
                except Exception:
                    pass
                self._serial = None
                # Reset fix on disconnect so UI doesn't show stale coords
                # as if we still had GPS.
                with self._lock:
                    self._fix = GPSFix()
                time.sleep(1.0)
