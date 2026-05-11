"""Hacker Achievement System — XP, Leveling, and Ranks.

Tracks operational success (handshakes, wardriving, scans) and assigns
XP and persistent ranks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from dataclasses import dataclass

STATE_PATH = Path("/etc/bigbox/achievements.json")

RANKS = [
    (0, "SCRIPT_KIDDIE"),
    (500, "WARDRIVER"),
    (2500, "NETRUNNER"),
    (10000, "CYBER_GHOST"),
    (50000, "DAEMON_LORD"),
]

@dataclass
class UserState:
    xp: int = 0
    level: int = 1
    total_handshakes: int = 0
    total_nodes: int = 0
    total_bt: int = 0
    total_wardrive_s: float = 0.0
    total_deauths: int = 0
    total_honeypot_creds: int = 0
    max_uptime_s: float = 0.0
    unlocked_milestones: list[str] = None

    def __post_init__(self):
        if self.unlocked_milestones is None:
            self.unlocked_milestones = []

    def get_rank(self) -> str:
        current_rank = RANKS[0][1]
        for xp_req, name in RANKS:
            if self.xp >= xp_req:
                current_rank = name
            else:
                break
        return current_rank

    def next_rank_xp(self) -> int:
        for xp_req, _ in RANKS:
            if xp_req > self.xp:
                return xp_req
        return self.xp

_APP_REF = None

def set_app_ref(app):
    global _APP_REF
    _APP_REF = app

def _load() -> UserState:
    try:
        if STATE_PATH.exists():
            with STATE_PATH.open(encoding="utf-8") as f:
                data = json.load(f)
            # Filter out keys that aren't in UserState
            from dataclasses import fields
            valid_keys = {field.name for field in fields(UserState)}
            filtered_data = {k: v for k, v in data.items() if k in valid_keys}
            return UserState(**filtered_data)
    except Exception:
        pass
    return UserState()

def _save(state: UserState) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.open("w", encoding="utf-8") as f:
            from dataclasses import asdict
            json.dump(asdict(state), f, indent=2)
    except Exception as e:
        print(f"[achievements] save failed: {e}")

def get_state() -> UserState:
    return _load()

def add_xp(amount: int):
    state = _load()
    state.xp += amount
    import math
    new_level = int(math.sqrt(state.xp / 100)) + 1
    if new_level > state.level:
        state.level = new_level
        if _APP_REF:
            _APP_REF.toast(f"LEVEL UP: {new_level} !!")
            _APP_REF.play_notification()
            if hasattr(_APP_REF, "monster"):
                _APP_REF.monster.set_state("HAPPY")
    
    _check_milestones(state)
    _save(state)

def get_milestones(state: UserState):
    """Returns a list of (key, progress_pct, description, unlocked)"""
    defs = [
        ("HANDSHAKE_HUNTER", min(1.0, state.total_handshakes / 10), "Capture 10 handshakes", 10),
        ("WI-FI_WARRIOR", min(1.0, state.total_nodes / 1000), "Find 1,000 nodes", 1000),
        ("BT_STALKER", min(1.0, state.total_bt / 100), "Track 100 BT devices", 100),
        ("ROAD_TRIP", min(1.0, state.total_wardrive_s / 3600), "1 hour of wardriving", 3600),
        ("SHADOW", min(1.0, state.total_deauths / 50), "Perform 50 deauths", 50),
        ("HONEY_POT_MASTER", min(1.0, state.total_honeypot_creds / 10), "Capture 10 credentials", 10),
        ("UPTIME_JUNKIE", min(1.0, state.max_uptime_s / 86400), "24 hours uptime", 86400),
    ]
    out = []
    for key, prog, desc, goal in defs:
        unlocked = key in state.unlocked_milestones
        out.append((key, prog, desc, unlocked))
    return out

def _check_milestones(state: UserState):
    milestones = get_milestones(state)
    
    for key, prog, desc, unlocked in milestones:
        if prog >= 1.0 and not unlocked:
            state.unlocked_milestones.append(key)
            if _APP_REF:
                _APP_REF.toast(f"ACHIEVEMENT: {key}")
                _APP_REF.play_notification()
                if hasattr(_APP_REF, "monster"):
                    _APP_REF.monster.set_state("HAPPY")

def report_handshake():
    state = _load()
    state.total_handshakes += 1
    _save(state)
    if _APP_REF and hasattr(_APP_REF, "monster"):
        _APP_REF.monster.set_state("HAPPY")
    add_xp(100)

def report_node(is_bt: bool = False):
    state = _load()
    if is_bt:
        state.total_bt += 1
        xp = 5
    else:
        state.total_nodes += 1
        xp = 1
    _save(state)
    add_xp(xp)

def report_deauth():
    state = _load()
    state.total_deauths += 1
    _save(state)
    add_xp(20)

def report_honeypot_cred():
    state = _load()
    state.total_honeypot_creds += 1
    _save(state)
    add_xp(150)

def report_uptime(seconds: float):
    state = _load()
    if seconds > state.max_uptime_s:
        state.max_uptime_s = seconds
    _save(state)

def report_wardrive_time(seconds: float):
    state = _load()
    state.total_wardrive_s += seconds
    _save(state)
    # 1 XP per minute
    if seconds > 60:
        add_xp(int(seconds / 60))

