"""Signal Scraper — Proximity Profiler (Wi-Fi + BT).

Scans for nearby devices via Wi-Fi probe requests and BLE advertisements to 
build a "Social Identity" map of the room. Reuses airodump-ng and btmon logic.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import pygame

from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext
from bigbox.ui.wifi_attack import AP, Client, _list_wlan_ifaces, _read_airodump_csv

if TYPE_CHECKING:
    from bigbox.app import App


@dataclass
class ScrapedDevice:
    mac: str
    type: str  # "WIFI" or "BLE"
    name: str = ""
    rssi: int = -100
    last_seen: float = 0.0
    probes: str = ""
    manufacturer: str = "Unknown"
    is_tracker: bool = False
    history: List[int] = field(default_factory=list)

    @property
    def identity(self) -> str:
        if self.name:
            return self.name
        if self.probes:
            # Clean up probes list
            p = self.probes.split(",")
            return f"Probing: {p[0][:15]}"
        return self.manufacturer


PHASE_PICK_IFACE = "iface"
PHASE_ENABLING = "enabling"
PHASE_SCANNING = "scanning"

LOOT_DIR = Path("loot/scraper")


class SignalScraperView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Select monitor adapter"

        self.ifaces = _list_wlan_ifaces()
        self.iface_cursor = 0
        self.mon_iface: str | None = None
        
        self.devices: Dict[str, ScrapedDevice] = {}
        self.cursor = 0
        self.scroll = 0
        
        self._stop = False
        self._airodump: Optional[subprocess.Popen] = None
        self._btmon: Optional[subprocess.Popen] = None
        self._capture_csv: Optional[Path] = None
        
        # UI Polish
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 16)
        
        self._grid_surf = self._create_grid_bg()

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill(theme.BG)
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (15, 15, 25), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (15, 15, 25), (0, y), (theme.SCREEN_W, y))
        return s

    def _get_manufacturer(self, mac: str) -> str:
        prefix = mac.lower().replace(":", "")[:6]
        ouis = {
            "4c7403": "Apple", "002500": "Apple", "f01898": "Apple",
            "d8bb2c": "Apple", "ac3c0b": "Apple", "64a769": "Apple",
            "0017f2": "Apple", "24a074": "Apple", "001c23": "Apple",
            "3c5ab4": "Google", "d8eb97": "Google", "705a0f": "Google",
            "001a11": "Google", "f4f5d8": "Google/Nest",
            "24f5a2": "Samsung", "508569": "Samsung", "948bc1": "Samsung",
            "00234a": "Tesla", "404cc1": "Tesla",
            "00044b": "NVIDIA", "b827eb": "RaspberryPi", "dca632": "RaspberryPi",
            "e45f01": "RaspberryPi", "000c29": "VMware", "005056": "VMware",
            "00155d": "Microsoft", "0003ff": "Microsoft",
        }
        return ouis.get(prefix, "Unknown Vendor")

    def _start_scanners(self):
        self._stop = False
        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = (LOOT_DIR / f"scrape_{ts}").absolute()
        self._capture_csv = Path(str(prefix) + "-01.csv")

        try:
            self._airodump = subprocess.Popen(
                ["airodump-ng", "--output-format", "csv", "-w", str(prefix), self.mon_iface],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid
            )
        except Exception: pass

        try:
            self._btmon = subprocess.Popen(
                ["btmon"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, preexec_fn=os.setsid
            )
            subprocess.run(["bluetoothctl", "scan", "le", "on"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except Exception: pass

        threading.Thread(target=self._poll_wifi, daemon=True).start()
        threading.Thread(target=self._poll_bt, daemon=True).start()

    def _poll_wifi(self):
        while not self._stop:
            if self._capture_csv and self._capture_csv.exists():
                _, clients = _read_airodump_csv(self._capture_csv)
                for c in clients:
                    mac = c.mac.lower()
                    dev = self.devices.get(mac, ScrapedDevice(mac=mac, type="WIFI"))
                    dev.rssi = c.power
                    dev.last_seen = time.time()
                    dev.probes = c.probes
                    dev.manufacturer = self._get_manufacturer(mac)
                    dev.history.append(c.power)
                    if len(dev.history) > 20: dev.history.pop(0)
                    self.devices[mac] = dev
            time.sleep(1.0)

    def _poll_bt(self):
        if not self._btmon or not self._btmon.stdout: return
        addr_re = re.compile(r"Address:\s+([0-9A-Fa-f:]{17})")
        name_re = re.compile(r"Name\s+\(.*\):\s+(.*)")
        rssi_re = re.compile(r"RSSI:\s*(-?\d+)")
        cur_addr = None
        for line in self._btmon.stdout:
            if self._stop: break
            m_addr = addr_re.search(line)
            if m_addr:
                cur_addr = m_addr.group(1).lower()
                if cur_addr not in self.devices:
                    self.devices[cur_addr] = ScrapedDevice(mac=cur_addr, type="BLE")
                dev = self.devices[cur_addr]
                dev.last_seen = time.time()
                dev.manufacturer = self._get_manufacturer(cur_addr)
            if cur_addr:
                dev = self.devices[cur_addr]
                m_rssi = rssi_re.search(line)
                if m_rssi:
                    rssi = int(m_rssi.group(1))
                    dev.rssi = rssi
                    dev.history.append(rssi)
                    if len(dev.history) > 20: dev.history.pop(0)
                m_name = name_re.search(line)
                if m_name:
                    dev.name = m_name.group(1).strip()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._cleanup()
            self.dismissed = True
            return
        if self.phase == PHASE_PICK_IFACE:
            if not self.ifaces: return
            if ev.button is Button.UP: self.iface_cursor = (self.iface_cursor - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN: self.iface_cursor = (self.iface_cursor + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                threading.Thread(target=self._enable_monitor, args=(self.ifaces[self.iface_cursor].name,), daemon=True).start()
            return
        if self.phase == PHASE_SCANNING:
            count = len(self.devices)
            if count == 0: return
            if ev.button is Button.UP:
                self.cursor = (self.cursor - 1) % count
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.cursor = (self.cursor + 1) % count
                self._adjust_scroll()

    def _enable_monitor(self, iface: str):
        self.phase = PHASE_ENABLING
        self.status_msg = f"Enabling monitor on {iface}..."
        mon = hardware.enable_monitor(iface)
        if mon:
            self.mon_iface = mon
            self.phase = PHASE_SCANNING
            self.status_msg = f"SWEEPING PROXIMITY :: {mon}"
            self._start_scanners()
        else:
            self.status_msg = "MONITOR MODE FAILED"
            self.phase = PHASE_PICK_IFACE

    def _cleanup(self):
        self._stop = True
        
        # 1. Kill background profile processes
        for proc in [self._airodump, self._btmon]:
            if proc and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                    proc.wait(timeout=2)
                except:
                    try: proc.kill()
                    except: pass
        self._airodump = self._btmon = None

        # 2. Stop Bluetooth scan
        subprocess.run(["bluetoothctl", "scan", "off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 3. Disable monitor mode and restore NetworkManager
        if self.mon_iface:
            subprocess.run(["airmon-ng", "stop", self.mon_iface], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Restore managed mode and services
            subprocess.run(["nmcli", "networking", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "restart", "NetworkManager"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.mon_iface = None
            
        # Global safety recovery
        hardware.ensure_wifi_managed()

    def _adjust_scroll(self):
        visible = 11
        if self.cursor < self.scroll: self.scroll = self.cursor
        elif self.cursor >= self.scroll + visible: self.scroll = self.cursor - visible + 1

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        head_h = 44
        pygame.draw.rect(surf, (10, 10, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("RECON :: SIGNAL SCRAPER", True, theme.ACCENT), (theme.PADDING, 8))
        
        # Status Bar
        foot_h = 30
        pygame.draw.rect(surf, (5, 5, 10), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.f_small.render(self.status_msg, True, theme.ACCENT), (10, theme.SCREEN_H - 22))
        hint = self.f_small.render("UP/DN: Select  B: Exit", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - 10, theme.SCREEN_H - 22))

        if self.phase == PHASE_PICK_IFACE:
            self._render_ifaces(surf, head_h)
        elif self.phase == PHASE_ENABLING:
            self._render_msg(surf, "INITIATING HARDWARE...")
        elif self.phase == PHASE_SCANNING:
            self._render_scanning(surf, head_h)

    def _render_ifaces(self, surf: pygame.Surface, head_h: int):
        y = head_h + 40
        surf.blit(self.f_main.render("SELECT MONITOR INTERFACE:", True, theme.FG), (50, y))
        for i, it in enumerate(self.ifaces):
            sel = i == self.iface_cursor
            rect = pygame.Rect(50, y + 40 + i * 45, 300, 40)
            pygame.draw.rect(surf, theme.BG_ALT if sel else (20, 20, 30), rect, border_radius=4)
            if sel: pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=4)
            surf.blit(self.f_main.render(it.name, True, theme.ACCENT if sel else theme.FG), (70, rect.y + 10))

    def _render_msg(self, surf: pygame.Surface, msg: str):
        s = self.f_main.render(msg, True, theme.ACCENT)
        surf.blit(s, (theme.SCREEN_W // 2 - s.get_width() // 2, theme.SCREEN_H // 2))

    def _render_scanning(self, surf: pygame.Surface, head_h: int):
        # Layout: Left = List (500px), Right = Detail Panel (300px)
        list_w = 510
        pygame.draw.line(surf, theme.DIVIDER, (list_w, head_h), (list_w, theme.SCREEN_H - 30))
        
        # Headers
        hy = head_h + 8
        surf.blit(self.f_tiny.render("TYPE", True, theme.FG_DIM), (15, hy))
        surf.blit(self.f_tiny.render("IDENTITY / PROBES", True, theme.FG_DIM), (60, hy))
        surf.blit(self.f_tiny.render("SIG", True, theme.FG_DIM), (400, hy))
        surf.blit(self.f_tiny.render("SEEN", True, theme.FG_DIM), (460, hy))
        
        sorted_devs = sorted(self.devices.values(), key=lambda d: d.last_seen, reverse=True)
        if not sorted_devs:
            self._render_msg(surf, "SEARCHING FOR SIGNALS...")
            return

        list_y = hy + 20
        for i in range(11):
            idx = self.scroll + i
            if idx >= len(sorted_devs): break
            d = sorted_devs[idx]
            y = list_y + i * 34
            
            sel = idx == self.cursor
            if sel:
                pygame.draw.rect(surf, (30, 30, 50), (5, y, list_w - 10, 32), border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT_DIM, (5, y, list_w - 10, 32), 1, border_radius=4)
            
            # Icon
            t_col = theme.ACCENT if d.type == "WIFI" else theme.WARN
            pygame.draw.circle(surf, t_col, (25, y + 16), 4)
            if d.type == "BLE": pygame.draw.circle(surf, t_col, (25, y + 16), 7, 1)
            
            # Identity
            ident = d.identity
            if len(ident) > 28: ident = ident[:25] + "..."
            surf.blit(self.f_main.render(ident, True, theme.FG if not sel else theme.ACCENT), (60, y + 6))
            
            # Signal bar
            self._draw_signal_bars(surf, 400, y + 10, d.rssi)
            
            # Last seen
            ago = int(time.time() - d.last_seen)
            surf.blit(self.f_tiny.render(f"{ago}s", True, theme.FG_DIM), (465, y + 10))

        # Detail Panel (Right)
        if sorted_devs:
            self._render_detail(surf, sorted_devs[self.cursor], list_w + 15, head_h + 15)

    def _draw_signal_bars(self, surf: pygame.Surface, x: int, y: int, rssi: int):
        # Map RSSI -100..-30 to 0..5 bars
        lvl = max(0, min(5, (rssi + 100) // 14))
        for i in range(5):
            h = 6 + i * 3
            bx = x + i * 6
            by = y + (18 - h)
            color = theme.ACCENT_DIM if i < lvl else (40, 40, 60)
            pygame.draw.rect(surf, color, (bx, by, 4, h))

    def _render_detail(self, surf: pygame.Surface, d: ScrapedDevice, x: int, y: int):
        surf.blit(self.f_title.render("DEVICE PROFILE", True, theme.ACCENT), (x, y))
        y += 40
        
        info = [
            ("MAC:", d.mac.upper()),
            ("VENDOR:", d.manufacturer),
            ("TYPE:", "Wi-Fi (802.11)" if d.type == "WIFI" else "Bluetooth LE"),
            ("RSSI:", f"{d.rssi} dBm"),
        ]
        if d.name: info.append(("NAME:", d.name))
        
        for lbl, val in info:
            surf.blit(self.f_small.render(lbl, True, theme.FG_DIM), (x, y))
            surf.blit(self.f_main.render(val, True, theme.FG), (x + 80, y - 2))
            y += 30
            
        if d.probes:
            surf.blit(self.f_small.render("PROBE REQUESTS:", True, theme.FG_DIM), (x, y))
            y += 22
            for p in d.probes.split(",")[:4]:
                if p:
                    surf.blit(self.f_tiny.render(f"> {p[:25]}", True, theme.ACCENT_DIM), (x + 10, y))
                    y += 18

        # Miniature signal history graph
        gy = theme.SCREEN_H - 120
        gw = 260
        gh = 60
        pygame.draw.rect(surf, (5, 5, 10), (x, gy, gw, gh))
        pygame.draw.rect(surf, theme.DIVIDER, (x, gy, gw, gh), 1)
        surf.blit(self.f_tiny.render("SIGNAL STRENGTH HISTORY", True, theme.FG_DIM), (x, gy - 18))
        
        if len(d.history) > 1:
            pts = []
            for i, val in enumerate(d.history):
                px = x + (i * (gw / 20))
                py = gy + gh - int((val + 100) * (gh / 70))
                py = max(gy + 2, min(gy + gh - 2, py))
                pts.append((px, py))
            pygame.draw.lines(surf, theme.ACCENT, False, pts, 2)
