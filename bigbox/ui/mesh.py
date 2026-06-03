"""Meshtastic UI — local LoRa mesh (USB dongle) + global internet mesh (MQTT).

A: compose & send a broadcast    X: toggle GLOBAL bridge
Y: rescan / reconnect dongle      B: back
UP/DOWN: scroll the message feed
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.meshtastic_link import MeshtasticLink

if TYPE_CHECKING:
    from bigbox.app import App

_SRC_COLOR = {
    "ME": theme.ACCENT,
    "LORA": theme.FG,
    "GLOBAL": theme.WARN,
    "SYS": theme.FG_DIM,
}
_SRC_TAG = {"ME": "TX", "LORA": "RF", "GLOBAL": "NET", "SYS": "··"}


class MeshtasticView:
    def __init__(self) -> None:
        self.dismissed = False
        self.link = MeshtasticLink()
        self.link.start()
        self.scroll = 0          # lines scrolled up from the bottom
        self._composing = False

        self.title_font = pygame.font.Font(None, 38)
        self.body_font = pygame.font.Font(None, 26)
        self.small_font = pygame.font.Font(None, 22)
        self.mono_font = pygame.font.Font(None, 24)

    # ---- input ----------------------------------------------------------

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            self.link.stop()
            self.dismissed = True
        elif ev.button is Button.A:
            self._composing = True
            ctx.get_input("Mesh message", self._on_message)
        elif ev.button is Button.X:
            self.link.toggle_global()
        elif ev.button is Button.Y:
            self.link.reconnect()
        elif ev.button is Button.UP:
            self.scroll += 1
        elif ev.button is Button.DOWN:
            self.scroll = max(0, self.scroll - 1)

    def _stop(self) -> None:
        """Cleanup hook used by the app's crash-recovery path."""
        self.link.stop()

    def _on_message(self, text: str | None) -> None:
        self._composing = False
        if text:
            self.link.send(text)
            self.scroll = 0  # snap to newest

    # ---- render ---------------------------------------------------------

    def render(self, surf: pygame.Surface) -> None:
        st = self.link.snapshot()
        surf.fill(theme.BG)
        pad = theme.PADDING

        title = self.title_font.render("MESHTASTIC :: MESH UPLINK", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        # connection-state pill (top right)
        self._draw_status_pill(surf, st)

        # info strip
        y = 58
        info = self._info_lines(st)
        for label, value, color in info:
            lab = self.small_font.render(label, True, theme.FG_DIM)
            surf.blit(lab, (pad, y))
            val = self.small_font.render(value, True, color)
            surf.blit(val, (pad + 150, y))
            y += 26

        # divider
        y += 4
        pygame.draw.line(surf, theme.DIVIDER, (pad, y), (theme.SCREEN_W - pad, y), 1)
        feed_top = y + 8
        feed_bottom = theme.SCREEN_H - 40

        self._draw_feed(surf, st, feed_top, feed_bottom)
        self._draw_footer(surf, st)

    def _info_lines(self, st):
        node = st.my_name or "—"
        if st.my_id:
            node = f"{node}  {st.my_id}"
        batt = f"{st.battery}%" if st.battery >= 0 else "—"
        region = st.region.split(".")[-1] if st.region else "—"
        if st.global_enabled:
            gtext = "CONNECTED" if st.mqtt_connected else "linking…"
            gcolor = theme.ACCENT if st.mqtt_connected else theme.WARN
        else:
            gtext = "OFF"
            gcolor = theme.FG_DIM
        return [
            ("NODE", node, theme.FG),
            ("REGION / CH", f"{region} / {st.channel}", theme.FG),
            ("NEIGHBORS", f"{st.num_nodes}   BATT {batt}", theme.FG),
            ("GLOBAL MESH", gtext, gcolor),
            ("TRAFFIC", f"TX {st.sent}   RF {st.recv_lora}   NET {st.recv_global}", theme.FG_DIM),
        ]

    def _draw_status_pill(self, surf, st) -> None:
        phase = st.phase
        # A live global bridge is a "good" state even with no local radio.
        if phase != "ONLINE" and st.mqtt_connected:
            text, color = "GLOBAL ONLY", theme.ACCENT
        else:
            text, color = {
                "ONLINE": ("DONGLE ONLINE", theme.ACCENT),
                "CONNECTING": ("CONNECTING…", theme.WARN),
                "INIT": ("STARTING…", theme.WARN),
                "NO_DEVICE": ("NO RADIO", theme.WARN),
                "NO_LIB": ("LIB MISSING", theme.ERR),
                "ERROR": ("LINK ERROR", theme.ERR),
            }.get(phase, (phase, theme.FG_DIM))
        surf_t = self.small_font.render(text, True, color)
        x = theme.SCREEN_W - theme.PADDING - surf_t.get_width()
        surf.blit(surf_t, (x, theme.PADDING + 6))
        if phase in ("ONLINE", "CONNECTING") and int(time.time() * 2) % 2:
            pygame.draw.circle(surf, color, (x - 14, theme.PADDING + 14), 5)

    def _draw_feed(self, surf, st, top: int, bottom: int) -> None:
        line_h = 24
        max_lines = max(1, (bottom - top) // line_h)
        msgs = st.messages

        if not msgs:
            tip = ("Waiting for mesh traffic… A: broadcast"
                   if st.global_enabled else
                   "No traffic yet.  X: connect GLOBAL mesh   A: broadcast")
            hint = self.body_font.render(tip, True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, top + 4))
            self._maybe_error(surf, st, top + 34)
            return

        # newest at bottom; apply scroll offset
        end = len(msgs) - self.scroll
        end = max(min(end, len(msgs)), 1)
        start = max(0, end - max_lines)
        view = msgs[start:end]

        y = bottom - len(view) * line_h
        for m in view:
            color = _SRC_COLOR.get(m.source, theme.FG)
            tag = _SRC_TAG.get(m.source, "··")
            stamp = time.strftime("%H:%M", time.localtime(m.ts))
            prefix = f"{stamp} [{tag}] {m.sender}: "
            line = self._fit(prefix + m.text, theme.SCREEN_W - 2 * theme.PADDING)
            surf.blit(self.mono_font.render(line, True, color), (theme.PADDING, y))
            y += line_h

        if self.scroll > 0:
            up = self.small_font.render("▲ scrolled — DOWN to catch up", True, theme.WARN)
            surf.blit(up, (theme.SCREEN_W - theme.PADDING - up.get_width(), top))

    def _maybe_error(self, surf, st, y: int) -> None:
        if st.error and st.phase in ("NO_DEVICE", "NO_LIB", "ERROR"):
            err = self._fit(st.error, theme.SCREEN_W - 2 * theme.PADDING)
            surf.blit(self.small_font.render(err, True, theme.ERR), (theme.PADDING, y))

    def _draw_footer(self, surf, st) -> None:
        gl = "GLOBAL ON" if st.global_enabled else "GLOBAL OFF"
        hint = f"A: Send   X: {gl}   Y: Rescan   B: Back"
        s = self.small_font.render(hint, True, theme.FG_DIM)
        surf.blit(s, (theme.PADDING, theme.SCREEN_H - 28))

    def _fit(self, text: str, max_w: int) -> str:
        """Truncate a single line to fit max_w pixels in mono_font."""
        if self.mono_font.size(text)[0] <= max_w:
            return text
        while text and self.mono_font.size(text + "…")[0] > max_w:
            text = text[:-1]
        return text + "…"
