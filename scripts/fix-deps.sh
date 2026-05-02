#!/bin/bash
# Script to ensure all bigbox core dependencies are installed.
# Designed to be run from the UI with progress feedback.

LOG="/tmp/bigbox-fix-deps.log"
: > "$LOG"

fail() {
    echo "STATUS: ERROR: $1"
    echo "PROGRESS: 100"
    exit 1
}

echo "STATUS: Checking core dependencies..."
echo "PROGRESS: 10"

# List of all tools used by bigbox (sync with install.sh)
PKGS=(
    python3 python3-venv python3-pip python3-pygame python3-lgpio
    libturbojpeg0 nmap arp-scan aircrack-ng iw wireless-tools
    tcpdump mdk4 wifite reaver bully pixiewps tshark hashcat macchanger
    cryptsetup bettercap bluez alsa-utils mpv mgba-sdl mednafen pcsxr
    python3-serial rfkill curl ca-certificates fonts-dejavu-core
    traceroute dnsutils iputils-ping sqlite3 build-essential pkg-config
    hostapd dnsmasq
)

NEEDED=()
for pkg in "${PKGS[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        NEEDED+=("$pkg")
    fi
done

if [ "${#NEEDED[@]}" -eq 0 ]; then
    echo "STATUS: All core tools present"
    echo "PROGRESS: 100"
    echo "Core dependencies are already installed."
    exit 0
fi

echo "STATUS: Installing ${#NEEDED[@]} missing packages..."
echo "PROGRESS: 30"

# Use DEBIAN_FRONTEND=noninteractive to avoid prompts
echo "Updating apt cache..." >>"$LOG"
sudo apt-get update >>"$LOG" 2>&1 || fail "apt-get update failed"
echo "PROGRESS: 50"

echo "Installing ${NEEDED[*]}..." >>"$LOG"
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    "${NEEDED[@]}" >>"$LOG" 2>&1 || fail "apt install failed"

echo "STATUS: Core tools verified"
echo "PROGRESS: 100"
echo "Installation complete."
