cat > install_ragnar_kali_pi.sh << 'EOF'
#!/bin/bash
set -e

echo "STATUS: Initializing Ragnar install..."
echo "PROGRESS: 5"

ORIGINAL_REPO="https://github.com/PierreGode/Ragnar.git"
HEADLESS_REPO="https://github.com/DezusAZ/hbp0_ragnar.git"
INSTALL_DIR="/opt/ragnar"

export DEBIAN_FRONTEND=noninteractive

echo "STATUS: Installing system dependencies..."
echo "PROGRESS: 10"
apt update
apt install -y \
  git curl wget ca-certificates \
  python3 python3-pip python3-venv python3-dev \
  build-essential pkg-config rustc cargo \
  libffi-dev libssl-dev libcap-dev \
  libjpeg-dev zlib1g-dev libopenjp2-7-dev libtiff-dev \
  python3-numpy python3-pandas python3-pil python3-psutil python3-cryptography \
  python3-rpi.gpio python3-spidev python3-smbus2 \
  bluetooth bluez libbluetooth-dev python3-bluez \
  network-manager wireless-tools iw rfkill iproute2 net-tools \
  nmap arp-scan traceroute dnsutils iputils-ping sqlite3

echo "STATUS: Preparing temp dirs..."
echo "PROGRESS: 25"
mkdir -p /root/pip-tmp /root/pip-cache
export TMPDIR=/root/pip-tmp
export TEMP=/root/pip-tmp
export TMP=/root/pip-tmp
export PIP_CACHE_DIR=/root/pip-cache

echo "STATUS: Removing old Ragnar install backup..."
echo "PROGRESS: 30"
if [ -d "$INSTALL_DIR" ]; then
  BACKUP="/root/Ragnar.backup.$(date +%Y%m%d-%H%M%S)"
  echo "[*] Existing Ragnar found. Moving to $BACKUP"
  mv "$INSTALL_DIR" "$BACKUP"
fi

echo "STATUS: Cloning Ragnar repository..."
echo "PROGRESS: 40"
git clone "$ORIGINAL_REPO" "$INSTALL_DIR"

echo "STATUS: Applying headless modifications..."
echo "PROGRESS: 50"
rm -rf /tmp/hbp0_ragnar
git clone "$HEADLESS_REPO" /tmp/hbp0_ragnar

cd "$INSTALL_DIR"

echo "[*] Replacing Ragnar.py with headless version..."
if [ -f Ragnar.py ]; then
  mv Ragnar.py Ragnar.py.old
fi

if [ -f /tmp/hbp0_ragnar/Ragnar.py ]; then
  cp /tmp/hbp0_ragnar/Ragnar.py "$INSTALL_DIR/Ragnar.py"
else
  echo "[FAIL] Could not find /tmp/hbp0_ragnar/Ragnar.py"
  exit 1
fi

chmod +x Ragnar.py

echo "STATUS: Creating required folders..."
echo "PROGRESS: 60"
mkdir -p data/logs data/output data/networks/default/db config var backup backup/backups backup/uploads
chmod -R 777 data/logs || true

echo "[*] Creating blank .env if missing..."
touch .env

echo "STATUS: Rebuilding Python venv..."
echo "PROGRESS: 70"
rm -rf venv
python3 -m venv --system-site-packages venv
source venv/bin/activate

python -m pip install --upgrade pip setuptools wheel

echo "STATUS: Installing Python dependencies..."
echo "PROGRESS: 80"
pip install --no-cache-dir \
  rich>=13.0.0 \
  ping3>=4.0.0 \
  get-mac>=0.9.0 \
  paramiko>=3.0.0 \
  smbprotocol>=1.10.0 \
  pysmb>=1.2.0 \
  pymysql>=1.0.0 \
  sqlalchemy>=1.4.0 \
  python-nmap>=0.7.0 \
  flask>=3.0.0 \
  flask-socketio>=5.3.0 \
  flask-cors>=4.0.0 \
  logger>=1.4 \
  openai>=2.0.0 \
  python-prctl>=1.8.1 \
  requests

echo "[*] Optional hardware/display deps..."
pip install --no-cache-dir \
  pisugar>=1.0.0 \
  luma.led_matrix>=1.3.0 \
  luma.core>=2.4.0 || true

echo "STATUS: Enabling Bluetooth service..."
echo "PROGRESS: 90"
systemctl enable bluetooth || true
systemctl start bluetooth || true

echo "STATUS: Running import check..."
echo "PROGRESS: 95"
python3 - << 'PY'
import importlib

mods = [
    "init_shared",
    "comment",
    "webapp_modern",
    "orchestrator",
    "logger",
    "wifi_manager",
    "env_manager",
    "flask",
    "flask_socketio",
    "flask_cors",
    "rich",
    "pandas",
    "numpy",
    "ping3",
    "getmac",
    "paramiko",
    "smbprotocol",
    "smb",
    "pymysql",
    "sqlalchemy",
    "nmap",
    "psutil",
    "openai",
    "cryptography",
    "prctl",
    "requests",
]

failed = []

print("\n=== Ragnar Import Check ===\n")

for m in mods:
    try:
        importlib.import_module(m)
        print(f"[OK] {m}")
    except Exception as e:
        print(f"[FAIL] {m}: {e}")
        failed.append(m)

if failed:
    print("\nFAILED IMPORTS:")
    for f in failed:
        print(" -", f)
    raise SystemExit(1)

print("\nAll required Ragnar headless imports passed.")
PY

echo "PROGRESS: 100"
echo "STATUS: Ragnar Installed Successfully"

echo
echo "=== Ragnar install complete ==="
echo
echo "Run Ragnar:"
echo "cd $INSTALL_DIR"
echo "source venv/bin/activate"
echo "sudo ./venv/bin/python3 Ragnar.py"
echo
echo "Web UI:"
echo "http://127.0.0.1:8000"
echo "http://<device-ip>:8000"
echo
echo "Stop Ragnar:"
echo "sudo pkill -f Ragnar.py"
echo
echo "Data:"
echo "$INSTALL_DIR/data/ragnar.db"
echo "$INSTALL_DIR/data/netkb.csv"
echo "$INSTALL_DIR/data/livestatus.csv"
echo "$INSTALL_DIR/data/logs/"
EOF

chmod +x install_ragnar_kali_pi.sh
sudo ./install_ragnar_kali_pi.sh
