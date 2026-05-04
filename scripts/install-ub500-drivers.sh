#!/usr/bin/env bash
# bigbox driver installer for TP-Link UB500 (RTL8761B)
# This installs the necessary Realtek Bluetooth firmware.
#
# Usage: sudo ./scripts/install-ub500-drivers.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root: sudo $0"
    exit 1
fi

FW_DIR="/lib/firmware/rtl_bt"
mkdir -p "$FW_DIR"

echo "==> downloading RTL8761B firmware for UB500"

# Official linux-firmware mirrors
BASE_URL="https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtl_bt"

# We need the firmware and the config file
files=(
    "rtl8761b_fw.bin"
    "rtl8761b_config.bin"
)

for f in "${files[@]}"; do
    echo "    fetching $f..."
    curl -sSL "$BASE_URL/$f" -o "$FW_DIR/$f"
done

echo "==> firmware installed to $FW_DIR"
echo "==> reloading bluetooth module"

# Try to reload the module to pick up new firmware without reboot
modprobe -r btusb || true
modprobe btusb

echo "==> detection check:"
if lsusb | grep -qi "TP-Link"; then
    echo "    [OK] TP-Link USB device found"
else
    echo "    [!] TP-Link USB device NOT found via lsusb"
fi

if bluetoothctl list | grep -q "Controller"; then
    echo "    [OK] Bluetooth controller(s) detected by BlueZ"
else
    echo "    [!] No controllers seen by bluetoothctl yet. A reboot may be required."
fi

echo "==> installation complete."
