#!/bin/bash
# X session for bigbox on the PocketTerm35. Launched by xinit (see
# bigbox-x.service). Running bigbox under a minimal X server gives DISPLAY=:0
# so the media players (mpv --vo=x11) and emulators work as the code expects.
export DISPLAY=:0
# no screen blanking / power management on a handheld
xset s off -dpms s noblank 2>/dev/null || true
# hide the X cursor when idle if the tool is available
command -v unclutter >/dev/null 2>&1 && unclutter -idle 1 &
# bigbox owns the whole screen; SDL uses the x11 video driver under X.
exec /opt/bigbox/.venv/bin/python -m bigbox
