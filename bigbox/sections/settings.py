"""Settings — system controls."""
from __future__ import annotations

from bigbox.runner import run_capture
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


def build() -> Section:
    return Section(
        title="Settings",
        icon="[=]",
        actions=[
            Action("Volume up", _vol_up),
            Action("Volume down", _vol_down),
            Action("Mute toggle", _vol_mute),
            Action("Reboot", _reboot),
            Action("Power off", _poweroff),
        ],
    )
