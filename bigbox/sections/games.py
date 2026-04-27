"""Games — section preserved as a placeholder while the emulator
integration (GB / GBC / GBA / PS1) is being reworked.

The underlying modules (bigbox/emulator.py, bigbox/retroachievements.py,
bigbox/ui/games.py) and the web UI's ROM upload + RA login flows are
left intact — ROMs the user has already uploaded stay at
/opt/bigbox/roms/<system>/, the BIOS at /opt/bigbox/bios/ps1/, and the
RA token at /etc/bigbox/retroachievements.json. Re-enable by switching
the action below back to ctx.show_games().
"""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _coming_soon(ctx: SectionContext) -> None:
    ctx.show_result(
        "Games — coming soon",
        "Emulator support (GB / GBC / GBA / PS1) is being reworked —\n"
        "fullscreen scaling, ALSA audio routing, and GPIO controls\n"
        "all need more work to be usable on this hardware.\n\n"
        "Anything you've already uploaded is preserved:\n"
        "  ROMs:       /opt/bigbox/roms/<system>/\n"
        "  PS1 BIOS:   /opt/bigbox/bios/ps1/\n"
        "  RA login:   /etc/bigbox/retroachievements.json\n\n"
        "The web UI's ROM upload + RetroAchievements login still work\n"
        "as data buckets — they just aren't wired to a launcher yet.\n",
    )


def build() -> Section:
    return Section(
        title="Games",
        icon="[g]",
        icon_img=load_icon("games"),
        background_img=load_background("games"),
        actions=[
            Action("Game Library", _coming_soon,
                   "emulator launcher coming back later"),
        ],
    )
