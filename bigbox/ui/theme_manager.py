"""Theme Manager UI — browse, install, and apply custom themes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


class ThemeManagerView:
    def __init__(self) -> None:
        self.dismissed = False
        self.themes: list[dict] = []
        self.cursor = 0
        self.error_msg = ""
        self.status_msg = ""
        
        self.themes_dir = Path("/opt/bigbox/config/themes")
        # Fallback to local dev directory if not on Pi
        if not self.themes_dir.exists():
            self.themes_dir = Path(__file__).resolve().parents[2] / "config" / "themes"
            self.themes_dir.mkdir(parents=True, exist_ok=True)
            
        self.repo_dir = self.themes_dir / "darkbox-themes"
        self._load_themes()

        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def _load_themes(self):
        self.themes = []
        # Load local user themes
        self._scan_dir(self.themes_dir, "Local")
        # Load community themes from repo
        if self.repo_dir.exists():
            self._scan_dir(self.repo_dir, "Community")

    def _scan_dir(self, directory: Path, source: str):
        if not directory.exists(): return
        for p in directory.glob("*.json"):
            if p.name in ("active.json", "template.json"):
                continue
            try:
                with p.open("r") as f:
                    data = json.load(f)
                    name = data.get("name", p.stem)
                    author = data.get("author", "Unknown")
                    self.themes.append({
                        "name": name,
                        "author": author,
                        "path": p,
                        "source": source
                    })
            except Exception:
                pass

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.UP and self.themes:
            self.cursor = (self.cursor - 1) % len(self.themes)
            self.status_msg = ""
            self.error_msg = ""
        elif ev.button is Button.DOWN and self.themes:
            self.cursor = (self.cursor + 1) % len(self.themes)
            self.status_msg = ""
            self.error_msg = ""
        elif ev.button is Button.A and self.themes:
            self._apply_theme(self.themes[self.cursor]["path"], ctx)
        elif ev.button is Button.X:
            self._sync_repo()

    def _apply_theme(self, path: Path, ctx: App):
        target = self.themes_dir / "active.json"
        try:
            shutil.copy(path, target)
            self.status_msg = "Theme applied! Reboot to see changes."
        except Exception as e:
            self.error_msg = f"Failed to apply: {e}"

    def _sync_repo(self):
        self.status_msg = "Syncing community themes..."
        self.error_msg = ""
        
        # Perform git operations in a thread to not block UI
        import threading
        threading.Thread(target=self._git_sync, daemon=True).start()

    def _git_sync(self):
        repo_url = "https://github.com/darkLabz001/darkbox-themes.git"
        try:
            if not self.repo_dir.exists():
                subprocess.run(["git", "clone", repo_url, str(self.repo_dir)], check=True, capture_output=True)
            else:
                subprocess.run(["git", "-C", str(self.repo_dir), "pull"], check=True, capture_output=True)
            self._load_themes()
            self.status_msg = "Themes synced successfully."
        except Exception as e:
            self.error_msg = "Failed to sync repo."

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("THEME MANAGER :: CUSTOMIZE", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        if self.status_msg:
            msg = self.body_font.render(self.status_msg, True, theme.FG)
            surf.blit(msg, (theme.PADDING, 60))
        elif self.error_msg:
            msg = self.body_font.render(self.error_msg, True, theme.ERR)
            surf.blit(msg, (theme.PADDING, 60))
        else:
            msg = self.body_font.render(f"Found {len(self.themes)} themes", True, theme.FG_DIM)
            surf.blit(msg, (theme.PADDING, 60))

        list_y = 100
        for i, t in enumerate(self.themes):
            y = list_y + i * 36
            if y > theme.SCREEN_H - 80: break
            
            color = theme.ACCENT if i == self.cursor else theme.FG
            if i == self.cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, (theme.PADDING, y-4, theme.SCREEN_W - 2*theme.PADDING, 32), border_radius=4)

            name_txt = self.body_font.render(f"{t['name']} by {t['author']} [{t['source']}]", True, color)
            surf.blit(name_txt, (theme.PADDING + 10, y))

        hint = self.body_font.render("A: Apply Theme  X: Sync Community Repo  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
