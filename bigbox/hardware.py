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
from typing import Iterable


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
       hcxdumptool) that might still hold the radio.
    2. airmon-ng stop on every monitor-mode interface — covers the case
       where WifiAttackView crashed before its cleanup ran.
    3. nmcli networking on (no-op if already on) so NM owns wlan0 again.
    """
    kill_by_name(
        "airodump-ng",
        "aireplay-ng",
        "airbase-ng",
        "hcxdumptool",
    )
    for mon in list_monitor_ifaces():
        _run(["airmon-ng", "stop", mon], timeout=10)
    if shutil.which("nmcli"):
        _run(["nmcli", "networking", "on"], timeout=5)
        if iface:
            _run(["nmcli", "device", "set", iface, "managed", "yes"],
                 timeout=5)


def ensure_bluetooth_on() -> None:
    """Ensure the BT controller is unblocked and powered.
    Prioritizes hci0 (usually USB) over hci1 (onboard).
    """
    kill_by_name("btmon", "hcidump")
    if shutil.which("rfkill"):
        _run(["rfkill", "unblock", "bluetooth"], timeout=3)
    
    if shutil.which("bluetoothctl"):
        # Explicitly power on both, but try to select hci0 as default
        _run(["bluetoothctl", "select", "hci0"], timeout=3)
        _run(["bluetoothctl", "power", "on"], timeout=5)
        _run(["bluetoothctl", "select", "hci1"], timeout=3)
        _run(["bluetoothctl", "power", "on"], timeout=5)
        # Re-select hci0 to make it the active context
        _run(["bluetoothctl", "select", "hci0"], timeout=3)


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


def list_monitor_capable_clients() -> list[str]:
    """Subset of list_wifi_clients() filtered to monitor-mode-capable ifaces.

    Used by views that put the iface into monitor mode so the picker
    only shows adapters that can actually do the job.
    """
    return [c for c in list_wifi_clients() if iface_supports_monitor(c)]


def enable_monitor(iface: str, timeout: float = 15.0) -> str | None:
    """Put `iface` into monitor mode and return the resulting iface name.

    Three-step strategy. Returns the monitor iface name on success, or
    None if every path failed. Robust against:
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
    return None


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
    return clients
