"""FlockSeeker Ultra — Advanced ALPR Interception Suite.

Enhanced detection using:
- MAC OUI Database (Falcon/Raven/Lite-On)
- BLE Manufacturer Data (XUNTONG 0x09C8)
- Real-time Signal Strength (RSSI) Tracking
- Multi-signal Hit Confidence Scoring
- Visual Proximity Indicators
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
import math

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

# MAC Prefixes associated with Flock infrastructure
OUI_DB = {
    "74:4C:A1": "FALCON_CAM",
    "9C:2F:9D": "FALCON_CAM",
    "EC:62:60": "RAVEN_AUDIO", # Shot detector
    "54:E8:23": "LITEON_BACKHAUL",
    "7E:B8:71": "LITEON_BACKHAUL",
    "48:B0:2D": "FLOCK_UNIT",
}

@dataclass
class FlockSignal:
    id: str
    mac: str
    type: str  # BLE, WIFI
    rssi: int
    last_seen: datetime
    details: str
    confidence: int = 20 # 0-100
    hits: int = 1
    history: list[int] = field(default_factory=list)

class FlockScannerView:
    def __init__(self) -> None:
        self.signals: dict[str, FlockSignal] = {}
        self.dismissed = False
        self._stop_threads = False
        self.status_msg = "ENGAGING SENSORS..."
        
        # Heuristics
        self.KNOWN_NAMES = ["PENGUIN", "PIGVISION", "FS EXT", "FLOCK", "RAVEN", "FALCON"]
        self.MANUFACTURER_ID = "09c8"
        
        self.selected_idx = 0
        self._start_scan()

    def _start_scan(self):
        self._bt_thread = threading.Thread(target=self._bt_worker, daemon=True)
        self._wifi_thread = threading.Thread(target=self._wifi_worker, daemon=True)
        self._bt_thread.start()
        self._wifi_thread.start()

    def _bt_worker(self):
        """Advanced BLE monitor with RSSI tracking."""
        try:
            # Ensure scan is on
            subprocess.run(["bluetoothctl", "scan", "on"], capture_output=True)
            
            # monitor mode provides raw attributes including RSSI and ManufacturerData
            proc = subprocess.Popen(["bluetoothctl", "monitor"], stdout=subprocess.PIPE, text=True)
            if not proc.stdout: return

            current_mac = ""
            for line in proc.stdout:
                if self._stop_threads: break
                
                # Extract MAC and RSSI from monitor output
                # Example: [mgmt] [0x0001] Event: Device Found (0x01)
                #          Address: 74:4C:A1:XX:XX:XX (Public)
                #          RSSI: -72 dBm (0xb8)
                mac_match = re.search(r'Address: ([0-9A-F:]{17})', line)
                if mac_match:
                    current_mac = mac_match.group(1)
                
                rssi_match = re.search(r'RSSI: (-\d+)', line)
                if rssi_match and current_mac:
                    rssi = int(rssi_match.group(1))
                    self._process_bt_hit(current_mac, rssi, line)

        except Exception as e:
            self.status_msg = "SENSORS OFFLINE"
        finally:
            subprocess.run(["bluetoothctl", "scan", "off"], capture_output=True)

    def _process_bt_hit(self, mac: str, rssi: int, raw_line: str):
        oui = mac[:8].upper()
        line_up = raw_line.upper()
        
        is_flock = False
        details = ""
        confidence = 10
        
        # 1. Check Manufacturer ID (High Confidence)
        if self.MANUFACTURER_ID.upper() in line_up:
            is_flock = True
            details = "FLOCK_MFG_DATA_DETECTED"
            confidence += 60
            
        # 2. Check Name (Medium-High Confidence)
        for name in self.KNOWN_NAMES:
            if name in line_up:
                is_flock = True
                details = f"SIGNATURE_MATCH: {name}"
                confidence += 40
                
        # 3. Check OUI (Medium Confidence)
        if oui in OUI_DB:
            is_flock = True
            details = f"HARDWARE_MATCH: {OUI_DB[oui]}"
            confidence += 30

        if is_flock:
            sig_id = f"ALPR_{mac[-5:].replace(':','')}"
            if sig_id not in self.signals:
                self.signals[sig_id] = FlockSignal(
                    id=sig_id, mac=mac, type="BLE", rssi=rssi, 
                    last_seen=datetime.now(), details=details, confidence=min(100, confidence)
                )
                # Auto-loot high confidence hits
                if confidence >= 80:
                    self._save_loot(self.signals[sig_id])
            else:
                s = self.signals[sig_id]
                s.rssi = rssi
                s.last_seen = datetime.now()
                s.hits += 1
                s.confidence = min(100, s.confidence + 2)
                s.history.append(rssi)
                if len(s.history) > 20: s.history.pop(0)

    def _save_loot(self, sig: FlockSignal):
        """Persists the detection to the loot folder."""
        import os
        loot_dir = "loot"
        if not os.path.exists(loot_dir):
            os.makedirs(loot_dir)
            
        fname = os.path.join(loot_dir, "flock_intel.txt")
        timestamp = sig.last_seen.strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"[{timestamp}] ID: {sig.id} | MAC: {sig.mac} | TYPE: {sig.type}\n"
            f"    CONFIDENCE: {sig.confidence}% | DETAILS: {sig.details}\n"
            f"    SIGNAL: {sig.rssi}dBm\n"
            "--------------------------------------------------\n"
        )
        
        # Avoid duplicate recent entries
        try:
            if os.path.exists(fname):
                with open(fname, "r") as f:
                    content = f.read()
                    if sig.mac in content and timestamp[:10] in content:
                        return
            
            with open(fname, "a") as f:
                f.write(entry)
        except Exception:
            pass

    def _wifi_worker(self):
        """Optimized Wi-Fi Polling."""
        while not self._stop_threads:
            try:
                # iw scan is more detailed than iwlist
                out = subprocess.check_output(["sudo", "iw", "dev", "wlan0", "scan"], text=True)
                
                # Scan chunks by BSS
                chunks = out.split("BSS ")
                for chunk in chunks:
                    # Look for Flock SSID
                    ssid_match = re.search(r'SSID: (Flock-[0-9A-F]{6})', chunk)
                    mac_match = re.search(r'([0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2})', chunk)
                    
                    if ssid_match and mac_match:
                        ssid = ssid_match.group(1)
                        mac = mac_match.group(1).upper()
                        # Extract RSSI (signal)
                        sig_match = re.search(r'signal: (-\d+\.\d+)', chunk)
                        rssi = int(float(sig_match.group(1))) if sig_match else -70
                        
                        self._add_wifi_signal(ssid, mac, rssi)
            except Exception:
                pass
            time.sleep(8)

    def _add_wifi_signal(self, ssid: str, mac: str, rssi: int):
        if ssid not in self.signals:
            self.signals[ssid] = FlockSignal(
                id=ssid, mac=mac, type="WIFI", rssi=rssi,
                last_seen=datetime.now(), details="ACTIVE_HOTSPOT_DETECTED", confidence=90
            )
        else:
            s = self.signals[ssid]
            s.rssi = rssi
            s.last_seen = datetime.now()
            s.hits += 1

    def handle(self, ev: ButtonEvent, ctx: any = None) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_threads = True
            self.dismissed = True
        elif ev.button is Button.UP:
            self.selected_idx = max(0, self.selected_idx - 1)
        elif ev.button is Button.DOWN:
            self.selected_idx = min(len(self.signals) - 1, self.selected_idx + 1)
        elif ev.button is Button.A:
            # Manual Loot Save
            sorted_sigs = sorted(self.signals.values(), key=lambda x: x.last_seen, reverse=True)
            if self.selected_idx < len(sorted_sigs):
                self._save_loot(sorted_sigs[self.selected_idx])
                if hasattr(ctx, "toast"):
                    ctx.toast("SAVED TO LOOT")

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header with Scanner Line
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: FLOCK_SEEKER_ULTRA", True, theme.ACCENT), (theme.PADDING, 8))
        
        # Signal Count
        f_small = pygame.font.Font(None, 22)
        count_txt = f_small.render(f"ACTIVE_NODES: {len(self.signals)}", True, theme.FG)
        surf.blit(count_txt, (theme.SCREEN_W - 160, 12))

        # Main Layout
        list_w = 480
        list_rect = pygame.Rect(theme.PADDING, head_h + 10, list_w, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 8), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)

        sorted_sigs = sorted(self.signals.values(), key=lambda x: x.last_seen, reverse=True)
        
        # List View
        for i, sig in enumerate(sorted_sigs):
            y = list_rect.y + i * 50
            if y > list_rect.bottom - 45: break
            
            sel = i == self.selected_idx
            bg = (20, 35, 50) if sel else (10, 15, 25)
            row_rect = pygame.Rect(list_rect.x + 5, y + 5, list_rect.width - 10, 45)
            pygame.draw.rect(surf, bg, row_rect, border_radius=4)
            if sel: pygame.draw.rect(surf, theme.ACCENT, row_rect, 1, border_radius=4)
            
            # Confidence Bar
            conf_w = int((row_rect.width - 20) * (sig.confidence / 100))
            pygame.draw.rect(surf, (0, 40, 0), (row_rect.x + 10, row_rect.bottom - 4, row_rect.width - 20, 2))
            pygame.draw.rect(surf, theme.ACCENT, (row_rect.x + 10, row_rect.bottom - 4, conf_w, 2))

            # ID and Type
            id_surf = f_small.render(f"{sig.id} ({sig.type})", True, theme.FG if sig.confidence > 50 else theme.FG_DIM)
            surf.blit(id_surf, (row_rect.x + 10, row_rect.y + 10))
            
            # RSSI Bar (Signal Strength)
            ss = max(0, min(100, (sig.rssi + 100) * 1.5))
            for b in range(5):
                b_color = theme.ACCENT if ss > (b * 20) else (40, 40, 40)
                pygame.draw.rect(surf, b_color, (row_rect.right - 60 + b*10, row_rect.y + 15, 6, 12))

        # Right Side: Detail View & Proximity
        detail_x = list_rect.right + 20
        detail_w = theme.SCREEN_W - detail_x - theme.PADDING
        
        if sorted_sigs and self.selected_idx < len(sorted_sigs):
            self._render_detail(surf, detail_x, head_h + 10, detail_w, sorted_sigs[self.selected_idx])
        else:
            msg = f_small.render("NO TARGET SELECTED", True, theme.FG_DIM)
            surf.blit(msg, (detail_x + 20, theme.SCREEN_H // 2))

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - 35), (theme.SCREEN_W, theme.SCREEN_H - 35))
        hint = f_small.render("UP/DOWN: Navigate  A: SAVE TO LOOT  B: EXIT", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 26))

    def _render_detail(self, surf: pygame.Surface, x: int, y: int, w: int, sig: FlockSignal):
        f_med = pygame.font.Font(None, 24)
        f_bold = pygame.font.Font(None, 28)
        
        # Detail Header
        surf.blit(f_bold.render("TARGET_INTEL", True, theme.ACCENT), (x, y))
        pygame.draw.line(surf, theme.DIVIDER, (x, y+25), (x+w, y+25))
        
        # Info Block
        rows = [
            ("MAC:", sig.mac),
            ("TYPE:", sig.type),
            ("RSSI:", f"{sig.rssi} dBm"),
            ("HITS:", str(sig.hits)),
            ("SIGHTED:", sig.last_seen.strftime("%H:%M:%S")),
        ]
        
        for i, (label, val) in enumerate(rows):
            surf.blit(f_med.render(label, True, theme.FG_DIM), (x, y + 40 + i*25))
            surf.blit(f_med.render(val, True, theme.FG), (x + 80, y + 40 + i*25))

        # Proximity Visualizer (Circle)
        prox_y = y + 200
        center_x = x + w // 2
        
        # Signal intensity (0.0 to 1.0)
        intensity = max(0.0, min(1.0, (sig.rssi + 90) / 60))
        
        # Outer rings
        for r in range(3):
            alpha = int(50 * (intensity if r == 0 else (intensity * 0.5)))
            pygame.draw.circle(surf, theme.ACCENT_DIM, (center_x, prox_y), 60 - r*15, 1)
        
        # Core
        core_r = int(5 + 30 * intensity)
        pygame.draw.circle(surf, theme.ACCENT, (center_x, prox_y), core_r)
        if int(time.time() * 5 * intensity) % 2:
            pygame.draw.circle(surf, theme.FG, (center_x, prox_y), core_r + 5, 2)
            
        dist_txt = "PROXIMITY_CRITICAL" if intensity > 0.8 else ("NEARBY" if intensity > 0.5 else "DISTANT")
        d_surf = f_med.render(dist_txt, True, theme.ERR if intensity > 0.8 else theme.ACCENT)
        surf.blit(d_surf, (center_x - d_surf.get_width()//2, prox_y + 70))

        # Analysis
        surf.blit(f_med.render("ANALYSIS:", True, theme.FG_DIM), (x, prox_y + 110))
        detail_lines = self._wrap_text(sig.details, f_med, w)
        for i, line in enumerate(detail_lines):
            surf.blit(f_med.render(line, True, theme.WARN), (x, prox_y + 130 + i*20))

    def _wrap_text(self, text, font, max_w):
        words = text.split()
        lines = []
        cur = []
        for w in words:
            if font.size(" ".join(cur + [w]))[0] < max_w:
                cur.append(w)
            else:
                lines.append(" ".join(cur))
                cur = [w]
        lines.append(" ".join(cur))
        return lines
