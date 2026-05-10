"""Signal Foxhunter — Direction finding / proximity tracker.

Focuses on a single MAC (Wi-Fi or Bluetooth) and provides a large,
high-contrast RSSI display with audio feedback (beeps).
"""
from __future__ import annotations

import time
import os
import signal
import re
import subprocess
import threading
import array
from collections import deque

import pygame

from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


class FoxhunterView:
    def __init__(self, target_mac: str, target_type: str = "WIFI") -> None:
        self.dismissed = False
        self.mac = target_mac.lower()
        self.type = target_type
        self.rssi_history = deque(maxlen=50)
        self.current_rssi = -100
        self.last_seen = 0.0
        self.status_msg = "Starting..."
        self.mon_iface = "wlan0mon"
        self._stop = False
        self._thread: threading.Thread | None = None
        
        # Audio feedback state
        self._last_beep = 0.0
        
        self.f_title = pygame.font.Font(None, 44)
        self.f_huge = pygame.font.Font(None, 120)
        self.f_main = pygame.font.Font(None, 32)
        
        self._start_scan()

    def _start_scan(self) -> None:
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        if self.type == "WIFI":
            # Targeted signal sniffer using tcpdump
            # -e: show link-level headers (radiotap)
            # -s 0: capture full packet
            # -l: line buffered
            # -n: don't resolve names
            # Filter: match the target MAC in any address field
            cmd = [
                "tcpdump", "-i", self.mon_iface, "-e", "-s", "256", "-l", "-n",
                f"ether host {self.mac}"
            ]
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, preexec_fn=os.setsid
                )
                self.status_msg = f"Hunting on {self.mon_iface}..."
                
                # Regex to find signal in Radiotap header: " -75dBm signal" or " 75% signal"
                # Different drivers show it differently. Most show -XXdBm.
                sig_re = re.compile(r"(-?\d+)dBm")
                
                for line in proc.stdout:
                    if self._stop: break
                    match = sig_re.search(line)
                    if match:
                        val = int(match.group(1))
                        self.current_rssi = val
                        self.last_seen = time.time()
                        self.rssi_history.append(val)
                
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception as e:
                self.status_msg = f"Error: {e}"
        else:
            # Bluetooth hunting: use btmon to watch for ADV packets (passive).
            cmd = ["btmon"]
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, preexec_fn=os.setsid
                )
                
                # bluetoothctl scan must be ON for btmon to see things reliably
                subprocess.run(["bluetoothctl", "scan", "le", "on"], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                target_upper = self.mac.upper()
                rssi_re = re.compile(r"RSSI: (-?\d+)")
                
                capture_next_rssi = False
                for line in proc.stdout:
                    if self._stop: break
                    
                    if target_upper in line:
                        capture_next_rssi = True
                        continue
                        
                    if capture_next_rssi:
                        m = rssi_re.search(line)
                        if m:
                            val = int(m.group(1))
                            self.current_rssi = val
                            self.last_seen = time.time()
                            self.rssi_history.append(val)
                            capture_next_rssi = False
                
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception: pass

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            self._shutdown()
            return

    def _shutdown(self) -> None:
        self._stop = True
        self.dismissed = True

    def _play_beep(self) -> None:
        # Beep frequency/interval based on RSSI
        # RSSI -100 (silent) to -30 (fast beeps)
        if self.current_rssi < -95:
            return
            
        interval = max(0.1, (self.current_rssi + 100) / 70.0) # wait... 
        # higher signal (-30) -> smaller interval
        interval = 1.0 - ((self.current_rssi + 100) / 70.0)
        interval = max(0.05, interval)
        
        if time.time() - self._last_beep > interval:
            self._last_beep = time.time()
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                sample_rate = 44100
                duration = 0.05
                n_samples = int(sample_rate * duration)
                buf = array.array('h', [0] * n_samples)
                freq = 880 + (self.current_rssi + 100) * 10
                for i in range(n_samples):
                    t = i / sample_rate
                    buf[i] = 10000 if (int(t * freq * 2) % 2) else -10000
                sound = pygame.mixer.Sound(buffer=buf)
                sound.set_volume(0.2)
                sound.play()
            except Exception:
                pass

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Audio feedback
        self._play_beep()
        
        # Title
        title = self.f_title.render("SIGNAL FOXHUNTER", True, theme.ACCENT)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, 30))
        
        # Target
        target = self.f_main.render(f"TARGET: {self.mac.upper()}", True, theme.FG)
        surf.blit(target, (theme.SCREEN_W // 2 - target.get_width() // 2, 80))
        
        # Big RSSI
        col = theme.ACCENT if self.current_rssi > -70 else theme.FG_DIM
        if self.current_rssi > -50: col = theme.WARN
        
        val_str = f"{self.current_rssi} dBm"
        val_surf = self.f_huge.render(val_str, True, col)
        surf.blit(val_surf, (theme.SCREEN_W // 2 - val_surf.get_width() // 2, 140))
        
        # Signal Bar
        bar_w = 600
        bar_h = 40
        bar_x = (theme.SCREEN_W - bar_w) // 2
        bar_y = 280
        pygame.draw.rect(surf, (20, 20, 30), (bar_x, bar_y, bar_w, bar_h))
        
        fill_w = int(bar_w * ((self.current_rssi + 100) / 70.0))
        fill_w = max(0, min(bar_w, fill_w))
        pygame.draw.rect(surf, col, (bar_x, bar_y, fill_w, bar_h))
        pygame.draw.rect(surf, theme.DIVIDER, (bar_x, bar_y, bar_w, bar_h), 2)
        
        # History Graph
        gy = 350
        gw = 600
        gh = 80
        gx = (theme.SCREEN_W - gw) // 2
        pygame.draw.rect(surf, (5, 5, 10), (gx, gy, gw, gh))
        pygame.draw.rect(surf, theme.DIVIDER, (gx, gy, gw, gh), 1)
        
        if len(self.rssi_history) > 1:
            pts = []
            for i, val in enumerate(self.rssi_history):
                px = gx + (i * (gw / (self.rssi_history.maxlen - 1)))
                py = gy + gh - int((val + 100) * (gh / 70))
                py = max(gy + 2, min(gy + gh - 2, py))
                pts.append((px, py))
            pygame.draw.lines(surf, theme.ACCENT, False, pts, 3)

        # Hint
        hint = self.f_main.render("B: BACK  (move around to find peak)", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W // 2 - hint.get_width() // 2, theme.SCREEN_H - 40))

        # Status
        status = self.f_main.render(self.status_msg, True, theme.ACCENT)
        surf.blit(status, (20, theme.SCREEN_H - 40))
