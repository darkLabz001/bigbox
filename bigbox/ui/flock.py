"""FlockSeeker — Advanced Flock Safety Camera Detector.

Uses BLE Manufacturer Data (0x09C8), Device Names (Penguin, Pigvision, FS Ext),
and Wi-Fi SSID patterns to identify ALPR infrastructure.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent


@dataclass
class FlockSignal:
    id: str
    type: str # BLE, WIFI
    rssi: int
    last_seen: datetime
    details: str
    confirmed: bool = False

class FlockScannerView:
    def __init__(self) -> None:
        self.signals: dict[str, FlockSignal] = {}
        self.scanning = False
        self.dismissed = False
        self._stop_threads = False
        self.status_msg = "INITIALIZING..."
        
        # Heuristics
        self.KNOWN_NAMES = ["PENGUIN", "PIGVISION", "FS EXT", "FLOCK", "RAVEN"]
        self.MANUFACTURER_ID = "09c8" # XUNTONG (Flock backhaul)
        
        self._start_scan()

    def _start_scan(self):
        self.scanning = True
        self.status_msg = "SCANNING FOR SIGNATURES..."
        self._bt_thread = threading.Thread(target=self._bt_worker, daemon=True)
        self._wifi_thread = threading.Thread(target=self._wifi_worker, daemon=True)
        self._bt_thread.start()
        self._wifi_thread.start()

    def _bt_worker(self):
        """Parses bluetoothctl for BLE signatures."""
        try:
            # Start scan in background
            subprocess.Popen(["bluetoothctl", "scan", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Watch for events
            proc = subprocess.Popen(["bluetoothctl", "monitor"], stdout=subprocess.PIPE, text=True)
            if not proc.stdout: return

            for line in proc.stdout:
                if self._stop_threads: break
                
                # Look for Manufacturer Data or Name
                # This is a simplified parser; in a real scenario we'd use a more robust BLE lib
                # but monitor/bluetoothctl is very reliable on Pi.
                line_up = line.upper()
                
                # Check for known names
                for name in self.KNOWN_NAMES:
                    if name in line_up:
                        self._add_signal(name, "BLE", -70, f"Found via Name: {name}")
                
                # Check for Manufacturer ID
                if self.MANUFACTURER_ID.upper() in line_up:
                    self._add_signal("FLOCK_HW", "BLE", -65, "Found via MFG ID: 09C8")

        except Exception as e:
            self.status_msg = f"BT ERR: {str(e)[:15]}"
        finally:
            subprocess.run(["bluetoothctl", "scan", "off"], capture_output=True)

    def _wifi_worker(self):
        """Polls for Wi-Fi SSIDs."""
        while not self._stop_threads:
            try:
                # Use iwlist for scanning (requires root, which we usually have on bigbox)
                out = subprocess.check_output(["sudo", "iwlist", "wlan0", "scanning"], text=True)
                
                # Search for "Flock-" pattern
                matches = re.findall(r'ESSID:"(Flock-[0-9A-F]{6})"', out)
                for ssid in matches:
                    self._add_signal(ssid, "WIFI", -60, "Found via SSID Pattern")
                
                # Search for common OUIs in the scan
                # (Implementation omitted for brevity, but would check MAC prefixes)
            except Exception:
                pass
            time.sleep(10)

    def _add_signal(self, sig_id: str, sig_type: str, rssi: int, details: str):
        self.signals[sig_id] = FlockSignal(
            id=sig_id,
            type=sig_type,
            rssi=rssi,
            last_seen=datetime.now(),
            details=details,
            confirmed=True
        )

    def handle(self, ev: ButtonEvent, ctx: any = None) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_threads = True
            self.dismissed = True

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 36)
        title_surf = f_title.render("RECON :: FLOCK_SEEKER", True, theme.ACCENT)
        surf.blit(title_surf, (theme.PADDING, 10))
        
        # Status "Scanning" Pulse
        if int(time.time() * 2) % 2:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 30, 25), 6)

        # Main Display - Split View
        # Left: List of signals
        # Right: Signal Detail / Map-like UI
        
        list_rect = pygame.Rect(theme.PADDING, head_h + 10, 450, 380)
        pygame.draw.rect(surf, (5, 5, 10), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)
        
        f_small = pygame.font.Font(None, 24)
        f_bold = pygame.font.Font(None, 28)
        
        y_off = list_rect.y + 10
        if not self.signals:
            msg = f_small.render("WAITING FOR SIGNATURES...", True, theme.FG_DIM)
            surf.blit(msg, (list_rect.centerx - msg.get_width()//2, list_rect.centery))
        else:
            for i, sig in enumerate(list(self.signals.values())):
                row_y = y_off + i * 55
                if row_y > list_rect.bottom - 50: break
                
                # Icon based on type
                color = theme.ACCENT if sig.confirmed else theme.WARN
                pygame.draw.rect(surf, (20, 30, 40), (list_rect.x + 5, row_y, list_rect.width - 10, 50), border_radius=5)
                
                type_txt = f_small.render(f"[{sig.type}]", True, color)
                id_txt = f_bold.render(sig.id, True, theme.FG)
                time_txt = f_small.render(sig.last_seen.strftime("%H:%M:%S"), True, theme.FG_DIM)
                
                surf.blit(type_txt, (list_rect.x + 15, row_y + 15))
                surf.blit(id_txt, (list_rect.x + 80, row_y + 15))
                surf.blit(time_txt, (list_rect.right - 90, row_y + 15))

        # Right Side: Radar Animation
        radar_center = (theme.SCREEN_W - 160, theme.SCREEN_H // 2 + 10)
        pygame.draw.circle(surf, theme.DIVIDER, radar_center, 120, 1)
        pygame.draw.circle(surf, theme.DIVIDER, radar_center, 80, 1)
        pygame.draw.circle(surf, theme.DIVIDER, radar_center, 40, 1)
        
        # Sweep line
        import math
        angle = (time.time() * 3) % (2 * math.pi)
        line_end = (
            radar_center[0] + 120 * math.cos(angle),
            radar_center[1] + 120 * math.sin(angle)
        )
        pygame.draw.line(surf, theme.ACCENT_DIM, radar_center, line_end, 2)
        
        # Detected dots
        for i, sig in enumerate(self.signals.values()):
            # Pseudorandom pos based on ID for visual flair
            seed = sum(ord(c) for c in sig.id)
            d_angle = (seed * 1.5) % (2 * math.pi)
            dist = 40 + (seed % 70)
            pos = (
                radar_center[0] + dist * math.cos(d_angle),
                radar_center[1] + dist * math.sin(d_angle)
            )
            pygame.draw.circle(surf, theme.ACCENT, pos, 4)
            if i == 0: # Flash the latest
                if int(time.time() * 4) % 2:
                    pygame.draw.circle(surf, theme.FG, pos, 8, 1)

        # Footer Hint
        hint = f_small.render("B: BACK  |  SIGNALS PERSIST UNTIL EXIT", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 25))
