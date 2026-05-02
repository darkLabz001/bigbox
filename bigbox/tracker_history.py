"""Long-term tracker sighting history + "is this following me" analysis.

The live :class:`bigbox.trackers.TrackerDetector` only keeps an
in-memory rolling window — useful for the live UI, useless for
"this Tile has shown up at four different places I've been this week."
Each detection is also appended here as a JSONL line so the timeline
survives reboots and accumulates across sessions.

Suspicion model:

  score = unique_locations * unique_days

A tracker that's parked next to your car at home (1 location, many
days) scores low. One that you've seen at 5 different places this
week (5 × 5 = 25) scores high. GPS-less detections still count
toward "days seen" but contribute 0 unique locations — they can't
distinguish "your kitchen" from "the airport" without a fix.

Locations are coarse-bucketed at 0.001° (~110 m at the equator) so
small GPS jitter doesn't manufacture phantom moves.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Local import to avoid a hard cycle.
HISTORY_PATH = Path("loot/tracker_history.jsonl")
LOC_BUCKET = 0.001  # ~110 m


@dataclass
class Sighting:
    ts: float
    type_key: str
    address: str
    rssi: int
    lat: float = 0.0
    lon: float = 0.0
    has_fix: bool = False


@dataclass
class FollowReport:
    address: str
    type_key: str
    sightings: int = 0
    unique_locations: int = 0
    unique_days: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    locations: set[tuple[int, int]] = field(default_factory=set)
    days: set[str] = field(default_factory=set)

    @property
    def score(self) -> int:
        return max(self.unique_locations, 1) * max(self.unique_days, 1)


def append(detection) -> None:
    """Persist one detection. ``detection`` is a Detection from
    bigbox.trackers — typed loosely to keep this module decoupled."""
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": float(getattr(detection, "ts", time.time())),
            "type": str(getattr(detection, "type_key", "?")),
            "mac": str(getattr(detection, "address", "")).lower(),
            "rssi": int(getattr(detection, "rssi", 0)),
            "lat": float(getattr(detection, "lat", 0.0)),
            "lon": float(getattr(detection, "lon", 0.0)),
            "fix": bool(getattr(detection, "has_fix", False)),
        }
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception as e:
        print(f"[tracker_history] append failed: {e}")


def _iter_lines() -> Iterable[Sighting]:
    if not HISTORY_PATH.is_file():
        return
    try:
        with HISTORY_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                yield Sighting(
                    ts=float(d.get("ts", 0)),
                    type_key=str(d.get("type", "?")),
                    address=str(d.get("mac", "")).lower(),
                    rssi=int(d.get("rssi", 0)),
                    lat=float(d.get("lat", 0.0)),
                    lon=float(d.get("lon", 0.0)),
                    has_fix=bool(d.get("fix", False)),
                )
    except OSError:
        return


def analyse(min_score: int = 2) -> list[FollowReport]:
    """Return one FollowReport per MAC seen, sorted by score
    (descending). Reports below ``min_score`` are dropped."""
    by_mac: dict[str, FollowReport] = {}
    for s in _iter_lines():
        if not s.address:
            continue
        rep = by_mac.get(s.address)
        if rep is None:
            rep = FollowReport(address=s.address, type_key=s.type_key,
                               first_seen=s.ts, last_seen=s.ts)
            by_mac[s.address] = rep
        rep.sightings += 1
        rep.first_seen = min(rep.first_seen, s.ts)
        rep.last_seen = max(rep.last_seen, s.ts)
        rep.days.add(datetime.fromtimestamp(s.ts).strftime("%Y-%m-%d"))
        if s.has_fix and (s.lat or s.lon):
            bucket = (int(s.lat / LOC_BUCKET), int(s.lon / LOC_BUCKET))
            rep.locations.add(bucket)

    out: list[FollowReport] = []
    for rep in by_mac.values():
        rep.unique_locations = len(rep.locations)
        rep.unique_days = len(rep.days)
        if rep.score >= min_score:
            out.append(rep)
    out.sort(key=lambda r: (r.score, r.last_seen), reverse=True)
    return out


def render_text(reports: list[FollowReport]) -> str:
    if not reports:
        return ("No suspicious tracker patterns detected.\n\n"
                "Run the Trackers view long enough to gather sightings —\n"
                "this view scores them across days and GPS locations.")
    lines = [f"{len(reports)} tracker(s) seen across multiple days/places:",
             ""]
    for r in reports[:50]:
        last = datetime.fromtimestamp(r.last_seen).strftime("%Y-%m-%d %H:%M")
        first = datetime.fromtimestamp(r.first_seen).strftime("%Y-%m-%d")
        lines.append(
            f"  [{r.score:>3}]  {r.address}  ({r.type_key:>8})  "
            f"sightings={r.sightings:<5}  "
            f"locs={r.unique_locations:<3}  days={r.unique_days:<2}  "
            f"first={first}  last={last}"
        )
    return "\n".join(lines)
