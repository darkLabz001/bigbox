"""Hardware Auto-Probe — Detects the current device and loads the correct GPIO map."""
from __future__ import annotations
import os
from pathlib import Path

def get_device_model() -> str:
    try:
        with open("/proc/device-tree/model", "r") as f:
            return f.read().strip().replace("\x00", "")
    except:
        return "Unknown"

def detect_handheld_type() -> str:
    model = get_device_model()
    if os.path.exists("/boot/config.txt"):
        with open("/boot/config.txt", "r") as f:
            cfg = f.read()
            if "waveshare-gamepi43" in cfg or "dpi24" in cfg:
                return "GAMEPI43"
    if "Zero 2" in model:
        return "ZERO_POCKET"
    return "GENERIC"

def get_best_config_path() -> Path:
    base_dir = Path(__file__).resolve().parents[2] / "config" / "keymaps"
    h_type = detect_handheld_type()
    mapping = {
        "GAMEPI43": "gamepi43.toml",
        "ZERO_POCKET": "pocket_zero.toml",
        "GENERIC": "standard.toml"
    }
    p = base_dir / mapping.get(h_type, "standard.toml")
    if p.is_file():
        return p
    return Path(__file__).resolve().parents[2] / "config" / "buttons.toml"
