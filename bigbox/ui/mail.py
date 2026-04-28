"""Tactical Mail — IMAP/SMTP email client for handheld."""
from __future__ import annotations

import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.header import decode_header
import threading
import time
import os
import json
import dataclasses
from dataclasses import dataclass, asdict
from typing import Optional, List, TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App

CONFIG_PATH = os.path.expanduser("~/.bigbox/mail.json")

@dataclass
class MailMessage:
    uid: str
    subject: str
    sender: str
    date: str
    body: str = ""

@dataclass
class MailConfig:
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    email: str = ""
    password: str = "" # Recommend App Password

    def save(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(asdict(self), f)
            # Set restrictive permissions (600) for security
            os.chmod(CONFIG_PATH, 0o600)
        except Exception as e:
            print(f"[mail] Save failed: {e}")

    @classmethod
    def load(cls):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = json.load(f)
                # Filter out keys not in dataclass to avoid TypeError
                valid_keys = {f.name for f in dataclasses.fields(cls)}
                filtered_data = {k: v for k, v in data.items() if k in valid_keys}
                return cls(**filtered_data)
            except Exception as e:
                print(f"[mail] Load failed: {e}")
        return cls()

class MailView:
    PHASE_INBOX = "INBOX"
    PHASE_READING = "READING"
    PHASE_COMPOSING = "COMPOSING"
    PHASE_CONFIG = "CONFIG"

    def __init__(self) -> None:
        self.dismissed = False
        self.config = MailConfig.load()
        self.phase = self.PHASE_INBOX if self.config.email else self.PHASE_CONFIG
        
        self.messages: List[MailMessage] = []
        self.selected_idx = 0
        self.current_msg: Optional[MailMessage] = None
        
        self.is_loading = False
        self.error_msg: Optional[str] = None
        self.status_msg: Optional[str] = None
        
        self.scroll_y = 0
        self.body_scroll_y = 0
        
        # Composition state
        self.comp_to = ""
        self.comp_subject = ""
        self.comp_body = ""
        
        self.title_font = pygame.font.Font(None, 32)
        self.body_font = pygame.font.Font(None, 24)
        self.small_font = pygame.font.Font(None, 18)

        if self.config.email:
            self._refresh_inbox()

    def _refresh_inbox(self):
        if self.is_loading: return
        self.is_loading = True
        self.error_msg = None
        self.status_msg = "Fetching messages..."
        threading.Thread(target=self._fetch_mails_thread, daemon=True).start()

    def _fetch_mails_thread(self):
        try:
            mail = imaplib.IMAP4_SSL(self.config.imap_server, self.config.imap_port)
            mail.login(self.config.email, self.config.password)
            mail.select("inbox")
            
            # Fetch last 20 messages
            status, data = mail.search(None, "ALL")
            if status != "OK" or not data[0]:
                self.messages = []
                self.is_loading = False
                mail.logout()
                return

            mail_ids = data[0].split()
            recent_ids = mail_ids[-20:]
            
            new_msgs = []
            for m_id in reversed(recent_ids):
                try:
                    status, msg_data = mail.fetch(m_id, "(RFC822)")
                    if status != "OK": continue
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            subject, encoding = decode_header(msg["Subject"] or "No Subject")[0]
                            if isinstance(subject, bytes):
                                try:
                                    subject = subject.decode(encoding or "utf-8")
                                except:
                                    subject = subject.decode("latin1")
                            
                            sender = msg.get("From", "Unknown")
                            date = msg.get("Date", "")
                            
                            body = ""
                            try:
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() == "text/plain":
                                            payload = part.get_payload(decode=True)
                                            if payload:
                                                body = payload.decode(errors="replace")
                                                break
                                else:
                                    payload = msg.get_payload(decode=True)
                                    if payload:
                                        body = payload.decode(errors="replace")
                            except:
                                body = "[Error decoding body]"
                                
                            new_msgs.append(MailMessage(
                                uid=m_id.decode(),
                                subject=str(subject),
                                sender=str(sender),
                                date=str(date),
                                body=body
                            ))
                except Exception as fe:
                    print(f"[mail] Fetch error for {m_id}: {fe}")
            
            self.messages = new_msgs
            self.is_loading = False
            self.status_msg = None
            mail.logout()
        except Exception as e:
            self.error_msg = str(e)
            self.is_loading = False
            self.status_msg = None

    def _test_connection(self):
        if self.is_loading: return
        self.is_loading = True
        self.error_msg = None
        self.status_msg = "Testing connection..."
        threading.Thread(target=self._test_connection_thread, daemon=True).start()

    def _test_connection_thread(self):
        try:
            mail = imaplib.IMAP4_SSL(self.config.imap_server, self.config.imap_port)
            mail.login(self.config.email, self.config.password)
            mail.logout()
            self.status_msg = "Connection Successful!"
            self.is_loading = False
            time.sleep(2)
            self.status_msg = None
        except Exception as e:
            self.error_msg = str(e)
            self.is_loading = False
            self.status_msg = None

    def _send_mail(self):
        if self.is_loading: return
        self.is_loading = True
        self.status_msg = "Sending mail..."
        threading.Thread(target=self._send_mail_thread, daemon=True).start()

    def _send_mail_thread(self):
        try:
            msg = MIMEText(self.comp_body)
            msg['Subject'] = self.comp_subject
            msg['From'] = self.config.email
            msg['To'] = self.comp_to
            
            server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
            server.starttls()
            server.login(self.config.email, self.config.password)
            server.send_message(msg)
            server.quit()
            
            self.phase = self.PHASE_INBOX
            self.is_loading = False
            self.status_msg = "Mail Sent!"
            time.sleep(1)
            self._refresh_inbox()
        except Exception as e:
            self.error_msg = str(e)
            self.is_loading = False
            self.status_msg = None

    def _on_email_done(self, v):
        if v:
            self.config.email = v.strip()
            self.config.save()
            
    def _on_pass_done(self, v):
        if v:
            self.config.password = v.strip()
            self.config.save()

    def _on_server_done(self, v):
        if v:
            self.config.imap_server = v.strip()
            self.config.save()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            if self.phase == self.PHASE_INBOX:
                self.dismissed = True
            elif self.phase == self.PHASE_READING:
                self.phase = self.PHASE_INBOX
            elif self.phase == self.PHASE_COMPOSING:
                self.phase = self.PHASE_INBOX
            elif self.phase == self.PHASE_CONFIG:
                if self.config.email: 
                    self.phase = self.PHASE_INBOX
                    self._refresh_inbox()
                else: self.dismissed = True
            return

        if self.phase == self.PHASE_INBOX:
            if ev.button is Button.UP:
                self.selected_idx = max(0, self.selected_idx - 1)
            elif ev.button is Button.DOWN:
                self.selected_idx = min(len(self.messages) - 1, self.selected_idx + 1)
            elif ev.button is Button.A:
                if self.messages:
                    self.current_msg = self.messages[self.selected_idx]
                    self.phase = self.PHASE_READING
                    self.body_scroll_y = 0
            elif ev.button is Button.X:
                self.phase = self.PHASE_COMPOSING
                self.comp_to = ""
                self.comp_subject = ""
                self.comp_body = ""
            elif ev.button is Button.Y:
                self._refresh_inbox()
            elif ev.button is Button.SELECT:
                self.phase = self.PHASE_CONFIG

        elif self.phase == self.PHASE_READING:
            if ev.button is Button.UP:
                self.body_scroll_y = max(0, self.body_scroll_y - 30)
            elif ev.button is Button.DOWN:
                self.body_scroll_y += 30 # capped in render

        elif self.phase == self.PHASE_COMPOSING:
            if ev.button is Button.A:
                # Context sensitive input
                if not self.comp_to:
                    ctx.get_input("To:", lambda v: setattr(self, "comp_to", v or ""))
                elif not self.comp_subject:
                    ctx.get_input("Subject:", lambda v: setattr(self, "comp_subject", v or ""))
                else:
                    ctx.get_input("Body:", lambda v: setattr(self, "comp_body", v or ""))
            elif ev.button is Button.X:
                if self.comp_to and self.comp_body:
                    self._send_mail()

        elif self.phase == self.PHASE_CONFIG:
            if ev.button is Button.A:
                ctx.get_input("Email:", self._on_email_done, initial=self.config.email)
            elif ev.button is Button.X:
                ctx.get_input("Password/App Pass:", self._on_pass_done)
            elif ev.button is Button.Y:
                ctx.get_input("IMAP Server:", self._on_server_done, initial=self.config.imap_server)
            elif ev.button is Button.START:
                self._test_connection()
            elif ev.button is Button.SELECT:
                self.config.save()
                self.phase = self.PHASE_INBOX
                self._refresh_inbox()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 1)
        
        title_str = f"MAIL :: {self.phase}"
        if self.phase == self.PHASE_INBOX: title_str = f"INBOX ({self.config.email})"
        title = self.title_font.render(title_str, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        if self.phase == self.PHASE_INBOX:
            self._render_inbox(surf, head_h)
        elif self.phase == self.PHASE_READING:
            self._render_reading(surf, head_h)
        elif self.phase == self.PHASE_COMPOSING:
            self._render_composing(surf, head_h)
        elif self.phase == self.PHASE_CONFIG:
            self._render_config(surf, head_h)

        if self.is_loading or self.status_msg:
            pygame.draw.rect(surf, (0,0,0,180), (0,0,theme.SCREEN_W, theme.SCREEN_H))
            msg_text = self.status_msg or "WORKING..."
            msg = self.body_font.render(msg_text, True, theme.ACCENT)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))

    def _render_inbox(self, surf: pygame.Surface, head_h: int):
        y = head_h + 5
        row_h = 60
        
        if self.error_msg:
            msg = self.error_msg
            if "application-specific password required" in msg.lower():
                msg = "GMAIL ERROR: You must use an 'App Password'!"
            
            err = self.body_font.render(f"ERROR: {msg[:60]}", True, theme.ERR)
            surf.blit(err, (theme.PADDING, y + 20))
            hint = self.small_font.render("Check Google Account -> Security -> App Passwords", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, y + 60))
            return

        if not self.messages and not self.is_loading:
            msg = self.body_font.render("No messages in Inbox.", True, theme.FG_DIM)
            surf.blit(msg, (theme.PADDING, y + 20))
        
        for i, m in enumerate(self.messages):
            ry = y + i * row_h
            if ry > theme.SCREEN_H - 40: break
            
            sel = i == self.selected_idx
            rect = pygame.Rect(5, ry, theme.SCREEN_W - 10, row_h - 2)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=4)
            
            color = theme.ACCENT if sel else theme.FG
            subj = self.body_font.render(m.subject[:50], True, color)
            surf.blit(subj, (rect.x + 10, rect.y + 5))
            
            from_str = self.small_font.render(f"From: {m.sender[:40]}", True, theme.FG_DIM)
            surf.blit(from_str, (rect.x + 10, rect.y + 30))
            
            date_str = self.small_font.render(m.date[:20], True, theme.FG_DIM)
            surf.blit(date_str, (rect.right - date_str.get_width() - 10, rect.y + 30))

        hint = self.small_font.render("A: Read  X: Compose  Y: Refresh  SELECT: Config  B: Exit", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 25))

    def _render_reading(self, surf: pygame.Surface, head_h: int):
        if not self.current_msg: return
        m = self.current_msg
        
        # Header Info
        pygame.draw.rect(surf, theme.BG_ALT, (10, head_h + 10, theme.SCREEN_W - 20, 80), border_radius=4)
        surf.blit(self.body_font.render(f"Subj: {m.subject[:60]}", True, theme.ACCENT), (20, head_h + 20))
        surf.blit(self.small_font.render(f"From: {m.sender[:80]}", True, theme.FG), (20, head_h + 50))
        
        # Body
        body_rect = pygame.Rect(10, head_h + 100, theme.SCREEN_W - 20, theme.SCREEN_H - head_h - 140)
        pygame.draw.rect(surf, (5,5,10), body_rect, border_radius=4)
        
        # Simple text wrapping/lines
        lines = m.body.split('\n')
        line_h = 20
        visible_lines = body_rect.height // line_h
        
        max_scroll = max(0, (len(lines) - visible_lines) * line_h)
        self.body_scroll_y = min(self.body_scroll_y, max_scroll)
        
        start_line = self.body_scroll_y // line_h
        for i in range(visible_lines + 1):
            idx = start_line + i
            if idx >= len(lines): break
            txt = self.small_font.render(lines[idx][:100], True, theme.FG)
            surf.blit(txt, (body_rect.x + 10, body_rect.y + 10 + i*line_h))

        hint = self.small_font.render("UP/DN: Scroll  B: Back to Inbox", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 25))

    def _render_composing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 20
        surf.blit(self.body_font.render(f"To: {self.comp_to or '[Click A]'}", True, theme.FG), (20, y))
        surf.blit(self.body_font.render(f"Subj: {self.comp_subject or '[Click A]'}", True, theme.FG), (20, y + 40))
        
        body_box = pygame.Rect(20, y + 80, theme.SCREEN_W - 40, 200)
        pygame.draw.rect(surf, (0,0,0), body_box, border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, body_box, 1, border_radius=4)
        
        # Render body preview
        body_lines = self.comp_body.split('\n')
        for i, ln in enumerate(body_lines[:8]):
            txt = self.small_font.render(ln[:80], True, theme.FG)
            surf.blit(txt, (body_box.x + 10, body_box.y + 10 + i*20))

        hint = self.small_font.render("A: Edit Fields  X: SEND  B: Cancel", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 25))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        lines = [
            f"A: Email: {self.config.email or 'NOT SET'}",
            f"X: Password: {'********' if self.config.password else 'NOT SET'}",
            f"Y: IMAP Server: {self.config.imap_server}",
            "",
            "START: Test Connection",
            "SELECT: Save and Return",
            "B: Cancel"
        ]
        for i, ln in enumerate(lines):
            color = theme.ERR if "NOT SET" in ln else theme.FG
            txt = self.body_font.render(ln, True, color)
            surf.blit(txt, (40, y + i*40))
        
        hint = self.small_font.render("Note: Gmail requires 'App Passwords' (not your main pass)", True, theme.FG_DIM)
        surf.blit(hint, (40, y + len(lines)*40 + 10))
        
        if self.error_msg:
            err = self.small_font.render(f"LAST ERROR: {self.error_msg[:80]}", True, theme.ERR)
            surf.blit(err, (40, theme.SCREEN_H - 100))
