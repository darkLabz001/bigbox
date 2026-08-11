# Pocketer35 Setup Guide

Complete setup for deploying BigBox to Waveshare PocketTerm35.

## Prerequisites

You need:
- PocketTerm35 device
- SD card (30GB+ recommended)
- Computer with SD card reader
- Network access (WiFi or Ethernet)

## Step 1: Flash Kali Linux to SD Card

Download Kali Linux ARM64 image for Raspberry Pi:
https://www.kali.org/get-kali/

Use one of these tools to flash the image:

**Option A: Balena Etcher (GUI)**
```bash
# Download from: https://www.balena.io/etcher/
# Open Etcher, select image, select SD card, click Flash
```

**Option B: dd command (Linux/Mac)**
```bash
# Find your SD card device
lsblk

# Flash the image (replace /dev/sdX with your device)
sudo dd if=kali-linux-arm64.img of=/dev/sdX bs=4M status=progress conv=fsync
sudo sync
```

After flashing, safely eject the SD card.

## Step 2: Boot PocketTerm35

1. Insert SD card into PocketTerm35
2. Power on the device
3. Wait for first boot (may take 2-3 minutes)
4. Find the device's IP address:
   - Check your router's connected devices
   - Or connect HDMI/keyboard and run: `ip addr`

Default Kali credentials:
- Username: `root`
- Password: `toor`

## Step 3: Deploy BigBox

SSH into the device and run the deployment script:

```bash
# From your computer:
ssh root@<pocketer35-ip> 'curl -s https://raw.githubusercontent.com/darkLabz001/bigbox/main/scripts/deploy-pocketer35.sh | bash'

# OR if SSH fails, download and run manually:
scp scripts/deploy-pocketer35.sh root@<pocketer35-ip>:/tmp/
ssh root@<pocketer35-ip> 'sudo bash /tmp/deploy-pocketer35.sh'
```

This will:
- Clone the latest bigbox code
- Install all dependencies
- Set up Pocketer35 display/keyboard hardware
- Install and enable the bigbox systemd service
- Start bigbox automatically

## Step 4: First Boot

Reboot the device:
```bash
ssh root@<pocketer35-ip> 'sudo reboot'
```

Wait 30 seconds for bigbox to start.

## Access BigBox

**Local display:**
- Power on and wait for boot splash
- Use face buttons (A/B/X/Y) and D-pad to navigate
- Press HK (system button) for menu

**Web UI (from another device):**
- Open: `http://<pocketer35-ip>:8080`
- Full control from laptop/phone

**SSH access:**
```bash
ssh root@<pocketer35-ip>
sudo journalctl -u bigbox -f  # View bigbox logs
```

## Hardware Auto-Detection

BigBox automatically detects Pocketer35:
- ✓ Screen resolution auto-fits (640x480)
- ✓ Keyboard mapped correctly (A/B/X/Y face buttons)
- ✓ GPIO disabled (RP2040 controls over USB)
- ✓ Zero manual config needed

## Troubleshooting

**BigBox won't start:**
```bash
# Check status
sudo systemctl status bigbox

# View logs
sudo journalctl -u bigbox -n 50

# Restart manually
sudo systemctl restart bigbox
```

**Keyboard not working:**
```bash
# Check if RP2040 is detected
lsusb | grep 1209

# Verify keyboard mode is set
grep "keyboard_mode\|gpio_enabled" /opt/bigbox/config/buttons.toml
```

**Display issues:**
```bash
# Check resolution
cat /sys/class/graphics/fb0/virtual_size 2>/dev/null || echo "Check /etc/bigbox/display.json"

# Verify display config loaded
sudo journalctl -u bigbox | grep "panel\|display"
```

**WiFi not connecting:**
```bash
# Check WiFi interface
iwconfig

# Scan networks
sudo iwlist wlan0 scan | grep ESSID

# Connect manually
sudo nmcli dev wifi connect "SSID" password "PASSWORD"
```

## Next Steps

- See README.md for BigBox features and usage
- Check `/opt/bigbox/config/` for configuration options
- Install additional tools via packet manager in BigBox UI

## Uninstall

```bash
sudo systemctl stop bigbox
sudo systemctl disable bigbox
sudo rm /etc/systemd/system/bigbox.service
sudo rm -rf /opt/bigbox
sudo systemctl daemon-reload
```
