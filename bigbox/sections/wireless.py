"""Wireless — Wi-Fi recon (requires root for monitor-mode actions)."""
from __future__ import annotations
import pygame

from bigbox.runner import run_capture
from bigbox.ui import Action, Section, SectionContext


def _wifi_interfaces(ctx: SectionContext) -> None:
    ctx.show_result("wifi interfaces", run_capture(["iw", "dev"]))


def _wifi_scan(ctx: SectionContext) -> None:
    # Default Pi 4 onboard adapter is wlan0; user with external should adjust.
    ctx.run_streaming("scan · wlan0", ["sudo", "iw", "dev", "wlan0", "scan"])


def _link(ctx: SectionContext) -> None:
    ctx.show_result("link", run_capture(["iw", "dev", "wlan0", "link"]))


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
        icon_img=pygame.image.load("/home/sinxneo/Pictures/bigbox/wireless.png"),
        actions=[
            Action("List Wi-Fi interfaces", _wifi_interfaces),
            Action("Current link", _link),
            Action("Scan APs (wlan0)", _wifi_scan, "iw dev wlan0 scan"),
            Action("airodump-ng (instructions)", _airodump_hint),
        ],
    )
