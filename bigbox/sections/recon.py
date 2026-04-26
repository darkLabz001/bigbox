"""Recon — host & service discovery."""
from __future__ import annotations

import ipaddress
import socket

from bigbox.sections._icons import load as load_icon, load_background

from bigbox.runner import run_capture
from bigbox.ui import Action, Section, SectionContext


def _local_subnet() -> str:
    """Best-effort guess of the /24 we're attached to. Falls back to localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        return str(net)
    except OSError:
        return "127.0.0.1/32"


def _nmap_ping_sweep(ctx: SectionContext) -> None:
    subnet = _local_subnet()
    ctx.run_streaming(f"nmap ping sweep · {subnet}", ["nmap", "-sn", "-T4", subnet])


def _nmap_quick_self(ctx: SectionContext) -> None:
    ctx.run_streaming("nmap quick · 127.0.0.1", ["nmap", "-T4", "-F", "127.0.0.1"])


def _arp_scan(ctx: SectionContext) -> None:
    ctx.show_arpscan()


def _whoami(ctx: SectionContext) -> None:
    out = run_capture(["id"]) + "\n" + run_capture(["uname", "-a"])
    ctx.show_result("identity", out)


def _cctv_viewer(ctx: SectionContext) -> None:
    ctx.show_cctv()


def _ping_sweep(ctx: SectionContext) -> None:
    ctx.show_pingsweep()


def build() -> Section:
    return Section(
        title="Recon",
        icon="[*]",
        icon_img=load_icon("recon"),
        background_img=load_background("recon"),
        actions=[
            Action("Ping sweep", _ping_sweep, "host discovery"),
            Action("ARP scan", _arp_scan, "local discovery"),
            Action("CCTV Viewer", _cctv_viewer, "live monitoring"),
            Action("Quick scan: localhost", _nmap_quick_self, "nmap -F"),
            Action("Whoami / kernel", _whoami),
        ],
    )
