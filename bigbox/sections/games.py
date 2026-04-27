"""Games — Game Boy / GBA / PS1 emulator launcher."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open_library(ctx: SectionContext) -> None:
    ctx.show_games()


def build() -> Section:
    return Section(
        title="Games",
        icon="[g]",
        icon_img=load_icon("games"),
        background_img=load_background("games"),
        actions=[
            Action("Game Library", _open_library,
                   "GB / GBA / PS1 — pick a system, then a ROM"),
        ],
    )
