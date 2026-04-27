"""Social Section — Tactical and offline communication tools."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open_chat(ctx: SectionContext) -> None:
    """Open the in-device darksec.uk chat client."""
    ctx.show_chat()


def _open_deaddrop(ctx: SectionContext) -> None:
    """Offline chat via captive portal rogue AP."""
    ctx.show_deaddrop()


def _open_ble_chat(ctx: SectionContext) -> None:
    """Bluetooth Low Energy peer-to-peer chat."""
    ctx.show_ble_chat()


def _open_bbs(ctx: SectionContext) -> None:
    """Local Bulletin Board System for LAN users."""
    ctx.show_bbs()


def _open_onion_chat(ctx: SectionContext) -> None:
    """Tor-routed IRC client for anonymous comms."""
    ctx.show_onion_chat()


def build() -> Section:
    return Section(
        title="Social",
        icon="[s]",
        icon_img=load_icon("media"),
        background_img=load_background("media"),
        actions=[
            Action("Global Chat", _open_chat, "darksec.uk in-device chat"),
            Action("Dead Drop", _open_deaddrop, "Rogue AP offline chatroom"),
            Action("BLE Mesh Chat", _open_ble_chat, "Bluetooth peer-to-peer"),
            Action("Local BBS", _open_bbs, "LAN-based message board"),
            Action("Onion IRC", _open_onion_chat, "Tor-routed anonymous chat"),
        ],
    )
