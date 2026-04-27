"""Dead Drop UI — Rogue AP based offline chat room."""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import TYPE_CHECKING, Optional

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.deaddrop import DeadDropServer
from bigbox.eviltwin import EvilTwinSession, iface_supports_ap

if TYPE_CHECKING:
    from bigbox.app import App


class DeadDropView:
    def __init__(self) -> None:
        self.dismissed = False
        self.session: Optional[EvilTwinSession] = None
        self.chat_server: Optional[DeadDropServer] = None
        
        self.iface = self._find_iface()
        self.ssid = "FREE_CHAT"
        self.phase = "SETUP" # SETUP, RUNNING, ERROR
        self.error_msg = ""
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def _find_iface(self) -> str:
        # Prefer wlan1 (external Alfa) if present, else wlan0
        for i in ("wlan1", "wlan0"):
            if os.path.exists(f"/sys/class/net/{i}"):
                return i
        return "wlan0"

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if self.phase == "SETUP":
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.A:
                self._start_session()
            elif ev.button is Button.X:
                ctx.get_input("Set SSID", self._on_ssid_done, initial=self.ssid)
        
        elif self.phase == "RUNNING":
            if ev.button is Button.B:
                self._stop_session()
                self.phase = "SETUP"
        
        elif self.phase == "ERROR":
            if ev.button in (Button.A, Button.B):
                self.phase = "SETUP"

    def _on_ssid_done(self, text: str | None):
        if text:
            self.ssid = text.strip()[:32] or "FREE_CHAT"

    def _start_session(self):
        self.session = EvilTwinSession(iface=self.iface, ssid=self.ssid)
        # Monkey-patch or swap the portal?
        # Let's just start the DeadDropServer on port 80 manually after session starts
        # and ensure EvilTwinSession doesn't start its own portal.
        
        # We need a modified session start that uses DeadDropServer instead of CaptivePortal
        self.chat_server = DeadDropServer(ssid=self.ssid)
        
        # Manually run the session steps to use our chat server
        ok, msg = self._manual_session_start()
        if not ok:
            self.phase = "ERROR"
            self.error_msg = msg
        else:
            self.phase = "RUNNING"

    def _manual_session_start(self) -> tuple[bool, str]:
        # Implementation of EvilTwinSession.start but swapping portal for DeadDropServer
        # This is a bit hacky but works for the prototype
        import subprocess
        from bigbox.eviltwin import _run, _write_hostapd_conf, _write_dnsmasq_conf, _install_iptables, DNSMASQ_LOG, HOSTAPD_LOG, DNSMASQ_CONF, HOSTAPD_CONF
        
        if not shutil.which("hostapd") or not shutil.which("dnsmasq"):
            return False, "hostapd/dnsmasq missing"

        _run(["nmcli", "device", "set", self.iface, "managed", "no"])
        _run(["ip", "addr", "flush", "dev", self.iface])
        _run(["ip", "link", "set", self.iface, "up"])
        _run(["ip", "addr", "add", "192.168.45.1/24", "dev", self.iface])

        _write_hostapd_conf(self.iface, self.ssid)
        _write_dnsmasq_conf(self.iface)
        _install_iptables(self.iface)

        self.session.dnsmasq_proc = subprocess.Popen(
            ["dnsmasq", "--keep-in-foreground", "--conf-file=" + str(DNSMASQ_CONF)],
            stdout=DNSMASQ_LOG.open("w"), stderr=subprocess.STDOUT
        )
        time.sleep(0.5)
        self.session.hostapd_proc = subprocess.Popen(
            ["hostapd", str(HOSTAPD_CONF)],
            stdout=HOSTAPD_LOG.open("w"), stderr=subprocess.STDOUT
        )
        time.sleep(1.0)
        
        if self.session.hostapd_proc.poll() is not None:
            return False, "hostapd failed"

        self.chat_server.start()
        return True, "Running"

    def _stop_session(self):
        if self.chat_server:
            self.chat_server.stop()
        if self.session:
            self.session.stop()
        self.session = None
        self.chat_server = None

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("DEAD DROP :: OFFLINE CHAT", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        if self.phase == "SETUP":
            lines = [
                f"Interface: {self.iface}",
                f"SSID: {self.ssid}",
                "",
                "A: Start AP & Chat Room",
                "X: Change SSID",
                "B: Cancel"
            ]
            for i, ln in enumerate(lines):
                s = self.body_font.render(ln, True, theme.FG)
                surf.blit(s, (theme.PADDING, 100 + i*30))
        
        elif self.phase == "RUNNING":
            clients = self.session.clients_connected() if self.session else 0
            msgs = len(self.chat_server.messages) if self.chat_server else 0
            
            lines = [
                "STATUS: ACTIVE",
                f"SSID: {self.ssid}",
                f"Clients: {clients}",
                f"Messages: {msgs}",
                "",
                "B: Stop Session"
            ]
            for i, ln in enumerate(lines):
                s = self.body_font.render(ln, True, theme.FG)
                surf.blit(s, (theme.PADDING, 100 + i*30))
            
            if int(time.time()) % 2:
                pygame.draw.circle(surf, theme.ACCENT, (theme.SCREEN_W - 50, 50), 10)

        elif self.phase == "ERROR":
            err = self.body_font.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (theme.PADDING, 100))
            hint = self.body_font.render("Press A or B to return", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, 140))
