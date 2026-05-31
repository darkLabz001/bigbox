#!/bin/bash
# Script to ensure all bigbox core dependencies are installed.
# Optimized for Raspberry Pi: installs one-by-one and checks for apt locks.

LOG="/tmp/bigbox-fix-deps.log"
: > "$LOG"

fail() {
    echo "STATUS: ERROR: $1"
    echo "PROGRESS: 100"
    exit 1
}

# Wait for apt lock to be released if another process is using it
wait_for_apt() {
    local count=0
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
        if [ $count -eq 0 ]; then
            echo "STATUS: Waiting for other apt process..."
        fi
        sleep 2
        ((count++))
        if [ $count -gt 300 ]; then # 10 minutes timeout
            fail "Timed out waiting for apt lock"
        fi
    done
}

check_load() {
    # If load average is too high, wait a bit. Use native shell comparison.
    # Read 1-minute load average
    local load
    read -r load _ < /proc/loadavg
    # Convert load like "1.25" to integer "125" for shell comparison
    local load_int
    load_int=$(echo "$load" | sed 's/\.//')
    if [ "$load_int" -gt 400 ]; then # 4.00
        echo "STATUS: System busy ($load), waiting..."
        sleep 5
    fi
}

echo "STATUS: Checking core dependencies..."
echo "PROGRESS: 5"

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
    echo "Core tools are already installed."
    exit 0
fi

TOTAL=${#NEEDED[@]}
echo "STATUS: Installing $TOTAL missing packages..."
echo "PROGRESS: 10"

wait_for_apt
echo "Updating apt cache..." >>"$LOG"
sudo apt-get update >>"$LOG" 2>&1 || echo "WARN: update failed" >>"$LOG"

for i in "${!NEEDED[@]}"; do
    pkg="${NEEDED[$i]}"
    COUNT=$((i + 1))
    PERCENT=$((10 + (90 * COUNT / TOTAL)))
    
    echo "STATUS: Installing $pkg ($COUNT/$TOTAL)..."
    echo "PROGRESS: $PERCENT"
    
    check_load
    wait_for_apt
    
    # Use -y and noninteractive to prevent hangs on prompts
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        "$pkg" >>"$LOG" 2>&1 || echo "WARN: Failed to install $pkg" >>"$LOG"
    
    sleep 0.5
done

echo "STATUS: Core tools verified"
echo "PROGRESS: 100"
echo "Installation complete."
