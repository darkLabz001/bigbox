"""Tactical Messenger — Free web-based and gateway SMS."""
from __future__ import annotations

import os
import json
import threading
import time
import requests
import smtplib
from email.mime.text import MIMEText
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.mail import MailConfig # Shared config

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_START = "start"
PHASE_METHOD = "method"
PHASE_TARGET = "target"
PHASE_MSG = "msg"
PHASE_SENDING = "sending"

GATEWAYS = {
    "Verizon": "vtext.com",
    "AT&T": "txt.att.net",
    "T-Mobile": "tmomail.net",
    "Sprint": "messaging.sprintpcs.com",
    "Virgin": "vmobl.com",
    "Boost": "myboostmobile.com",
    "Cricket": "sms.cricketwireless.net",
    "Google Fi": "msg.fi.google.com"
}

class MessengerView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_START
        self.mail_config = MailConfig.load()
        
        self.method: str = "TEXTBELT" # "TEXTBELT" or "GATEWAY"
        self.target_num: str = ""
        self.target_gateway: str = "vtext.com"
        self.message: str = ""
        
        self.status_msg = "READY"
        self.error_msg: Optional[str] = None
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)
        
        self.gateway_keys = list(GATEWAYS.keys())
        self.cursor = 0

    def _send_sms(self):
        self.phase = PHASE_SENDING
        self.status_msg = "UPLINKING MESSAGE..."
        self.error_msg = None
        threading.Thread(target=self._send_worker, daemon=True).start()

    def _send_worker(self):
        try:
            if self.method == "TEXTBELT":
                res = requests.post('https://textbelt.com/text', {
                    'phone': self.target_num,
                    'message': self.message,
                    'key': 'textbelt',
                }, timeout=10)
                data = res.json()
                if not data.get('success'):
                    raise Exception(data.get('error', 'Unknown Textbelt error'))
            
            else: # GATEWAY
                if not self.mail_config.email or not self.mail_config.password:
                    raise Exception("Email not configured! Set up in Tactical Mail first.")
                
                recipient = f"{self.target_num}@{self.target_gateway}"
                msg = MIMEText(self.message)
                msg['Subject'] = ""
                msg['From'] = self.mail_config.email
                msg['To'] = recipient
                
                server = smtplib.SMTP(self.mail_config.smtp_server, self.mail_config.smtp_port)
                server.starttls()
                server.login(self.mail_config.email, self.mail_config.password)
                server.send_message(msg)
                server.quit()

            self.status_msg = "MESSAGE SENT SUCCESSFULLY"
            time.sleep(2)
            self.dismissed = True
        except Exception as e:
            self.error_msg = str(e)
            self.status_msg = "TRANSMISSION FAILED"
            self.phase = PHASE_MSG

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.phase == PHASE_START: self.dismissed = True
            elif self.phase == PHASE_METHOD: self.phase = PHASE_START
            elif self.phase == PHASE_TARGET: self.phase = PHASE_METHOD
            elif self.phase == PHASE_MSG: self.phase = PHASE_TARGET
            return

        if self.phase == PHASE_START:
            if ev.button is Button.A: self.phase = PHASE_METHOD
        
        elif self.phase == PHASE_METHOD:
            if ev.button is Button.UP: self.cursor = (self.cursor - 1) % 2
            elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % 2
            elif ev.button is Button.A:
                self.method = "TEXTBELT" if self.cursor == 0 else "GATEWAY"
                self.cursor = 0
                self.phase = PHASE_TARGET
        
        elif self.phase == PHASE_TARGET:
            if self.method == "TEXTBELT":
                if ev.button is Button.A:
                    ctx.get_input("Phone (e.g. +1...)", self._on_target_done, initial=self.target_num)
            else:
                # Select Gateway
                if ev.button is Button.UP: self.cursor = (self.cursor - 1) % len(self.gateway_keys)
                elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % len(self.gateway_keys)
                elif ev.button is Button.A:
                    ctx.get_input("Phone (digits only)", self._on_target_done, initial=self.target_num)
                elif ev.button is Button.X:
                    self.target_gateway = GATEWAYS[self.gateway_keys[self.cursor]]
        
        elif self.phase == PHASE_MSG:
            if ev.button is Button.A:
                ctx.get_input("Message Content", self._on_msg_done, initial=self.message)
            elif ev.button is Button.X and self.target_num and self.message:
                self._send_sms()

    def _on_target_done(self, v):
        if v: 
            self.target_num = v.strip()
            self.phase = PHASE_MSG

    def _on_msg_done(self, v):
        if v: self.message = v

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("SOCIAL :: TACTICAL_MESSENGER", True, theme.ACCENT), (theme.PADDING, 8))
        
        y = head_h + 40
        if self.phase == PHASE_START:
            surf.blit(self.f_main.render("ENCRYPTED TRANSMISSION PROTOCOL", True, theme.FG), (50, y))
            surf.blit(self.f_small.render("Send text messages via web-API or Carrier Gateway.", True, theme.FG_DIM), (50, y + 30))
            surf.blit(self.f_main.render("> PRESS A TO INITIATE", True, theme.ACCENT), (50, y + 80))

        elif self.phase == PHASE_METHOD:
            surf.blit(self.f_main.render("SELECT TRANSMISSION METHOD:", True, theme.FG), (50, y))
            m1 = "TEXTBELT (1 free msg/day, no config)"
            m2 = "CARRIER GATEWAY (Requires Email setup)"
            for i, txt in enumerate([m1, m2]):
                sel = i == self.cursor
                color = theme.ACCENT if sel else theme.FG
                surf.blit(self.f_main.render(f"{'> ' if sel else '  '}{txt}", True, color), (60, y + 50 + i*40))

        elif self.phase == PHASE_TARGET:
            surf.blit(self.f_main.render(f"DESTINATION: {self.target_num or '[A: ENTER NUMBER]'}", True, theme.FG), (50, y))
            if self.method == "GATEWAY":
                surf.blit(self.f_small.render(f"CARRIER: {self.target_gateway}", True, theme.ACCENT), (50, y + 30))
                y += 60
                surf.blit(self.f_small.render("SELECT CARRIER (X to Confirm):", True, theme.FG_DIM), (50, y))
                for i, g in enumerate(self.gateway_keys):
                    if abs(i - self.cursor) > 4: continue
                    sel = i == self.cursor
                    color = theme.ACCENT if sel else theme.FG
                    surf.blit(self.f_main.render(f"{'> ' if sel else '  '}{g}", True, color), (60, y + 30 + (i-self.cursor+4)*25))

        elif self.phase == PHASE_MSG:
            surf.blit(self.f_main.render(f"TO: {self.target_num}", True, theme.FG_DIM), (50, y))
            surf.blit(self.f_main.render(f"MSG: {self.message or '[A: COMPOSE]'}", True, theme.FG), (50, y + 40))
            if self.message:
                surf.blit(self.f_main.render("> PRESS X TO TRANSMIT", True, theme.WARN), (50, y + 120))

        elif self.phase == PHASE_SENDING:
            msg = self.status_msg
            s = self.f_main.render(msg, True, theme.ACCENT)
            surf.blit(s, (theme.SCREEN_W//2 - s.get_width()//2, theme.SCREEN_H//2))

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        if self.error_msg:
            surf.blit(self.f_small.render(f"ERROR: {self.error_msg[:70]}", True, theme.ERR), (10, theme.SCREEN_H - 26))
        else:
            surf.blit(self.f_small.render(f"STATUS: {self.status_msg}", True, theme.ACCENT), (10, theme.SCREEN_H - 26))
        hint = "UP/DN: Nav  A: Action  B: Back"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))
