"""Colors and fonts. One place to retune the whole look."""
from __future__ import annotations
import json
import os
from pathlib import Path

# Designed for the GamePi43's 800x480 panel.
# The app auto-fits the window to the real panel at startup (so a
# PocketTerm35's 640x480 panel "just works"), and an explicit
# /etc/bigbox/display.json override always wins — see _load_display_config().
SCREEN_W = 800
SCREEN_H = 480
# True once a display.json has been applied; app.py skips auto-detection then.
DISPLAY_OVERRIDE = False

# Default Palette — high-contrast, terminal-ish.
BG          = (10, 12, 18)
BG_ALT      = (18, 22, 32)
FG          = (220, 226, 236)
FG_DIM      = (130, 140, 158)
ACCENT      = (90, 230, 170)
ACCENT_DIM  = (40, 110, 80)
WARN        = (240, 180, 70)
ERR         = (235, 90, 90)
DIVIDER     = (40, 46, 60)
SELECTION   = (90, 230, 170)
SELECTION_BG = (24, 60, 48)

STATUS_BAR_H = 28
TAB_BAR_H    = 40
PADDING      = 14
ROW_H        = 36

# Font sizes
FS_STATUS = 16
FS_TAB    = 20
FS_TITLE  = 28
FS_BODY   = 22
FS_SMALL  = 16

# Custom Assets
ASSETS_BG: str | None = None
ASSETS_ICONS: str | None = None

def _load_active_theme():
    """Load colors from /opt/bigbox/config/themes/active.json or local config."""
    paths = [
        Path("/etc/bigbox/theme.json"),
        Path("/opt/bigbox/config/themes/active.json"),
        Path(__file__).resolve().parents[1] / "config" / "themes" / "active.json"
    ]
    
    for p in paths:
        if p.exists():
            try:
                with p.open("r") as f:
                    data = json.load(f)
                    
                colors = data.get("colors", {})
                global BG, BG_ALT, FG, FG_DIM, ACCENT, ACCENT_DIM, WARN, ERR, DIVIDER, SELECTION, SELECTION_BG
                
                def to_tuple(hex_str: str, fallback: tuple):
                    if not hex_str or not hex_str.startswith("#"): return fallback
                    hex_str = hex_str.lstrip('#')
                    try:
                        return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
                    except:
                        return fallback

                BG = to_tuple(colors.get("BG"), BG)
                BG_ALT = to_tuple(colors.get("BG_ALT"), BG_ALT)
                FG = to_tuple(colors.get("FG"), FG)
                FG_DIM = to_tuple(colors.get("FG_DIM"), FG_DIM)
                ACCENT = to_tuple(colors.get("ACCENT"), ACCENT)
                ACCENT_DIM = to_tuple(colors.get("ACCENT_DIM"), ACCENT_DIM)
                WARN = to_tuple(colors.get("WARN"), WARN)
                ERR = to_tuple(colors.get("ERR"), ERR)
                DIVIDER = to_tuple(colors.get("DIVIDER"), DIVIDER)
                SELECTION = to_tuple(colors.get("SELECTION"), SELECTION)
                SELECTION_BG = to_tuple(colors.get("SELECTION_BG"), SELECTION_BG)
                
                assets = data.get("assets", {})
                global ASSETS_BG, ASSETS_ICONS
                ASSETS_BG = assets.get("background")
                ASSETS_ICONS = assets.get("icons_dir")
                
                break # Loaded successfully
            except Exception as e:
                print(f"[theme] Failed to load {p}: {e}")

_load_active_theme()


def _load_display_config():
    """Override the logical screen size from JSON.

    Lookup order: /etc/bigbox/display.json (survives OTA resets), then the
    repo's config/display.json. Missing/absent values keep the defaults.
    """
    global SCREEN_W, SCREEN_H, DISPLAY_OVERRIDE
    paths = [
        Path("/etc/bigbox/display.json"),
        Path(__file__).resolve().parents[1] / "config" / "display.json",
    ]
    for p in paths:
        if not p.exists():
            continue
        try:
            with p.open("r") as f:
                data = json.load(f)
            SCREEN_W = int(data.get("width", SCREEN_W))
            SCREEN_H = int(data.get("height", SCREEN_H))
            DISPLAY_OVERRIDE = True
            print(f"[theme] display resolution {SCREEN_W}x{SCREEN_H} from {p}")
            break
        except Exception as e:
            print(f"[theme] Failed to load {p}: {e}")

_load_display_config()

