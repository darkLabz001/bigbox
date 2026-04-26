#!/usr/bin/env bash
# One-shot: convert /opt/bigbox (rsync-deployed) into a git checkout of
# darkLabz001/bigbox so OTA updates work. Run once on the Pi after the
# upstream repo has been pushed and is at-or-ahead of what's deployed.
#
#   sudo /opt/bigbox/scripts/git-init-pi.sh
#
# Backs up your current /opt/bigbox into /opt/bigbox.pre-git-<timestamp>
# before resetting, in case the upstream is missing something.
set -euo pipefail

REPO_URL="${BIGBOX_REPO_URL:-https://github.com/darkLabz001/bigbox.git}"
INSTALL_DIR=/opt/bigbox

if [[ $EUID -ne 0 ]]; then
    echo "must run as root: sudo $0"
    exit 1
fi

cd "$INSTALL_DIR"

if [[ -d .git ]]; then
    echo "$INSTALL_DIR already has a .git directory."
    echo "    remote: $(git remote get-url origin 2>/dev/null || echo 'none')"
    echo "    HEAD:   $(git rev-parse --short HEAD 2>/dev/null || echo 'none')"
    exit 0
fi

# Save user pin map to /etc/bigbox/ before we let git touch /opt/bigbox.
# This guarantees an upstream config/buttons.toml never clobbers the user's
# real pin layout.
if [[ -f /opt/bigbox/config/buttons.toml && ! -f /etc/bigbox/buttons.toml ]]; then
    install -d -m 0755 /etc/bigbox
    install -m 0644 /opt/bigbox/config/buttons.toml /etc/bigbox/buttons.toml
    echo "==> saved current buttons.toml -> /etc/bigbox/buttons.toml"
fi

backup="/opt/bigbox.pre-git-$(date +%Y%m%dT%H%M%S)"
echo "==> backing up $INSTALL_DIR -> $backup"
cp -a "$INSTALL_DIR" "$backup"

echo "==> initializing git checkout in $INSTALL_DIR"
git init -q
git remote add origin "$REPO_URL"
git fetch --quiet origin main

# Reset working tree to upstream. .venv survives because .gitignore matches.
git reset --hard origin/main
git branch --set-upstream-to=origin/main main 2>/dev/null || git checkout -B main --track origin/main

# Re-install the systemd update timer (in case it landed in this update).
if [[ -f "$INSTALL_DIR/scripts/bigbox-update.service" ]]; then
    install -m 0644 "$INSTALL_DIR/scripts/bigbox-update.service" /etc/systemd/system/bigbox-update.service
    install -m 0644 "$INSTALL_DIR/scripts/bigbox-update.timer"   /etc/systemd/system/bigbox-update.timer
    systemctl daemon-reload
fi

echo
echo "==> done."
echo "    HEAD:   $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
echo "    backup: $backup  (delete with: sudo rm -rf $backup)"
echo
echo "Enable hourly auto-update:"
echo "    sudo systemctl enable --now bigbox-update.timer"
echo
echo "Trigger a one-shot update now:"
echo "    sudo systemctl start bigbox-update.service"
