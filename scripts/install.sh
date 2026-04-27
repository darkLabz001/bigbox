#!/usr/bin/env bash
# bigbox installer for Raspberry Pi 4 + Waveshare GamePi43.
#
# Idempotent: re-running is safe. Logs everything it changes so you can roll
# back by inspecting /etc/bigbox.installed.
#
# Run as root (sudo ./scripts/install.sh) on the Pi.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root: sudo $0"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="/opt/bigbox"
LOG="/etc/bigbox.installed"

echo "==> bigbox install"
echo "    repo:    $REPO_DIR"
echo "    target:  $INSTALL_DIR"

# --- 1. apt packages ----------------------------------------------------------
echo "==> apt packages"
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    python3-pygame \
    python3-lgpio \
    libturbojpeg0 \
    nmap arp-scan \
    aircrack-ng iw wireless-tools \
    bluez \
    alsa-utils \
    mpv \
    mgba-sdl \
    mednafen \
    pcsxr \
    python3-serial rfkill \
    curl ca-certificates \
    fonts-dejavu-core

# --- 2. copy source to /opt/bigbox -------------------------------------------
echo "==> copy source -> $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
# Skip the copy if we're already running from $INSTALL_DIR (sdcard-prepare
# pre-stages the source there, so install.sh would self-copy).
if [[ "$REPO_DIR" != "$INSTALL_DIR" ]]; then
    rsync -a --delete \
        --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
        --exclude='.claude' --exclude='.vscode' --exclude='.idea' \
        --exclude='memory' \
        "$REPO_DIR"/ "$INSTALL_DIR"/
fi

# --- 3. python venv (system pygame is fine; venv keeps deps isolated) --------
echo "==> python venv"
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    python3 -m venv --system-site-packages "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# --- 4. /boot/firmware/config.txt: KMS + audio out 3.5mm ---------------------
echo "==> /boot/firmware/config.txt"
CFG="/boot/firmware/config.txt"
[[ -f "$CFG" ]] || CFG="/boot/config.txt"   # older Raspbian path
backup="$CFG.bigbox.bak"
[[ -f "$backup" ]] || cp "$CFG" "$backup"

ensure_line() {
    local line="$1"
    grep -qxF "$line" "$CFG" || echo "$line" >> "$CFG"
}

# Audio out to 3.5mm jack (GamePi43 routes to its built-in speaker via this).
ensure_line "dtparam=audio=on"

# NOTE: GamePi43 display driver is intentionally NOT touched here.
# The DPI/SPI overlay differs between unit revisions; follow Waveshare's
# wiki ( https://www.waveshare.com/wiki/GamePi43 ) and run their LCD-show
# script for your model. If the screen already works in raspi-config, you
# do not need to do anything more.

# --- 4b. OSINT tools (sherlock + theHarvester + phoneinfoga) ----------------
# Idempotent installer — see scripts/install-osint.sh for what it does.
if [[ -x "$REPO_DIR/scripts/install-osint.sh" ]]; then
    echo "==> OSINT tools"
    "$REPO_DIR/scripts/install-osint.sh"
fi

# --- 5. systemd service + OTA update timer (timer is installed but disabled) -
echo "==> systemd units"
install -m 0644 "$REPO_DIR/scripts/bigbox.service" /etc/systemd/system/bigbox.service
install -m 0644 "$REPO_DIR/scripts/bigbox-update.service" /etc/systemd/system/bigbox-update.service
install -m 0644 "$REPO_DIR/scripts/bigbox-update.timer"   /etc/systemd/system/bigbox-update.timer
systemctl daemon-reload
systemctl enable bigbox.service

# /etc/bigbox is the config-override directory — buttons.toml lives here so
# OTA git resets never touch user-tuned pin maps.
install -d -m 0755 /etc/bigbox

# --- 6. record what we did ----------------------------------------------------
{
    echo "# bigbox install log"
    echo "installed_at=$(date -Iseconds)"
    echo "repo=$REPO_DIR"
    echo "target=$INSTALL_DIR"
    echo "config_txt=$CFG (backed up to $backup)"
} > "$LOG"

cat <<EOF

==> done.

Next steps:
  - confirm the GamePi43 screen works (Waveshare LCD-show driver per their wiki)
  - reboot, or:  sudo systemctl start bigbox
  - inspect logs with:  journalctl -u bigbox -f
  - to stop autostart:  sudo systemctl disable bigbox

OTA updates:
  - one-shot now:    sudo systemctl start bigbox-update.service
  - hourly auto:     sudo systemctl enable --now bigbox-update.timer
  - manual via UI:   Settings -> Check for updates (OTA)
  - your pin map:    /etc/bigbox/buttons.toml (overrides bundled default; survives updates)

If the screen stays blank under bigbox but a desktop session works fine,
you're probably on fbcon/legacy fbdev — switch to KMS in raspi-config
(Advanced Options -> GL driver -> KMS).
EOF
