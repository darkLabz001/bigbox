"""Honeypot AP view — pick interface + SSID, watch the live log of
clients that try to connect.

Builds on :mod:`bigbox.honeypot` for the actual hostapd/dnsmasq
lifecycle. Tries hard to *not* trash the user's wifi config: only
ifaces returned by ``hardware.list_wifi_clients`` are offered, NM
detach happens before AP start, and on stop the iface is flushed +
handed back to NM via ``hardware.ensure_wifi_managed``.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pygame

from bigbox import background, hardware, honeypot, theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


PHASE_PICK_IFACE = "iface"
PHASE_RUNNING = "running"
PHASE_ERROR = "error"


class HoneypotView:
    title = "WIRELESS :: HONEYPOT"

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Pick a wireless interface"
        self.error_msg = ""

        self.ifaces = hardware.list_wifi_clients() or ["wlan0"]
        self.cursor = 0
        self.session: honeypot.Session | None = None
        self.ssid = honeypot.DEFAULT_SSID

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.small_font = pygame.font.Font(None, theme.FS_SMALL)
        self.mono_font = pygame.font.Font(None, 16)

    # ---------- input ------------------------------------------------------
    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            self.dismissed = True
            return

        if self.phase == PHASE_PICK_IFACE:
            if not self.ifaces:
                return
            if ev.button is Button.UP:
                self.cursor = (self.cursor - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN:
                self.cursor = (self.cursor + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                self._start(self.ifaces[self.cursor])
            elif ev.button is Button.X:
                def _cb(val):
                    if val:
                        self.ssid = val.strip()
                ctx.get_input("Honeypot SSID", _cb, self.ssid)
            return

        if self.phase == PHASE_RUNNING:
            if ev.button is Button.A:
                self._shutdown()
                self.phase = PHASE_PICK_IFACE
                self.status_msg = "Stopped"
            return

    def _start(self, iface: str) -> None:
        # Detach iface from NetworkManager so hostapd can grab it.
        # nmcli is enough; airmon-ng-style teardown isn't needed for an
        # open AP that doesn't require monitor mode.
        import subprocess
        try:
            subprocess.run(["nmcli", "device", "set", iface, "managed", "no"],
                           check=False, timeout=3,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass
        sess, msg = honeypot.start(iface, ssid=self.ssid)
        if sess is None:
            self.phase = PHASE_ERROR
            self.error_msg = msg
            self.status_msg = msg
            return
        self.session = sess
        self.phase = PHASE_RUNNING
        self.status_msg = msg
        background.register(
            "honeypot",
            f"Honeypot AP '{sess.ssid}' on {sess.iface}",
            "Wireless",
            stop=self._shutdown,
        )

    def _shutdown(self) -> None:
        if self.session is None:
            return
        try:
            honeypot.stop(self.session)
        except Exception:
            pass
        try:
            hardware.ensure_wifi_managed(self.session.iface)
        except Exception:
            pass
        self.session = None
        background.unregister("honeypot")

    # ---------- render -----------------------------------------------------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title = self.title_font.render(self.title, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        foot_h = 32
        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - foot_h - 16)

        if self.phase == PHASE_PICK_IFACE:
            self._render_pick(surf, body)
        elif self.phase == PHASE_ERROR:
            self._render_error(surf, body)
        else:
            self._render_running(surf, body)

        # Footer
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        status = self.small_font.render(self.status_msg[:80], True, theme.ACCENT)
        surf.blit(status, (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        hint = self.small_font.render(self._hint(), True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - theme.PADDING,
                         theme.SCREEN_H - foot_h + 8))

    def _hint(self) -> str:
        if self.phase == PHASE_PICK_IFACE:
            return "A: Start  X: Edit SSID  B: Back"
        if self.phase == PHASE_RUNNING:
            return "A: Stop  B: Back"
        return "B: Back"

    def _render_pick(self, surf, body) -> None:
        f = self.body_font
        ssid_label = f.render(f"SSID: {self.ssid}", True, theme.ACCENT)
        surf.blit(ssid_label, (body.x, body.y))

        sub = self.small_font.render(
            "Open AP — every probe / association / DHCP is logged.",
            True, theme.FG_DIM)
        surf.blit(sub, (body.x, body.y + 30))

        if not self.ifaces:
            err = f.render("No wlan interfaces found.", True, theme.ERR)
            surf.blit(err, (body.centerx - err.get_width() // 2,
                            body.centery))
            return

        list_y = body.y + 70
        for i, iface in enumerate(self.ifaces):
            sel = i == self.cursor
            rect = pygame.Rect(body.centerx - 200, list_y + i * 40, 400, 36)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=4)
            color = theme.ACCENT if sel else theme.FG
            label = f.render(iface, True, color)
            surf.blit(label, (rect.x + 14, rect.y + 6))

    def _render_error(self, surf, body) -> None:
        f = self.body_font
        err = f.render(self.error_msg or "error", True, theme.ERR)
        surf.blit(err, (body.centerx - err.get_width() // 2,
                        body.centery - 30))
        hint = self.small_font.render(
            "B: Back. Most common cause: hostapd or dnsmasq not installed.",
            True, theme.FG_DIM)
        surf.blit(hint, (body.centerx - hint.get_width() // 2,
                         body.centery + 8))

    def _render_running(self, surf, body) -> None:
        if self.session is None:
            return
        f = self.body_font

        elapsed = int(time.time() - self.session.started_at)
        header = f.render(
            f"AP '{self.session.ssid}' on {self.session.iface}  ·  uptime {elapsed}s",
            True, theme.ACCENT)
        surf.blit(header, (body.x, body.y))

        # Two-pane log view: hostapd events on the left, dnsmasq on the right.
        col_w = body.width // 2 - 4
        log_top = body.y + 36
        log_h = body.height - 36

        for i, (label, log_path, color) in enumerate([
            ("HOSTAPD (clients)",
             self.session.hostapd_log, theme.ACCENT),
            ("DNSMASQ (DHCP / DNS)",
             self.session.dnsmasq_log, theme.WARN),
        ]):
            x = body.x + i * (col_w + 8)
            pygame.draw.rect(surf, (5, 5, 10), (x, log_top, col_w, log_h))
            pygame.draw.rect(surf, theme.DIVIDER, (x, log_top, col_w, log_h), 1)
            surf.blit(self.small_font.render(label, True, color),
                      (x + 6, log_top + 4))
            line_h = 16
            visible = (log_h - 24) // line_h
            lines = honeypot.tail_log(log_path, n=visible)
            for li, line in enumerate(lines):
                ls = self.mono_font.render(line[:60], True, theme.FG_DIM)
                surf.blit(ls, (x + 6, log_top + 22 + li * line_h))
