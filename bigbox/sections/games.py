"""Games — pick a system, pick a ROM, hand off to the emulator."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open_games(ctx: SectionContext) -> None:
    ctx.show_games()


def build() -> Section:
    return Section(
        title="Games",
        icon="[g]",
        icon_img=load_icon("games"),
        background_img=load_background("games"),
        actions=[
            Action("Game Library", _open_games, "Play GB, GBC, GBA, and PS1 games"),
        ],
    )
