"""Social Section — chat and social tools."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open_chat(ctx: SectionContext) -> None:
    """Open the in-device darksec.uk chat client."""
    ctx.show_chat()


def build() -> Section:
    return Section(
        title="Social",
        icon="[s]",
        icon_img=load_icon("media"),
        background_img=load_background("media"),
        actions=[
            Action("Chat", _open_chat, "darksec.uk in-device chat"),
        ],
    )
