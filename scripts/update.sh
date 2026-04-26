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
cd "$REPO_DIR"

# Ensure we're on a branch and can pull
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Fetch latest
git fetch origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "New updates found. Pulling..."
    git pull origin "$BRANCH"
    
    # Check for missing system dependencies
    if ! dpkg -l | grep -q libturbojpeg0; then
        echo "Installing missing system dependency: libturbojpeg0"
        apt-get update && apt-get install -y libturbojpeg0
    fi
    
    echo "Updating dependencies..."
    if [ -f "requirements.txt" ]; then
        # Use the venv directory relative to the repo
        "$REPO_DIR/.venv/bin/pip" install -r requirements.txt
    fi

    echo "Restarting $SERVICE_NAME..."
    sudo systemctl restart "$SERVICE_NAME"
    echo "Update complete."
else
    echo "Already up to date."
fi
