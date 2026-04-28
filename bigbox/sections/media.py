"""Media Section — browse and play uploaded movies."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open_player(ctx: SectionContext) -> None:
    ctx.show_media_player()


def _open_tv(ctx: SectionContext) -> None:
    ctx.show_tv()


def build() -> Section:
    return Section(
        title="Media",
        icon="[M]",
        icon_img=load_icon("media"),
        background_img=load_background("media"),
        actions=[
            Action("Open Media Player", _open_player, "Browse and play movies"),
            Action("Free Internet TV", _open_tv, "Watch live world news & TV"),
        ],
    )
