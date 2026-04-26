#!/usr/bin/env bash
# bigbox driver installer for Alfa AWUS036ACS (RTL8821AU)
# Supports Monitor Mode and Frame Injection.
#
# Usage: sudo ./scripts/install-alfa-drivers.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root: sudo $0"
    exit 1
fi

echo "==> installing dependencies for Alfa RTL8821AU driver"
apt-get update
apt-get install -y raspberrypi-kernel-headers build-essential bc dkms git

# Use the morrownr driver (community standard for pen-testing)
DRIVER_REPO="https://github.com/morrownr/8821au-20210708.git"
BUILD_DIR="/tmp/8821au-build"

echo "==> cloning driver source to $BUILD_DIR"
rm -rf "$BUILD_DIR"
git clone "$DRIVER_REPO" "$BUILD_DIR"

cd "$BUILD_DIR"

echo "==> building and installing driver (this may take several minutes)..."
# The install script handles DKMS setup automatically
# We pass 'No' to the reboot prompt so we can finish the script
printf "n\n" | ./install-driver.sh

echo "==> driver installation complete."
echo "    check status with: dkms status"
echo "    reboot recommended to engage the new module."
