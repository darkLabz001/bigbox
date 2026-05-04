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

# Module-level fonts — cached by _font_cache.py, but lifting refs out of
# the render hot path saves a dict lookup per glyph blit.
_F_TITLE = None
_F_HEAD = None
_F_BODY = None
_F_SMALL = None
_F_TINY = None


def _ensure_fonts() -> None:
    global _F_TITLE, _F_HEAD, _F_BODY, _F_SMALL, _F_TINY
    if _F_TITLE is None:
        _F_TITLE = pygame.font.Font(None, 32)
        _F_HEAD = pygame.font.Font(None, 28)
        _F_BODY = pygame.font.Font(None, 22)
        _F_SMALL = pygame.font.Font(None, 18)
        _F_TINY = pygame.font.Font(None, 16)


# Per-type accent colors for chips / left edges. Themes share the
# global accent (it's already a "look-and-feel" category); the others
# get distinct colors so categories are scannable at a glance.
TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "themes":   (90, 230, 170),    # mint (= theme.ACCENT)
    "ble":      (210, 130, 230),   # magenta
    "wireless": (90, 180, 240),    # cyan
    "recon":    (240, 180, 70),    # amber
}
DEFAULT_TYPE_COLOR = (140, 140, 160)

FILTER_ORDER = ["ALL", "themes", "ble", "wireless", "recon"]


HEADER_H = 44
FILTER_H = 32
HINT_H = 30
ROW_H = 60


class ShopView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LIST
        self.cursor = 0
        self.scroll = 0
        self.filter_idx = 0   # index into FILTER_ORDER
        self.status_msg = ""
        self.error_msg = ""
        self._busy = False
        self._all_items: list[dict] = shop.list_items()
        if not self._all_items:
            self._refresh_async()

    # ---------- helpers ----------
    @property
    def items(self) -> list[dict]:
        f = FILTER_ORDER[self.filter_idx]
        if f == "ALL":
            return self._all_items
        return [it for it in self._all_items if it.get("type") == f]

    def _clear_msgs(self) -> None:
        self.status_msg = ""
        self.error_msg = ""

    def _clamp_cursor(self) -> None:
        n = len(self.items)
        if n == 0:
            self.cursor = 0
            self.scroll = 0
        else:
            self.cursor = min(self.cursor, n - 1)

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
            return
        if ev.button is Button.X:
            self._refresh_async()
            return
        if ev.button is Button.LEFT:
            self.filter_idx = (self.filter_idx - 1) % len(FILTER_ORDER)
            self.cursor = 0
            self.scroll = 0
            self._clear_msgs()
            return
        if ev.button is Button.RIGHT:
            self.filter_idx = (self.filter_idx + 1) % len(FILTER_ORDER)
            self.cursor = 0
            self.scroll = 0
            self._clear_msgs()
            return
        if not self.items:
            return
        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(self.items)
            self._clear_msgs()
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(self.items)
            self._clear_msgs()
        elif ev.button is Button.A:
            self.phase = PHASE_DETAIL
            self._clear_msgs()

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

    # ---------- background ops ----------
    def _refresh_async(self) -> None:
        def worker():
            self._busy = True
            self.status_msg = "Refreshing catalog..."
            ok, msg = shop.refresh()
            if ok:
                self._all_items = shop.list_items()
                self._clamp_cursor()
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
        _ensure_fonts()
        surf.fill(theme.BG)
        self._render_header(surf)
        if self.phase == PHASE_LIST:
            self._render_filters(surf)
            self._render_list(surf)
        else:
            self._render_detail(surf)
        self._render_hint(surf)

    def _render_header(self, surf: pygame.Surface) -> None:
        # Header bar: dark tint, title left, count + status right.
        pygame.draw.rect(surf, theme.BG_ALT,
                         (0, 0, theme.SCREEN_W, HEADER_H))
        pygame.draw.line(surf, theme.ACCENT_DIM,
                         (0, HEADER_H), (theme.SCREEN_W, HEADER_H), 1)
        title = _F_TITLE.render("BOXSHOP", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (HEADER_H - title.get_height()) // 2))

        right_text = self._header_right_text()
        right_color = theme.ERR if self.error_msg else (
            theme.WARN if self._busy else theme.FG_DIM)
        rt = _F_SMALL.render(right_text, True, right_color)
        surf.blit(rt, (theme.SCREEN_W - rt.get_width() - theme.PADDING,
                       (HEADER_H - rt.get_height()) // 2))

    def _header_right_text(self) -> str:
        if self.error_msg:
            return self.error_msg[:64]
        if self.status_msg:
            return self.status_msg[:64]
        if not self._all_items:
            return "Press X to refresh"
        f = FILTER_ORDER[self.filter_idx]
        scope = f"{len(self.items)} of {len(self._all_items)}" if f != "ALL" \
            else f"{len(self._all_items)} payload(s)"
        return scope

    def _render_filters(self, surf: pygame.Surface) -> None:
        y = HEADER_H + 6
        x = theme.PADDING
        for i, key in enumerate(FILTER_ORDER):
            label = "ALL" if key == "ALL" else key.upper()
            color = TYPE_COLORS.get(key, theme.ACCENT)
            text = _F_SMALL.render(label, True,
                                   theme.BG if i == self.filter_idx else color)
            chip_w = text.get_width() + 18
            chip_rect = pygame.Rect(x, y, chip_w, FILTER_H - 8)
            if i == self.filter_idx:
                pygame.draw.rect(surf, color, chip_rect, border_radius=12)
            else:
                pygame.draw.rect(surf, theme.BG_ALT, chip_rect, border_radius=12)
                pygame.draw.rect(surf, color, chip_rect, 1, border_radius=12)
            surf.blit(text, (x + 9, y + (chip_rect.height - text.get_height()) // 2))
            x += chip_w + 8

    def _render_list(self, surf: pygame.Surface) -> None:
        list_top = HEADER_H + FILTER_H + 6
        list_bottom = theme.SCREEN_H - HINT_H
        list_h = list_bottom - list_top
        max_rows = max(1, list_h // ROW_H)

        items = self.items
        if not items:
            empty = _F_BODY.render(
                "No payloads in this category. Press X to refresh.",
                True, theme.FG_DIM)
            surf.blit(empty,
                      ((theme.SCREEN_W - empty.get_width()) // 2,
                       list_top + 40))
            return

        # Keep cursor in view.
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + max_rows:
            self.scroll = self.cursor - max_rows + 1

        for i in range(self.scroll, min(self.scroll + max_rows, len(items))):
            it = items[i]
            y = list_top + (i - self.scroll) * ROW_H
            self._render_item_row(surf, it, y, selected=(i == self.cursor))

        # Tiny scroll indicator if list overflows.
        if len(items) > max_rows:
            self._render_scrollbar(surf, list_top, list_bottom, max_rows, len(items))

    def _render_item_row(self, surf: pygame.Surface, it: dict, y: int,
                         selected: bool) -> None:
        type_key = it.get("type", "")
        type_color = TYPE_COLORS.get(type_key, DEFAULT_TYPE_COLOR)
        x0 = theme.PADDING
        x1 = theme.SCREEN_W - theme.PADDING
        row_rect = pygame.Rect(x0, y, x1 - x0, ROW_H - 6)

        if selected:
            pygame.draw.rect(surf, theme.SELECTION_BG, row_rect, border_radius=4)
        # Colored left edge — the "category bar".
        pygame.draw.rect(surf, type_color,
                         (x0, y, 4, ROW_H - 6), border_radius=2)

        # Type chip
        tc_text = _F_TINY.render(type_key.upper(), True, theme.BG)
        chip_w = tc_text.get_width() + 12
        chip_rect = pygame.Rect(x0 + 14, y + 8, chip_w, 18)
        pygame.draw.rect(surf, type_color, chip_rect, border_radius=9)
        surf.blit(tc_text, (chip_rect.x + 6, chip_rect.y + 1))

        # Name
        name_color = theme.ACCENT if selected else theme.FG
        name = _F_HEAD.render(it.get("name", it["id"]), True, name_color)
        surf.blit(name, (chip_rect.right + 10, y + 4))

        # Summary (dim, truncated to width of available row)
        summary_max = x1 - (x0 + 14) - 90
        summary = _truncate(_F_BODY, it.get("summary", ""), summary_max)
        if summary:
            s = _F_BODY.render(summary, True, theme.FG_DIM)
            surf.blit(s, (x0 + 14, y + 32))

        # Right-side meta column: badges stacked + version
        right_x = x1 - 6
        installed = shop.is_installed(it["id"])
        trusted = it.get("trusted", False)
        badges = []
        if installed:
            badges.append(("INSTALLED", theme.ACCENT))
        if not trusted:
            badges.append(("UNVERIFIED", theme.WARN))
        bx = right_x
        by = y + 6
        for txt, color in reversed(badges):
            b = _F_TINY.render(txt, True, color)
            bx_left = bx - b.get_width()
            surf.blit(b, (bx_left, by))
            bx -= b.get_width() + 10
        # Version
        v = _F_TINY.render(f"v{it.get('version', '?')}", True, theme.FG_DIM)
        surf.blit(v, (right_x - v.get_width(), y + 32))

    def _render_scrollbar(self, surf: pygame.Surface, top: int, bottom: int,
                          max_rows: int, total: int) -> None:
        track_x = theme.SCREEN_W - 4
        track_h = bottom - top
        pygame.draw.rect(surf, theme.BG_ALT, (track_x, top, 2, track_h))
        thumb_h = max(20, int(track_h * (max_rows / total)))
        thumb_y = top + int(track_h * (self.scroll / total))
        pygame.draw.rect(surf, theme.ACCENT_DIM,
                         (track_x, thumb_y, 2, thumb_h))

    # ---------- detail ----------
    def _render_detail(self, surf: pygame.Surface) -> None:
        if not self.items:
            return
        it = self.items[self.cursor]
        type_key = it.get("type", "")
        type_color = TYPE_COLORS.get(type_key, DEFAULT_TYPE_COLOR)
        installed = shop.is_installed(it["id"])
        trusted = it.get("trusted", False)

        y = HEADER_H + 16

        # Big name + type chip on the right
        name = _F_TITLE.render(it.get("name", it["id"]), True, theme.FG)
        surf.blit(name, (theme.PADDING, y))

        # Type chip on right edge
        tc_text = _F_SMALL.render(type_key.upper(), True, theme.BG)
        chip_w = tc_text.get_width() + 18
        chip_rect = pygame.Rect(
            theme.SCREEN_W - theme.PADDING - chip_w,
            y + 4, chip_w, 22,
        )
        pygame.draw.rect(surf, type_color, chip_rect, border_radius=11)
        surf.blit(tc_text, (chip_rect.x + 9, chip_rect.y + 2))
        y += name.get_height() + 6

        # Meta line: version · author · id
        meta = (f"v{it.get('version', '?')}   ·   "
                f"{it.get('author', 'unknown')}   ·   "
                f"{it['id']}")
        surf.blit(_F_SMALL.render(meta, True, theme.FG_DIM),
                  (theme.PADDING, y))
        y += 26

        # Trust banner (only for unverified)
        if not trusted:
            banner_h = 28
            pygame.draw.rect(surf, (60, 40, 0),
                             (theme.PADDING, y, theme.SCREEN_W - 2 * theme.PADDING, banner_h),
                             border_radius=4)
            warn_txt = _F_SMALL.render(
                "UNVERIFIED — review the manifest before installing.",
                True, theme.WARN)
            surf.blit(warn_txt, (theme.PADDING + 10,
                                 y + (banner_h - warn_txt.get_height()) // 2))
            y += banner_h + 8

        # Summary block — wrapped
        summary = it.get("summary", "")
        for line in _wrap(_F_BODY, summary,
                          theme.SCREEN_W - 2 * theme.PADDING):
            surf.blit(_F_BODY.render(line, True, theme.FG),
                      (theme.PADDING, y))
            y += 24
        y += 6

        # Tags
        tags = it.get("tags", [])
        if tags:
            label = _F_SMALL.render("TAGS", True, theme.FG_DIM)
            surf.blit(label, (theme.PADDING, y))
            y += label.get_height() + 4
            tx = theme.PADDING
            for tag in tags:
                t = _F_SMALL.render(tag, True, theme.FG)
                tag_w = t.get_width() + 14
                pygame.draw.rect(surf, theme.BG_ALT,
                                 (tx, y, tag_w, 22), border_radius=11)
                pygame.draw.rect(surf, theme.ACCENT_DIM,
                                 (tx, y, tag_w, 22), 1, border_radius=11)
                surf.blit(t, (tx + 7, y + 3))
                tx += tag_w + 8
            y += 30

        # Install target path
        from bigbox.shop import INSTALL_ROOTS
        root = INSTALL_ROOTS.get(type_key)
        if root:
            label = _F_SMALL.render("INSTALL TARGET", True, theme.FG_DIM)
            surf.blit(label, (theme.PADDING, y))
            path = _F_SMALL.render(f"{root}/{it['id']}/", True, theme.FG)
            surf.blit(path, (theme.PADDING, y + 18))

        # Action button
        self._render_action_button(surf, installed, type_color)

    def _render_action_button(self, surf: pygame.Surface, installed: bool,
                              accent: tuple[int, int, int]) -> None:
        label = "PRESS A TO REMOVE" if installed else "PRESS A TO INSTALL"
        color = theme.WARN if installed else accent
        text = _F_HEAD.render(label, True, theme.BG)
        btn_w = text.get_width() + 60
        btn_h = 38
        bx = (theme.SCREEN_W - btn_w) // 2
        by = theme.SCREEN_H - HINT_H - btn_h - 8
        pygame.draw.rect(surf, color, (bx, by, btn_w, btn_h),
                         border_radius=6)
        surf.blit(text, (bx + (btn_w - text.get_width()) // 2,
                         by + (btn_h - text.get_height()) // 2))

    # ---------- hint bar ----------
    def _render_hint(self, surf: pygame.Surface) -> None:
        y = theme.SCREEN_H - HINT_H
        pygame.draw.rect(surf, theme.BG_ALT, (0, y, theme.SCREEN_W, HINT_H))
        pygame.draw.line(surf, theme.ACCENT_DIM,
                         (0, y), (theme.SCREEN_W, y), 1)
        if self.phase == PHASE_LIST:
            hint = "A: open   X: refresh   ←→: filter   ↑↓: select   B: back"
        else:
            hint = "A: install/remove   B: back"
        s = _F_SMALL.render(hint, True, theme.FG_DIM)
        surf.blit(s, ((theme.SCREEN_W - s.get_width()) // 2,
                      y + (HINT_H - s.get_height()) // 2))


# ---------- text helpers ----------

def _truncate(font: pygame.font.Font, text: str, max_px: int) -> str:
    if not text:
        return ""
    if font.size(text)[0] <= max_px:
        return text
    ellipsis = "..."
    while text and font.size(text + ellipsis)[0] > max_px:
        text = text[:-1]
    return text + ellipsis if text else ""


def _wrap(font: pygame.font.Font, text: str, max_px: int) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w) if cur else w
        if font.size(candidate)[0] > max_px:
            if cur:
                lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines
