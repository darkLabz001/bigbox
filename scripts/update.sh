#!/bin/bash
# OTA update script for bigbox.
# Pulls latest changes from GitHub and restarts the service.

set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( dirname "$SCRIPT_DIR" )"
SERVICE_NAME="bigbox.service"

# When the service runs as root but /opt/bigbox is owned by the install user
# (e.g. 'kali'), git refuses to operate ("dubious ownership"). Mark the repo
# as safe globally for the running user. Idempotent.
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

echo "Checking for updates..."
echo "STATUS: Initializing..."
echo "PROGRESS: 5"
cd "$REPO_DIR"

# Ensure we're on a branch and can pull
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Fetch latest
echo "STATUS: Fetching from GitHub..."
echo "PROGRESS: 15"
git fetch origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "New updates found. Syncing..."
    echo "STATUS: Downloading updates..."
    echo "PROGRESS: 40"
    # Reset is more robust than pull for an automated appliance; 
    # it clears local modifications that might cause merge conflicts.
    git reset --hard "origin/$BRANCH"
    
    # Check for missing system dependencies
    echo "STATUS: Checking system dependencies..."
    echo "PROGRESS: 60"
    for pkg in libturbojpeg0 vlc; do
        if ! dpkg -l | grep -q "$pkg"; then
            echo "Installing missing system dependency: $pkg"
            apt-get update && apt-get install -y "$pkg"
        fi
    done
    
    echo "Updating dependencies..."
    echo "STATUS: Updating python packages..."
    echo "PROGRESS: 80"
    if [ -f "requirements.txt" ]; then
        if [ -d "$REPO_DIR/.venv" ]; then
            "$REPO_DIR/.venv/bin/pip" install -r requirements.txt
        else
            echo "Warning: .venv not found in $REPO_DIR. Skipping pip install."
            echo "Try running scripts/install.sh first."
        fi
    fi

    echo "STATUS: Restarting service..."
    echo "PROGRESS: 95"
    if systemctl is-active --quiet "$SERVICE_NAME" || systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "Restarting $SERVICE_NAME..."
        sudo systemctl restart "$SERVICE_NAME"
    else
        echo "Service $SERVICE_NAME not found or not active. Skipping restart."
    fi
    echo "STATUS: Update complete"
    echo "PROGRESS: 100"
    echo "Update complete."
else
    echo "STATUS: Already up to date"
    echo "PROGRESS: 100"
    echo "Already up to date."
fi
