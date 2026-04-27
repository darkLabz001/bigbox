#!/usr/bin/env bash
# Hide the Raspberry Pi rainbow splash, the kernel boot text, and the
# blinking tty1 console so the user only ever sees:
#   black screen -> bigbox splash (assets/boot.mp3 + Arasaka animation)
#   -> carousel.
#
# Run once on the device:
#   sudo ./scripts/hide-boot.sh
#
# Idempotent. Backs up the original cmdline.txt and config.txt the first
# time it runs (.bigbox.bak). Re-running just no-ops.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root: sudo $0"
    exit 1
fi

# Resolve config path: Bookworm uses /boot/firmware, older uses /boot.
BOOT_DIR="/boot/firmware"
[[ -d "$BOOT_DIR" ]] || BOOT_DIR="/boot"

CONFIG_TXT="$BOOT_DIR/config.txt"
CMDLINE_TXT="$BOOT_DIR/cmdline.txt"

[[ -f "$CONFIG_TXT" ]]  || { echo "no $CONFIG_TXT"; exit 1; }
[[ -f "$CMDLINE_TXT" ]] || { echo "no $CMDLINE_TXT"; exit 1; }

# --- backups (once) ---------------------------------------------------------
[[ -f "$CONFIG_TXT.bigbox.bak"  ]] || cp "$CONFIG_TXT"  "$CONFIG_TXT.bigbox.bak"
[[ -f "$CMDLINE_TXT.bigbox.bak" ]] || cp "$CMDLINE_TXT" "$CMDLINE_TXT.bigbox.bak"

# --- config.txt: kill rainbow splash ---------------------------------------
ensure_kv() {
    local k="$1"; local v="$2"
    if grep -qE "^[#[:space:]]*${k}=" "$CONFIG_TXT"; then
        sed -i "s|^[#[:space:]]*${k}=.*|${k}=${v}|" "$CONFIG_TXT"
    else
        echo "${k}=${v}" >> "$CONFIG_TXT"
    fi
}
ensure_kv disable_splash 1

# --- cmdline.txt: quiet kernel + hide tux + hide cursor + map console -----
# cmdline.txt MUST be a single line. We rebuild it carefully so duplicates
# don't pile up across runs.
read -r CMDLINE < "$CMDLINE_TXT"

# Drop any of our managed flags first so we can re-add deterministically.
for k in quiet splash logo.nologo loglevel vt.global_cursor_default fbcon plymouth.ignore-serial-consoles; do
    CMDLINE=$(echo "$CMDLINE" | sed -E "s/(^| )${k}([= ][^ ]*)?/ /g")
done

# Trim runs of spaces.
CMDLINE=$(echo "$CMDLINE" | tr -s ' ' | sed -E 's/^ //; s/ $//')

# Re-add our boot-quieting flags.
#   loglevel=0  - kernel prints nothing
#   quiet       - same idea, conventional pair
#   splash      - hand off to plymouth if installed (no-op otherwise)
#   logo.nologo - hides the framebuffer tux
#   vt.global_cursor_default=0 - kills the blinking _ on tty1
#   fbcon=map:10 - moves fbcon off all VTs (no console text on screen)
NEW_CMDLINE="$CMDLINE quiet splash logo.nologo loglevel=0 vt.global_cursor_default=0 fbcon=map:10"

# cmdline.txt must end with newline; preserve that.
echo "$NEW_CMDLINE" > "$CMDLINE_TXT"

# --- disable getty on tty1 -------------------------------------------------
# Bigbox owns tty3 via xinit; tty1 normally shows a login prompt that
# briefly flashes during boot. Mask it so it never starts.
if systemctl list-unit-files | grep -q '^getty@.service'; then
    systemctl mask getty@tty1.service >/dev/null 2>&1 || true
fi

cat <<EOF

Boot quieting applied:
  - $CONFIG_TXT  : disable_splash=1
  - $CMDLINE_TXT : added quiet splash logo.nologo loglevel=0
                   vt.global_cursor_default=0 fbcon=map:10
  - getty@tty1   : masked

Reboot once for the changes to take effect.

To revert:
  sudo cp $CONFIG_TXT.bigbox.bak  $CONFIG_TXT
  sudo cp $CMDLINE_TXT.bigbox.bak $CMDLINE_TXT
  sudo systemctl unmask getty@tty1
  sudo reboot
EOF
