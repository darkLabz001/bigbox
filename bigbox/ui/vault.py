"""Encrypted Loot Vault — Secure storage for captured data.

Uses cryptsetup (LUKS) to manage a password-protected container.
Container: /opt/bigbox/vault.img (loopback)
Mapped: /dev/mapper/bigbox_loot
Mount: /mnt/loot
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App

VAULT_IMG = Path("/opt/bigbox/vault.img")
MAPPER_NAME = "bigbox_loot"
MOUNT_POINT = Path("/mnt/loot")

PHASE_STATUS = "status"
PHASE_UNLOCK = "unlock"
PHASE_BROWSE = "browse"
PHASE_WORKING = "working"
PHASE_CREATE = "create"

class VaultView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_STATUS
        self.status_msg = "CHECKING_VAULT..."
        self.is_mounted = False
        self.is_mapped = False
        self.working_msg = ""
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 20)
        
        self.list: Optional[ScrollList] = None
        
        self._refresh_state()

    def _refresh_state(self):
        self.is_mounted = os.path.ismount(str(MOUNT_POINT))
        self.is_mapped = os.path.exists(f"/dev/mapper/{MAPPER_NAME}")
        
        if not VAULT_IMG.exists():
            self.status_msg = "VAULT_IMAGE_NOT_FOUND"
        elif self.is_mounted:
            self.status_msg = "VAULT_UNLOCKED_AND_MOUNTED"
        elif self.is_mapped:
            self.status_msg = "VAULT_MAPPED_BUT_UNMOUNTED"
        else:
            self.status_msg = "VAULT_LOCKED"

    def _run_bg(self, cmd: List[str], next_msg: str, on_done: Optional[callable] = None):
        self.phase = PHASE_WORKING
        self.working_msg = next_msg
        
        def _worker():
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                if on_done: on_done(True, "")
            except subprocess.CalledProcessError as e:
                if on_done: on_done(False, e.stderr.decode() or str(e))
            except Exception as e:
                if on_done: on_done(False, str(e))
            
            self._refresh_state()
            self.phase = PHASE_STATUS
            
        threading.Thread(target=_worker, daemon=True).start()

    def _create_vault(self, password: str):
        self.phase = PHASE_WORKING
        self.working_msg = "CREATING SECURE CONTAINER..."
        
        def _worker():
            try:
                # 1. Create 512MB sparse file
                subprocess.run(["sudo", "dd", "if=/dev/zero", f"of={VAULT_IMG}", "bs=1M", "count=512"], check=True)
                # 2. Format with LUKS
                proc = subprocess.Popen(["sudo", "cryptsetup", "luksFormat", str(VAULT_IMG)], 
                                      stdin=subprocess.PIPE, text=True)
                proc.communicate(input=f"{password}\nYES\n") # Double confirmation for cryptsetup
                
                # 3. Open to format filesystem
                proc = subprocess.Popen(["sudo", "cryptsetup", "open", str(VAULT_IMG), MAPPER_NAME],
                                      stdin=subprocess.PIPE, text=True)
                proc.communicate(input=password)
                
                # 4. Format ext4
                subprocess.run(["sudo", "mkfs.ext4", f"/dev/mapper/{MAPPER_NAME}"], check=True)
                
                # 5. Close
                subprocess.run(["sudo", "cryptsetup", "close", MAPPER_NAME], check=True)
                self.status_msg = "VAULT_CREATED_SUCCESSFULLY"
            except Exception as e:
                self.status_msg = f"CREATE_FAILED: {str(e)[:30]}"
            
            self._refresh_state()
            self.phase = PHASE_STATUS
            
        threading.Thread(target=_worker, daemon=True).start()

    def _unlock(self, password: str):
        self.phase = PHASE_WORKING
        self.working_msg = "DECRYPTING..."
        
        def _worker():
            try:
                # 1. Open LUKS
                proc = subprocess.Popen(["sudo", "cryptsetup", "open", str(VAULT_IMG), MAPPER_NAME],
                                      stdin=subprocess.PIPE, text=True)
                proc.communicate(input=password)
                
                # 2. Mount
                MOUNT_POINT.mkdir(parents=True, exist_ok=True)
                subprocess.run(["sudo", "mount", f"/dev/mapper/{MAPPER_NAME}", str(MOUNT_POINT)], check=True)
                # 3. Ensure permissions
                subprocess.run(["sudo", "chown", "root:root", str(MOUNT_POINT)], check=True)
                subprocess.run(["sudo", "chmod", "777", str(MOUNT_POINT)], check=True)
            except Exception as e:
                self.status_msg = "INVALID_PASSWORD_OR_ERROR"
            
            self._refresh_state()
            self.phase = PHASE_STATUS
            
        threading.Thread(target=_worker, daemon=True).start()

    def _lock(self):
        self.phase = PHASE_WORKING
        self.working_msg = "SECURING VAULT..."
        
        def _worker():
            try:
                subprocess.run(["sudo", "umount", str(MOUNT_POINT)], stderr=subprocess.DEVNULL)
                subprocess.run(["sudo", "cryptsetup", "close", MAPPER_NAME], check=True)
            except:
                pass
            self._refresh_state()
            self.phase = PHASE_STATUS
            
        threading.Thread(target=_worker, daemon=True).start()

    def _browse(self):
        if not self.is_mounted: return
        items = []
        try:
            for p in MOUNT_POINT.iterdir():
                items.append(Action(p.name, None))
        except:
            pass
        if not items:
            items.append(Action("[ Empty Vault ]", None))
        self.list = ScrollList(items)
        self.phase = PHASE_BROWSE

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.phase == PHASE_BROWSE:
                self.phase = PHASE_STATUS
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_STATUS:
            if not VAULT_IMG.exists():
                if ev.button is Button.A:
                    ctx.get_input("New Vault Password", self._create_vault)
            elif self.is_mounted:
                if ev.button is Button.A:
                    self._browse()
                elif ev.button is Button.X:
                    self._lock()
            else:
                if ev.button is Button.A:
                    ctx.get_input("Enter Vault Password", self._unlock)
        
        elif self.phase == PHASE_BROWSE and self.list:
            self.list.handle(ev)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("SYSTEM :: ENCRYPTED_VAULT", True, theme.ACCENT), (theme.PADDING, 8))
        
        if self.phase == PHASE_WORKING:
            msg = self.f_main.render(self.working_msg, True, theme.ACCENT)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))
        
        elif self.phase == PHASE_BROWSE and self.list:
            list_rect = pygame.Rect(20, head_h + 20, theme.SCREEN_W - 40, theme.SCREEN_H - head_h - 80)
            self.list.render(surf, list_rect, self.f_main)
            hint = "UP/DOWN: Scroll  B: BACK"
            h_surf = self.f_small.render(hint, True, theme.FG_DIM)
            surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))

        else:
            # Main status screen
            x, y = 50, head_h + 50
            surf.blit(self.f_main.render("STATUS:", True, theme.FG_DIM), (x, y))
            color = theme.ACCENT if self.is_mounted else theme.ERR
            surf.blit(self.f_main.render(self.status_msg, True, color), (x + 120, y))
            
            y += 50
            if not VAULT_IMG.exists():
                surf.blit(self.f_main.render("PRESS (A) TO INITIALIZE NEW VAULT", True, theme.ACCENT_DIM), (x, y))
            elif self.is_mounted:
                surf.blit(self.f_main.render("PRESS (A) TO BROWSE SECURE FILES", True, theme.ACCENT_DIM), (x, y))
                y += 40
                surf.blit(self.f_main.render("PRESS (X) TO LOCK VAULT", True, theme.ERR), (x, y))
            else:
                surf.blit(self.f_main.render("PRESS (A) TO UNLOCK", True, theme.ACCENT), (x, y))
            
            # Footer
            hint = "B: BACK"
            h_surf = self.f_small.render(hint, True, theme.FG_DIM)
            surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))
