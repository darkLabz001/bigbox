"""Wireless — Wi-Fi recon (requires root for monitor-mode actions)."""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _wifi_interfaces(ctx: SectionContext) -> None:
    ctx.show_result("wifi interfaces", run_capture(["iw", "dev"]))


def _wifi_scan(ctx: SectionContext) -> None:
    # Default Pi 4 onboard adapter is wlan0; user with external should adjust.
    ctx.run_streaming("scan · wlan0", ["sudo", "iw", "dev", "wlan0", "scan"])


def _link(ctx: SectionContext) -> None:
    ctx.show_result("link", run_capture(["iw", "dev", "wlan0", "link"]))


def _handshake_deauth(ctx: SectionContext) -> None:
    ctx.show_wifi_attack()


def _wifi_multi_tool(ctx: SectionContext) -> None:
    ctx.show_wifi_multi_tool()


def _wifite(ctx: SectionContext) -> None:
    """Wifite — Interactive automated wireless auditor."""
    ctx.show_wifite()


def _crack_handshake(ctx: SectionContext) -> None:
    ctx.show_cracker()


def _pmkid_sniper(ctx: SectionContext) -> None:
    ctx.show_pmkid_sniper()


def _evil_twin(ctx: SectionContext) -> None:
    ctx.show_eviltwin()


def _honeypot(ctx: SectionContext) -> None:
    ctx.show_honeypot()


def _probe_sniffer(ctx: SectionContext) -> None:
    ctx.show_probe_sniffer()


def _beacon_flood(ctx: SectionContext) -> None:
    ctx.show_beacon_flood()


def _karma_lite(ctx: SectionContext) -> None:
    ctx.show_karma_lite()


def _airodump_hint(ctx: SectionContext) -> None:
    ctx.show_result(
        "airodump-ng",
        "Live airodump UI is not embeddable here.\n"
        "Drop to a TTY (Ctrl-Alt-F2) for: \n"
        "    sudo airmon-ng start wlan0 \n"
        "    sudo airodump-ng wlan0mon \n"
        "Capture & handshake review will land in a future build.\n",
    )


def build() -> Section:
    return Section(
        title="Wireless",
        icon="[w]",
        icon_img=load_icon("wireless"),
        background_img=load_background("wireless"),
        actions=[
            Action("Wifite 2", _wifite, "automated auditor — interactive terminal"),
            Action("PMKID Sniper", _pmkid_sniper, "silent capture — no clients needed"),
            Action("WiFi Multi-Tool", _wifi_multi_tool, "integrated scanner & attacks"),
            Action("Handshake / Deauth", _handshake_deauth, "capture WPA handshakes"),
            Action("Crack Handshake (offline)", _crack_handshake, "aircrack-ng + wordlist"),
            Action("Evil Twin / Captive Portal", _evil_twin, "rogue AP + cred capture"),
            Action("Honeypot AP", _honeypot, "open SSID — log who connects"),
            Action("Probe Sniffer", _probe_sniffer, "passive — see every phone's known SSIDs"),
            Action("Beacon Flood", _beacon_flood, "spam fake SSIDs into nearby devices"),
            Action("Karma-lite", _karma_lite, "broadcast SSIDs phones are probing for"),
            Action("List Wi-Fi interfaces", _wifi_interfaces),
            Action("Current link", _link),
            Action("Scan APs (wlan0)", _wifi_scan, "iw dev wlan0 scan"),
            Action("airodump-ng (instructions)", _airodump_hint),
        ],
    )
