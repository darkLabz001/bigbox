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
        self.total_bt_seen = 0
        self.total_wifi_seen = 0
        self.dismissed = False
        self._stop_threads = False
        self.status_msg = "INITIALIZING..."
        
        # Heuristics
        self.KNOWN_NAMES = ["PENGUIN", "PIGVISION", "FS EXT", "FLOCK", "RAVEN", "FALCON"]
        self.MANUFACTURER_ID = "09c8"
        
        self.selected_idx = 0
        self._start_scan()

    def _start_scan(self):
        print("[flock] starting scan threads...")
        self._bt_thread = threading.Thread(target=self._bt_worker, daemon=True)
        self._wifi_thread = threading.Thread(target=self._wifi_worker, daemon=True)
        self._bt_thread.start()
        self._wifi_thread.start()

    def _bt_worker(self):
        """Advanced BLE monitor using raw btmon for dual-adapter support."""
        print("[flock] bt_worker: started")
        try:
            # Ensure both adapters are powered and scanning
            adapters = ["hci1", "hci0"]
            for hci in adapters:
                print(f"[flock] bringing up {hci}...")
                subprocess.run(["sudo", "-n", "hciconfig", hci, "up"], capture_output=True)
                subprocess.run(["sudo", "-n", "bluetoothctl", "select", hci], capture_output=True)
                subprocess.run(["sudo", "-n", "bluetoothctl", "power", "on"], capture_output=True)
                subprocess.Popen(["sudo", "-n", "bluetoothctl", "scan", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.status_msg = "SENSORS ACTIVE"

            # Use btmon for raw access to all controllers
            print("[flock] bt_worker: launching btmon")
            proc = subprocess.Popen(["sudo", "-n", "btmon"], stdout=subprocess.PIPE, text=True)
            if not proc.stdout: 
                self.status_msg = "BTMON_START_FAILED"
                return

            current_mac = ""
            for line in proc.stdout:
                if self._stop_threads: break
                
                m = re.search(r'Address: ([0-9A-F:]{17})', line)
                if m:
                    current_mac = m.group(1)
                    self.total_bt_seen += 1
                    continue

                r = re.search(r'RSSI: (-\d+)', line)
                if r and current_mac:
                    rssi = int(r.group(1))
                    self._process_bt_hit(current_mac, rssi, line)

                if "> HCI Event" in line or "@ MGMT Event" in line:
                    current_mac = ""

        except Exception as e:
            print(f"[flock] bt_worker error: {e}")
            self.status_msg = "BT_ERROR"
        finally:
            print("[flock] bt_worker: stopping")
            subprocess.run(["sudo", "-n", "bluetoothctl", "scan", "off"], capture_output=True)

    def _process_bt_hit(self, mac: str, rssi: int, raw_line: str):
        oui = mac[:8].upper()
        line_up = raw_line.upper()
        
        is_flock = False
        details = ""
        confidence = 10
        
        if self.MANUFACTURER_ID.upper() in line_up:
            is_flock = True
            details = "FLOCK_MFG_DATA_DETECTED"
            confidence += 60
            
        for name in self.KNOWN_NAMES:
            if name in line_up:
                is_flock = True
                details = f"SIGNATURE_MATCH: {name}"
                confidence += 40
                
        if oui in OUI_DB:
            is_flock = True
            details = f"HARDWARE_MATCH: {OUI_DB[oui]}"
            confidence += 30

        if is_flock:
            sig_id = f"ALPR_{mac[-5:].replace(':','')}"
            new_hit = sig_id not in self.signals
            if new_hit:
                print(f"[flock] ble hit: {sig_id} ({mac}) conf={confidence}")
                self.signals[sig_id] = FlockSignal(
                    id=sig_id, mac=mac, type="BLE", rssi=rssi, 
                    last_seen=datetime.now(), details=details, confidence=min(100, confidence)
                )
                if confidence >= 80:
                    self._save_loot(self.signals[sig_id])
                    self._play_alert()
            else:
                s = self.signals[sig_id]
                s.rssi = rssi
                s.last_seen = datetime.now()
                s.hits += 1
                s.confidence = min(100, s.confidence + 2)
                s.history.append(rssi)
                if len(s.history) > 20: s.history.pop(0)

    def _play_alert(self):
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            import array
            sample_rate = 44100
            freq = 880
            duration = 0.1
            n_samples = int(sample_rate * duration)
            buf = array.array('h', [0] * n_samples)
            for i in range(n_samples):
                t = i / sample_rate
                buf[i] = 16384 if (int(t * freq * 2) % 2) else -16384
            sound = pygame.mixer.Sound(buffer=buf)
            sound.set_volume(0.3)
            sound.play()
        except Exception:
            pass

    def _save_loot(self, sig: FlockSignal):
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
        print("[flock] wifi_worker: started")
        while not self._stop_threads:
            try:
                ifaces_out = subprocess.check_output(["ls", "/sys/class/net"], text=True)
                wlan_ifaces = [i for i in ifaces_out.split() if i.startswith("wlan")]
                
                for iface in wlan_ifaces:
                    subprocess.run(["sudo", "-n", "ip", "link", "set", iface, "up"], capture_output=True)

                self.status_msg = "SCANNING_WIFI"
                subprocess.Popen(["sudo", "-n", "nmcli", "dev", "wifi", "rescan"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(3)

                cmd = ["nmcli", "-t", "-f", "BSSID,SSID,SIGNAL", "dev", "wifi", "list"]
                out = subprocess.check_output(cmd, text=True)
                
                lines = out.splitlines()
                self.total_wifi_seen = len(lines)
                
                for line in lines:
                    raw_parts = line.split(':')
                    if len(raw_parts) < 3: continue
                    
                    mac_parts = []
                    idx = 0
                    while len(mac_parts) < 6 and idx < len(raw_parts):
                        part = raw_parts[idx].replace("\\", "")
                        if len(part) == 2:
                            mac_parts.append(part)
                        idx += 1
                    
                    if len(mac_parts) < 6: continue
                    
                    mac = ":".join(mac_parts).upper()
                    ssid = raw_parts[idx]
                    try:
                        rssi = int(raw_parts[-1])
                        rssi_dbm = (rssi / 2) - 100
                    except (ValueError, IndexError):
                        rssi_dbm = -70
                    
                    if "Flock-" in ssid or "PENGUIN" in ssid.upper() or "PIGVISION" in ssid.upper():
                        print(f"[flock] wifi hit: {ssid} ({mac})")
                        self._add_wifi_signal(ssid, mac, int(rssi_dbm))
                    
                    oui = mac[:8].upper()
                    if oui in OUI_DB:
                        label = f"{OUI_DB[oui]}_{mac[-5:].replace(':','')}"
                        print(f"[flock] wifi OUI hit: {label} ({mac})")
                        self._add_wifi_signal(label, mac, int(rssi_dbm))

                self.status_msg = "SENSORS ACTIVE"
            except Exception as e:
                print(f"[flock] wifi_worker error: {e}")
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
            num_sigs = len(self.signals)
            if num_sigs > 0:
                self.selected_idx = min(num_sigs - 1, self.selected_idx + 1)
        elif ev.button is Button.A:
            sorted_sigs = sorted(self.signals.values(), key=lambda x: x.last_seen, reverse=True)
            if sorted_sigs and self.selected_idx < len(sorted_sigs):
                self._save_loot(sorted_sigs[self.selected_idx])
                if hasattr(ctx, "toast"):
                    ctx.toast("SAVED TO LOOT")

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: FLOCK_SEEKER_ULTRA", True, theme.ACCENT), (theme.PADDING, 8))
        f_small = pygame.font.Font(None, 22)
        
        # Status and Device Counters
        stat_txt = f"STATUS: {self.status_msg} | BLE_RAW: {self.total_bt_seen} | WIFI_RAW: {self.total_wifi_seen}"
        surf.blit(f_small.render(stat_txt, True, theme.FG_DIM), (theme.PADDING, theme.SCREEN_H - 60))
        
        count_txt = f_small.render(f"ACTIVE_NODES: {len(self.signals)}", True, theme.FG)
        surf.blit(count_txt, (theme.SCREEN_W - 160, 12))
        
        list_w = 480
        list_rect = pygame.Rect(theme.PADDING, head_h + 10, list_w, theme.SCREEN_H - head_h - 75)
        pygame.draw.rect(surf, (5, 5, 8), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)
        sorted_sigs = sorted(self.signals.values(), key=lambda x: x.last_seen, reverse=True)
        for i, sig in enumerate(sorted_sigs):
            y = list_rect.y + i * 50
            if y > list_rect.bottom - 45: break
            sel = i == self.selected_idx
            bg = (20, 35, 50) if sel else (10, 15, 25)
            row_rect = pygame.Rect(list_rect.x + 5, y + 5, list_rect.width - 10, 45)
            pygame.draw.rect(surf, bg, row_rect, border_radius=4)
            if sel: pygame.draw.rect(surf, theme.ACCENT, row_rect, 1, border_radius=4)
            conf_w = int((row_rect.width - 20) * (sig.confidence / 100))
            pygame.draw.rect(surf, (0, 40, 0), (row_rect.x + 10, row_rect.bottom - 4, row_rect.width - 20, 2))
            pygame.draw.rect(surf, theme.ACCENT, (row_rect.x + 10, row_rect.bottom - 4, conf_w, 2))
            id_surf = f_small.render(f"{sig.id} ({sig.type})", True, theme.FG if sig.confidence > 50 else theme.FG_DIM)
            surf.blit(id_surf, (row_rect.x + 10, row_rect.y + 10))
            ss = max(0, min(100, (sig.rssi + 100) * 1.5))
            for b in range(5):
                b_color = theme.ACCENT if ss > (b * 20) else (40, 40, 40)
                pygame.draw.rect(surf, b_color, (row_rect.right - 60 + b*10, row_rect.y + 15, 6, 12))
        detail_x = list_rect.right + 20
        detail_w = theme.SCREEN_W - detail_x - theme.PADDING
        if sorted_sigs and self.selected_idx < len(sorted_sigs):
            self._render_detail(surf, detail_x, head_h + 10, detail_w, sorted_sigs[self.selected_idx])
        else:
            msg = f_small.render("NO TARGET SELECTED", True, theme.FG_DIM)
            surf.blit(msg, (detail_x + 20, theme.SCREEN_H // 2))
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - 35), (theme.SCREEN_W, theme.SCREEN_H - 35))
        hint = f_small.render("UP/DOWN: Navigate  A: SAVE TO LOOT  B: EXIT", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 26))

    def _render_detail(self, surf: pygame.Surface, x: int, y: int, w: int, sig: FlockSignal):
        f_med = pygame.font.Font(None, 24)
        f_bold = pygame.font.Font(None, 28)
        surf.blit(f_bold.render("TARGET_INTEL", True, theme.ACCENT), (x, y))
        pygame.draw.line(surf, theme.DIVIDER, (x, y+25), (x+w, y+25))
        rows = [("MAC:", sig.mac), ("TYPE:", sig.type), ("RSSI:", f"{sig.rssi} dBm"), ("HITS:", str(sig.hits)), ("SIGHTED:", sig.last_seen.strftime("%H:%M:%S"))]
        for i, (label, val) in enumerate(rows):
            surf.blit(f_med.render(label, True, theme.FG_DIM), (x, y + 40 + i*25))
            surf.blit(f_med.render(val, True, theme.FG), (x + 80, y + 40 + i*25))
        prox_y = y + 200
        center_x = x + w // 2
        intensity = max(0.0, min(1.0, (sig.rssi + 90) / 60))
        for r in range(3):
            pygame.draw.circle(surf, theme.ACCENT_DIM, (center_x, prox_y), 60 - r*15, 1)
        core_r = int(5 + 30 * intensity)
        pygame.draw.circle(surf, theme.ACCENT, (center_x, prox_y), core_r)
        if int(time.time() * 5 * intensity) % 2:
            pygame.draw.circle(surf, theme.FG, (center_x, prox_y), core_r + 5, 2)
        dist_txt = "PROXIMITY_CRITICAL" if intensity > 0.8 else ("NEARBY" if intensity > 0.5 else "DISTANT")
        d_surf = f_med.render(dist_txt, True, theme.ERR if intensity > 0.8 else theme.ACCENT)
        surf.blit(d_surf, (center_x - d_surf.get_width()//2, prox_y + 70))
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
