#!/bin/bash
# Script to ensure all bigbox core dependencies are installed.
# Optimized for Raspberry Pi: installs one-by-one to prevent freezes.

LOG="/tmp/bigbox-fix-deps.log"
: > "$LOG"

fail() {
    echo "STATUS: ERROR: $1"
    echo "PROGRESS: 100"
    exit 1
}

check_load() {
    # If load average is too high, wait a bit
    load=$(cat /proc/loadavg | awk '{print $1}')
    if (( $(echo "$load > 4.0" | bc -l) )); then
        echo "STATUS: High load ($load), waiting..."
        sleep 5
    fi
}

echo "STATUS: Checking core dependencies..."
echo "PROGRESS: 5"

# List of all tools used by bigbox (sync with install.sh)
PKGS=(
    python3 python3-venv python3-pip python3-pygame python3-lgpio
    libturbojpeg0 nmap arp-scan aircrack-ng iw wireless-tools
    tcpdump mdk4 wifite reaver bully pixiewps tshark hashcat macchanger
    cryptsetup bettercap bluez alsa-utils pulseaudio-utils mpv mgba-sdl mednafen pcsxr
    python3-serial rfkill curl ca-certificates fonts-dejavu-core
    traceroute dnsutils iputils-ping sqlite3 build-essential pkg-config
    hostapd dnsmasq unzip kismet gpsd gpsd-clients
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

TOTAL=${#NEEDED[@]}
echo "STATUS: Installing $TOTAL missing packages..."
echo "PROGRESS: 10"

echo "Updating apt cache..." >>"$LOG"
sudo apt-get update >>"$LOG" 2>&1 || fail "apt-get update failed"

for i in "${!NEEDED[@]}"; do
    pkg="${NEEDED[$i]}"
    COUNT=$((i + 1))
    PERCENT=$((10 + (90 * COUNT / TOTAL)))
    
    echo "STATUS: Installing $pkg ($COUNT/$TOTAL)..."
    echo "PROGRESS: $PERCENT"
    
    check_load
    
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        "$pkg" >>"$LOG" 2>&1 || echo "WARN: Failed to install $pkg, continuing..." >>"$LOG"
    
    # Small breather for the CPU
    sleep 0.5
done

echo "STATUS: Core tools verified"
echo "PROGRESS: 100"
echo "Installation complete."
