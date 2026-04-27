"""Unknown-tracker detector.

Watches BLE advertisements via `btmon` and fingerprints them against
the broadcast formats used by:
  - Apple FindMy / AirTag (separated mode)
  - Apple FindMy / AirTag (near-owner mode)
  - Apple AirPods (case in pairing range)
  - Samsung Galaxy SmartTag

For each detection we record (timestamp, address, RSSI, GPS fix). Apple
intentionally rotates the BLE MAC every ~15 minutes so we can't link a
specific tracker across rotations — instead we check whether *any*
tracker of a given type has been continuously visible while we've
moved enough distance for a stationary tracker to drop out (>100 m
across the window). That's the "this thing is following you, not just
in someone's house we passed once" heuristic.

btmon (bluez-tools) is the source. Bigbox runs as root so we have
access. Same scanner won't run alongside FlockSeeker without a fight —
hardware.ensure_bluetooth_on() is called on view entry to recover.
"""
from __future__ import annotations

import math
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from bigbox.gps import GPSFix, GPSReader


# ---------- tracker fingerprints ----------

@dataclass
class TrackerType:
    key: str
    label: str
    color: tuple[int, int, int]   # RGB for the UI swatch
    company_id: int               # 16-bit BLE manufacturer ID
    payload_prefix: bytes         # bytes that must be at start of mfr payload


TRACKER_TYPES: list[TrackerType] = [
    TrackerType(
        key="airtag",
        label="AirTag (separated)",
        color=(255, 80, 80),
        company_id=0x004C,
        payload_prefix=b"\x12\x19",  # FindMy "separated from owner"
    ),
    TrackerType(
        key="airtag-near",
        label="AirTag (near-owner)",
        color=(255, 140, 80),
        company_id=0x004C,
        payload_prefix=b"\x12\x02",  # FindMy "near owner"
    ),
    TrackerType(
        key="airpods",
        label="AirPods",
        color=(120, 160, 255),
        company_id=0x004C,
        payload_prefix=b"\x07",       # AirPods proximity
    ),
    TrackerType(
        key="smarttag",
        label="Samsung SmartTag",
        color=(120, 200, 80),
        company_id=0x0075,
        payload_prefix=b"\x42\x04",
    ),
]


def _by_key() -> dict[str, TrackerType]:
    return {t.key: t for t in TRACKER_TYPES}


# ---------- detection record ----------

@dataclass
class Detection:
    ts: float
    type_key: str
    address: str
    rssi: int
    lat: float = 0.0
    lon: float = 0.0
    has_fix: bool = False


# ---------- btmon parser ----------

# Manufacturer data appears in the report block as either:
#   Company: Apple, Inc. (76)
#     Type: Unknown (18)
#     Data: 19 10 ...
# or as one chunk:
#   Manufacturer Specific Data (Apple, Inc. <0x004c>) (76)
#   ... 12 19 10 ...
#
# btmon formatting varies a lot across bluez versions, so we tolerate
# multiple forms.

_RE_HCI_BOUNDARY = re.compile(r"^>\s*HCI Event:")
_RE_ADDRESS = re.compile(r"^\s*Address:\s+([0-9A-Fa-f:]{17})")
_RE_RSSI = re.compile(r"RSSI:\s*(-?\d+)\s*dBm")
_RE_COMPANY = re.compile(r"Company:.*\((\d+)\)")
_RE_DATA_LINE = re.compile(r"^\s*Data:\s+(.*)$")


def _parse_hex_bytes(s: str) -> bytes:
    """Accept '12 19 10', '1219 1010', '12:19:10' — whatever btmon emits."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", s)
    if len(cleaned) % 2:
        cleaned = cleaned[:-1]
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return b""


class _ReportParser:
    """Streaming parser. Feed lines via .feed(line); calls .on_match
    every time a parsed advertisement matches a TrackerType."""

    def __init__(self, on_match: Callable[[str, str, int], None]) -> None:
        self.on_match = on_match
        self._addr: Optional[str] = None
        self._rssi: int = -100
        self._company: Optional[int] = None
        self._data: bytes = b""

    def _flush(self) -> None:
        if self._addr and self._company is not None and self._data:
            for t in TRACKER_TYPES:
                if t.company_id == self._company \
                   and self._data.startswith(t.payload_prefix):
                    self.on_match(t.key, self._addr, self._rssi)
                    break
        self._addr = None
        self._company = None
        self._data = b""
        self._rssi = -100

    def feed(self, line: str) -> None:
        if _RE_HCI_BOUNDARY.match(line):
            self._flush()
            return

        m = _RE_ADDRESS.match(line)
        if m:
            self._flush()
            self._addr = m.group(1).lower()
            return

        m = _RE_COMPANY.search(line)
        if m:
            try:
                self._company = int(m.group(1))
            except ValueError:
                self._company = None
            return

        m = _RE_DATA_LINE.match(line)
        if m:
            # "Data:" line might span — accumulate trailing hex from following
            # indented continuation lines too. For now, take just this line —
            # bluez emits the full payload here in current versions.
            self._data = _parse_hex_bytes(m.group(1))
            return

        m = _RE_RSSI.search(line)
        if m:
            try:
                self._rssi = int(m.group(1))
            except ValueError:
                pass


# ---------- detector ----------

@dataclass
class _Window:
    """5-min bucket of detections for a single tracker type."""
    start: float
    count: int = 0
    points: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon)


WINDOW_SECONDS = 60.0   # 1-minute buckets
ALERT_WINDOWS = 15      # 15 windows w/ detections = 15 minutes of contact
MIN_DISTANCE_M = 100.0  # spread of GPS waypoints required


class TrackerDetector:
    def __init__(self, gps: GPSReader) -> None:
        self.gps = gps
        self.detections: list[Detection] = []
        # last seen per address (for "recent" list display)
        self.recent: dict[str, Detection] = {}
        # per-type rolling windows
        self._windows: dict[str, list[_Window]] = {t.key: [] for t in TRACKER_TYPES}

        self._stop = False
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._reader_thread and self._reader_thread.is_alive():
            return
        try:
            self._proc = subprocess.Popen(
                ["btmon"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
                bufsize=1,
                text=True,
            )
        except FileNotFoundError:
            self._proc = None
            return
        self._stop = False
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True
        )
        self._reader_thread.start()
        # Kick the controller into LE scanning mode. Without this, btmon
        # sits silent — the controller has to actively LE-scan to even
        # see advertisements.
        try:
            subprocess.run(
                ["bluetoothctl", "scan", "le", "on"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=3,
            )
        except Exception:
            pass

    def stop(self) -> None:
        self._stop = True
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        try:
            subprocess.run(
                ["bluetoothctl", "scan", "off"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, timeout=3,
            )
        except Exception:
            pass

    # ---------- worker ----------
    def _reader_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        parser = _ReportParser(on_match=self._on_match)
        for line in self._proc.stdout:
            if self._stop:
                break
            parser.feed(line)

    def _on_match(self, type_key: str, address: str, rssi: int) -> None:
        fix = self.gps.latest()
        d = Detection(
            ts=time.time(),
            type_key=type_key,
            address=address,
            rssi=rssi,
            lat=fix.lat,
            lon=fix.lon,
            has_fix=fix.has_fix,
        )
        self.detections.append(d)
        self.recent[address] = d
        # Trim recent dict to most recent ~50
        if len(self.recent) > 50:
            oldest = sorted(self.recent.values(), key=lambda x: x.ts)[: -50]
            for x in oldest:
                self.recent.pop(x.address, None)
        # Trim raw detections list to last 30 min so memory stays bounded
        cutoff = time.time() - 30 * 60
        if len(self.detections) > 2000:
            self.detections = [x for x in self.detections if x.ts >= cutoff]

        self._record_window(type_key, d)

    def _record_window(self, type_key: str, d: Detection) -> None:
        windows = self._windows[type_key]
        bucket_start = (int(d.ts) // int(WINDOW_SECONDS)) * WINDOW_SECONDS
        if not windows or windows[-1].start != bucket_start:
            windows.append(_Window(start=bucket_start))
        w = windows[-1]
        w.count += 1
        if d.has_fix:
            w.points.append((d.lat, d.lon))
        # Keep only the last hour
        cutoff = d.ts - 60 * 60
        windows[:] = [w for w in windows if w.start >= cutoff]

    # ---------- alert ----------
    def alerts(self) -> list[tuple[TrackerType, int, float]]:
        """Returns [(tracker_type, consecutive_minutes, span_meters), ...]
        for each type that's been seen continuously while we've moved at
        least MIN_DISTANCE_M."""
        now = time.time()
        bucket_now = (int(now) // int(WINDOW_SECONDS)) * WINDOW_SECONDS
        out: list[tuple[TrackerType, int, float]] = []
        types_by_key = _by_key()
        for key, windows in self._windows.items():
            if not windows:
                continue
            # Walk back from now; require unbroken windows back through time.
            consecutive = 0
            current = bucket_now
            for w in reversed(windows):
                if w.start == current and w.count > 0:
                    consecutive += 1
                    current -= WINDOW_SECONDS
                elif w.start < current:
                    break  # gap
                # If w.start > current we're in a future bucket; ignore.
            if consecutive < ALERT_WINDOWS:
                continue
            # Compute distance span over the involved windows
            recent_points: list[tuple[float, float]] = []
            cutoff = bucket_now - WINDOW_SECONDS * consecutive
            for w in windows:
                if w.start >= cutoff:
                    recent_points.extend(w.points)
            span = _max_distance(recent_points)
            if span >= MIN_DISTANCE_M:
                out.append((types_by_key[key], consecutive, span))
        return out


# ---------- haversine helper ----------

def _max_distance(points: list[tuple[float, float]]) -> float:
    """Return the maximum pairwise haversine distance (meters) among
    `points`. Cheap O(n) approximation: use min/max lat,lon corners."""
    if len(points) < 2:
        return 0.0
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return _haversine(min(lats), min(lons), max(lats), max(lons))


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
