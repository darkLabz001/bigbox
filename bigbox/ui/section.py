"""Base classes for sections (the pages in the carousel) and their actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import pygame.surface


class SectionContext(Protocol):
    """What sections receive when their action handlers fire.

    Kept as a Protocol so we don't import the App class here (cycles).
    """

    def show_result(self, title: str, text: str) -> None: ...
    def run_streaming(self, title: str, argv: list[str]) -> None: ...
    def go_back(self) -> None: ...
    def toast(self, msg: str) -> None: ...


@dataclass
class Action:
    label: str
    handler: Callable[[SectionContext], None] | None = None
    description: str = ""


@dataclass
class Section:
    title: str
    actions: list[Action] = field(default_factory=list)
    icon: str = ""   # short string drawn beside title in the tab bar
    icon_img: pygame.surface.Surface | None = None
    background_img: pygame.surface.Surface | None = None  # full page bg

    # Hooks — override in subclasses if needed.
    def on_enter(self, ctx: SectionContext) -> None: ...
    def on_leave(self, ctx: SectionContext) -> None: ...
