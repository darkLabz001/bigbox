#!/usr/bin/env bash
# Deploy bigbox to Pocketer35 (Kali ARM64)
# Run this AFTER Kali Linux has been flashed and booted on the device
#
# Usage:
#   ssh root@pocketer35 'bash -s' < deploy-pocketer35.sh
#   OR:
#   sudo bash deploy-pocketer35.sh  (if running on the device directly)

set -euo pipefail

echo "=== BigBox Pocketer35 Deployment ==="

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "Must run as root: sudo $0"
    exit 1
fi

INSTALL_DIR="/opt/bigbox"
REPO_URL="https://github.com/darkLabz001/bigbox.git"

echo "[1] Cloning bigbox repository..."
rm -rf "$INSTALL_DIR"
git clone "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "[2] Running bigbox installer..."
bash scripts/install.sh

echo "[3] Setting up Pocketer35 display/keyboard..."
# Display/touch/keyboard provisioning lives in its own repo (bootstrap
# config: waveshare-35dpi-5b overlay, i2c for the GT911 touch, dwc2 for the
# RP2040). Fetch it and apply to the mounted boot partition.
BOOT="${BOOT_MOUNT:-/boot/firmware}"
PROVISION_REPO="https://github.com/darkLabz001/pocketterm35-kali.git"
if [[ ! -d /opt/pocketterm35-kali/.git ]]; then
    rm -rf /opt/pocketterm35-kali
    git clone "$PROVISION_REPO" /opt/pocketterm35-kali
fi
bash /opt/pocketterm35-kali/scripts/provision.sh "$BOOT" || true

echo "[4] Installing bigbox systemd service (Pocketer35)..."
install -m 0644 scripts/bigbox-pocketterm.service /etc/systemd/system/bigbox.service
systemctl daemon-reload
systemctl enable bigbox.service
systemctl start bigbox.service

echo ""
echo "✓ BigBox deployed successfully!"
echo ""
echo "Next steps:"
echo "  1. Reboot the Pocketer35"
echo "  2. BigBox will start automatically"
echo "  3. Web UI available at: http://<ip>:8080"
echo ""
echo "View logs:"
echo "  sudo journalctl -u bigbox -f"
