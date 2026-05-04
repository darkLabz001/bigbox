#!/bin/bash
# OTA update script for bigbox.
#
# Goals (in priority order):
#   1. Never hang. Every external command is bounded by a timeout, has its
#      stdin closed, and has its stderr captured to a log.
#   2. Always reach a terminal state. Any failure emits a STATUS:ERROR
#      marker and PROGRESS:100 so the UI doesn't spin forever.
#   3. Survive its own restart. The bigbox restart is deferred via a
#      transient systemd timer so this script can finish emitting its
#      final markers before bigbox (its parent, when invoked from the UI)
#      gets torn down.
#
# Progress markers are emitted AFTER each step, weighted by real wall time:
#   fetch        0..15
#   reset        15..25
#   apt install  25..70   (the slow / variable phase)
#   pip install  70..90
#   restart      90..100

# Note: no "set -e" — we want to handle errors explicitly via fail().

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( dirname "$SCRIPT_DIR" )"
SERVICE_NAME="bigbox.service"
LOG="/tmp/bigbox-ota.log"

# Reset log so each run is self-contained.
: > "$LOG" 2>/dev/null || true

# Run a command silently: stdin from /dev/null, stdout+stderr to log.
# Used for commands whose progress isn't useful but whose failure is.
silent() {
    "$@" </dev/null >>"$LOG" 2>&1
}

# Run a command with a hard timeout. Closes stdin, logs output, returns
# the command's exit code, or 124 if the timeout fires.
bounded() {
    local secs="$1"; shift
    timeout --foreground "$secs" "$@" </dev/null >>"$LOG" 2>&1
}

# Emit a fatal error and exit non-zero. The UI's UpdateView reads STATUS
# and PROGRESS from stdout, so the bar will jump to 100 with the message
# instead of spinning at the failure point.
fail() {
    echo "STATUS: ERROR: $1"
    echo "PROGRESS: 100"
    echo "Update failed. See $LOG"
    exit 1
}

# Mark the repo as safe globally for the running user (works around git's
# "dubious ownership" check when the service runs as root but the working
# tree was checked out by another user). Idempotent and never fatal.
git config --global --add safe.directory "$REPO_DIR" >>"$LOG" 2>&1 || true

echo "STATUS: Initializing..."
echo "PROGRESS: 2"
cd "$REPO_DIR" || fail "cannot cd to $REPO_DIR"

# Pre-flight: on Raspberry Pi installs whose bootloader does *not* load an
# initramfs (the default — `cmdline.txt` boots the kernel directly), the
# `initramfs-tools` postinst hook periodically fails on apt upgrades with
#     mkinitramfs: failed to determine device for /
# That single failed trigger then poisons every subsequent apt run because
# dpkg reports the broken state at the end of each transaction. Since the
# generated initrd is never read by the bootloader anyway, disable its
# regeneration here. Idempotent; only flips when both:
#   1. the conf is currently `yes`
#   2. no `initramfs ... followkernel` line exists in any config.txt
# To re-enable later: sed -i 's/^update_initramfs=no/update_initramfs=yes/' \
#                          /etc/initramfs-tools/update-initramfs.conf
preflight_initramfs() {
    local cfg=/etc/initramfs-tools/update-initramfs.conf
    [ -f "$cfg" ] || return 0
    grep -qE '^update_initramfs=no' "$cfg" && return 0
    if grep -hE '^[[:space:]]*initramfs[[:space:]]+[^#[:space:]]' \
            /boot/firmware/config.txt /boot/config.txt 2>/dev/null \
            | grep -q .; then
        return 0   # bootloader is actually using initramfs — don't touch
    fi
    if sudo sed -i 's/^update_initramfs=yes/update_initramfs=no/' "$cfg" \
            >>"$LOG" 2>&1; then
        echo "Disabled update-initramfs regeneration (bootloader does not use it)" >>"$LOG"
    fi
}
preflight_initramfs

# Ensure system time is roughly correct. Raspberry Pis without RTC often drift,
# which breaks HTTPS and Git (SSL certificate validation fails).
if command -v timedatectl >/dev/null 2>&1; then
    echo "STATUS: Syncing system time..."
    # Try to wait for NTP sync if it's enabled but not synced.
    if ! bounded 15 timedatectl wait-sync; then
        echo "Warning: timedatectl wait-sync timed out." >>"$LOG"
    fi
fi

# Sanity check: if the year is 1970, HTTPS fetch WILL fail.
YEAR=$(date +%Y)
if [ "$YEAR" -lt 2024 ]; then
    echo "Warning: System clock is set to $YEAR. Attempting to force time via NTP..." >>"$LOG"
    sudo timeout 10 sntp -sS pool.ntp.org >>"$LOG" 2>&1 || true
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>>"$LOG")
[ -n "$BRANCH" ] || fail "not on a branch"

echo "STATUS: Fetching from GitHub..."
# Retry loop for unstable connections (e.g. mobile hotspots)
MAX_RETRIES=3
RETRY_COUNT=0
FETCH_SUCCESS=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    # 120s per attempt. 
    if bounded 120 git \
            -c credential.helper= \
            -c core.askpass=true \
            fetch origin "$BRANCH"; then
        FETCH_SUCCESS=1
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
        echo "STATUS: Fetch failed, retrying ($RETRY_COUNT/$MAX_RETRIES)..."
        sleep 5
    fi
done

if [ $FETCH_SUCCESS -eq 0 ]; then
    LAST_ERR=$(tail -n 2 "$LOG" | tr '\n' ' ')
    fail "fetch failed after $MAX_RETRIES attempts: $LAST_ERR"
fi
echo "PROGRESS: 15"

LOCAL=$(git rev-parse HEAD 2>>"$LOG")
REMOTE=$(git rev-parse "origin/$BRANCH" 2>>"$LOG")

if [ "$LOCAL" = "$REMOTE" ]; then
    # Even when git is in sync, the *running* bigbox can be older than the
    # code on disk — happens when a previous OTA pulled new files but its
    # deferred restart never fired (cgroup teardown race, hot-spot wifi
    # drop, etc). Detect that case and force a restart so the user isn't
    # stuck with stale code that "already updated" can't fix.
    NEED_RESTART=0
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        START_EPOCH=$(systemctl show "$SERVICE_NAME" \
                        -p ActiveEnterTimestamp --value 2>/dev/null \
                        | xargs -I{} date -d "{}" +%s 2>/dev/null || echo 0)
        # mtime of the most recently touched .py / .sh file in the repo
        NEWEST_FILE_EPOCH=$(find "$REPO_DIR" -type f \
                                \( -name '*.py' -o -name '*.sh' -o -name '*.html' -o -name '*.toml' \) \
                                -printf '%T@\n' 2>/dev/null \
                              | sort -nr | head -1 | cut -d. -f1)
        NEWEST_FILE_EPOCH=${NEWEST_FILE_EPOCH:-0}
        if [ "$NEWEST_FILE_EPOCH" -gt "$START_EPOCH" ] 2>/dev/null; then
            NEED_RESTART=1
        fi
    fi

    if [ "$NEED_RESTART" -eq 1 ]; then
        echo "STATUS: Restarting bigbox to load synced code..."
        echo "PROGRESS: 70"
        if command -v systemd-run >/dev/null 2>&1; then
            sudo systemd-run --quiet --unit=bigbox-ota-restart \
                --on-active=2 /bin/systemctl restart "$SERVICE_NAME" \
                </dev/null >>"$LOG" 2>&1 \
              || sudo systemctl restart "$SERVICE_NAME" </dev/null >>"$LOG" 2>&1 &
        else
            ( sleep 2; sudo systemctl restart "$SERVICE_NAME" ) \
                </dev/null >>"$LOG" 2>&1 &
            disown
        fi
        echo "PROGRESS: 100"
        echo "STATUS: Already synced — restart scheduled"
        echo "Code already in sync; bigbox will reload in 2s."
        exit 0
    fi

    echo "STATUS: Already up to date"
    echo "PROGRESS: 100"
    echo "Already up to date."
    exit 0
fi

echo "New updates found. Syncing..."
echo "STATUS: Applying updates..."
if ! silent git reset --hard "origin/$BRANCH"; then
    fail "git reset failed"
fi
echo "PROGRESS: 25"

# --- system packages -------------------------------------------------------
# Build the list of missing packages first so we can:
#   (a) skip apt entirely when nothing is missing
#   (b) run a single apt install instead of one per package
#   (c) skip the slow apt-get update too
# True if `$pkg` is installed *or* a virtual package whose providers are
# installed. The virtual-package fallback is what stops dnsutils (which
# resolves to bind9-dnsutils on Debian 13) from being flagged "missing"
# on every single OTA, which used to drag the apt step into every run.
is_pkg_installed() {
    local pkg="$1"
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        return 0
    fi
    local providers prov
    providers=$(apt-cache showpkg "$pkg" 2>/dev/null \
        | awk '/^Reverse Provides:/{p=1;next} /^[A-Z][a-z]+ [A-Z]/{p=0} p && NF {print $1}')
    for prov in $providers; do
        if dpkg-query -W -f='${Status}' "$prov" 2>/dev/null \
                | grep -q "ok installed"; then
            return 0
        fi
    done
    return 1
}

NEEDED=()
for pkg in libturbojpeg0 vlc mpv mgba-sdl mednafen pcsxr python3-serial rfkill hcxdumptool hcxtools dnsmasq hostapd sherlock tcpdump mdk4 wifite reaver bully pixiewps tshark hashcat macchanger cryptsetup bettercap traceroute dnsutils iputils-ping sqlite3 build-essential pkg-config rustc cargo libffi-dev libssl-dev libcap-dev libjpeg-dev zlib1g-dev libopenjp2-7-dev libtiff-dev; do
    is_pkg_installed "$pkg" || NEEDED+=("$pkg")
done

# When apt fails, the last 50 lines of dpkg's term.log usually identify the
# real problem (broken postinst, missing dependency, conffile conflict, etc).
# The OTA-only log isn't useful otherwise — it just sees apt's exit code.
dump_apt_log() {
    {
        echo "--- /var/log/apt/term.log (tail) ---"
        tail -n 50 /var/log/apt/term.log 2>/dev/null
        echo "--- /var/log/apt/history.log (tail) ---"
        tail -n 30 /var/log/apt/history.log 2>/dev/null
    } >>"$LOG" 2>&1
}

apt_install() {
    bounded 600 env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        --no-install-recommends \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        "$@"
}

if [ "${#NEEDED[@]}" -gt 0 ]; then
    echo "STATUS: Installing ${NEEDED[*]}..."
    # 120s for apt-get update, 600s for install. Both run with stdin closed
    # and conffile prompts pinned, so dpkg can't trip us into SIGTTIN.
    if ! bounded 120 apt-get update; then
        dump_apt_log
        fail "apt-get update failed (network? see $LOG)"
    fi
    echo "PROGRESS: 40"
    if ! apt_install "${NEEDED[@]}"; then
        # Clear any half-configured packages and retry once. This handles
        # transient breakage like a postinst that failed on the previous
        # run; without it, every future OTA inherits the broken state.
        echo "STATUS: Recovering dpkg state and retrying..."
        silent dpkg --configure -a
        if ! apt_install "${NEEDED[@]}"; then
            dump_apt_log
            fail "apt install failed (see $LOG for dpkg output)"
        fi
    fi
    echo "PROGRESS: 70"
else
    echo "STATUS: System dependencies up to date"
    echo "PROGRESS: 70"
fi

# --- python packages -------------------------------------------------------
echo "STATUS: Updating python packages..."
if [ -f "requirements.txt" ] && [ -d "$REPO_DIR/.venv" ]; then
    if ! bounded 180 "$REPO_DIR/.venv/bin/pip" install -q -r requirements.txt; then
        fail "pip install failed"
    fi
elif [ ! -d "$REPO_DIR/.venv" ]; then
    echo "Warning: .venv not found in $REPO_DIR. Skipping pip install." >>"$LOG"
fi
echo "PROGRESS: 90"

# --- wordlists -------------------------------------------------------------
echo "STATUS: Preparing wordlists..."
mkdir -p /usr/share/wordlists
if [ ! -f /usr/share/wordlists/rockyou.txt ]; then
    if [ -f /usr/share/wordlists/rockyou.txt.gz ]; then
        echo "Decompressing rockyou.txt.gz..." >>"$LOG"
        gunzip -f /usr/share/wordlists/rockyou.txt.gz >>"$LOG" 2>&1
    else
        echo "Creating fallback wordlist..." >>"$LOG"
        echo "12345678" > /usr/share/wordlists/rockyou.txt
        echo "password" >> /usr/share/wordlists/rockyou.txt
        echo "admin123" >> /usr/share/wordlists/rockyou.txt
    fi
fi
echo "PROGRESS: 95"

# --- service restart -------------------------------------------------------
# This script is usually invoked as a child of bigbox.service. A direct
# `systemctl restart bigbox.service` here would kill the whole cgroup,
# including this script, before we can emit the final progress marker.
# Defer the restart by 2 seconds via a transient systemd unit so we exit
# cleanly first.
echo "STATUS: Restarting bigbox..."
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null \
   || systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    if command -v systemd-run >/dev/null 2>&1; then
        sudo systemd-run --quiet \
            --unit=bigbox-ota-restart \
            --on-active=2 \
            /bin/systemctl restart "$SERVICE_NAME" \
            </dev/null >>"$LOG" 2>&1 || \
            sudo systemctl restart "$SERVICE_NAME" </dev/null >>"$LOG" 2>&1 &
    else
        # Fallback: detached subshell so this script can finish first.
        ( sleep 2; sudo systemctl restart "$SERVICE_NAME" ) \
            </dev/null >>"$LOG" 2>&1 &
        disown
    fi
fi
echo "PROGRESS: 100"
echo "STATUS: Update complete"
echo "Update complete."
