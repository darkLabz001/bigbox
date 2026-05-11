"""Achievement View — Operational stats and milestones.

Displays rank, level, XP, and a list of unlocked achievement medals.
"""
from __future__ import annotations

import pygame

from bigbox import theme, achievements
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


class AchievementView:
    def __init__(self) -> None:
        self.dismissed = False
        self.state = achievements.get_state()
        self.cursor = 0
        
        self.f_title = pygame.font.Font(None, 36)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)
        self.f_huge = pygame.font.Font(None, 72)

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self.dismissed = True
            return

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, 60))
        pygame.draw.line(surf, theme.ACCENT, (0, 59), (theme.SCREEN_W, 59), 2)
        surf.blit(self.f_title.render("OPERATIONAL ACHIEVEMENTS", True, theme.ACCENT), (20, 15))
        
        # 1. Big Rank & Level
        col_left = 300
        rank_str = self.state.get_rank()
        surf.blit(self.f_small.render("CURRENT_RANK", True, theme.FG_DIM), (20, 80))
        surf.blit(self.f_title.render(rank_str, True, theme.ACCENT), (20, 100))
        
        surf.blit(self.f_small.render("LEVEL", True, theme.FG_DIM), (20, 160))
        lvl_surf = self.f_huge.render(str(self.state.level), True, theme.FG)
        surf.blit(lvl_surf, (20, 180))
        
        # XP Progress
        next_xp = self.state.next_rank_xp()
        needed = next_xp - 0 # Simplified
        got = self.state.xp
        
        bar_w = 200
        bar_y = 260
        pygame.draw.rect(surf, (30, 35, 45), (20, bar_y, bar_w, 15), border_radius=5)
        if next_xp > 0:
            pct = min(1.0, got / next_xp)
            pygame.draw.rect(surf, theme.ACCENT, (20, bar_y, int(bar_w * pct), 15), border_radius=5)
        surf.blit(self.f_small.render(f"{self.state.xp} / {next_xp} XP", True, theme.FG_DIM), (20, bar_y + 20))

        # 2. Stats Column
        sx = 350
        surf.blit(self.f_small.render("OPERATIONAL_STATS", True, theme.FG_DIM), (sx, 80))
        stats = [
            ("HANDSHAKES", str(self.state.total_handshakes)),
            ("WI-FI NODES", str(self.state.total_nodes)),
            ("BT TRACKERS", str(self.state.total_bt)),
            ("DEAUTHS", str(self.state.total_deauths)),
            ("HONEYPOT", str(self.state.total_honeypot_creds)),
            ("DRIVE TIME", f"{int(self.state.total_wardrive_s / 60)}m"),
        ]
        
        for i, (lbl, val) in enumerate(stats):
            y = 110 + i * 32
            surf.blit(self.f_main.render(lbl, True, theme.FG), (sx, y))
            val_surf = self.f_main.render(val, True, theme.ACCENT)
            surf.blit(val_surf, (sx + 200 - val_surf.get_width(), y))

        # 3. Milestones (Bottom Section)
        mx = 20
        my = 310
        surf.blit(self.f_small.render("OPERATIONAL_MILESTONES", True, theme.FG_DIM), (mx, my))
        
        milestones = achievements.get_milestones(self.state)
        
        for i, (key, prog, desc, unlocked) in enumerate(milestones):
            row = i // 2
            col = i % 2
            color = theme.ACCENT if unlocked else theme.FG_DIM
            
            rx = mx + col * 380
            ry = my + 25 + row * 50
            
            # Progress bar background
            bw, bh = 360, 40
            pygame.draw.rect(surf, (20, 25, 35), (rx, ry, bw, bh), border_radius=5)
            if prog > 0:
                pygame.draw.rect(surf, theme.ACCENT if unlocked else (60, 60, 80), (rx, ry, int(bw * prog), bh), border_radius=5)
            pygame.draw.rect(surf, color, (rx, ry, bw, bh), 1, border_radius=5)

            # Text
            label = key.replace("_", " ")
            surf.blit(self.f_small.render(label, True, theme.FG if unlocked else theme.FG_DIM), (rx + 10, ry + 5))
            surf.blit(self.f_small.render(desc, True, (200, 200, 200) if unlocked else theme.FG_DIM), (rx + 10, ry + 22))
            
            # Pct
            pct_text = "COMPLETE" if unlocked else f"{int(prog * 100)}%"
            pct_surf = self.f_small.render(pct_text, True, theme.ACCENT if unlocked else theme.FG_DIM)
            surf.blit(pct_surf, (rx + bw - pct_surf.get_width() - 10, ry + 12))

        # Hint
        hint = self.f_small.render("B: BACK", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - 80, theme.SCREEN_H - 30))
