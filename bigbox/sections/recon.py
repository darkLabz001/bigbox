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


def _signal_scraper(ctx: SectionContext) -> None:
    """Signal Scraper — Proximity profiling of Wi-Fi and BT devices."""
    ctx.show_signal_scraper()


def _wardrive(ctx: SectionContext) -> None:
    ctx.show_camscan()


def _ping_sweep(ctx: SectionContext) -> None:
    ctx.show_pingsweep()


def _flock_seeker(ctx: SectionContext) -> None:
    ctx.show_flock()


def _wardrive(ctx: SectionContext) -> None:
    ctx.show_wardrive()


def _username_search(ctx: SectionContext) -> None:
    """Sherlock — search a username across hundreds of social networks."""
    def _go(val: str | None) -> None:
        v = (val or "").strip()
        if not v:
            return
        ctx.show_sherlock(v)
    ctx.get_input("Username (e.g. johndoe)", _go)


def _email_harvest(ctx: SectionContext) -> None:
    """theHarvester — emails + subdomains for a target domain.

    Sources are restricted to the no-API-key set that still ships in
    4.10.1. The earlier list (bing, hackertarget, otx, rapiddns,
    urlscan) had been removed from theHarvester's source roster, so
    `-b` returned "invalid source". Verified against
    `theHarvester -h` on 4.10.1.
    """
    def _go(val: str | None) -> None:
        v = (val or "").strip().lower()
        if not v:
            return
        bin_path = "/opt/bigbox/.venv/bin/theHarvester"
        ctx.run_streaming(
            f"theHarvester · {v}",
            ["stdbuf", "-oL",
             bin_path,
             "-d", v,
             "-l", "200",
             "-b", "duckduckgo,crtsh,certspotter"],
        )
    ctx.get_input("Domain (e.g. example.com)", _go)


def _phone_osint(ctx: SectionContext) -> None:
    """phoneinfoga — carrier / region / formatting + free-tier OSINT
    scanners for a phone number.

    The `scan` subcommand only takes -D (--disable), --env-file,
    --plugin, and -n (--number) — the `--no-color` flag from earlier
    was rejected. Output is plain text by default, no escape needed.
    """
    def _go(val: str | None) -> None:
        v = (val or "").strip()
        if not v:
            return
        # Accept "+15551234567" or "5551234567"; phoneinfoga tolerates both.
        ctx.run_streaming(
            f"phoneinfoga · {v}",
            ["phoneinfoga", "scan", "-n", v],
        )
    ctx.get_input("Phone (e.g. +15551234567)", _go)


def _wayback(ctx: SectionContext) -> None:
    target = "https://github.com/darkLabz001/bigbox"
    out = run_capture([
        "sh", "-c",
        f"curl -s --max-time 6 'https://archive.org/wayback/available?url={target}'"
        " | python3 -m json.tool 2>/dev/null"
        " || echo offline",
    ])
    ctx.show_result(f"wayback · {target}", out)


def _whois_repo(ctx: SectionContext) -> None:
    out = run_capture(["sh", "-c", "whois github.com 2>&1 | head -40 || echo 'whois not installed'"])
    ctx.show_result("whois · github.com", out)


def build() -> Section:
    return Section(
        title="Recon",
        icon="[*]",
        icon_img=load_icon("recon"),
        background_img=load_background("recon"),
        actions=[
            Action("Signal Scraper", _signal_scraper, "proximity profiling"),
            Action("FlockSeeker", _flock_seeker, "detect ALPR infrastructure"),
            Action("Wardriving", _wardrive, "GPS-tagged Wi-Fi+BT for WiGLE"),
            Action("Ping sweep", _ping_sweep, "host discovery"),
            Action("ARP scan", _arp_scan, "local discovery"),
            Action("CCTV Viewer", _cctv_viewer, "live monitoring"),
            Action("IP Camera Scanner", _cam_scanner, "find cameras on the LAN"),
            Action("Username search (sherlock)", _username_search, "OSINT"),
            Action("Email harvester (theHarvester)", _email_harvest, "OSINT"),
            Action("Phone OSINT (phoneinfoga)", _phone_osint, "OSINT"),
            Action("Wayback availability check", _wayback),
            Action("WHOIS · github.com", _whois_repo),
            Action("Quick scan: localhost", _nmap_quick_self, "nmap -F"),
            Action("Whoami / kernel", _whoami),
        ],
    )
