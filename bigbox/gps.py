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
import json
import socket
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
    device_path: str = ""    # which /dev node or service we're reading from

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
    if len(head) < 6: return
    if head[3:] == "GGA":
        _parse_gga(parts, fix)
    elif head[3:] == "RMC":
        _parse_rmc(parts, fix)


def _candidate_devices() -> list[str]:
    paths: list[str] = []
    for pat in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/serial/by-id/*"):
        paths.extend(sorted(glob.glob(pat)))
    return paths


class GPSReader:
    """Background GPS reader. Tries gpsd then serial NMEA.
    Thread-safe latest() snapshot.
    """

    _shared: Optional[GPSReader] = None

    @classmethod
    def get_shared(cls) -> GPSReader:
        if cls._shared is None:
            cls._shared = GPSReader()
            cls._shared.start()
        return cls._shared

    BAUDS = (9600, 115200, 38400)
    
    _external_fix: Optional[GPSFix] = None
    _external_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fix = GPSFix()
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional["serial.Serial"] = None  # type: ignore[name-defined]
        self._socket: Optional[socket.socket] = None

    @classmethod
    def inject_external_fix(cls, lat: float, lon: float, alt_m: float = 0.0, hdop: float = 1.0) -> None:
        with cls._external_lock:
            cls._external_fix = GPSFix(
                has_fix=True,
                lat=lat,
                lon=lon,
                alt_m=alt_m,
                hdop=hdop,
                device_path="PHONE",
                timestamp_iso=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._close_all()

    def _close_all(self):
        try:
            if self._serial:
                self._serial.close()
        except Exception: pass
        self._serial = None
        try:
            if self._socket:
                self._socket.close()
        except Exception: pass
        self._socket = None

    def latest(self) -> GPSFix:
        # Check for external fix first (Phone GPS)
        with self._external_lock:
            if self._external_fix and self._external_fix.has_fix:
                try:
                    import datetime
                    fix_time = datetime.datetime.strptime(self._external_fix.timestamp_iso, "%Y-%m-%d %H:%M:%S")
                    now = datetime.datetime.utcnow()
                    if (now - fix_time).total_seconds() < 15:
                        return GPSFix(**self._external_fix.__dict__)
                except Exception:
                    pass

        with self._lock:
            return GPSFix(**self._fix.__dict__)

    def _try_gpsd(self) -> bool:
        """Connect to gpsd and poll for fixes."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(("localhost", 2947))
            
            # Read banner
            banner = s.recv(1024)
            if b"VERSION" not in banner:
                s.close()
                return False
                
            s.sendall(b'?WATCH={"enable":true,"json":true};')
            self._socket = s
            with self._lock:
                self._fix.device_path = "gpsd"
            
            # Loop inside _try_gpsd as long as we have data
            while not self._stop:
                try:
                    line = s.recv(4096).decode("utf-8", errors="ignore")
                    if not line: break
                    for part in line.split("\n"):
                        if not part.strip(): continue
                        try:
                            data = json.loads(part)
                            if data.get("class") == "TPV":
                                with self._lock:
                                    self._fix.has_fix = data.get("mode", 0) >= 2
                                    if self._fix.has_fix:
                                        self._fix.lat = data.get("lat", 0.0)
                                        self._fix.lon = data.get("lon", 0.0)
                                        self._fix.alt_m = data.get("alt", 0.0)
                                        self._fix.speed_kmh = data.get("speed", 0.0) * 3.6
                                        self._fix.heading_deg = data.get("track", 0.0)
                                        t = data.get("time")
                                        if t:
                                            # gpsd: 2023-01-01T12:00:00.000Z
                                            self._fix.timestamp_iso = t.replace("T", " ").split(".")[0]
                            elif data.get("class") == "SKY":
                                with self._lock:
                                    self._fix.sats = data.get("nSat", 0)
                                    self._fix.hdop = data.get("hdop", 99.9)
                        except json.JSONDecodeError:
                            continue
                except socket.timeout:
                    continue
            return True
        except Exception:
            return False
        finally:
            if self._socket:
                self._socket.close()
                self._socket = None
        return False

    def _open_serial(self) -> Optional["serial.Serial"]:  # type: ignore[name-defined]
        if not _HAS_SERIAL:
            return None
        for path in _candidate_devices():
            for baud in self.BAUDS:
                try:
                    s = serial.Serial(path, baudrate=baud, timeout=1.0)
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
                    continue
        return None

    def _loop(self) -> None:
        while not self._stop:
            # 1. Try gpsd
            if self._try_gpsd():
                # If it exited but we're not stopping, it might have crashed.
                time.sleep(2.0)
                continue
                
            # 2. Try serial fallback
            self._serial = self._open_serial()
            if not self._serial:
                time.sleep(2.0)
                continue
            try:
                while not self._stop:
                    raw = self._serial.readline()
                    if not raw: continue
                    line = raw.decode("ascii", errors="ignore").strip()
                    if not line.startswith(_NMEA_PREFIXES): continue
                    with self._lock:
                        _parse_line(line, self._fix)
            except Exception:
                pass
            finally:
                self._close_all()
                with self._lock:
                    self._fix = GPSFix()
                time.sleep(1.0)

