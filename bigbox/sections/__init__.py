"""Builds the ordered list of sections shown in the carousel.

To add a new section: write a module that exports `build()` returning a
Section, then append its name here.
"""
from __future__ import annotations

from bigbox.sections import about, bluetooth, network, recon, settings, wireless
from bigbox.ui import Section


def build_sections() -> list[Section]:
    return [
        recon.build(),
        network.build(),
        wireless.build(),
        bluetooth.build(),
        settings.build(),
        about.build(),
    ]
