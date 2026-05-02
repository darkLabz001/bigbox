"""Per-system game-launch history — used to float recently-played
ROMs to the top of the picker.

Persisted at /etc/bigbox/games_state.json so OTA wipes (which scrub
/opt/bigbox) don't lose your "what did I play yesterday" sort.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


STATE_PATH = Path("/etc/bigbox/games_state.json")


def _load() -> dict:
    try:
        with STATE_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"plays": {}}


def _save(data: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[games_state] save failed: {e}")


def record_play(system_key: str, rom_filename: str) -> None:
    """Mark a ROM as just-played. Bumps last_ts and play_count."""
    data = _load()
    plays = data.setdefault("plays", {})
    key = f"{system_key}/{rom_filename}"
    entry = plays.get(key, {"count": 0, "last_ts": 0.0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_ts"] = time.time()
    plays[key] = entry
    _save(data)


def sorted_roms(system_key: str, roms: list[str]) -> list[str]:
    """Return ``roms`` ordered with most-recently-played first, then
    alphabetical for everything not yet played."""
    data = _load()
    plays = data.get("plays", {}) or {}

    def sort_key(name: str) -> tuple:
        entry = plays.get(f"{system_key}/{name}")
        # Recent plays sort by negative timestamp so they land at the
        # top; un-played roms sort by name afterwards.
        if entry:
            return (0, -float(entry.get("last_ts", 0)), name.lower())
        return (1, 0.0, name.lower())

    return sorted(roms, key=sort_key)


def play_count(system_key: str, rom_filename: str) -> int:
    data = _load()
    entry = data.get("plays", {}).get(f"{system_key}/{rom_filename}")
    return int(entry.get("count", 0)) if entry else 0
