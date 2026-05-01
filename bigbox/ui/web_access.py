"""Web UI Access — show scannable QR codes for the :8080 web UI.

Each QR encodes ``http://<ip>:8080/?token=<token>`` so a phone scans it
once and the auth middleware turns the query token into a 30-day
cookie. Two QRs are rendered when both are available: Tailscale (works
from anywhere on the user's tailnet) and LAN (works on the same Wi-Fi).

If ``segno`` isn't installed the view degrades gracefully to plain
text URLs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from bigbox import qr, theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


class WebAccessView:
    PORT = 8080

    def __init__(self) -> None:
        self.dismissed = False
        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.small_font = pygame.font.Font(None, theme.FS_SMALL)
        self.tiny_font = pygame.font.Font(None, 16)

        # Resolve once. The handheld doesn't churn IPs while this view
        # is open; cheap, but no need to hit `ip`/`tailscale` every frame.
        self.ts_ip = qr.tailscale_ipv4()
        self.lan_ip = qr.lan_ipv4()

        self._qr_cache: dict[str, pygame.Surface] = {}

    def _url(self, ip: str) -> str:
        return f"http://{ip}:{self.PORT}/"

    def _render_qr_surface(self, text: str, size_px: int) -> pygame.Surface | None:
        cache_key = f"{text}|{size_px}"
        cached = self._qr_cache.get(cache_key)
        if cached:
            return cached

        matrix = qr.make_matrix(text)
        if matrix is None:
            return None

        n = len(matrix)
        quiet = 4
        total = n + 2 * quiet
        # Pick the largest integer module size that fits.
        module_px = max(1, size_px // total)
        actual_px = module_px * total

        surf = pygame.Surface((actual_px, actual_px))
        surf.fill((255, 255, 255))
        for r, row in enumerate(matrix):
            for c, dark in enumerate(row):
                if not dark:
                    continue
                pygame.draw.rect(
                    surf, (0, 0, 0),
                    ((c + quiet) * module_px,
                     (r + quiet) * module_px,
                     module_px, module_px),
                )
        self._qr_cache[cache_key] = surf
        return surf

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            self.dismissed = True

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title = self.title_font.render("WEB UI ACCESS", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Two-up if both IPs are known, otherwise one centered.
        slots: list[tuple[str, str]] = []
        if self.ts_ip:
            slots.append(("TAILSCALE", self.ts_ip))
        if self.lan_ip:
            slots.append(("LAN", self.lan_ip))

        body_top = head_h + 8
        body_bottom = theme.SCREEN_H - 60
        body_h = body_bottom - body_top
        qr_size = min(body_h - 60, 280)

        if not slots:
            msg = self.body_font.render(
                "No IPs available — connect Wi-Fi or Tailscale and try again.",
                True, theme.ERR)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2))
            return

        if len(slots) == 1:
            xs = [theme.SCREEN_W // 2]
        else:
            xs = [theme.SCREEN_W // 4, 3 * theme.SCREEN_W // 4]

        for (label, ip), cx in zip(slots, xs):
            url = self._url(ip)
            qr_surf = self._render_qr_surface(url, qr_size)

            if qr_surf is not None:
                qx = cx - qr_surf.get_width() // 2
                qy = body_top + 4
                surf.blit(qr_surf, (qx, qy))
                text_y = qy + qr_surf.get_height() + 6
            else:
                # No segno → just show the URL as wrapped text.
                text_y = body_top + 60

            label_surf = self.body_font.render(label, True, theme.ACCENT)
            surf.blit(label_surf,
                      (cx - label_surf.get_width() // 2, text_y))

            ip_surf = self.small_font.render(
                f"{ip}:{self.PORT}", True, theme.FG)
            surf.blit(ip_surf,
                      (cx - ip_surf.get_width() // 2,
                       text_y + label_surf.get_height() + 2))

        # Footer hint
        hint = self.tiny_font.render(
            "Scan with phone — auth is automatic.    B: Back",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 28))
