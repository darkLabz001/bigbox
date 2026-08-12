#!/bin/bash
# X session for bigbox on the PocketTerm35. Launched by xinit (see
# bigbox-x.service). Running bigbox under a minimal X server gives DISPLAY=:0
# so the media players (mpv --vo=x11) and emulators work as the code expects.
export DISPLAY=:0

# no screen blanking / power management on a handheld
xset s off -dpms s noblank 2>/dev/null || true

# Force the panel's native 640x480. X can otherwise come up at a larger EDID
# mode, which makes bigbox's fullscreen window (and any tool/emulator windows)
# spill off the visible panel. Pin it so everything fits exactly.
CONN=$(xrandr 2>/dev/null | awk '/ connected/{print $1; exit}')
if [ -n "$CONN" ]; then
    if ! xrandr --output "$CONN" --mode 640x480 2>/dev/null; then
        # mode not offered by EDID: add a standard 640x480@60 modeline
        xrandr --newmode "640x480_60" 25.18 640 656 720 800 480 481 484 500 -hsync +vsync 2>/dev/null
        xrandr --addmode "$CONN" "640x480_60" 2>/dev/null
        xrandr --output "$CONN" --mode "640x480_60" 2>/dev/null
    fi
fi

# hide the X cursor when idle if the tool is available
command -v unclutter >/dev/null 2>&1 && unclutter -idle 1 &

# bigbox owns the whole screen; SDL uses the x11 video driver under X and
# pygame.SCALED fits the 800x480 UI to this 640x480 output.
exec /opt/bigbox/.venv/bin/python -m bigbox
