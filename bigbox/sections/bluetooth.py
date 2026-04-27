"""Bluetooth — controller info and discovery."""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _ctl_show(ctx: SectionContext) -> None:
    ctx.show_result("controller", run_capture(["bluetoothctl", "show"]))


def _devices(ctx: SectionContext) -> None:
    ctx.show_result("known devices", run_capture(["bluetoothctl", "devices"]))


def _scan(ctx: SectionContext) -> None:
    # bluetoothctl --timeout flag is BlueZ 5.65+; degrades gracefully otherwise.
    ctx.run_streaming("BT scan (10s)", ["bluetoothctl", "--timeout", "10", "scan", "on"])


def _ble_spam(ctx: SectionContext) -> None:
    ctx.show_ble_spam()


def _trackers(ctx: SectionContext) -> None:
    ctx.show_trackers()


def build() -> Section:
    return Section(
        title="Bluetooth",
        icon="[b]",
        icon_img=load_icon("bluetooth"),
        background_img=load_background("bluetooth"),
        actions=[
            Action("Tracker Detector", _trackers, "AirTag/SmartTag/Tile follow-alarm"),
            Action("BLE Spam (AppleJuice)", _ble_spam, "spoof pairing popups"),
            Action("Controller info", _ctl_show),
            Action("Known devices", _devices),
            Action("Scan (10s)", _scan),
        ],
    )
