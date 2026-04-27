"""Chat — darksec.uk live chat client."""
from __future__ import annotations

import threading
import time
import requests
from datetime import datetime
from collections import deque

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent


API_URL = "https://darksec.uk/api/chat"
POLL_INTERVAL = 3.0


class ChatView:
    def __init__(self) -> None:
        self.messages = []
        self.last_id = 0
        self.username = "anon"
        self.dismissed = False
        self.is_loading = True
        self.error_msg = None
        
        # UI State
        self.scroll_y = 0
        self.max_scroll = 0
        self.input_text = ""
        
        # Threading
        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                url = f"{API_URL}?after={self.last_id}" if self.last_id > 0 else API_URL
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    new_msgs = res.json()
                    if new_msgs:
                        for m in new_msgs:
                            self.messages.append(m)
                            if m['id'] > self.last_id:
                                self.last_id = m['id']
                        # Keep history manageable
                        if len(self.messages) > 100:
                            self.messages = self.messages[-100:]
                        self.is_loading = False
                else:
                    self.error_msg = f"HTTP {res.status_code}"
            except Exception as e:
                self.error_msg = str(e)
            
            self._stop_event.wait(POLL_INTERVAL)

    def _send_message(self, msg: str):
        if not msg.strip():
            return
        try:
            requests.post(API_URL, json={
                "username": self.username,
                "message": msg
            }, timeout=5)
        except Exception as e:
            print(f"[chat] send failed: {e}")

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._stop_event.set()
            self.dismissed = True
        elif ev.button is Button.A:
            ctx.get_input("Chat Message", self._on_keyboard_done)
        elif ev.button is Button.X:
            ctx.get_input("Set Handle", self._on_handle_done, initial=self.username)
        elif ev.button is Button.UP:
            self.scroll_y = max(0, self.scroll_y - 40)
        elif ev.button is Button.DOWN:
            self.scroll_y = min(self.max_scroll, self.scroll_y + 40)

    def _on_keyboard_done(self, text: str | None):
        if text:
            threading.Thread(target=self._send_message, args=(text,), daemon=True).start()

    def _on_handle_done(self, text: str | None):
        if text:
            self.username = text.strip()[:20] or "anon"

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 36)
        title = f_title.render(f"CHAT :: {self.username.upper()}", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Chat Area
        chat_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING, 
                                theme.SCREEN_W - 2*theme.PADDING, 
                                theme.SCREEN_H - head_h - 2*theme.PADDING - 40)
        pygame.draw.rect(surf, (5, 5, 10), chat_rect)
        pygame.draw.rect(surf, theme.DIVIDER, chat_rect, 1)

        f_body = pygame.font.Font(None, 24)
        f_meta = pygame.font.Font(None, 18)
        
        # Render messages bottom-up
        y = chat_rect.bottom - 10
        total_content_h = 0
        
        # We'll render messages to a temporary surface to handle scrolling if needed,
        # but for now let's just draw them directly with offset.
        
        # Calculate full height first
        for msg in reversed(self.messages):
            # Wrap text? Simplistic wrap for now
            text = msg['message']
            # timestamp = msg['created_at'][11:16]
            user = f"<{msg['username']}>"
            
            # Draw user and text
            user_surf = f_meta.render(user, True, theme.ACCENT_DIM)
            text_surf = f_body.render(text, True, theme.FG)
            
            total_content_h += text_surf.get_height() + 5

        self.max_scroll = max(0, total_content_h - chat_rect.height)
        
        current_y = chat_rect.bottom - 10 + self.scroll_y
        for msg in reversed(self.messages):
            user = f"<{msg['username']}> "
            text = msg['message']
            
            user_surf = f_meta.render(user, True, theme.ACCENT_DIM)
            text_surf = f_body.render(text, True, theme.FG)
            
            h = text_surf.get_height() + 5
            current_y -= h
            
            if chat_rect.y < current_y < chat_rect.bottom - h:
                surf.blit(user_surf, (chat_rect.x + 10, current_y + 2))
                surf.blit(text_surf, (chat_rect.x + 10 + user_surf.get_width(), current_y))

        if self.is_loading:
            msg = f_body.render("Connecting...", True, theme.FG_DIM)
            surf.blit(msg, (chat_rect.centerx - msg.get_width()//2, chat_rect.centery))
        elif self.error_msg and not self.messages:
            err = f_body.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (chat_rect.centerx - err.get_width()//2, chat_rect.centery))

        # Footer
        hint = pygame.font.Font(None, 22).render("A: Send Message  X: Set Handle  UP/DN: Scroll  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
