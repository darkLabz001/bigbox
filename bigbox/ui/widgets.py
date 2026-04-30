"""Status bar at the top, and a full-screen scrollable result view."""
from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Callable

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent


class StatusBar:
    """Thin bar across the top: clock, hostname, optional IP."""

    def __init__(self) -> None:
        self._hostname = socket.gethostname()
        self._last_ip_check = 0.0
        self._ip = "—"
        self._ts_ip = ""
        self._last_ts_check = 0.0

    def _refresh_ip(self) -> None:
        now = time.monotonic()
        if now - self._last_ip_check < 5.0:
            return
        self._last_ip_check = now
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.1)
                s.connect(("10.255.255.255", 1))   # never actually sends a packet
                self._ip = s.getsockname()[0]
        except OSError:
            self._ip = "—"

    def _refresh_tailscale_ip(self) -> None:
        import subprocess
        now = time.monotonic()
        if now - self._last_ts_check < 10.0:
            return
        self._last_ts_check = now
        try:
            # tailscale ip -4 is fast and returns just the IP
            out = subprocess.check_output(["tailscale", "ip", "-4"], text=True, stderr=subprocess.DEVNULL).strip()
            self._ts_ip = out
        except Exception:
            self._ts_ip = ""

    def render(self, surf: pygame.Surface, app: Optional[App] = None) -> None:
        self._refresh_ip()
        self._refresh_tailscale_ip()
        bar = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, bar)
        pygame.draw.line(surf, theme.DIVIDER, (0, bar.bottom - 1), (bar.right, bar.bottom - 1))
        font = pygame.font.Font(None, theme.FS_STATUS)
        left = font.render(f"bigbox · {self._hostname}", True, theme.FG_DIM)
        surf.blit(left, (theme.PADDING, (bar.height - left.get_height()) // 2))

        # Update Notification
        if app and getattr(app, "update_checker", None) and app.update_checker.update_ready:
            import math
            pulse = int(127 + 128 * math.sin(time.time() * 4))
            notif = font.render("UPDATE AVAILABLE", True, theme.ACCENT)
            notif.set_alpha(pulse)
            surf.blit(notif, (theme.SCREEN_W // 2 - notif.get_width() // 2, (bar.height - notif.get_height()) // 2))

        # Recording Indicator
        if app and getattr(app, "recording_proc", None):
            import math
            pulse = int(127 + 128 * math.sin(time.time() * 8))
            rec_surf = font.render("• REC", True, theme.ERR)
            rec_surf.set_alpha(pulse)
            # Place it to the right of the hostname
            surf.blit(rec_surf, (theme.PADDING + left.get_width() + 20, (bar.height - rec_surf.get_height()) // 2))

        # Display IPs: Local and optionally Tailscale
        ip_str = self._ip
        if self._ts_ip:
            ip_str = f"TS: {self._ts_ip}   {ip_str}"

        right = font.render(
            f"{ip_str}   {datetime.now().strftime('%H:%M')}",
            True,
            theme.FG_DIM,
        )
        surf.blit(
            right,
            (bar.right - right.get_width() - theme.PADDING, (bar.height - right.get_height()) // 2),
        )


class ResultView:
    """Full-screen scrollable text. Used to display tool output. B dismisses."""

    def __init__(self, title: str, text: str) -> None:
        self.title = title
        self.lines = text.splitlines() or [""]
        self.scroll = 0
        self.dismissed = False

    def append(self, text: str) -> None:
        # Append streaming output.
        new = text.splitlines()
        if not new:
            return
        # If the previous chunk ended without a newline, glue.
        if self.lines and not self.lines[-1].endswith("\n") and not text.startswith("\n"):
            self.lines[-1] += new[0]
            self.lines.extend(new[1:])
        else:
            self.lines.extend(new)
        # Auto-stick to bottom unless user scrolled up.
        # (Simple heuristic: if user is within 2 lines of the end, stay pinned.)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B and not ev.repeat:
            self.dismissed = True
        elif ev.button is Button.UP:
            self.scroll = max(0, self.scroll - 1)
        elif ev.button is Button.DOWN:
            self.scroll = min(max(0, len(self.lines) - 1), self.scroll + 1)
        elif ev.button is Button.LL and not ev.repeat:
            self.scroll = max(0, self.scroll - 10)
        elif ev.button is Button.RR and not ev.repeat:
            self.scroll = min(max(0, len(self.lines) - 1), self.scroll + 10)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        body_font = pygame.font.Font(None, theme.FS_BODY)
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        pygame.draw.line(surf, theme.DIVIDER, (0, head.bottom - 1), (head.right, head.bottom - 1))
        title = title_font.render(self.title, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        hint_font = pygame.font.Font(None, theme.FS_SMALL)
        hint = hint_font.render("UP/DOWN scroll · LL/RR page · B back", True, theme.FG_DIM)
        surf.blit(
            hint,
            (head.right - hint.get_width() - theme.PADDING, (head.height - hint.get_height()) // 2),
        )

        body_top = head.bottom + 6
        line_h = body_font.get_linesize()
        max_visible = (theme.SCREEN_H - body_top - 6) // line_h
        for i in range(max_visible):
            li = self.scroll + i
            if li >= len(self.lines):
                break
            text = self.lines[li]
            if len(text) > 120:    # crude wrap-protect for a fixed-width-ish font
                text = text[:117] + "..."
            surf.blit(
                body_font.render(text, True, theme.FG),
                (theme.PADDING, body_top + i * line_h),
            )

        # Scrollbar
        if len(self.lines) > max_visible:
            sb_w = 4
            track = pygame.Rect(theme.SCREEN_W - sb_w - 2, body_top, sb_w, max_visible * line_h)
            pygame.draw.rect(surf, theme.DIVIDER, track)
            thumb_h = max(20, int(track.height * max_visible / len(self.lines)))
            thumb_y = track.y + int(track.height * self.scroll / max(1, len(self.lines)))
            pygame.draw.rect(surf, theme.ACCENT_DIM, pygame.Rect(track.x, thumb_y, sb_w, thumb_h))


class MenuView:
    """A centered modal menu for system actions. Dismissed by B."""

    def __init__(self, title: str, actions: list[tuple[str, Callable[[], None]]]) -> None:
        self.title = title
        self.actions = actions
        self.selected = 0
        self.dismissed = False

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B and not ev.repeat:
            self.dismissed = True
        elif ev.button is Button.UP:
            self.selected = (self.selected - 1) % len(self.actions)
        elif ev.button is Button.DOWN:
            self.selected = (self.selected + 1) % len(self.actions)
        elif ev.button is Button.A and not ev.repeat:
            self.actions[self.selected][1]()
            self.dismissed = True

    def render(self, surf: pygame.Surface) -> None:
        # Darken the background.
        overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        surf.blit(overlay, (0, 0))

        w, h = 320, 240
        rect = pygame.Rect((theme.SCREEN_W - w) // 2, (theme.SCREEN_H - h) // 2, w, h)
        pygame.draw.rect(surf, theme.BG_ALT, rect)
        pygame.draw.rect(surf, theme.ACCENT, rect, width=2)

        font = pygame.font.Font(None, theme.FS_TITLE)
        body_font = pygame.font.Font(None, theme.FS_BODY)

        title = font.render(self.title, True, theme.ACCENT)
        surf.blit(title, (rect.x + theme.PADDING, rect.y + theme.PADDING))
        pygame.draw.line(
            surf, theme.DIVIDER,
            (rect.x, rect.y + 44),
            (rect.right, rect.y + 44)
        )

        for i, (label, _) in enumerate(self.actions):
            selected = i == self.selected
            color = theme.SELECTION if selected else theme.FG
            text = body_font.render(label, True, color)
            y = rect.y + 60 + i * 36
            if selected:
                row_rect = pygame.Rect(rect.x + 4, y - 4, rect.width - 8, 32)
                pygame.draw.rect(surf, theme.SELECTION_BG, row_rect)
            surf.blit(text, (rect.x + 20, y))

