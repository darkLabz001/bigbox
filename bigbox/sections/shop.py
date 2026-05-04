"""Shop — browse and install payloads from the BoxShop catalog."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open(ctx: SectionContext) -> None:
    ctx.show_shop()


def build() -> Section:
    return Section(
        title="Shop",
        icon="[$]",
        icon_img=load_icon("shop"),
        background_img=load_background("shop"),
        actions=[
            Action("Browse Catalog", _open,
                   "themes, BLE, wireless, recon"),
        ],
    )
