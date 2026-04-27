"""Terminal — Functional bash terminal for DaRkb0x."""
from __future__ import annotations

import os
import subprocess
import threading
import pty
import select
from collections import deque

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent


class TerminalView:
    def __init__(self) -> None:
        self.dismissed = False
        self.history = deque(maxlen=200)
        self.input_line = ""
        
        # UI dimensions
        self.margin = 10
        self.font_size = 18
        self.font = pygame.font.Font(None, self.font_size)
        
        # Process management
        self.master_fd, self.slave_fd = pty.openpty()
        self.process = subprocess.Popen(
            ["/bin/bash", "--login"],
            preexec_fn=os.setsid,
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            env=os.environ
        )
        
        self._stop_event = threading.Event()
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def _read_output(self):
        while not self._stop_event.is_set():
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        # Simple ANSI escape code stripping (not exhaustive)
                        clean_data = self._strip_ansi(data)
                        for line in clean_data.splitlines():
                            self.history.append(line)
                except OSError:
                    break

    def _strip_ansi(self, text: str) -> str:
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def _send_input(self, text: str):
        if text:
            os.write(self.master_fd, (text + "\n").encode())

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._stop_event.set()
            if self.process:
                self.process.terminate()
            self.dismissed = True
        elif ev.button is Button.A:
            ctx.get_input("Terminal Command", self._on_input_done)
        elif ev.button is Button.UP:
            # Maybe scroll history?
            pass

    def _on_input_done(self, text: str | None):
        if text is not None:
            self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill((0, 0, 0)) # Classic black terminal
        
        # Header
        head_h = 40
        pygame.draw.rect(surf, (20, 20, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 1)
        
        f_title = pygame.font.Font(None, 24)
        title = f_title.render("BASH TERMINAL :: root@darkb0x", True, theme.ACCENT)
        surf.blit(title, (10, (head_h - title.get_height()) // 2))

        # Terminal lines
        y = head_h + 5
        line_h = self.font_size + 2
        max_lines = (theme.SCREEN_H - head_h - 40) // line_h
        
        # Show last N lines
        lines = list(self.history)[-max_lines:]
        for line in lines:
            txt_surf = self.font.render(line, True, (200, 200, 200))
            surf.blit(txt_surf, (10, y))
            y += line_h

        # Footer hint
        hint_y = theme.SCREEN_H - 30
        pygame.draw.rect(surf, (15, 15, 15), (0, hint_y - 5, theme.SCREEN_W, 35))
        hint = pygame.font.Font(None, 20).render("A: Enter Command  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (10, hint_y))
