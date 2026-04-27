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

    - rfkill unblock bluetooth (handles soft-block from a flight-mode toggle)
    - bluetoothctl power on (idempotent)
    - kill stale btmon / hcidump that FlockSeeker might have left behind
    """
    kill_by_name("btmon", "hcidump")
    if shutil.which("rfkill"):
        _run(["rfkill", "unblock", "bluetooth"], timeout=3)
    if shutil.which("bluetoothctl"):
        _run(["bluetoothctl", "power", "on"], timeout=5)


def stop_bluetooth_scan() -> None:
    """Best-effort: stop any active BT scan we (or another tool) started."""
    if shutil.which("bluetoothctl"):
        _run(["bluetoothctl", "scan", "off"], timeout=3)


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
