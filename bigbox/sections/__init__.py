"""Builds the ordered list of sections shown in the carousel.

To add a new section: write a module that exports `build()` returning a
Section, then append its name here.
"""
from __future__ import annotations

from bigbox.sections import about, bluetooth, games, network, recon, settings, social, wireless, media, loot
from bigbox.ui import Section


def build_sections() -> list[Section]:
    return [
        recon.build(),
        loot.build(),
        network.build(),
        wireless.build(),
        bluetooth.build(),
        media.build(),
        games.build(),
        social.build(),
        settings.build(),
        about.build(),
    ]
