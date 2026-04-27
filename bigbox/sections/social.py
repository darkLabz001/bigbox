"""Social Section — chat and social tools."""
from __future__ import annotations

import webbrowser
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _open_chat(ctx: SectionContext) -> None:
    """Open the darksec.uk chat in the default browser."""
    url = "https://darksec.uk/chat"
    try:
        # On Pi, this usually tries x-www-browser or chromium-browser
        webbrowser.open(url)
        ctx.show_result("Chat", f"Opening chatroom:\n{url}\n\nCheck your browser window.")
    except Exception as e:
        ctx.show_result("Chat Error", f"Failed to open browser:\n{e}")


def build() -> Section:
    return Section(
        title="Social",
        icon="[s]",
        icon_img=load_icon("media"),
        background_img=load_background("media"),
        actions=[
            Action("Chat", _open_chat, "darksec.uk chatroom"),
        ],
    )
