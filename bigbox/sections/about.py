"""About — version & system info."""
from __future__ import annotations

from bigbox import __version__
from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


import socket

def _version(ctx: SectionContext) -> None:
    ctx.show_result(
        "bigbox",
        f"bigbox v{__version__}\n"
        "pentesting handheld for Raspberry Pi 4 + GamePi43\n"
        "see README.md for layout & key map\n",
    )


def _web_ui_info(ctx: SectionContext) -> None:
    # Try to get the local IP address
    ip = "UNKNOWN"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            # Doesn't need to be reachable, just triggers local IP selection
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except Exception:
        pass
    
    msg = (
        "REMOTE CONTROL WEB UI\n\n"
        f"URL:  http://{ip}:8080\n\n"
        "Connect your phone to the same\n"
        "Wi-Fi to mirror the screen and\n"
        "control the device remotely."
    )
    ctx.show_result("Web UI", msg)


def _sys(ctx: SectionContext) -> None:
    out = (
        run_capture(["uname", "-a"])
        + "\n"
        + run_capture(["sh", "-c", "cat /etc/os-release"])
        + "\n"
        + run_capture(["sh", "-c", "vcgencmd measure_temp 2>/dev/null || true"])
    )
    ctx.show_result("system", out)


def build() -> Section:
    return Section(
        title="About",
        icon="[i]",
        icon_img=load_icon("about"),
        background_img=load_background("about"),
        actions=[
            Action("bigbox version", _version),
            Action("Remote Web UI", _web_ui_info),
            Action("System info", _sys),
        ],
    )
