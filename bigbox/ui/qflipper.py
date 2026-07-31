"""qFlipper — Complete Flipper Zero device manager and controller."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.flipper_zero_link import FliperZeroLink

if TYPE_CHECKING:
    from bigbox.app import App


class QFlipperView:
    """Full-featured Flipper Zero device manager with app launcher and control."""

    def __init__(self) -> None:
        self.dismissed = False
        self.link = FliperZeroLink()
        self.link.start()

        self.mode = "HOME"  # HOME, APPS, FILES, SETTINGS, INFO
        self.cursor = 0
        self.scroll = 0
        self.apps: list[dict] = []
        self.files: list[str] = []
        self.output_buffer = ""
        self._loading = False

        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)
        self.small_font = pygame.font.Font(None, 20)
        self.mono_font = pygame.font.Font(None, 18)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            if self.mode == "HOME":
                self.link.stop()
                self.dismissed = True
            else:
                self.mode = "HOME"
                self.cursor = 0

        elif self.mode == "HOME":
            self._handle_home(ev, ctx)
        elif self.mode == "APPS":
            self._handle_apps(ev)
        elif self.mode == "FILES":
            self._handle_files(ev)
        elif self.mode == "SETTINGS":
            self._handle_settings(ev)
        elif self.mode == "INFO":
            self._handle_info(ev)

    def _handle_home(self, ev: ButtonEvent, ctx: App) -> None:
        """Handle input on home menu."""
        menu_items = ["Scan Devices", "Device Info", "Browse Apps", "File Manager", "Settings"]

        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(menu_items)
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(menu_items)
        elif ev.button is Button.A:
            if self.cursor == 0:
                # Scan for devices
                self.output_buffer = "Scanning for Flipper Zero..."
                self.link = FliperZeroLink()
                self.link.start()
            elif self.cursor == 1:
                self.mode = "INFO"
            elif self.cursor == 2:
                self.mode = "APPS"
                self._load_apps()
            elif self.cursor == 3:
                self.mode = "FILES"
                self._load_files()
            elif self.cursor == 4:
                self.mode = "SETTINGS"
            self.cursor = 0

    def _handle_apps(self, ev: ButtonEvent) -> None:
        """Handle app browser input."""
        if not self.apps:
            return

        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(self.apps)
            self.scroll = max(0, min(self.cursor, len(self.apps) - 4))
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(self.apps)
            self.scroll = max(0, min(self.cursor - 3, len(self.apps) - 4))
        elif ev.button is Button.A:
            app = self.apps[self.cursor]
            self.output_buffer = self.link.launch_app(app["name"])

    def _handle_files(self, ev: ButtonEvent) -> None:
        """Handle file browser input."""
        if not self.files:
            return

        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(self.files)
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(self.files)

    def _handle_settings(self, ev: ButtonEvent) -> None:
        """Handle settings menu input."""
        menu_items = ["Reboot Device", "Update Firmware", "Format Storage"]

        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(menu_items)
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(menu_items)
        elif ev.button is Button.A:
            if self.cursor == 0:
                self.output_buffer = self.link.send_command("reboot")

    def _handle_info(self, ev: ButtonEvent) -> None:
        """Handle info screen input."""
        pass

    def _load_apps(self) -> None:
        """Load app list in background."""
        self._loading = True
        thread = threading.Thread(target=self._load_apps_thread, daemon=True)
        thread.start()

    def _load_apps_thread(self) -> None:
        """Background thread to load apps."""
        try:
            self.apps = self.link.list_apps()
        finally:
            self._loading = False

    def _load_files(self) -> None:
        """Load file list."""
        self._loading = True
        thread = threading.Thread(target=self._load_files_thread, daemon=True)
        thread.start()

    def _load_files_thread(self) -> None:
        """Background thread to load files."""
        try:
            result = subprocess.run(
                ["find", "/mnt/flipper", "-type", "f", "-not", "-path", "*/.*"],
                capture_output=True,
                text=True,
                timeout=10
            )
            self.files = [f for f in result.stdout.strip().split("\n") if f][:100]
        except Exception:
            self.files = []
        finally:
            self._loading = False

    def render(self, surf: pygame.Surface) -> None:
        if self.mode == "HOME":
            self._render_home(surf)
        elif self.mode == "APPS":
            self._render_apps(surf)
        elif self.mode == "FILES":
            self._render_files(surf)
        elif self.mode == "SETTINGS":
            self._render_settings(surf)
        elif self.mode == "INFO":
            self._render_info(surf)

    def _render_home(self, surf: pygame.Surface) -> None:
        """Render home screen."""
        surf.fill(theme.BG)
        pad = theme.PADDING

        # Header
        title = self.title_font.render("FLIPPER ZERO", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        # Connection status
        st = self.link.snapshot()
        status_text = "● CONNECTED" if st.connected else "○ NOT CONNECTED"
        status_color = theme.ACCENT if st.connected else theme.WARN
        status = self.body_font.render(status_text, True, status_color)
        surf.blit(status, (theme.SCREEN_W - pad - status.get_width(), pad + 6))

        # Connection type badge
        conn_type = st.connection_type if st.connection_type else "—"
        conn_badge = self.small_font.render(f"[{conn_type}]", True, theme.FG_DIM)
        surf.blit(conn_badge, (theme.SCREEN_W - pad - conn_badge.get_width(), pad + 32))

        # Menu items
        menu_items = ["Scan Devices", "Device Info", "Browse Apps", "File Manager", "Settings"]
        y = 90

        for i, item in enumerate(menu_items):
            selected = i == self.cursor
            color = theme.ACCENT if selected else theme.FG

            if selected:
                pygame.draw.rect(
                    surf, theme.SELECTION_BG,
                    (pad, y - 4, theme.SCREEN_W - 2 * pad, 28),
                    border_radius=4
                )

            text = self.body_font.render(f"{'▶' if selected else ' '} {item}", True, color)
            surf.blit(text, (pad + 10, y))
            y += 36

        # Status/output and phase info
        phase_text = st.phase if st.phase != "DISCONNECTED" else ""
        if self.output_buffer or phase_text:
            display_text = self.output_buffer or phase_text
            color = theme.WARN if "CONNECTING" in phase_text or "Scanning" in self.output_buffer else theme.FG_DIM
            out = self.small_font.render(display_text[:60], True, color)
            surf.blit(out, (pad, theme.SCREEN_H - 50))

        # Device info strip
        self._render_status_strip(surf, st)

    def _render_apps(self, surf: pygame.Surface) -> None:
        """Render app browser."""
        surf.fill(theme.BG)
        pad = theme.PADDING

        # Header
        title = self.title_font.render("APPS", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        # Loading indicator
        if self._loading:
            loading = self.body_font.render("Loading...", True, theme.WARN)
            surf.blit(loading, (pad, 80))
            return

        # App list
        if not self.apps:
            no_apps = self.body_font.render("No apps found", True, theme.FG_DIM)
            surf.blit(no_apps, (pad, 80))
            return

        y = 80
        for i, app in enumerate(self.apps[self.scroll : self.scroll + 4]):
            selected = i + self.scroll == self.cursor
            color = theme.ACCENT if selected else theme.FG

            if selected:
                pygame.draw.rect(
                    surf, theme.SELECTION_BG,
                    (pad, y - 4, 300, 26),
                    border_radius=4
                )

            text = self.body_font.render(f"{'▶' if selected else ' '} {app['name']}", True, color)
            surf.blit(text, (pad + 10, y))
            y += 30

        # Output
        if self.output_buffer:
            out = self.small_font.render(self.output_buffer[:50], True, theme.FG_DIM)
            surf.blit(out, (pad, theme.SCREEN_H - 40))

    def _render_files(self, surf: pygame.Surface) -> None:
        """Render file manager."""
        surf.fill(theme.BG)
        pad = theme.PADDING

        title = self.title_font.render("FILES", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        if self._loading:
            loading = self.body_font.render("Loading...", True, theme.WARN)
            surf.blit(loading, (pad, 80))
            return

        if not self.files:
            no_files = self.body_font.render("No files found", True, theme.FG_DIM)
            surf.blit(no_files, (pad, 80))
            return

        y = 80
        for i, file in enumerate(self.files[:8]):
            selected = i == self.cursor
            color = theme.ACCENT if selected else theme.FG

            if selected:
                pygame.draw.rect(
                    surf, theme.SELECTION_BG,
                    (pad, y - 4, theme.SCREEN_W - 2 * pad, 24),
                    border_radius=4
                )

            fname = file.split("/")[-1][:40]
            text = self.small_font.render(fname, True, color)
            surf.blit(text, (pad + 10, y))
            y += 26

    def _render_settings(self, surf: pygame.Surface) -> None:
        """Render settings screen."""
        surf.fill(theme.BG)
        pad = theme.PADDING

        title = self.title_font.render("SETTINGS", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        menu_items = ["Reboot Device", "Update Firmware", "Format Storage"]
        y = 80

        for i, item in enumerate(menu_items):
            selected = i == self.cursor
            color = theme.ERR if i > 0 else (theme.ACCENT if selected else theme.FG)

            if selected:
                pygame.draw.rect(
                    surf, theme.SELECTION_BG,
                    (pad, y - 4, 300, 28),
                    border_radius=4
                )

            text = self.body_font.render(f"{'⚠' if i > 0 else '▶' if selected else ' '} {item}", True, color)
            surf.blit(text, (pad + 10, y))
            y += 36

    def _render_info(self, surf: pygame.Surface) -> None:
        """Render device info screen."""
        surf.fill(theme.BG)
        pad = theme.PADDING

        title = self.title_font.render("DEVICE INFO", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        st = self.link.snapshot()
        y = 80

        info_items = [
            ("STATUS", "Connected" if st.connected else "Disconnected"),
            ("DEVICE", st.device_name or "Flipper Zero"),
            ("FIRMWARE", st.firmware_version or "Unknown"),
            ("BATTERY", f"{st.battery}%" if st.battery >= 0 else "N/A"),
        ]

        for label, value in info_items:
            label_surf = self.small_font.render(f"{label}:", True, theme.FG_DIM)
            val_surf = self.small_font.render(str(value)[:40], True, theme.FG)
            surf.blit(label_surf, (pad, y))
            surf.blit(val_surf, (pad + 150, y))
            y += 28

    def _render_status_strip(self, surf: pygame.Surface, st) -> None:
        """Render device status strip at bottom."""
        y = theme.SCREEN_H - 40
        pad = theme.PADDING

        pygame.draw.line(surf, theme.DIVIDER, (pad, y - 8), (theme.SCREEN_W - pad, y - 8), 1)

        info_text = f"BATT: {st.battery}%  |  " if st.battery >= 0 else ""
        info_text += f"FW: {st.firmware_version[:20] if st.firmware_version else 'Unknown'}"

        info = self.small_font.render(info_text, True, theme.FG_DIM)
        surf.blit(info, (pad, y))

        hint = self.small_font.render("A: Select  B: Back  ↑↓: Navigate", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - pad - hint.get_width(), y))
