"""Horizontal page carousel.

Holds N Sections, lets the user step through them with LEFT/RIGHT (or L/R
shoulders), and animates a smooth slide between pages. Each Section has its
own ScrollList for vertical content scrolling.
"""
from __future__ import annotations

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action, Section, SectionContext


class Carousel:
    def __init__(self, sections: list[Section]) -> None:
        if not sections:
            raise ValueError("Carousel needs at least one section")
        self.sections = sections
        self.index = 0
        self._slide_px = 0.0      # current x-offset, in pixels
        self._slide_target = 0.0  # where we're animating toward
        self._lists = [ScrollList(s.actions) for s in sections]

    @property
    def current(self) -> Section:
        return self.sections[self.index]

    @property
    def current_list(self) -> ScrollList:
        return self._lists[self.index]

    def step(self, delta: int, ctx: SectionContext) -> None:
        new_index = max(0, min(len(self.sections) - 1, self.index + delta))
        if new_index == self.index:
            return
        self.sections[self.index].on_leave(ctx)
        self.index = new_index
        self._slide_target = float(new_index * theme.SCREEN_W)
        self.sections[self.index].on_enter(ctx)

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> Action | None:
        if not ev.pressed:
            return None
        # Section switching: D-pad LEFT/RIGHT (only edges, no key-repeat) or shoulders.
        if ev.button is Button.LEFT and not ev.repeat:
            self.step(-1, ctx)
            return None
        if ev.button is Button.RIGHT and not ev.repeat:
            self.step(+1, ctx)
            return None
        if ev.button is Button.LL and not ev.repeat:
            self.step(-1, ctx)
            return None
        if ev.button is Button.RR and not ev.repeat:
            self.step(+1, ctx)
            return None
        # Otherwise it's a content-list event for the current page.
        return self.current_list.handle(ev)

    # ----- render -----
    def render(self, surf: pygame.Surface, font: pygame.font.Font, title_font: pygame.font.Font) -> None:
        # Tab bar across the top.
        self._render_tab_bar(surf, title_font)

        # Animate slide.
        self._slide_px += (self._slide_target - self._slide_px) * 0.22

        body_top = theme.STATUS_BAR_H + theme.TAB_BAR_H
        body_rect = pygame.Rect(0, body_top, theme.SCREEN_W, theme.SCREEN_H - body_top)

        # Render only the pages adjacent to the current one (perf + cleanliness).
        for offset in (-1, 0, 1):
            i = self.index + offset
            if not (0 <= i < len(self.sections)):
                continue
            page_x = int(i * theme.SCREEN_W - self._slide_px)
            if page_x + theme.SCREEN_W <= 0 or page_x >= theme.SCREEN_W:
                continue
            page_rect = pygame.Rect(page_x, body_rect.y, theme.SCREEN_W, body_rect.height)
            self._render_page(surf, page_rect, self.sections[i], self._lists[i], font, title_font)

    def _render_tab_bar(self, surf: pygame.Surface, title_font: pygame.font.Font) -> None:
        bar = pygame.Rect(0, theme.STATUS_BAR_H, theme.SCREEN_W, theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, bar)
        pygame.draw.line(surf, theme.DIVIDER, (0, bar.bottom - 1), (bar.right, bar.bottom - 1))

        # Indicator + label of current section, with subtle hints of neighbors.
        cur = self.sections[self.index]
        x = theme.PADDING
        y = bar.y + (bar.height - title_font.get_height()) // 2

        if cur.icon_img:
            # Render image icon, then title
            img_y = bar.y + (bar.height - cur.icon_img.get_height()) // 2
            surf.blit(cur.icon_img, (x, img_y))
            x += cur.icon_img.get_width() + 8
            label = title_font.render(cur.title, True, theme.ACCENT)
            surf.blit(label, (x, y))
        else:
            # Fallback to text icon
            label = title_font.render(f"{cur.icon}  {cur.title}".strip(), True, theme.ACCENT)
            surf.blit(label, (theme.PADDING, y))

        # "‹ prev / next ›" hints on the right.
        small = pygame.font.Font(None, theme.FS_SMALL)
        prev_name = self.sections[self.index - 1].title if self.index > 0 else ""
        next_name = self.sections[self.index + 1].title if self.index + 1 < len(self.sections) else ""
        hint = small.render(
            f"{('< ' + prev_name) if prev_name else '':<14}{(next_name + ' >') if next_name else '':>14}",
            True,
            theme.FG_DIM,
        )
        surf.blit(hint, (bar.right - hint.get_width() - theme.PADDING, bar.y + (bar.height - hint.get_height()) // 2))

        # Page-dot indicator.
        dots_y = bar.bottom - 4
        total = len(self.sections)
        dot_gap = 8
        dots_w = total * dot_gap
        x0 = (theme.SCREEN_W - dots_w) // 2
        for i in range(total):
            color = theme.ACCENT if i == self.index else theme.DIVIDER
            pygame.draw.circle(surf, color, (x0 + i * dot_gap, dots_y), 2)

    def _render_page(
        self,
        surf: pygame.Surface,
        rect: pygame.Rect,
        section: Section,
        slist: ScrollList,
        font: pygame.font.Font,
        title_font: pygame.font.Font,
    ) -> None:
        # Page background: image if the section has one, else solid theme color.
        if section.background_img is not None:
            # background was sized at theme.SCREEN_W x page-height; blit at the
            # rect origin so the slide animation carries it sideways.
            surf.blit(section.background_img, (rect.x, rect.y))
        else:
            pygame.draw.rect(surf, theme.BG, rect)
        # 1px left divider so the slide animation reads cleanly between pages.
        if rect.x > 0:
            pygame.draw.line(surf, theme.DIVIDER, (rect.x, rect.y), (rect.x, rect.bottom))

        # Section heading inside the page.
        head_h = 44
        head_rect = pygame.Rect(rect.x + theme.PADDING, rect.y + 6, rect.width - 2 * theme.PADDING, head_h)
        title = title_font.render(section.title, True, theme.FG)
        surf.blit(title, (head_rect.x, head_rect.y + (head_h - title.get_height()) // 2))
        pygame.draw.line(
            surf, theme.DIVIDER,
            (head_rect.x, head_rect.bottom),
            (head_rect.right, head_rect.bottom),
        )

        list_rect = pygame.Rect(
            rect.x + theme.PADDING,
            head_rect.bottom + 6,
            rect.width - 2 * theme.PADDING,
            rect.bottom - head_rect.bottom - 12,
        )
        slist.render(surf, list_rect, font)
