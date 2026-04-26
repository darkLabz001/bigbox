"""About — version & system info."""
from __future__ import annotations

from bigbox import __version__
from bigbox.runner import run_capture
from bigbox.ui import Action, Section, SectionContext


def _version(ctx: SectionContext) -> None:
    ctx.show_result(
        "bigbox",
        f"bigbox v{__version__}\n"
        "pentesting handheld for Raspberry Pi 4 + GamePi43\n"
        "see README.md for layout & key map\n",
    )


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
        actions=[
            Action("bigbox version", _version),
            Action("System info", _sys),
        ],
    )
