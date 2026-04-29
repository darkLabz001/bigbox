"""Chat — darksec.uk live chat client."""
from __future__ import annotations

import threading
import time
import requests
from datetime import datetime
from collections import deque
from typing import TYPE_CHECKING, List, Dict, Optional

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

API_URL = "https://darksec.uk/api/chat"
POLL_INTERVAL = 3.0


class ChatView:
    def __init__(self) -> None:
        self.messages: List[Dict] = []
        self.last_id = 0
        self.username = "anon"
        self.dismissed = False
        self.is_loading = True
        self.error_msg = None
        
        # UI State
        self.scroll_y = 0
        self.max_scroll = 0
        
        # Fonts
        self.f_title = pygame.font.Font(None, 36)
        self.f_body = pygame.font.Font(None, 24)
        self.f_meta = pygame.font.Font(None, 18)
        self.f_hint = pygame.font.Font(None, 22)
        
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
                        was_at_bottom = self.scroll_y >= self.max_scroll - 10 or self.max_scroll == 0
                        for m in new_msgs:
                            self.messages.append(m)
                            if m['id'] > self.last_id:
                                self.last_id = m['id']
                        # Keep history manageable
                        if len(self.messages) > 100:
                            self.messages = self.messages[-100:]
                        self.is_loading = False
                        self.error_msg = None
                        
                        # Trigger a re-calculation of max_scroll and scroll to bottom if needed
                        # (The next render will update max_scroll, but we can hint it)
                        if was_at_bottom:
                            self.scroll_y = 999999 # Hack to stay at bottom
                elif res.status_code == 401:
                    self.error_msg = "UNAUTHORIZED: API KEY REQUIRED"
                else:
                    self.error_msg = f"SERVER ERROR: {res.status_code}"
            except Exception as e:
                self.error_msg = f"CONNECTION ERROR: {str(e)[:20]}"
            
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

    def _wrap_text(self, text: str, font: pygame.font.Font, max_width: int) -> List[str]:
        words = text.split(' ')
        lines = []
        current_line = []

        for word in words:
            test_line = ' '.join(current_line + [word])
            w, _ = font.size(test_line)
            if w <= max_width:
                current_line.append(word)
            else:
                lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        return lines

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        title = self.f_title.render(f"CHAT :: {self.username.upper()}", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Chat Area
        chat_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING, 
                                theme.SCREEN_W - 2*theme.PADDING, 
                                theme.SCREEN_H - head_h - 2*theme.PADDING - 40)
        pygame.draw.rect(surf, (5, 5, 10), chat_rect)
        pygame.draw.rect(surf, theme.DIVIDER, chat_rect, 1)

        # Prepare messages to be rendered
        rendered_lines = []
        max_text_w = chat_rect.width - 20
        
        for msg in self.messages:
            user = f"<{msg['username']}>"
            text = msg['message']
            
            # Metadata line
            rendered_lines.append(('meta', user))
            
            # Wrapped text lines
            wrapped = self._wrap_text(text, self.f_body, max_text_w - 20)
            for line in wrapped:
                rendered_lines.append(('text', line))
            
            # Spacer
            rendered_lines.append(('spacer', ''))

        line_h = 24
        total_h = len(rendered_lines) * line_h
        self.max_scroll = max(0, total_h - chat_rect.height)
        self.scroll_y = min(self.scroll_y, self.max_scroll)
        
        # Create a surface for the chat content
        content_surf = pygame.Surface((chat_rect.width, total_h), pygame.SRCALPHA)
        
        curr_y = 0
        for type, content in rendered_lines:
            if type == 'meta':
                s = self.f_meta.render(content, True, theme.ACCENT_DIM)
                content_surf.blit(s, (10, curr_y + 4))
            elif type == 'text':
                s = self.f_body.render(content, True, theme.FG)
                content_surf.blit(s, (25, curr_y))
            curr_y += line_h

        # Subsurface blit for scrolling
        if total_h > 0:
            view_rect = pygame.Rect(0, self.scroll_y, chat_rect.width, min(total_h - self.scroll_y, chat_rect.height))
            if view_rect.height > 0:
                surf.blit(content_surf.subsurface(view_rect), chat_rect.topleft)

        # Scrollbar
        if self.max_scroll > 0:
            sb_w = 4
            track_h = chat_rect.height
            pygame.draw.rect(surf, theme.DIVIDER, (chat_rect.right - sb_w - 2, chat_rect.y, sb_w, track_h))
            thumb_h = max(20, int(track_h * (chat_rect.height / total_h)))
            thumb_y = chat_rect.y + int((self.scroll_y / self.max_scroll) * (track_h - thumb_h))
            pygame.draw.rect(surf, theme.ACCENT_DIM, (chat_rect.right - sb_w - 2, thumb_y, sb_w, thumb_h))

        if self.is_loading:
            msg = self.f_body.render("Connecting...", True, theme.FG_DIM)
            surf.blit(msg, (chat_rect.centerx - msg.get_width()//2, chat_rect.centery))
        elif self.error_msg and not self.messages:
            err = self.f_body.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (chat_rect.centerx - err.get_width()//2, chat_rect.centery))

        # Footer
        hint = self.f_hint.render("A: Send Message  X: Set Handle  UP/DN: Scroll  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
