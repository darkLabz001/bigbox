"""Honeypot AP — stand up an open SSID and log every device that
probes / associates / asks for a DHCP lease.

Reverse-recon: walk into a coffee shop, beam an open "free_wifi" or
"Hilton_Lobby" SSID, and watch what tries to grab it. Every probe
request, association, and DHCP request lands in
``loot/honeypot/<ts>/{hostapd.log,dnsmasq.log,clients.jsonl}``.

Ships hostapd + dnsmasq config files into a per-session temp dir so
nothing on the device's persistent config gets touched. Both daemons
need to run as root (we already do — bigbox.service runs as root).

Requires ``hostapd`` and ``dnsmasq`` to be installed; if either is
missing :func:`start` returns ``(None, "missing dep")`` — the view
surfaces this with a "run Toolbox → Verify Core Tools" hint.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


LOOT_BASE = Path("loot/honeypot")
DEFAULT_SSID = "free_wifi"
DEFAULT_CHANNEL = 6
SUBNET = "10.99.99"  # /24 handed out by dnsmasq


@dataclass
class Session:
    iface: str
    ssid: str
    session_dir: Path
    hostapd: subprocess.Popen
    dnsmasq: subprocess.Popen
    started_at: float

    @property
    def hostapd_log(self) -> Path:
        return self.session_dir / "hostapd.log"

    @property
    def dnsmasq_log(self) -> Path:
        return self.session_dir / "dnsmasq.log"


def _missing_deps() -> list[str]:
    return [t for t in ("hostapd", "dnsmasq") if shutil.which(t) is None]


def _write_hostapd_conf(session_dir: Path, iface: str,
                        ssid: str, channel: int) -> Path:
    conf = session_dir / "hostapd.conf"
    conf.write_text(
        f"interface={iface}\n"
        f"driver=nl80211\n"
        f"ssid={ssid}\n"
        f"hw_mode=g\n"
        f"channel={channel}\n"
        f"auth_algs=1\n"
        # No encryption — honeypot is intentionally open.
        # logger_syslog=-1 sends events to journal; we capture stdout
        # via Popen so we don't need that.
        f"logger_stdout=-1\n"
        f"logger_stdout_level=2\n"
    )
    return conf


def _write_dnsmasq_conf(session_dir: Path, iface: str) -> Path:
    conf = session_dir / "dnsmasq.conf"
    conf.write_text(
        f"interface={iface}\n"
        f"bind-interfaces\n"
        f"dhcp-range={SUBNET}.10,{SUBNET}.250,255.255.255.0,12h\n"
        f"dhcp-option=3,{SUBNET}.1\n"          # gateway
        f"dhcp-option=6,{SUBNET}.1\n"          # DNS
        f"log-dhcp\n"
        f"log-queries\n"
        # No upstream DNS — we don't want to actually proxy traffic.
        f"no-resolv\n"
        f"address=/#/{SUBNET}.1\n"             # answer everything with us
        f"pid-file={session_dir}/dnsmasq.pid\n"
    )
    return conf


def _bring_up_iface(iface: str) -> None:
    """Assign the gateway IP to the interface so dnsmasq can bind."""
    for cmd in (
        ["ip", "link", "set", iface, "down"],
        ["ip", "addr", "flush", "dev", iface],
        ["ip", "addr", "add", f"{SUBNET}.1/24", "dev", iface],
        ["ip", "link", "set", iface, "up"],
    ):
        try:
            subprocess.run(cmd, check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def start(iface: str, ssid: str = DEFAULT_SSID,
          channel: int = DEFAULT_CHANNEL) -> tuple[Optional[Session], str]:
    """Spin up hostapd + dnsmasq on ``iface``. Returns (session, msg)."""
    missing = _missing_deps()
    if missing:
        return None, f"missing: {' '.join(missing)} (Toolbox → Verify Core Tools)"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = LOOT_BASE / ts
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return None, f"could not create {session_dir}: {e}"

    _bring_up_iface(iface)

    hostapd_conf = _write_hostapd_conf(session_dir, iface, ssid, channel)
    dnsmasq_conf = _write_dnsmasq_conf(session_dir, iface)

    try:
        hostapd_log = session_dir / "hostapd.log"
        hostapd_proc = subprocess.Popen(
            ["hostapd", str(hostapd_conf)],
            stdout=hostapd_log.open("ab"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    except Exception as e:
        return None, f"hostapd: {e}"

    # Give hostapd a beat to grab the iface before dnsmasq tries to bind.
    time.sleep(0.5)

    try:
        dnsmasq_log = session_dir / "dnsmasq.log"
        dnsmasq_proc = subprocess.Popen(
            ["dnsmasq", "--no-daemon", "-C", str(dnsmasq_conf), "--log-facility=-"],
            stdout=dnsmasq_log.open("ab"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    except Exception as e:
        try:
            os.killpg(os.getpgid(hostapd_proc.pid), signal.SIGTERM)
        except Exception:
            pass
        return None, f"dnsmasq: {e}"

    return Session(
        iface=iface, ssid=ssid, session_dir=session_dir,
        hostapd=hostapd_proc, dnsmasq=dnsmasq_proc,
        started_at=time.time(),
    ), f"AP '{ssid}' on {iface}"


def stop(session: Session) -> None:
    for proc in (session.dnsmasq, session.hostapd):
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    # Strip the gateway IP so the iface is hand-able back to NetworkManager.
    try:
        subprocess.run(["ip", "addr", "flush", "dev", session.iface],
                       check=False, timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def tail_log(path: Path, n: int = 200) -> list[str]:
    """Return the last ``n`` lines of a log file, oldest first."""
    if not path.is_file():
        return []
    try:
        with path.open("rb") as f:
            data = f.read()[-65536:]
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-n:]
    except OSError:
        return []
