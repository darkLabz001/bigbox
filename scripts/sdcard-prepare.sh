#!/usr/bin/env bash
# Prepare an SD card that already has Raspberry Pi OS Lite (64-bit) flashed
# on it (use Raspberry Pi Imager). This script:
#
#   1. Verifies the target is a removable SD card and not your system disk.
#   2. Mounts boot and rootfs partitions.
#   3. Copies the bigbox source tree to /opt/bigbox.
#   4. Drops a firstrun script onto the rootfs at /usr/local/sbin/ and wires
#      it into cmdline.txt so the install completes on first boot. We use
#      a rootfs path because /boot is mounted differently on different Pi
#      OS versions (legacy: /boot, Bookworm+: /boot/firmware) — a rootfs
#      path is unambiguous.
#   5. Optionally enables SSH and seeds Wi-Fi creds.
#
# Usage:
#   sudo ./scripts/sdcard-prepare.sh /dev/sdX
#   sudo ./scripts/sdcard-prepare.sh --rescue /dev/sdX     # undo a stuck card
#
# Optional env (install mode):
#   BIGBOX_HOSTNAME=bigbox      (only set if explicitly given)
#   BIGBOX_SSH=1                (touch ssh on boot to enable sshd)
#   BIGBOX_WIFI_SSID=...        (set both to seed wpa_supplicant)
#   BIGBOX_WIFI_PSK=...
#   BIGBOX_WIFI_COUNTRY=US

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root: sudo $0 $*"
    exit 1
fi

MODE="install"
if [[ "${1:-}" == "--rescue" ]]; then
    MODE="rescue"
    shift
fi

if [[ $# -ne 1 ]]; then
    echo "usage: sudo $0 [--rescue] /dev/sdX"
    echo
    echo "  install (default): copy bigbox to the card and arm firstrun"
    echo "  --rescue:          undo the firstrun hook from cmdline.txt only"
    echo
    echo "available block devices:"
    lsblk -dpno NAME,SIZE,RM,MODEL,TRAN | awk '$3==1 || $5=="usb" || $5=="mmc" {print "   " $0}'
    exit 1
fi

DEV="$1"
HOSTNAME="${BIGBOX_HOSTNAME:-}"

# --- safety: it must be a block device --------------------------------------
[[ -b "$DEV" ]] || { echo "not a block device: $DEV"; exit 1; }

# --- safety: refuse non-removable disks unless overridden -------------------
RM="$(lsblk -dno RM "$DEV" 2>/dev/null || echo 0)"
if [[ "$RM" != "1" && "${BIGBOX_FORCE:-0}" != "1" ]]; then
    echo "$DEV is not flagged removable. Refusing."
    echo "Override with BIGBOX_FORCE=1 if you are absolutely sure."
    exit 1
fi

# --- safety: refuse the disk that holds the running root --------------------
SYSROOT_SRC="$(findmnt -no SOURCE /)"
SYSROOT_DISK="$(lsblk -dno PKNAME "$SYSROOT_SRC" 2>/dev/null || true)"
if [[ -n "$SYSROOT_DISK" && "$DEV" == "/dev/$SYSROOT_DISK" ]]; then
    echo "$DEV holds your running system. Refusing."
    exit 1
fi

# --- safety: confirm with the user -----------------------------------------
echo "==> mode: $MODE"
echo "==> target device:"
lsblk -po NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT,MODEL "$DEV"
echo
prompt="Write bigbox onto $DEV? Type 'yes' to proceed: "
[[ "$MODE" == "rescue" ]] && prompt="Roll back firstrun hook on $DEV? Type 'yes' to proceed: "
read -rp "$prompt" ans
[[ "$ans" == "yes" ]] || { echo "aborted."; exit 1; }

# --- partition naming (sdX1 vs mmcblk0p1) ----------------------------------
case "$DEV" in
    *mmcblk*|*nvme*) P="${DEV}p" ;;
    *)               P="${DEV}"  ;;
esac
BOOT_PART="${P}1"
ROOT_PART="${P}2"
[[ -b "$BOOT_PART" ]] || { echo "missing boot partition $BOOT_PART"; exit 1; }
[[ -b "$ROOT_PART" ]] || { echo "missing root partition $ROOT_PART"; exit 1; }

# --- ensure neither partition is mounted -----------------------------------
for p in "$BOOT_PART" "$ROOT_PART"; do
    if findmnt -no TARGET "$p" >/dev/null 2>&1; then
        umount "$p" || { echo "could not unmount $p"; exit 1; }
    fi
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MNT_BOOT="$(mktemp -d)"
MNT_ROOT="$(mktemp -d)"
cleanup() {
    sync
    umount "$MNT_BOOT" 2>/dev/null || true
    umount "$MNT_ROOT" 2>/dev/null || true
    rmdir "$MNT_BOOT" "$MNT_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> mounting partitions"
mount "$BOOT_PART" "$MNT_BOOT"
mount "$ROOT_PART" "$MNT_ROOT"

CMDLINE="$MNT_BOOT/cmdline.txt"
[[ -f "$CMDLINE" ]] || { echo "boot partition missing cmdline.txt"; exit 1; }

# Strip any prior bigbox firstrun additions from cmdline.txt — both old
# /boot/ and new /usr/local/sbin/ paths.
echo "==> cleaning prior firstrun hooks from cmdline.txt"
cur="$(tr -d '\n' < "$CMDLINE")"
cur="${cur// systemd.run=\/boot\/firstrun.sh/}"
cur="${cur// systemd.run=\/usr\/local\/sbin\/bigbox-firstrun.sh/}"
cur="${cur// systemd.run_success_action=reboot/}"
cur="${cur// systemd.unit=kernel-command-line.target/}"
printf '%s\n' "$cur" > "$CMDLINE"
# Also nuke any stray firstrun.sh sitting on the boot partition.
rm -f "$MNT_BOOT/firstrun.sh"

if [[ "$MODE" == "rescue" ]]; then
    # Also drop the sbin script if it was installed.
    rm -f "$MNT_ROOT/usr/local/sbin/bigbox-firstrun.sh"
    echo "==> rescue done. Card will boot normally next time."
    exit 0
fi

# --- sanity: rootfs looks like a Pi OS install ------------------------------
if [[ ! -d "$MNT_ROOT/etc" || ! -d "$MNT_ROOT/home" ]]; then
    echo "rootfs partition does not look like a Linux system; aborting."
    exit 1
fi

# --- 1. copy source tree to /opt/bigbox -------------------------------------
echo "==> copying bigbox -> /opt/bigbox on rootfs"
mkdir -p "$MNT_ROOT/opt/bigbox"
rsync -a --delete \
    --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    --exclude='dist' --exclude='build' --exclude='*.egg-info' \
    --exclude='.claude' --exclude='.vscode' --exclude='.idea' \
    --exclude='memory' \
    --exclude='.DS_Store' --exclude='*.swp' \
    "$REPO_DIR"/ "$MNT_ROOT/opt/bigbox"/
chmod +x "$MNT_ROOT/opt/bigbox/scripts/install.sh"

# --- 2. hostname (only if explicitly requested) -----------------------------
if [[ -n "$HOSTNAME" ]]; then
    echo "==> hostname: $HOSTNAME"
    echo "$HOSTNAME" > "$MNT_ROOT/etc/hostname"
    if grep -q '^127\.0\.1\.1' "$MNT_ROOT/etc/hosts"; then
        sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$HOSTNAME/" "$MNT_ROOT/etc/hosts"
    else
        echo -e "127.0.1.1\t$HOSTNAME" >> "$MNT_ROOT/etc/hosts"
    fi
else
    echo "==> keeping existing hostname ($(cat "$MNT_ROOT/etc/hostname" 2>/dev/null || echo unknown))"
fi

# --- 3. enable SSH on first boot --------------------------------------------
if [[ "${BIGBOX_SSH:-0}" == "1" ]]; then
    echo "==> enabling SSH"
    : > "$MNT_BOOT/ssh"
fi

# --- 4. seed Wi-Fi if creds were given --------------------------------------
if [[ -n "${BIGBOX_WIFI_SSID:-}" && -n "${BIGBOX_WIFI_PSK:-}" ]]; then
    echo "==> seeding Wi-Fi (${BIGBOX_WIFI_SSID})"
    cat > "$MNT_BOOT/wpa_supplicant.conf" <<EOF
country=${BIGBOX_WIFI_COUNTRY:-US}
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={
    ssid="${BIGBOX_WIFI_SSID}"
    psk="${BIGBOX_WIFI_PSK}"
    key_mgmt=WPA-PSK
}
EOF
    chmod 600 "$MNT_BOOT/wpa_supplicant.conf"
fi

# --- 5. firstrun on the rootfs (path-stable across Pi OS versions) ----------
echo "==> installing /usr/local/sbin/bigbox-firstrun.sh on rootfs"
install -d -m 0755 "$MNT_ROOT/usr/local/sbin"
install -m 0755 "$REPO_DIR/scripts/firstrun.sh" "$MNT_ROOT/usr/local/sbin/bigbox-firstrun.sh"

# Append the systemd.run= directive (cmdline.txt was already cleaned above).
cur="$(tr -d '\n' < "$CMDLINE")"
printf '%s systemd.run=/usr/local/sbin/bigbox-firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n' \
    "$cur" > "$CMDLINE"

echo "==> done. Eject and put the card in the Pi."
echo
echo "First boot will: connect to network, run install.sh, reboot, then bigbox autostarts."
echo "Watch progress with:  sudo journalctl -fu bigbox  (post-reboot)"
echo "Install log lives at: /var/log/bigbox-firstrun.log"
