#!/usr/bin/env bash
# flash-pocketer35.sh — flash Kali ARM64 to an SD card AND bake in a first-boot
# auto-installer so BigBox sets itself up and starts on boot (no SSH needed).
#
# Run on your HOST computer (not the PocketTerm35) as root:
#   sudo bash scripts/flash-pocketer35.sh /dev/sdX --yes
#
# What it does:
#   1. Safety-checks the target (removable, not your system disk).
#   2. Flashes the Kali RPi ARM64 image with dd.
#   3. Mounts the rootfs and copies this bigbox tree to /opt/bigbox.
#   4. Installs a one-shot first-boot service that runs install.sh, swaps in
#      the PocketTerm35 systemd unit, enables it, then disables itself.
#   5. Seeds Wi-Fi (so first boot has internet to apt/pip) and enables SSH.
set -euo pipefail

# ---- config (override via env) ---------------------------------------------
IMG="${IMG:-/home/greyhat/Downloads/kali-linux-2026.2-raspberry-pi-arm64.img.xz}"
WIFI_SSID="${WIFI_SSID:-iPhone}"
WIFI_PSK="${WIFI_PSK:-REDACTED}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ---- args ------------------------------------------------------------------
DEV="${1:-}"
CONFIRM="${2:-}"
if [[ -z "$DEV" ]]; then
    echo "usage: sudo bash $0 /dev/sdX --yes"
    echo
    echo "removable devices detected:"
    lsblk -dpno NAME,SIZE,RM,MODEL,TRAN | awk '$3==1 || $5=="usb" || $5=="mmc"{print "   "$0}'
    exit 1
fi
[[ $EUID -eq 0 ]] || { echo "must run as root: sudo bash $0 $*"; exit 1; }
[[ -b "$DEV" ]] || { echo "not a block device: $DEV"; exit 1; }
[[ -f "$IMG" ]] || { echo "image not found: $IMG"; exit 1; }

# ---- safety: never touch the system disk -----------------------------------
ROOTDISK="$(lsblk -no PKNAME "$(findmnt -no SOURCE / )" 2>/dev/null || true)"
if [[ -n "$ROOTDISK" && "$DEV" == *"$ROOTDISK"* ]]; then
    echo "REFUSING: $DEV appears to be your system disk (/dev/$ROOTDISK)."; exit 1
fi
RM="$(lsblk -dno RM "$DEV" 2>/dev/null || echo 0)"
if [[ "$RM" != "1" && "${FORCE:-0}" != "1" ]]; then
    echo "REFUSING: $DEV is not flagged removable. Set FORCE=1 to override."; exit 1
fi
SIZE="$(lsblk -dno SIZE "$DEV")"
LABEL="$(lsblk -no LABEL "$DEV" | grep -v '^$' | head -1 || true)"
echo "================================================================"
echo " TARGET : $DEV  ($SIZE${LABEL:+, label: $LABEL})"
echo " IMAGE  : $IMG"
echo " WIFI   : $WIFI_SSID"
echo " THIS WILL ERASE EVERYTHING ON $DEV"
echo "================================================================"
if [[ "$CONFIRM" != "--yes" ]]; then
    read -r -p "Type ERASE to continue: " a; [[ "$a" == "ERASE" ]] || { echo "aborted"; exit 1; }
fi

# ---- partition-name helper (sda2 vs mmcblk0p2) -----------------------------
part() { case "$DEV" in *[0-9]) echo "${DEV}p$1";; *) echo "${DEV}$1";; esac; }

# ---- unmount anything already mounted from the card ------------------------
for m in $(lsblk -lno MOUNTPOINT "$DEV" | grep -v '^$' || true); do umount -f "$m" || true; done

# ---- 1. flash --------------------------------------------------------------
echo "==> flashing image (this takes several minutes)..."
xzcat "$IMG" | dd of="$DEV" bs=4M conv=fsync status=progress
sync; partprobe "$DEV" || true; sleep 3

BOOT="$(part 1)"; ROOT="$(part 2)"
for i in $(seq 1 15); do [[ -b "$ROOT" ]] && break; sleep 1; partprobe "$DEV" || true; done
[[ -b "$ROOT" ]] || { echo "rootfs partition $ROOT never appeared"; exit 1; }

# ---- 2. mount --------------------------------------------------------------
MNT="$(mktemp -d)"; BMT="$(mktemp -d)"
mount "$ROOT" "$MNT"
mount "$BOOT" "$BMT" 2>/dev/null || true
cleanup(){ umount "$BMT" 2>/dev/null||true; umount "$MNT" 2>/dev/null||true; rmdir "$BMT" "$MNT" 2>/dev/null||true; }
trap cleanup EXIT

# ---- 3. copy bigbox --------------------------------------------------------
echo "==> copying bigbox -> /opt/bigbox"
mkdir -p "$MNT/opt/bigbox"
rsync -a --delete \
    --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    --exclude='*.pyc' "$REPO_DIR"/ "$MNT/opt/bigbox/"

# ---- 4. first-boot installer ----------------------------------------------
echo "==> installing first-boot auto-setup service"
install -d "$MNT/usr/local/sbin"
cat > "$MNT/usr/local/sbin/bigbox-firstboot.sh" <<'FB'
#!/bin/bash
LOG=/var/log/bigbox-firstboot.log
exec >>"$LOG" 2>&1
echo "=== bigbox firstboot $(date) ==="

# 1. wait for network (up to ~4 min) so we can sync time + fetch deps
for i in $(seq 1 80); do ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && break; sleep 3; done

# 2. FIX THE CLOCK — the Pi has no RTC; apt signature checks reject the repo
#    ("Not live until ...") when the clock is wrong. Try NTP, then fall back
#    to an HTTP Date header (works even if NTP/123 is blocked).
timedatectl set-ntp true 2>/dev/null || true
sleep 3
for host in cloudflare.com google.com kali.org; do
  D=$(curl -sI --max-time 8 "http://$host" 2>/dev/null | awk -F': ' 'tolower($1)=="date"{print $2; exit}')
  if [ -n "$D" ]; then date -s "$D" >/dev/null 2>&1 && { echo "clock set from $host -> $(date -u)"; break; }; fi
done

# 3. install bigbox
cd /opt/bigbox || exit 1
bash scripts/install.sh || echo "WARN: install.sh returned non-zero"

# 4. boot to console + install the PocketTerm35 unit (KMSDRM fullscreen tty1)
systemctl set-default multi-user.target || true
install -m 0644 scripts/bigbox-pocketterm.service /etc/systemd/system/bigbox.service
systemctl daemon-reload
systemctl enable bigbox.service

# 5. only finish (disable self) if deps really installed; else retry next boot
if [ -x /opt/bigbox/.venv/bin/python ]; then
  systemctl start bigbox.service || true
  systemctl disable bigbox-firstboot.service || true
  echo "=== firstboot SUCCESS $(date) ==="
else
  echo "=== firstboot INCOMPLETE (no venv) - will retry next boot $(date) ==="
fi
FB
chmod +x "$MNT/usr/local/sbin/bigbox-firstboot.sh"

cat > "$MNT/etc/systemd/system/bigbox-firstboot.service" <<'FBS'
[Unit]
Description=BigBox first-boot auto-installer
After=network-online.target
Wants=network-online.target
ConditionPathExists=/opt/bigbox/scripts/install.sh
ConditionPathExists=!/opt/bigbox/.venv/bin/python

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/bigbox-firstboot.sh
RemainAfterExit=yes
TimeoutStartSec=1200

[Install]
WantedBy=multi-user.target
FBS
# enable it (create the wants symlink directly since systemctl isn't available here)
install -d "$MNT/etc/systemd/system/multi-user.target.wants"
ln -sf ../bigbox-firstboot.service \
    "$MNT/etc/systemd/system/multi-user.target.wants/bigbox-firstboot.service"

# ---- 5. seed Wi-Fi + enable SSH -------------------------------------------
echo "==> seeding Wi-Fi ($WIFI_SSID) and enabling SSH"
install -d -m 0700 "$MNT/etc/NetworkManager/system-connections"
cat > "$MNT/etc/NetworkManager/system-connections/${WIFI_SSID}.nmconnection" <<NM
[connection]
id=$WIFI_SSID
type=wifi
autoconnect=true
[wifi]
mode=infrastructure
ssid=$WIFI_SSID
[wifi-security]
key-mgmt=wpa-psk
psk=$WIFI_PSK
[ipv4]
method=auto
[ipv6]
method=auto
NM
chmod 600 "$MNT/etc/NetworkManager/system-connections/${WIFI_SSID}.nmconnection"
# enable ssh (Kali ships it installed; just make sure it starts)
ln -sf /lib/systemd/system/ssh.service \
    "$MNT/etc/systemd/system/multi-user.target.wants/ssh.service" 2>/dev/null || true

sync
echo
echo "================================================================"
echo " DONE. Insert this card into the PocketTerm35 and power on."
echo " First boot: it connects to Wi-Fi, installs deps, then starts"
echo " BigBox automatically on the 640x480 screen (give it a few min)."
echo " SSH fallback: ssh kali@<ip>   (default pass: kali)"
echo " Install log on device: /var/log/bigbox-firstboot.log"
echo "================================================================"
