"""Settings — system controls."""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _vol_up(ctx: SectionContext) -> None:
    out = run_capture(["amixer", "set", "Master", "5%+"])
    ctx.show_result("volume +", out)


def _vol_down(ctx: SectionContext) -> None:
    out = run_capture(["amixer", "set", "Master", "5%-"])
    ctx.show_result("volume -", out)


def _vol_mute(ctx: SectionContext) -> None:
    out = run_capture(["amixer", "set", "Master", "toggle"])
    ctx.show_result("mute toggle", out)


def _reboot(ctx: SectionContext) -> None:
    ctx.run_streaming("reboot", ["sudo", "reboot"])


def _poweroff(ctx: SectionContext) -> None:
    ctx.run_streaming("poweroff", ["sudo", "poweroff"])


def _view_loot(ctx: SectionContext) -> None:
    import os
    fname = "loot/flock_intel.txt"
    if not os.path.exists(fname):
        ctx.show_result("Flock Loot", "No loot captured yet.\n\nRun FlockSeeker to gather intel.")
        return
        
    try:
        with open(fname, "r") as f:
            content = f.read()
            if not content.strip():
                content = "Loot file is empty."
            ctx.show_result("Flock Loot", content)
    except Exception as e:
        ctx.show_result("Error", f"Could not read loot: {e}")


def _wifi_connect(ctx: SectionContext) -> None:
    ctx.show_wifi()


def _terminal(ctx: SectionContext) -> None:
    ctx.show_terminal()


def _update(ctx: SectionContext) -> None:
    # Always resolve the script via the package layout, never via cwd.
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    ctx.show_update("OTA update", [str(script)])


def build() -> Section:
    return Section(
        title="Settings",
        icon="[=]",
        icon_img=load_icon("settings"),
        background_img=load_background("settings"),
        actions=[
            Action("Connect to Wi-Fi", _wifi_connect, "scan, select, save a network"),
            Action("Bash Terminal", _terminal, "full root shell with OSK"),
            Action("Check for updates (OTA)", _update),
            Action("View Flock Loot", _view_loot, "intel gathered from FlockSeeker"),
            Action("Volume up", _vol_up),
            Action("Volume down", _vol_down),
            Action("Mute toggle", _vol_mute),
            Action("Reboot", _reboot),
            Action("Power off", _poweroff),
        ],
    )
