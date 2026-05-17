"""Resource coordination helpers — keeps tools from stomping on each other.

Tools that grab Wi-Fi (WifiAttackView, WardriveView) or Bluetooth
(FlockSeekerView, WardriveView) call these on entry to put the hardware
into a known-good state. Cleanup on exit is each tool's responsibility,
but if a tool crashed and left things weird, the next tool's
known-good-state call will recover.

Every function is idempotent and best-effort — never raises.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
from typing import Iterable


import json
from pathlib import Path

_lock = threading.Lock()
_IN_USE_IFACES: set[str] = set()
_IN_USE_BT: set[str] = set()

_HW_CFG_PATH = Path("/etc/bigbox/hardware.json")

def load_hardware_config() -> dict:
    if _HW_CFG_PATH.exists():
        try:
            with open(_HW_CFG_PATH, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_hardware_config(cfg: dict):
    try:
        _HW_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_HW_CFG_PATH, "w") as f:
            json.dump(cfg, f, indent=4)
    except:
        pass

# Global preference: if True, tactical tools will ignore wlan0 if a USB
# adapter (wlan1+) is present.
_cfg = load_hardware_config()
PREFER_USB_WIFI = _cfg.get("prefer_usb_wifi", True)

def set_prefer_usb_wifi(val: bool):
    global PREFER_USB_WIFI
    PREFER_USB_WIFI = val
    cfg = load_hardware_config()
    cfg["prefer_usb_wifi"] = val
    save_hardware_config(cfg)

def check_dependencies(*binaries: str) -> list[str]:
    """Check if a list of binaries are in the PATH.
    Returns a list of missing binary names."""
    missing = []
    for b in binaries:
        if not shutil.which(b):
            missing.append(b)
    return missing


def request_iface(iface: str) -> bool:
    """Mark a Wi-Fi interface as in-use. Returns False if already busy."""
    with _lock:
        if iface in _IN_USE_IFACES:
            return False
        _IN_USE_IFACES.add(iface)
        return True


def release_iface(iface: str) -> None:
    """Mark a Wi-Fi interface as available."""
    with _lock:
        if iface in _IN_USE_IFACES:
            _IN_USE_IFACES.remove(iface)


def request_bluetooth(hci: str) -> bool:
    """Mark a BT controller as in-use."""
    with _lock:
        if hci in _IN_USE_BT:
            return False
        _IN_USE_BT.add(hci)
        return True


def release_bluetooth(hci: str) -> None:
    """Mark a BT controller as available."""
    with _lock:
        if hci in _IN_USE_BT:
            _IN_USE_BT.remove(hci)


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """Best-effort run; returns (rc, combined_output). Never raises."""
    try:
        out = subprocess.run(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True, timeout=timeout,
        )
        return out.returncode, out.stdout or ""
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out"
    except Exception as e:
        return 1, f"{cmd[0]}: {type(e).__name__}: {e}"


def kill_by_name(*names: str) -> None:
    """SIGTERM (then SIGKILL) processes whose argv0 matches any of names."""
    for n in names:
        _run(["pkill", "-TERM", "-x", n], timeout=3)
    # short grace, then -9 stragglers
    for n in names:
        _run(["pkill", "-KILL", "-x", n], timeout=3)


def list_monitor_ifaces() -> list[str]:
    """Returns names of interfaces currently in monitor mode."""
    rc, out = _run(["iw", "dev"], timeout=3)
    if rc != 0:
        return []
    mons: list[str] = []
    cur_name: str | None = None
    cur_type: str = ""
    for line in out.splitlines():
        m = re.match(r"\s*Interface\s+(\S+)", line)
        if m:
            if cur_name and cur_type == "monitor":
                mons.append(cur_name)
            cur_name = m.group(1)
            cur_type = ""
            continue
        m = re.match(r"\s*type\s+(\S+)", line)
        if m and cur_name:
            cur_type = m.group(1)
    if cur_name and cur_type == "monitor":
        mons.append(cur_name)
    return mons


def ensure_wifi_managed(iface: str | None = None) -> None:
    """Bring Wi-Fi back to a clean managed state.

    1. Kill any leftover capture / injection processes (airodump, aireplay,
       hcxdumptool, hostapd, dnsmasq) that might still hold the radio or
       the network stack.
    2. airmon-ng stop on every monitor-mode interface — covers the case
       where WifiAttackView crashed before its cleanup ran.
    3. nmcli networking on (no-op if already on) so NM owns wlan0 again.
    """
    kill_by_name(
        "airodump-ng",
        "aireplay-ng",
        "airbase-ng",
        "hcxdumptool",
        "hostapd",
        "dnsmasq",
    )
    for mon in list_monitor_ifaces():
        _run(["airmon-ng", "stop", mon], timeout=10)
    if shutil.which("nmcli"):
        _run(["nmcli", "networking", "on"], timeout=5)
        if iface:
            _run(["nmcli", "device", "set", iface, "managed", "yes"],
                 timeout=5)


def list_bluetooth_controllers() -> list[str]:
    """Returns a list of available hciX controller names."""
    import os
    if os.path.exists("/sys/class/bluetooth"):
        try:
            return sorted([d for d in os.listdir("/sys/class/bluetooth") if d.startswith("hci")])
        except OSError:
            pass
    return []


def is_usb_bluetooth(hci: str) -> bool:
    """True if hciX is backed by a USB device (e.g. TP-Link UB500),
    False for the Pi's onboard SoC controller."""
    import os
    try:
        target = os.readlink(f"/sys/class/bluetooth/{hci}")
    except OSError:
        return False
    # Onboard Pi BT resolves under .../platform/soc/...; USB dongles
    # under .../usb<N>/<bus>-<port>/...  The string "/usb" only appears
    # for USB-attached controllers.
    return "/usb" in target


def preferred_bluetooth_controller() -> str | None:
    """Pick the best controller for scanning. Prefers a USB adapter
    (the UB500 has a stronger receiver and external antenna than the
    Pi's onboard) and falls back to whatever's first."""
    controllers = list_bluetooth_controllers()
    if not controllers:
        return None
    for hci in controllers:
        if is_usb_bluetooth(hci):
            return hci
    return controllers[0]


def ensure_bluetooth_on() -> str | None:
    """Power on every BT controller and select the preferred one as
    bluetoothctl's default. Returns the chosen hciX name (or None)."""
    kill_by_name("btmon", "hcidump")
    if shutil.which("rfkill"):
        _run(["rfkill", "unblock", "bluetooth"], timeout=3)

    if not shutil.which("bluetoothctl"):
        return None

    controllers = list_bluetooth_controllers()
    for hci in controllers:
        _run(["bluetoothctl", "select", hci], timeout=3)
        _run(["bluetoothctl", "power", "on"], timeout=5)

    chosen = preferred_bluetooth_controller()
    if chosen:
        _run(["bluetoothctl", "select", chosen], timeout=3)
    return chosen


def stop_bluetooth_scan() -> None:
    """Best-effort: stop any active BT scan we (or another tool) started."""
    if shutil.which("bluetoothctl"):
        _run(["bluetoothctl", "scan", "off"], timeout=3)


def iface_phy(iface: str) -> str | None:
    """Return the phyN name backing a wlan interface, or None on failure."""
    rc, out = _run(["iw", "dev", iface, "info"], timeout=3)
    if rc != 0:
        return None
    m = re.search(r"wiphy\s+(\d+)", out)
    if m:
        return f"phy{m.group(1)}"
    return None


def iface_supports_monitor(iface: str) -> bool:
    """Check `iw phy <phy> info` for monitor mode in the supported list.

    Pi 4's onboard wlan0 (BCM43455 without nexmon firmware) does NOT
    support monitor mode — the iface picker should hide it instead of
    letting the user pick something that's guaranteed to fail.
    """
    phy = iface_phy(iface)
    if not phy:
        # Couldn't determine — be permissive so a working adapter
        # we don't recognize still gets a chance.
        return True
    rc, out = _run(["iw", "phy", phy, "info"], timeout=5)
    if rc != 0:
        return True
    in_modes = False
    for line in out.splitlines():
        s = line.strip()
        if "Supported interface modes" in s:
            in_modes = True
            continue
        if in_modes:
            if s.startswith("*"):
                if s.startswith("* monitor"):
                    return True
            else:
                # Block ended without finding monitor.
                return False
    return False


def get_internet_iface() -> str | None:
    """Returns the name of the interface used for the default gateway (internet)."""
    rc, out = _run(["ip", "route", "show", "default"], timeout=2)
    if rc == 0:
        m = re.search(r"dev\s+(\S+)", out)
        if m:
            return m.group(1)
    return None


def list_monitor_capable_interfaces() -> list[WifiInterface]:
    """Subset of list_wifi_interfaces() filtered to monitor-mode-capable ifaces."""
    return [i for i in list_wifi_interfaces() if iface_supports_monitor(i.name)]


def list_monitor_capable_clients() -> list[str]:
    """Subset of list_wifi_clients() filtered to monitor-mode-capable ifaces.

    Used by views that put the iface into monitor mode so the picker
    only shows adapters that can actually do the job.
    """
    all_clients = list_wifi_clients()
    capable = [c for c in all_clients if iface_supports_monitor(c)]
    
    # Prioritization logic: sort USB/Alfa adapters to the front.
    alfas = list_alfa_ifaces()
    usb_capable = [c for c in capable if c in alfas]
    internal_capable = [c for c in capable if c not in alfas]
    
    if PREFER_USB_WIFI and usb_capable:
        return usb_capable + internal_capable
    return usb_capable + internal_capable


# Set by enable_monitor() on failure so views can surface a specific
# message ("plug in an Alfa") instead of the generic "monitor mode failed".
_last_monitor_error: str | None = None


def last_monitor_error() -> str | None:
    return _last_monitor_error


def is_internet_iface(iface: str) -> bool:
    """True if `iface` is currently providing the default route."""
    return iface == get_internet_iface()


def preferred_monitor_iface() -> str | None:
    """Pick the best adapter for monitor mode.

    Priority:
      1. A USB Alfa adapter if plugged in (preferred per user request — keeps
         the Pi's onboard radio free for the normal managed connection).
      2. The first monitor-capable client that is NOT currently providing
         the default route, so we never tear down the user's internet.
      3. None (caller should surface "plug in an Alfa" to the user).
    """
    alfas = list_alfa_ifaces()
    for a in alfas:
        if iface_supports_monitor(a):
            return a

    inet = get_internet_iface()
    for c in list_wifi_clients():
        if c == inet:
            continue
        if iface_supports_monitor(c):
            return c
    return None


def enable_monitor(iface: str, timeout: float = 15.0, force: bool = False) -> str | None:
    """Put `iface` into monitor mode and return the resulting iface name.

    Refuses to monitor-mode whatever interface is currently providing the
    default route unless `force=True` — putting the Pi's only uplink into
    monitor mode kills SSH, NTP, the update checker, and anything else
    that needs the network. Plug in an Alfa instead.

    Three-step strategy on the chosen iface. Returns the monitor iface name
    on success, or None if every path failed. Robust against:
      - NetworkManager re-claiming the radio mid-setup
      - airmon-ng output format variance across BlueZ versions
      - airmon-ng-less systems

    1. Tell NetworkManager to release the iface so airmon-ng's child
       interface doesn't get yanked back.
    2. Run `airmon-ng start <iface>`. Parse the resulting *mon iface
       from any of the three known output formats; if parsing fails
       fall back to scanning `iw dev` for any interface in monitor mode.
    3. If airmon-ng didn't produce a monitor iface, try the manual
       sequence: `ip link set <iface> down ; iw dev <iface> set type
       monitor ; ip link set <iface> up`. Some adapters won't let
       airmon-ng create a *mon vif but will let you flip the existing
       iface into monitor mode directly.
    """
    global _last_monitor_error
    _last_monitor_error = None

    if not force and is_internet_iface(iface):
        alt = preferred_monitor_iface()
        if alt and alt != iface:
            _last_monitor_error = (
                f"{iface} is providing internet — using {alt} instead"
            )
            print(f"[hardware] {_last_monitor_error}")
            iface = alt
        else:
            _last_monitor_error = (
                f"refusing to put {iface} in monitor mode — it's providing "
                "internet. plug in an Alfa adapter or pass force=True."
            )
            print(f"[hardware] {_last_monitor_error}")
            return None

    if shutil.which("nmcli"):
        _run(["nmcli", "device", "set", iface, "managed", "no"], timeout=5)

    # Step 2 — airmon-ng if available
    if shutil.which("airmon-ng"):
        rc, out = _run(["airmon-ng", "start", iface], timeout=timeout)
        # Try the three known output shapes.
        for pattern in (
            r"monitor mode\s+vif enabled for[^\]]+\]\S+\s+on\s+\[(?:[^\]]+)\]?(\S+)",
            r"\(monitor mode enabled on (\S+?)\)",
            r"monitor mode enabled\s+(\S+)",
        ):
            m = re.search(pattern, out)
            if m:
                return m.group(1)
        # Fallback: scan iw dev for any iface that's now in monitor mode.
        for mon in list_monitor_ifaces():
            return mon

    # Step 3 — manual mode flip
    rc1, _ = _run(["ip", "link", "set", iface, "down"], timeout=5)
    rc2, _ = _run(["iw", "dev", iface, "set", "type", "monitor"], timeout=5)
    rc3, _ = _run(["ip", "link", "set", iface, "up"], timeout=5)
    if rc1 == 0 and rc2 == 0 and rc3 == 0:
        # iface keeps its name when switched in-place
        if iface in list_monitor_ifaces():
            return iface
    _last_monitor_error = f"every path failed for {iface}"
    return None


def list_alfa_ifaces() -> list[str]:
    """Identify Alfa or other high-performance USB adapters.
    
    Looks for common Alfa chipsets (Atheros, Realtek, Mediatek) in USB
    descriptors or by checking the driver associated with the interface.
    """
    alfa_ifaces = []
    clients = list_wifi_clients()
    
    for iface in clients:
        # Check driver via ethtool
        rc, out = _run(["ethtool", "-i", iface], timeout=2)
        if rc == 0:
            driver = ""
            for line in out.splitlines():
                if line.startswith("driver:"):
                    driver = line.split(":")[1].strip()
                    break
            
            # Common Alfa / High-gain drivers
            # rtl88xx: AWUS036ACH, AWUS036ACS, etc.
            # ath9k_htc: AWUS036NHA
            # rt2800usb: AWUS036NH
            # mt76: AWUS036ACM
            if any(d in driver for d in ["rtl88", "ath9k", "rt2800", "mt76"]):
                alfa_ifaces.append(iface)
                continue

        # Check for 'Alfa' in uevent if we can
        uevent_path = f"/sys/class/net/{iface}/device/uevent"
        try:
            with open(uevent_path, "r") as f:
                content = f.read().upper()
                if "ALFA" in content or "AWUS" in content:
                    if iface not in alfa_ifaces:
                        alfa_ifaces.append(iface)
        except: pass

    return alfa_ifaces


from dataclasses import dataclass, field

@dataclass
class WifiInterface:
    name: str
    is_monitor: bool = False
    is_internet: bool = False
    vendor: str = ""


def list_wifi_interfaces() -> list[WifiInterface]:
    """Detailed list of wlan interfaces with metadata."""
    rc, out = _run(["iw", "dev"], timeout=3)
    if rc != 0:
        return []
    
    internet_iface = get_internet_iface()
    alfa_ifaces = list_alfa_ifaces()
    
    ifaces: list[WifiInterface] = []
    cur_name: str | None = None
    cur_type: str = ""
    for line in out.splitlines():
        m = re.match(r"\s*Interface\s+(\S+)", line)
        if m:
            if cur_name is not None:
                vendor = "Alfa/USB" if cur_name in alfa_ifaces else "Internal"
                ifaces.append(WifiInterface(cur_name, cur_type == "monitor", cur_name == internet_iface, vendor))
            cur_name = m.group(1)
            cur_type = ""
            continue
        m = re.match(r"\s*type\s+(\S+)", line)
        if m and cur_name is not None:
            cur_type = m.group(1)
    if cur_name is not None:
        vendor = "Alfa/USB" if cur_name in alfa_ifaces else "Internal"
        ifaces.append(WifiInterface(cur_name, cur_type == "monitor", cur_name == internet_iface, vendor))
    return ifaces


def list_wifi_clients() -> list[str]:
    """Names of wlan ifaces in managed (client) mode — usable for scanning."""
    rc, out = _run(["iw", "dev"], timeout=3)
    if rc != 0:
        return []
    clients: list[str] = []
    cur_name: str | None = None
    cur_type: str = ""
    for line in out.splitlines():
        m = re.match(r"\s*Interface\s+(\S+)", line)
        if m:
            if cur_name and cur_type == "managed":
                clients.append(cur_name)
            cur_name = m.group(1)
            cur_type = ""
            continue
        m = re.match(r"\s*type\s+(\S+)", line)
        if m and cur_name:
            cur_type = m.group(1)
    if cur_name and cur_type == "managed":
        clients.append(cur_name)

    # Sort so USB/Alfa ifaces come first
    alfas = list_alfa_ifaces()
    usb_ifaces = [c for c in clients if c in alfas]
    internal_ifaces = [c for c in clients if c not in alfas]
    
    if PREFER_USB_WIFI and usb_ifaces:
        return usb_ifaces + internal_ifaces
    return usb_ifaces + internal_ifaces
