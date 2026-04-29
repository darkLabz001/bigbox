"""Tactical Messenger — Free web-based and gateway SMS with background sync and notifications."""
from __future__ import annotations

import os
import re
import json
import threading
import time
import requests
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.header import decode_header
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent
from bigbox.ui.mail import MailConfig

if TYPE_CHECKING:
    from bigbox.app import App

MSG_DB_PATH = os.path.expanduser("~/.bigbox/messages.json")

@dataclass
class SMSMessage:
    sender: str
    body: str
    timestamp: str
    is_me: bool = False

@dataclass
class Conversation:
    number: str
    messages: List[SMSMessage] = field(default_factory=list)

class MessageStore:
    def __init__(self):
        self.conversations: Dict[str, Conversation] = {}
        self.load()

    def load(self):
        if os.path.exists(MSG_DB_PATH):
            try:
                with open(MSG_DB_PATH, "r") as f:
                    data = json.load(f)
                    for num, conv_data in data.items():
                        msgs = [SMSMessage(**m) for m in conv_data['messages']]
                        self.conversations[num] = Conversation(number=num, messages=msgs)
            except: pass

    def save(self):
        os.makedirs(os.path.dirname(MSG_DB_PATH), exist_ok=True)
        try:
            data = {num: {"number": c.number, "messages": [asdict(m) for m in c.messages]} 
                    for num, c in self.conversations.items()}
            with open(MSG_DB_PATH, "w") as f:
                json.dump(data, f)
        except: pass

    def add_message(self, number: str, body: str, is_me: bool = False) -> bool:
        """Adds a message if it doesn't already exist. Returns True if new."""
        if number not in self.conversations:
            self.conversations[number] = Conversation(number=number)
        
        # Simple dedupe check based on body and timestamp (within same minute)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for m in self.conversations[number].messages:
            if m.body == body and m.timestamp[:16] == ts[:16]:
                return False
        
        self.conversations[number].messages.append(SMSMessage(number, body, ts, is_me))
        self.save()
        return True

    def delete_conversation(self, number: str):
        if number in self.conversations:
            del self.conversations[number]
            self.save()

class MessengerSync:
    """Background service that polls for SMS replies and triggers notifications."""
    def __init__(self, app: App):
        self.app = app
        self.store = MessageStore()
        self.config = MailConfig.load()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            if not self.config.email or not self.config.password:
                time.sleep(10)
                self.config = MailConfig.load() # Reload if changed
                continue

            try:
                mail = imaplib.IMAP4_SSL(self.config.imap_server)
                mail.login(self.config.email, self.config.password)
                mail.select("inbox")
                
                new_found = False
                # Known gateway domains
                domains = ["vtext.com", "txt.att.net", "tmomail.net", "messaging.sprintpcs.com", "vmobl.com"]
                for domain in domains:
                    status, data = mail.search(None, f'(FROM "{domain}")')
                    if status != "OK": continue
                    
                    for m_id in data[0].split():
                        res, msg_data = mail.fetch(m_id, "(RFC822)")
                        for part in msg_data:
                            if isinstance(part, tuple):
                                msg = email.message_from_bytes(part[1])
                                sender = msg.get("From", "")
                                num_match = re.search(r'(\d+)@', sender)
                                if num_match:
                                    number = num_match.group(1)
                                    body = ""
                                    if msg.is_multipart():
                                        for p in msg.walk():
                                            if p.get_content_type() == "text/plain":
                                                body = p.get_payload(decode=True).decode(errors="replace")
                                                break
                                    else:
                                        body = msg.get_payload(decode=True).decode(errors="replace")
                                    
                                    body = body.split("\n--")[0].strip()
                                    if self.store.add_message(number, body, is_me=False):
                                        new_found = True
                                        self.app.toast(f"NEW SMS: {number}")
                                        self._play_ping()
                
                mail.close()
                mail.logout()
            except: pass
            time.sleep(30)

    def _play_ping(self):
        try:
            if not pygame.mixer.get_init(): pygame.mixer.init()
            import array
            sample_rate = 44100
            freq = 1200
            duration = 0.15
            n_samples = int(sample_rate * duration)
            buf = array.array('h', [0] * n_samples)
            for i in range(n_samples):
                t = i / sample_rate
                buf[i] = int(16384 * math.sin(2 * math.pi * freq * t))
            sound = pygame.mixer.Sound(buffer=buf)
            sound.play()
        except: pass

PHASE_CONVS = "conversations"
PHASE_CHAT = "chat"
PHASE_TARGET = "target"
PHASE_MSG = "msg"
PHASE_SENDING = "sending"

GATEWAYS = {
    "Verizon": "vtext.com", "AT&T": "txt.att.net", "T-Mobile": "tmomail.net",
    "Sprint": "messaging.sprintpcs.com", "Virgin": "vmobl.com", "Boost": "myboostmobile.com",
    "Cricket": "sms.cricketwireless.net", "Google Fi": "msg.fi.google.com"
}

class MessengerView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_CONVS
        self.mail_config = MailConfig.load()
        self.store = MessageStore() # Local copy
        
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
        self.chat_scroll = 0

    def _send_sms(self):
        self.phase = PHASE_SENDING
        self.status_msg = "UPLINKING..."
        threading.Thread(target=self._send_worker, daemon=True).start()

    def _send_worker(self):
        try:
            use_gateway = bool(self.mail_config.email and self.mail_config.password)
            if not use_gateway:
                res = requests.post('https://textbelt.com/text', {
                    'phone': self.target_num, 'message': self.message, 'key': 'textbelt'
                }, timeout=10)
                if not res.json().get('success'): raise Exception(res.json().get('error'))
            else:
                recipient = f"{self.target_num}@{self.target_gateway}"
                msg = MIMEText(self.message)
                msg['To'] = recipient
                msg['From'] = self.mail_config.email
                server = smtplib.SMTP(self.mail_config.smtp_server, self.mail_config.smtp_port)
                server.starttls()
                server.login(self.mail_config.email, self.mail_config.password)
                server.send_message(msg)
                server.quit()

            self.store.add_message(self.target_num, self.message, is_me=True)
            self.status_msg = "SENT"
            time.sleep(1)
            self.phase = PHASE_CHAT
        except Exception as e:
            self.error_msg = str(e)
            self.phase = PHASE_MSG

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.phase == PHASE_CONVS: self.dismissed = True
            elif self.phase == PHASE_CHAT: self.phase = PHASE_CONVS
            elif self.phase == PHASE_TARGET: self.phase = PHASE_CONVS
            elif self.phase == PHASE_MSG: self.phase = PHASE_CHAT if self.target_num in self.store.conversations else PHASE_TARGET
            return

        # Refresh store periodically or on interaction
        self.store.load()
        conv_list = sorted(self.store.conversations.values(), key=lambda c: c.messages[-1].timestamp if c.messages else "", reverse=True)

        if self.phase == PHASE_CONVS:
            if ev.button is Button.UP: self.cursor = max(0, self.cursor - 1)
            elif ev.button is Button.DOWN: self.cursor = min(len(conv_list) - 1, self.cursor + 1)
            elif ev.button is Button.A:
                if conv_list:
                    self.target_num = conv_list[self.cursor].number
                    self.phase = PHASE_CHAT
                    self.chat_scroll = 0
            elif ev.button is Button.X:
                self.phase = PHASE_TARGET
                self.target_num = ""
            elif ev.button is Button.Y: # DELETE
                if conv_list:
                    self.store.delete_conversation(conv_list[self.cursor].number)
                    self.cursor = max(0, self.cursor - 1)

        elif self.phase == PHASE_CHAT:
            if ev.button is Button.UP: self.chat_scroll = max(0, self.chat_scroll - 1)
            elif ev.button is Button.DOWN: self.chat_scroll += 1
            elif ev.button is Button.A:
                self.phase = PHASE_MSG
                self.message = ""
            elif ev.button is Button.Y:
                self.store.delete_conversation(self.target_num)
                self.phase = PHASE_CONVS

        elif self.phase == PHASE_TARGET:
            if ev.button is Button.UP: self.cursor = (self.cursor - 1) % len(self.gateway_keys)
            elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % len(self.gateway_keys)
            elif ev.button is Button.A:
                ctx.get_input("Phone Number", self._on_target_done, initial=self.target_num)
            elif ev.button is Button.X:
                self.target_gateway = GATEWAYS[self.gateway_keys[self.cursor]]
                self.status_msg = f"GATEWAY: {self.target_gateway}"

        elif self.phase == PHASE_MSG:
            if ev.button is Button.A:
                ctx.get_input("Message", self._on_msg_done, initial=self.message)
            elif ev.button is Button.X and self.message:
                self._send_sms()

    def _on_target_done(self, v):
        if v: 
            self.target_num = v.strip().replace("-","").replace(" ","")
            self.phase = PHASE_MSG

    def _on_msg_done(self, v):
        if v: self.message = v

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("SOCIAL :: TACTICAL_MESSENGER", True, theme.ACCENT), (theme.PADDING, 8))

        if self.phase == PHASE_CONVS: self._render_convs(surf, head_h)
        elif self.phase == PHASE_CHAT: self._render_chat(surf, head_h)
        elif self.phase == PHASE_TARGET: self._render_target(surf, head_h)
        elif self.phase == PHASE_MSG: self._render_msg_input(surf, head_h)
        elif self.phase == PHASE_SENDING:
            s = self.f_main.render(self.status_msg, True, theme.ACCENT)
            surf.blit(s, (theme.SCREEN_W//2 - s.get_width()//2, theme.SCREEN_H//2))

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        msg = self.error_msg if self.error_msg else self.status_msg
        surf.blit(self.f_small.render(f"STATUS: {msg[:70]}", True, theme.ERR if self.error_msg else theme.ACCENT), (10, theme.SCREEN_H - 26))
        h_surf = self.f_small.render("UP/DN: Nav  A: Select  X: New  Y: Delete  B: Back", True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))

    def _render_convs(self, surf: pygame.Surface, head_h: int):
        convs = sorted(self.store.conversations.values(), key=lambda c: c.messages[-1].timestamp if c.messages else "", reverse=True)
        y = head_h + 10
        if not convs:
            surf.blit(self.f_main.render("NO CONVERSATIONS FOUND", True, theme.FG_DIM), (50, y + 40))
        for i, c in enumerate(convs):
            sel = i == self.cursor
            rect = pygame.Rect(10, y + i*55, theme.SCREEN_W - 20, 50)
            pygame.draw.rect(surf, (30, 30, 50) if sel else (15, 15, 25), rect, border_radius=4)
            if sel: pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=4)
            surf.blit(self.f_main.render(c.number, True, theme.ACCENT if sel else theme.FG), (rect.x + 15, rect.y + 10))
            last_msg = c.messages[-1].body[:60] if c.messages else ""
            surf.blit(self.f_small.render(last_msg, True, theme.FG_DIM), (rect.x + 15, rect.y + 30))

    def _render_chat(self, surf: pygame.Surface, head_h: int):
        conv = self.store.conversations.get(self.target_num)
        if not conv: return
        y = head_h + 10
        surf.blit(self.f_main.render(f"CHAT: {self.target_num}", True, theme.ACCENT), (20, y))
        
        chat_rect = pygame.Rect(10, y + 30, theme.SCREEN_W - 20, theme.SCREEN_H - head_h - 80)
        pygame.draw.rect(surf, (5, 5, 10), chat_rect, border_radius=4)
        
        msgs = conv.messages
        max_v = chat_rect.height // 40
        start = max(0, len(msgs) - max_v - self.chat_scroll)
        visible = msgs[start : start + max_v]
        
        for i, m in enumerate(visible):
            my = chat_rect.y + 10 + i*40
            color = (40, 60, 100) if m.is_me else (40, 40, 40)
            bubble = pygame.Rect(chat_rect.x + (150 if m.is_me else 10), my, theme.SCREEN_W - 200, 35)
            pygame.draw.rect(surf, color, bubble, border_radius=6)
            surf.blit(self.f_small.render(m.body[:80], True, theme.FG), (bubble.x + 10, bubble.y + 10))

    def _render_target(self, surf: pygame.Surface, head_h: int):
        y = head_h + 40
        surf.blit(self.f_main.render(f"DESTINATION: {self.target_num or '[A: ENTER NUMBER]'}", True, theme.FG), (50, y))
        surf.blit(self.f_small.render(f"GATEWAY: {self.target_gateway}", True, theme.ACCENT), (50, y + 30))
        y += 60
        for i, g in enumerate(self.gateway_keys):
            if abs(i - self.cursor) > 4: continue
            sel = i == self.cursor
            surf.blit(self.f_main.render(f"{'> ' if sel else '  '}{g}", True, theme.ACCENT if sel else theme.FG), (60, y + (i-self.cursor+4)*25))

    def _render_msg_input(self, surf: pygame.Surface, head_h: int):
        y = head_h + 40
        surf.blit(self.f_main.render(f"TO: {self.target_num}", True, theme.FG_DIM), (50, y))
        surf.blit(self.f_main.render(f"MSG: {self.message or '[A: COMPOSE]'}", True, theme.FG), (50, y + 40))
        if self.message: surf.blit(self.f_main.render("> PRESS X TO TRANSMIT", True, theme.WARN), (50, y + 120))
