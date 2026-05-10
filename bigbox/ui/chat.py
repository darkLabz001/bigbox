"""Chat — darksec.uk live chat client."""
from __future__ import annotations

import os
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
        
        # Audio
        self.notify_sound: Optional[pygame.mixer.Sound] = None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            asset_path = "assets/chat_notify.mp3"
            if os.path.exists(asset_path):
                self.notify_sound = pygame.mixer.Sound(asset_path)
                self.notify_sound.set_volume(0.4)
        except Exception as e:
            print(f"[chat] audio init failed: {e}")

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
                        # Only play sound if this isn't the first load
                        if self.last_id > 0 and self.notify_sound:
                            self.notify_sound.play()
                        
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
        elif ev.button in (Button.A, Button.START):
            ctx.get_input("Chat Message", self._on_keyboard_done)
        elif ev.button in (Button.X, Button.SELECT):
            ctx.get_input("Set Handle", self._on_handle_done, initial=self.username)
        elif ev.button is Button.UP:
            self.scroll_y = max(0, self.scroll_y - 40)
        elif ev.button is Button.DOWN:
            self.scroll_y = min(self.max_scroll, self.scroll_y + 40)
        elif ev.button is Button.LL:
            self.scroll_y = max(0, self.scroll_y - 200)
        elif ev.button is Button.RR:
            self.scroll_y = min(self.max_scroll, self.scroll_y + 200)

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
        
        # Draw subtle grid/background for chat area
        pygame.draw.rect(surf, (12, 14, 22), chat_rect)
        for x in range(chat_rect.x, chat_rect.right, 40):
            pygame.draw.line(surf, (20, 22, 32), (x, chat_rect.y), (x, chat_rect.bottom))
        for y in range(chat_rect.y, chat_rect.bottom, 40):
            pygame.draw.line(surf, (20, 22, 32), (chat_rect.x, y), (chat_rect.right, y))
        pygame.draw.rect(surf, theme.DIVIDER, chat_rect, 1)

        # Pre-calculate bubble layouts
        bubbles = []
        max_bubble_w = int(chat_rect.width * 0.75)
        BUBBLE_SPACING = 25
        
        for msg in self.messages:
            is_self = msg['username'] == self.username
            text = msg['message']
            user = msg['username']
            
            # Extract timestamp or use current time
            ts_str = ""
            try:
                # Assuming API might provide 'timestamp' field
                if 'timestamp' in msg:
                    ts_str = msg['timestamp'][-8:-3] # HH:MM
            except: pass
            
            wrapped = self._wrap_text(text, self.f_body, max_bubble_w - 40)
            
            # Calculate bubble dimensions
            txt_surfs = [self.f_body.render(line, True, theme.FG) for line in wrapped]
            b_w = max([s.get_width() for s in txt_surfs] + [0]) + 30
            b_h = len(txt_surfs) * 24 + 20
            
            bubbles.append({
                'is_self': is_self,
                'user': user,
                'ts': ts_str,
                'txt_surfs': txt_surfs,
                'w': b_w,
                'h': b_h
            })

        # Calculate total height correctly
        total_h = sum(b['h'] + BUBBLE_SPACING for b in bubbles)
        self.max_scroll = max(0, total_h - chat_rect.height + 10)
        
        # Auto-scroll to bottom if we were hinted
        if self.scroll_y > 900000:
            self.scroll_y = self.max_scroll
            
        self.scroll_y = min(self.scroll_y, self.max_scroll)
        
        # Create a surface for the chat content
        content_surf = pygame.Surface((chat_rect.width, total_h + 50), pygame.SRCALPHA)
        
        curr_y = 10
        for b in bubbles:
            is_self = b['is_self']
            bx = chat_rect.width - b['w'] - 10 if is_self else 50
            by = curr_y
            
            # Avatar
            avatar_x = chat_rect.width - 35 if is_self else 10
            avatar_rect = pygame.Rect(avatar_x, by, 30, 30)
            pygame.draw.ellipse(content_surf, theme.ACCENT_DIM if not is_self else theme.SELECTION_BG, avatar_rect)
            char = b['user'][0].upper()
            av_txt = self.f_meta.render(char, True, theme.ACCENT)
            content_surf.blit(av_txt, (avatar_rect.centerx - av_txt.get_width()//2, avatar_rect.centery - av_txt.get_height()//2))

            # Bubble background with glow
            bubble_rect = pygame.Rect(bx, by, b['w'], b['h'])
            bg_col = (30, 35, 50) if is_self else (22, 26, 40)
            border_col = theme.ACCENT if is_self else theme.DIVIDER
            
            # Subtle glow for own bubbles
            if is_self:
                glow_rect = bubble_rect.inflate(4, 4)
                pygame.draw.rect(content_surf, (*theme.ACCENT, 40), glow_rect, border_radius=12)

            pygame.draw.rect(content_surf, bg_col, bubble_rect, border_radius=10)
            pygame.draw.rect(content_surf, border_col, bubble_rect, 1, border_radius=10)
            
            # Username and Timestamp (only for others)
            if not is_self:
                u_str = b['user']
                if b['ts']: u_str += f"  [{b['ts']}]"
                u_surf = self.f_meta.render(u_str, True, theme.ACCENT_DIM)
                content_surf.blit(u_surf, (bx, by - 16))
            elif b['ts']:
                t_surf = self.f_meta.render(b['ts'], True, theme.FG_DIM)
                content_surf.blit(t_surf, (bx + b['w'] - t_surf.get_width(), by - 16))
            
            # Render lines
            ly = by + 10
            for ts in b['txt_surfs']:
                content_surf.blit(ts, (bx + 15, ly))
                ly += 24
                
            curr_y += b['h'] + BUBBLE_SPACING

        # Subsurface blit for scrolling
        if total_h > 0:
            try:
                view_rect = pygame.Rect(0, self.scroll_y, chat_rect.width, min(total_h + 50 - self.scroll_y, chat_rect.height))
                if view_rect.height > 0:
                    surf.blit(content_surf.subsurface(view_rect), chat_rect.topleft)
            except Exception:
                # Fallback if subsurface fails
                surf.blit(content_surf, chat_rect.topleft, (0, self.scroll_y, chat_rect.width, chat_rect.height))

        # Fancy Scrollbar
        if self.max_scroll > 0:
            sb_w = 6
            track_h = chat_rect.height - 10
            track_x = chat_rect.right - sb_w - 4
            track_y = chat_rect.y + 5
            pygame.draw.rect(surf, (15, 15, 25), (track_x, track_y, sb_w, track_h), border_radius=3)
            
            thumb_h = max(30, int(track_h * (chat_rect.height / (total_h + 50))))
            thumb_y = track_y + int((self.scroll_y / self.max_scroll) * (track_h - thumb_h))
            pygame.draw.rect(surf, theme.ACCENT, (track_x, thumb_y, sb_w, thumb_h), border_radius=3)

        if self.is_loading:
            msg = self.f_body.render("Connecting to darksec.uk...", True, theme.FG_DIM)
            surf.blit(msg, (chat_rect.centerx - msg.get_width()//2, chat_rect.centery))
        elif self.error_msg and not self.messages:
            err = self.f_body.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (chat_rect.centerx - err.get_width()//2, chat_rect.centery))

        # Footer
        footer_y = theme.SCREEN_H - 35
        pygame.draw.line(surf, theme.DIVIDER, (0, footer_y - 5), (theme.SCREEN_W, footer_y - 5))
        hint = self.f_hint.render("A: SEND MESSAGE  X: SET HANDLE  UP/DN: SCROLL  B: BACK", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W // 2 - hint.get_width() // 2, footer_y))
