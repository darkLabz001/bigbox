#!/bin/bash
# OTA update script for bigbox.
# Pulls latest changes from GitHub and restarts the service.
#
# Progress markers are emitted AFTER each step completes (not before), and
# weighted by real-world wall time: apt install dominates on first-time
# package additions, so it gets the biggest slice of the bar.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( dirname "$SCRIPT_DIR" )"
SERVICE_NAME="bigbox.service"

# When the service runs as root but /opt/bigbox is owned by the install user
# (e.g. 'kali'), git refuses to operate ("dubious ownership"). Mark the repo
# as safe globally for the running user. Idempotent.
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

echo "STATUS: Initializing..."
echo "PROGRESS: 2"
cd "$REPO_DIR"
BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "STATUS: Fetching from GitHub..."
git fetch origin "$BRANCH"
echo "PROGRESS: 15"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "STATUS: Already up to date"
    echo "PROGRESS: 100"
    echo "Already up to date."
    exit 0
fi

echo "New updates found. Syncing..."
echo "STATUS: Applying updates..."
git reset --hard "origin/$BRANCH" >/dev/null
echo "PROGRESS: 25"

# --- system packages -------------------------------------------------------
# Build the list of missing packages first so we can:
#   (a) skip apt-get update entirely when nothing is missing
#   (b) run a single apt-get install instead of one per package
# This is the slowest variable phase, so it owns the 25..70 slice of the bar.
NEEDED=()
for pkg in libturbojpeg0 mpv; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        NEEDED+=("$pkg")
    fi
done

if [ "${#NEEDED[@]}" -gt 0 ]; then
    echo "STATUS: Installing ${NEEDED[*]}..."
    apt-get update >/dev/null
    echo "PROGRESS: 40"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${NEEDED[@]}" >/dev/null
    echo "PROGRESS: 70"
else
    echo "STATUS: System dependencies up to date"
    echo "PROGRESS: 70"
fi

# --- python packages -------------------------------------------------------
echo "STATUS: Updating python packages..."
if [ -f "requirements.txt" ] && [ -d "$REPO_DIR/.venv" ]; then
    "$REPO_DIR/.venv/bin/pip" install -q -r requirements.txt
elif [ ! -d "$REPO_DIR/.venv" ]; then
    echo "Warning: .venv not found in $REPO_DIR. Skipping pip install."
    echo "Try running scripts/install.sh first."
fi
echo "PROGRESS: 90"

# --- service restart -------------------------------------------------------
echo "STATUS: Restarting bigbox..."
if systemctl is-active --quiet "$SERVICE_NAME" || systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    sudo systemctl restart "$SERVICE_NAME"
else
    echo "Service $SERVICE_NAME not found or not active. Skipping restart."
fi
echo "PROGRESS: 100"
echo "STATUS: Update complete"
echo "Update complete."
