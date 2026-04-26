#!/usr/bin/env bash
# Runs once on the Pi's first boot, fired by systemd.run= in cmdline.txt.
#
# It expands the rootfs (Pi OS doesn't auto-expand if cmdline.txt was
# rewritten), waits for network, runs the bigbox installer, and removes
# itself from cmdline.txt so subsequent boots are normal.
#
# All output is teed to /var/log/bigbox-firstrun.log.
set -uo pipefail

LOG=/var/log/bigbox-firstrun.log
exec > >(tee -a "$LOG") 2>&1

echo "==> bigbox firstrun  $(date -Iseconds)"

# --- expand the root filesystem to fill the SD card ------------------------
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config --expand-rootfs || true
fi

# --- wait for network (DHCP or seeded Wi-Fi) up to 60s ---------------------
echo "==> waiting for network"
for i in $(seq 1 30); do
    if getent hosts deb.debian.org >/dev/null 2>&1 \
       || getent hosts archive.raspberrypi.org >/dev/null 2>&1; then
        echo "network up after ${i}x2s"
        break
    fi
    sleep 2
done

# --- run the regular installer ---------------------------------------------
if [[ -x /opt/bigbox/scripts/install.sh ]]; then
    echo "==> running /opt/bigbox/scripts/install.sh"
    /opt/bigbox/scripts/install.sh || {
        echo "!! installer failed; bigbox will not autostart this boot."
        echo "!! fix and re-run: sudo /opt/bigbox/scripts/install.sh"
    }
else
    echo "!! /opt/bigbox/scripts/install.sh missing"
fi

# --- remove ourselves from cmdline.txt so we don't run again ---------------
# Try Bookworm+ path first, then legacy. Mount briefly if not already up.
BOOT=/boot/firmware
[[ -d "$BOOT" && -f "$BOOT/cmdline.txt" ]] || BOOT=/boot
CMDLINE="$BOOT/cmdline.txt"
if [[ -f "$CMDLINE" ]]; then
    sed -i \
        -e 's| systemd\.run=/usr/local/sbin/bigbox-firstrun\.sh||g' \
        -e 's| systemd\.run=/boot/firstrun\.sh||g' \
        -e 's| systemd\.run_success_action=reboot||g' \
        -e 's| systemd\.unit=kernel-command-line\.target||g' \
        "$CMDLINE"
fi
rm -f /usr/local/sbin/bigbox-firstrun.sh "$BOOT/firstrun.sh" /boot/firstrun.sh 2>/dev/null || true

echo "==> firstrun done. systemd will now reboot."
exit 0
