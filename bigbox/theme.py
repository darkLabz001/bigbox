"""Colors and fonts. One place to retune the whole look."""
from __future__ import annotations

# Designed for the GamePi43's 800x480 panel.
SCREEN_W = 800
SCREEN_H = 480

# Palette — high-contrast, terminal-ish.
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

# Font sizes — actual font is loaded by the app (pygame's default is fine to start).
FS_STATUS = 16
FS_TAB    = 20
FS_TITLE  = 28
FS_BODY   = 22
FS_SMALL  = 16
