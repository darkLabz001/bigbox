"""Shop UI — browse the BoxShop catalog and install/uninstall payloads."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pygame
from bigbox import shop, theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


PHASE_LIST = "list"
PHASE_DETAIL = "detail"


class ShopView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LIST
        self.cursor = 0
        self.scroll = 0
        self.status_msg = ""
        self.error_msg = ""
        self._busy = False

        self.items: list[dict] = shop.list_items()
        # Auto-refresh in background on first open if cache is empty.
        if not self.items:
            self._refresh_async()

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if self._busy:
            return

        if self.phase == PHASE_LIST:
            self._handle_list(ev)
        elif self.phase == PHASE_DETAIL:
            self._handle_detail(ev)

    def _handle_list(self, ev: ButtonEvent) -> None:
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.UP and self.items:
            self.cursor = (self.cursor - 1) % len(self.items)
            self._clear_msgs()
        elif ev.button is Button.DOWN and self.items:
            self.cursor = (self.cursor + 1) % len(self.items)
            self._clear_msgs()
        elif ev.button is Button.A and self.items:
            self.phase = PHASE_DETAIL
            self._clear_msgs()
        elif ev.button is Button.X:
            self._refresh_async()

    def _handle_detail(self, ev: ButtonEvent) -> None:
        if ev.button is Button.B:
            self.phase = PHASE_LIST
            self._clear_msgs()
            return
        if not self.items:
            return
        item = self.items[self.cursor]
        if ev.button is Button.A:
            if shop.is_installed(item["id"]):
                self._uninstall_async(item["id"])
            else:
                self._install_async(item["id"])

    def _clear_msgs(self) -> None:
        self.status_msg = ""
        self.error_msg = ""

    # ---------- background ops ----------
    def _refresh_async(self) -> None:
        def worker():
            self._busy = True
            self.status_msg = "Refreshing catalog..."
            ok, msg = shop.refresh()
            if ok:
                self.items = shop.list_items()
                self.cursor = min(self.cursor, max(0, len(self.items) - 1))
                self.status_msg = f"Catalog: {msg}"
                self.error_msg = ""
            else:
                self.error_msg = f"Refresh failed: {msg}"
                self.status_msg = ""
            self._busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _install_async(self, item_id: str) -> None:
        def worker():
            self._busy = True
            self.status_msg = f"Installing {item_id}..."
            ok, msg = shop.install(item_id)
            if ok:
                self.status_msg = f"Installed: {msg}"
                self.error_msg = ""
            else:
                self.error_msg = f"Install failed: {msg}"
                self.status_msg = ""
            self._busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _uninstall_async(self, item_id: str) -> None:
        def worker():
            self._busy = True
            self.status_msg = f"Removing {item_id}..."
            ok, msg = shop.uninstall(item_id)
            if ok:
                self.status_msg = f"Removed: {msg}"
                self.error_msg = ""
            else:
                self.error_msg = f"Remove failed: {msg}"
                self.status_msg = ""
            self._busy = False
        threading.Thread(target=worker, daemon=True).start()

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        f_title = pygame.font.Font(None, 36)
        f_body = pygame.font.Font(None, 22)
        f_small = pygame.font.Font(None, 18)

        title = f_title.render("BOXSHOP :: PAYLOAD CATALOG", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        # Status / error line
        msg_y = 50
        if self.error_msg:
            s = f_body.render(self.error_msg[:90], True, theme.ERR)
            surf.blit(s, (theme.PADDING, msg_y))
        elif self.status_msg:
            s = f_body.render(self.status_msg[:90], True, theme.FG)
            surf.blit(s, (theme.PADDING, msg_y))
        else:
            s = f_body.render(
                f"{len(self.items)} payload(s)   X: refresh   A: open   B: back",
                True, theme.FG_DIM)
            surf.blit(s, (theme.PADDING, msg_y))

        if self.phase == PHASE_LIST:
            self._render_list(surf, f_body, f_small)
        else:
            self._render_detail(surf, f_body, f_small)

    def _render_list(self, surf: pygame.Surface, f_body, f_small) -> None:
        if not self.items:
            empty = f_body.render(
                "No payloads cached. Press X to fetch the catalog.",
                True, theme.FG_DIM)
            surf.blit(empty, (theme.PADDING, 110))
            return

        list_y = 90
        row_h = 38
        max_rows = (theme.SCREEN_H - list_y - 60) // row_h
        # Keep cursor in view.
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + max_rows:
            self.scroll = self.cursor - max_rows + 1

        for i in range(self.scroll, min(self.scroll + max_rows, len(self.items))):
            it = self.items[i]
            y = list_y + (i - self.scroll) * row_h
            if i == self.cursor:
                pygame.draw.rect(
                    surf, theme.SELECTION_BG,
                    (theme.PADDING - 4, y - 4,
                     theme.SCREEN_W - 2 * theme.PADDING + 8, row_h - 4),
                    border_radius=4,
                )
            installed = shop.is_installed(it["id"])
            trusted = it.get("trusted", False)

            type_tag = f"[{it.get('type', '?').upper()}]"
            tag_color = theme.FG_DIM
            label_color = theme.ACCENT if i == self.cursor else theme.FG
            name = it.get("name", it["id"])
            line = f"{type_tag}  {name}"
            surf.blit(f_body.render(line, True, label_color),
                      (theme.PADDING + 4, y))

            badges = []
            if installed:
                badges.append(("INSTALLED", theme.ACCENT))
            if not trusted:
                badges.append(("UNVERIFIED", theme.WARN))
            x = theme.SCREEN_W - theme.PADDING - 4
            for txt, color in reversed(badges):
                b = f_small.render(txt, True, color)
                x -= b.get_width() + 8
                surf.blit(b, (x, y + 2))

        hint = f_small.render(
            "UP/DOWN move   A: open   X: refresh   B: back",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 26))

    def _render_detail(self, surf: pygame.Surface, f_body, f_small) -> None:
        if not self.items:
            return
        it = self.items[self.cursor]
        y = 90

        name = it.get("name", it["id"])
        head = pygame.font.Font(None, 30).render(name, True, theme.ACCENT)
        surf.blit(head, (theme.PADDING, y))
        y += 36

        meta = (f"id: {it['id']}   type: {it.get('type', '?')}   "
                f"v{it.get('version', '?')}   author: {it.get('author', '?')}")
        surf.blit(f_small.render(meta, True, theme.FG_DIM), (theme.PADDING, y))
        y += 24

        if not it.get("trusted", False):
            warn = f_body.render("UNVERIFIED — review the manifest before installing.",
                                 True, theme.WARN)
            surf.blit(warn, (theme.PADDING, y))
            y += 28

        # Wrap summary across multiple lines.
        summary = it.get("summary", "")
        for line in _wrap(summary, 70):
            surf.blit(f_body.render(line, True, theme.FG), (theme.PADDING, y))
            y += 24

        tags = ", ".join(it.get("tags", []))
        if tags:
            surf.blit(f_small.render(f"tags: {tags}", True, theme.FG_DIM),
                      (theme.PADDING, y + 8))

        installed = shop.is_installed(it["id"])
        action = "A: REMOVE" if installed else "A: INSTALL"
        hint = f_body.render(f"{action}     B: back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 36))


def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap to roughly `width` chars per line."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return lines
