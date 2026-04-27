"""BLE Mesh Chat UI — uses hcitool/hcidump for low-level BLE comms."""
from __future__ import annotations
import subprocess
import threading
import time
import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigbox.app import App

class BLEChatView:
    def __init__(self) -> None:
        self.dismissed = False
        self.scanning = False
        self.devices = [] # List of (mac, name)
        self.error_msg = ""
        self._stop = threading.Event()
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop.set()
            self.dismissed = True
        elif ev.button is Button.A:
            if not self.scanning:
                self._start_scan()
            else:
                self._stop_scan()

    def _start_scan(self):
        self.scanning = True
        self.devices = []
        self._stop.clear()
        threading.Thread(target=self._scan_loop, daemon=True).start()

    def _stop_scan(self):
        self._stop.set()
        self.scanning = False

    def _scan_loop(self):
        try:
            # Enable BLE
            subprocess.run(["sudo", "hciconfig", "hci0", "up"], timeout=2)
            # Scan using hcitool
            proc = subprocess.Popen(["sudo", "hcitool", "lescan", "--duplicates", "--passive"], 
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            
            while not self._stop.is_set():
                line = proc.stdout.readline().decode().strip()
                if line and " " in line:
                    mac, name = line.split(" ", 1)
                    if mac not in [d[0] for d in self.devices]:
                        self.devices.append((mac, name))
                        if len(self.devices) > 15: self.devices.pop(0)
                time.sleep(0.1)
            
            proc.terminate()
            subprocess.run(["sudo", "hciconfig", "hci0", "down"], timeout=2)
        except Exception as e:
            self.error_msg = str(e)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        title = self.title_font.render("BLE MESH :: DISCOVERY", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        status = "SCANNING..." if self.scanning else "IDLE"
        s_surf = self.body_font.render(f"STATUS: {status}", True, theme.FG)
        surf.blit(s_surf, (theme.PADDING, 70))

        y = 110
        for mac, name in self.devices:
            d_surf = self.body_font.render(f"{mac} | {name}", True, theme.FG_DIM)
            surf.blit(d_surf, (theme.PADDING, y))
            y += 24
            if y > theme.SCREEN_H - 60: break

        hint = self.body_font.render("A: Toggle Scan  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
