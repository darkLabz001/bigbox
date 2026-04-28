"""Wifite — Interactive automated wireless auditor."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import pty
import select
import time
from collections import deque
from typing import TYPE_CHECKING, List, Optional

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_LANDING = "landing"
PHASE_CONFIG = "config"
PHASE_RUNNING = "running"

class WifiteView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=300)
        self.status_msg = "Manual/Interactive Auditor"
        
        # UI dimensions
        self.font_size = 18
        self.font = pygame.font.Font(None, self.font_size)
        
        # Attack Options (Toggles)
        self.opt_wps = True
        self.opt_wpa = True
        self.opt_pmkid = True
        self.opt_pixie = True
        self.opt_kill = True
        self.custom_args = ""
        
        self.selected_iface: Optional[str] = None
        clients = hardware.list_wifi_clients()
        if clients: self.selected_iface = clients[0]
        else: self.selected_iface = "wlan0"

        # Process management
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        self.cursor = 0 # Config cursor
        self.is_scanning = False

    def _get_full_args(self) -> List[str]:
        args = ["--dict", "/usr/share/wordlists/rockyou.txt"]
        if self.opt_wps: args.append("--wps")
        if self.opt_wpa: args.append("--wpa")
        if self.opt_pmkid: args.append("--pmkid")
        if self.opt_pixie: args.append("--pixie")
        if self.opt_kill: args.append("--kill")
        if self.custom_args:
            args.extend(self.custom_args.split())
        return args

    def _start_wifite(self):
        wordlist = "/usr/share/wordlists/rockyou.txt"
        if not os.path.exists(wordlist):
            self.status_msg = "ERROR: Wordlist missing"
            return

        self.phase = PHASE_RUNNING
        self.is_scanning = True
        self.history.clear()
        
        cmd = ["wifite", "-i", self.selected_iface] + self._get_full_args()
        self.history.append(f"EXEC: {' '.join(cmd)}")
        
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                cmd,
                preexec_fn=os.setsid,
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                env=os.environ
            )
            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self.status_msg = "Scanning for targets..."
        except Exception as e:
            self.status_msg = f"Error: {e}"

    def _read_output(self):
        while not self._stop_event.is_set() and self.master_fd:
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        import re
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(line)
                                # Auto-detect if wifite is asking for targets
                                if "select target" in line.lower() or "enter number" in line.lower():
                                    self.is_scanning = False
                                    self.status_msg = "SELECT TARGETS (A to input)"
                except OSError:
                    break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def _send_ctrl_c(self):
        """Sends SIGINT to the entire process group to stop scanning."""
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                self.is_scanning = False
                self.status_msg = "Wait for prompt..."
            except Exception as e:
                print(f"[wifite] ctrl-c error: {e}")

    def _cleanup(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                time.sleep(0.5)
                if self.process.poll() is None:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except: pass
        
        if self.master_fd:
            try: os.close(self.master_fd)
            except: pass
        if self.slave_fd:
            try: os.close(self.slave_fd)
            except: pass
            
        self.master_fd = self.slave_fd = self.process = None
        self.is_scanning = False
        hardware.ensure_wifi_managed()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "Audit Stopped"
            elif self.phase == PHASE_CONFIG:
                self.phase = PHASE_LANDING
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self._start_wifite()
            elif ev.button is Button.X:
                self.phase = PHASE_CONFIG
            elif ev.button is Button.Y:
                self.opt_kill = not self.opt_kill
                self.status_msg = f"Auto-Kill Processes: {'ON' if self.opt_kill else 'OFF'}"

        elif self.phase == PHASE_CONFIG:
            if ev.button is Button.UP:
                self.cursor = (self.cursor - 1) % 7
            elif ev.button is Button.DOWN:
                self.cursor = (self.cursor + 1) % 7
            elif ev.button is Button.A:
                self._toggle_config_option(ctx)
            elif ev.button is Button.START:
                self.phase = PHASE_LANDING

        elif self.phase == PHASE_RUNNING:
            if ev.button is Button.A:
                # Always allow input in running phase
                ctx.get_input("Input / Targets", self._on_terminal_input)
            elif ev.button is Button.X:
                if self.is_scanning:
                    self._send_ctrl_c()
                else:
                    # In attack phase, X can send another ctrl-c to skip or stop
                    self._send_ctrl_c()
            elif ev.button is Button.Y:
                self.history.clear()

    def _toggle_config_option(self, ctx: App):
        if self.cursor == 0:
            common = ["wlan0", "wlan1", "wlan0mon", "wlan1mon"]
            active = hardware.list_wifi_clients() + hardware.list_monitor_ifaces()
            ifaces = sorted(list(set(common + active)))
            idx = (ifaces.index(self.selected_iface) + 1) % len(ifaces) if self.selected_iface in ifaces else 0
            self.selected_iface = ifaces[idx]
        elif self.cursor == 1: self.opt_wps = not self.opt_wps
        elif self.cursor == 2: self.opt_wpa = not self.opt_wpa
        elif self.cursor == 3: self.opt_pmkid = not self.opt_pmkid
        elif self.cursor == 4: self.opt_pixie = not self.opt_pixie
        elif self.cursor == 5: self.opt_kill = not self.opt_kill
        elif self.cursor == 6:
            ctx.get_input("Custom Args", lambda v: setattr(self, "custom_args", v or ""), initial=self.custom_args)

    def _on_terminal_input(self, text: str | None):
        if text is not None:
            self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill((0, 0, 0))
        head_h = 44
        pygame.draw.rect(surf, (20, 20, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 32)
        title_text = f"WIRELESS :: WIFITE 2"
        if self.phase == PHASE_RUNNING: title_text += " [ACTIVE]"
        surf.blit(f_title.render(title_text, True, theme.ACCENT), (theme.PADDING, 8))

        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        elif self.phase == PHASE_RUNNING: self._render_terminal(surf, head_h)

        # Status Bar
        foot_h = 30
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.font.render(self.status_msg, True, theme.ACCENT), (10, theme.SCREEN_H - 22))
        
        hint = self._get_hint()
        h_surf = self.font.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 22))

    def _get_hint(self) -> str:
        if self.phase == PHASE_LANDING: return "A: Start  X: Config  B: Back"
        if self.phase == PHASE_CONFIG: return "UP/DN: Select  A: Toggle  START: Done"
        if self.phase == PHASE_RUNNING:
            if self.is_scanning: return "X: STOP SCAN (CTRL+C)  B: Exit"
            return "A: INPUT TARGET #  X: SKIP/CTRL+C  B: Exit"
        return "B: Back"

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        f_big = pygame.font.Font(None, 40)
        f_med = pygame.font.Font(None, 24)
        y = head_h + 60
        surf.blit(f_big.render("WIFITE 2 INTERACTIVE", True, theme.FG), (theme.SCREEN_W // 2 - 150, y))
        
        attacks = []
        if self.opt_wps: attacks.append("WPS")
        if self.opt_pixie: attacks.append("Pixie")
        if self.opt_wpa: attacks.append("WPA")
        if self.opt_pmkid: attacks.append("PMKID")
        
        lines = [
            f"Interface: {self.selected_iface}",
            f"Active Attacks: {', '.join(attacks) or 'NONE'}",
            f"Custom: {self.custom_args or 'None'}",
            "",
            "Press A to start target discovery.",
            "Once targets appear, press X to stop scanning,",
            "then press A to enter the target numbers."
        ]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if "Press A" in ln else theme.FG_DIM
            surf.blit(f_med.render(ln, True, col), (100, y + 60 + i * 30))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        opts = [
            ("INTERFACE", self.selected_iface),
            ("ATTACK WPS", "YES" if self.opt_wps else "NO"),
            ("ATTACK WPA", "YES" if self.opt_wpa else "NO"),
            ("ATTACK PMKID", "YES" if self.opt_pmkid else "NO"),
            ("PIXIE-DUST", "YES" if self.opt_pixie else "NO"),
            ("KILL CONFLICTS", "YES" if self.opt_kill else "NO"),
            ("CUSTOM ARGS", self.custom_args or "(none)"),
        ]
        for i, (lbl, val) in enumerate(opts):
            sel = i == self.cursor
            color = theme.ACCENT if sel else theme.FG
            if sel: pygame.draw.rect(surf, (30, 30, 40), (40, y + i*40, 500, 35), border_radius=4)
            surf.blit(self.font.render(f"{lbl}:", True, theme.FG_DIM), (60, y + 10 + i*40))
            surf.blit(self.font.render(str(val), True, color), (220, y + 10 + i*40))

    def _render_terminal(self, surf: pygame.Surface, head_h: int):
        term_rect = pygame.Rect(5, head_h + 5, theme.SCREEN_W - 10, theme.SCREEN_H - head_h - 40)
        pygame.draw.rect(surf, (5, 5, 10), term_rect)
        pygame.draw.rect(surf, theme.DIVIDER, term_rect, 1)
        line_h = self.font_size + 2
        max_lines = term_rect.height // line_h
        visible_lines = list(self.history)[-max_lines:]
        for i, line in enumerate(visible_lines):
            surf.blit(self.font.render(line[:110], True, (220, 220, 220)), (term_rect.x + 10, term_rect.y + 10 + i * line_h))
