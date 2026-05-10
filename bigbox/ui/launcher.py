"""Grid-based launcher.

Displays Sections in a 4x3 grid of icons. Clicking a section opens its
vertical list. Replaces the horizontal Carousel for a more 'appliance-like'
feel.
"""
from __future__ import annotations

import os
from pathlib import Path
import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action, Section, SectionContext


class Launcher:
    def __init__(self, sections: list[Section]) -> None:
        if not sections:
            raise ValueError("Launcher needs at least one section")
        self.sections = sections
        self.index = 0
        self.state = "grid"  # "grid" or "section"
        self._lists = [ScrollList(s.actions) for s in sections]
        
        # Grid layout
        self.cols = 4
        self.rows = 3
        self.icon_size = 64

        # Home background
        self._home_bg = None
        try:
            bg_path = Path(__file__).resolve().parents[2] / "assets" / "home_bg.png"
            if bg_path.exists():
                img = pygame.image.load(str(bg_path)).convert()
                # Cover-fit to screen
                iw, ih = img.get_size()
                scale = max(theme.SCREEN_W / iw, theme.SCREEN_H / ih)
                new_w, new_h = max(1, int(iw * scale)), max(1, int(ih * scale))
                img = pygame.transform.smoothscale(img, (new_w, new_h))
                self._home_bg = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H)).convert()
                self._home_bg.blit(img, ((theme.SCREEN_W - new_w) // 2, (theme.SCREEN_H - new_h) // 2))
                # Add a darkening overlay so icons stay readable
                overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H)).convert()
                overlay.fill((0, 0, 0))
                overlay.set_alpha(160)
                self._home_bg.blit(overlay, (0, 0))
        except Exception as e:
            print(f"[launcher] Failed to load home background: {e}")

    @property
    def current(self) -> Section:
        return self.sections[self.index]

    @property
    def current_list(self) -> ScrollList:
        return self._lists[self.index]

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> Action | None:
        if not ev.pressed:
            return None

        if self.state == "grid":
            if ev.button in (Button.LEFT, Button.LL):
                self.index = (self.index - 1) % len(self.sections)
                return None
            if ev.button in (Button.RIGHT, Button.RR):
                self.index = (self.index + 1) % len(self.sections)
                return None
            if ev.button is Button.UP:
                self.index = (self.index - self.cols) % len(self.sections)
                return None
            if ev.button is Button.DOWN:
                self.index = (self.index + self.cols) % len(self.sections)
                return None
            if ev.button is Button.A:
                self.state = "section"
                self.sections[self.index].on_enter(ctx)
                return None
            return None
        else:
            if ev.button is Button.B and not ev.repeat:
                self.sections[self.index].on_leave(ctx)
                self.state = "grid"
                return None
            # Allow L/R to switch sections even while inside one
            if ev.button in (Button.LL, Button.RR) and not ev.repeat:
                delta = -1 if ev.button is Button.LL else 1
                self.sections[self.index].on_leave(ctx)
                self.index = (self.index + delta) % len(self.sections)
                self.sections[self.index].on_enter(ctx)
                return None
                
            return self.current_list.handle(ev)

    def render(self, surf: pygame.Surface, font: pygame.font.Font, title_font: pygame.font.Font) -> None:
        if self.state == "grid":
            self._render_grid(surf, title_font)
        else:
            self._render_section(surf, font, title_font)

    def _render_grid(self, surf: pygame.Surface, title_font: pygame.font.Font) -> None:
        if self._home_bg:
            surf.blit(self._home_bg, (0, 0))
        else:
            surf.fill(theme.BG)
        
        # 1. Cyberpunk scanlines & Vignette
        for y in range(0, theme.SCREEN_H, 4):
            color = (15, 18, 25) if (y // 4) % 2 == 0 else (10, 12, 18)
            pygame.draw.line(surf, color, (0, y), (theme.SCREEN_W, y))
        
        # 2. Sidebar Background & Telemetry (Left Side)
        sidebar_w = 170
        pygame.draw.rect(surf, (15, 15, 25), (0, 0, sidebar_w, theme.SCREEN_H))
        pygame.draw.line(surf, theme.ACCENT, (sidebar_w, 0), (sidebar_w, theme.SCREEN_H), 1)
        
        from bigbox import system
        stats = system.get_system_stats()
        
        sy = 50
        f_stat = pygame.font.Font(None, 18)
        f_stat_bold = pygame.font.Font(None, 20)
        
        # --- System Vitals ---
        surf.blit(f_stat_bold.render("SYSTEM_VITALS", True, theme.ACCENT), (15, sy))
        sy += 22
        
        # IP Address
        from bigbox import qr
        lan_ip = qr.lan_ipv4() or "DISCONNECTED"
        surf.blit(f_stat.render(f"IP: {lan_ip}", True, theme.FG), (15, sy))
        sy += 18
        
        # CPU & Temp
        temp = stats.get("temp_f")
        temp_str = f"{temp:.0f}F" if temp else "N/A"
        surf.blit(f_stat.render(f"CPU: {stats.get('cpu_usage',0)}% | {temp_str}", True, theme.FG), (15, sy))
        sy += 18
        
        # Memory
        surf.blit(f_stat.render(f"MEM: {stats.get('mem_usage',0)}%", True, theme.FG), (15, sy))
        sy += 25
        
        # --- Anonymity HUD ---
        surf.blit(f_stat_bold.render("OPSEC_STATUS", True, theme.ACCENT), (15, sy))
        sy += 22
        
        is_tor = os.path.exists("/run/tor/tor.pid")
        tor_col = (100, 255, 100) if is_tor else theme.FG_DIM
        surf.blit(f_stat.render(f"ANONSURF: {'ACTIVE' if is_tor else 'OFF'}", True, tor_col), (15, sy))
        sy += 18
        
        is_vpn = os.path.exists("/proc/sys/net/ipv4/conf/tun0")
        vpn_col = (100, 255, 100) if is_vpn else theme.FG_DIM
        surf.blit(f_stat.render(f"VPN_TUN: {'ON' if is_vpn else 'OFF'}", True, vpn_col), (15, sy))
        sy += 30

        # --- Achievements ---
        from bigbox import achievements
        from bigbox.achievements import RANKS
        state = achievements.get_state()
        surf.blit(f_stat_bold.render("OPERATOR_RANK", True, theme.ACCENT), (15, sy))
        sy += 22
        
        rank = state.get_rank()
        surf.blit(f_stat.render(rank, True, theme.FG), (15, sy))
        sy += 18
        
        surf.blit(f_stat.render(f"LEVEL: {state.level}", True, theme.FG_DIM), (15, sy))
        sy += 20
        
        # XP Bar
        bar_w = 140
        bar_h = 6
        pygame.draw.rect(surf, (30, 35, 45), (15, sy, bar_w, bar_h), border_radius=3)
        
        # XP progress calculation
        next_xp = state.next_rank_xp()
        current_rank_xp = 0
        for r_xp, r_name in reversed(RANKS):
            if state.xp >= r_xp:
                current_rank_xp = r_xp
                break
        
        needed = next_xp - current_rank_xp
        got = state.xp - current_rank_xp
        if needed > 0:
            pct = min(1.0, got / needed)
            pygame.draw.rect(surf, theme.ACCENT, (15, sy, int(bar_w * pct), bar_h), border_radius=3)
        
        sy += 12
        surf.blit(f_stat.render(f"{state.xp} XP", True, theme.FG_DIM), (15, sy))

        # 3. Grid Layout (Shifted Right)
        margin_x = sidebar_w + 30
        margin_y = 50
        top_offset = 60
        
        available_w = theme.SCREEN_W - margin_x - 40
        available_h = theme.SCREEN_H - top_offset - 100
        
        cell_w = available_w // self.cols
        cell_h = available_h // self.rows
        
        # XP progress calculation (simple version for now)
        next_xp = state.next_rank_xp()
        current_rank_xp = 0
        for r_xp, r_name in reversed(RANKS):
            if state.xp >= r_xp:
                current_rank_xp = r_xp
                break
        
        needed = next_xp - current_rank_xp
        got = state.xp - current_rank_xp
        if needed > 0:
            pct = min(1.0, got / needed)
            pygame.draw.rect(surf, theme.ACCENT, (15, sy, int(bar_w * pct), bar_h), border_radius=4)
        
        sy += 15
        surf.blit(f_stat.render(f"{state.xp} XP", True, theme.FG_DIM), (15, sy))

        # 3. Grid Layout (Shifted Right)
        margin_x = sidebar_w + 30
        margin_y = 50
        top_offset = 60
        
        available_w = theme.SCREEN_W - margin_x - 30
        available_h = theme.SCREEN_H - top_offset - 100
        
        cell_w = available_w // self.cols
        cell_h = available_h // self.rows
        
        label_font = pygame.font.Font(None, 22)
        desc_font = pygame.font.Font(None, 20)

        for i, section in enumerate(self.sections):
            row = i // self.cols
            col = i % self.cols
            
            x = margin_x + col * cell_w
            y = top_offset + row * cell_h
            
            selected = (i == self.index)
            
            # Selection Highlight
            if selected:
                rect = pygame.Rect(x + 5, y + 2, cell_w - 10, cell_h - 4)
                # Outer glow
                import math
                import time
                glow_pulse = int(100 + 50 * math.sin(time.time() * 8))
                
                s = pygame.Surface((rect.width + 10, rect.height + 10), pygame.SRCALPHA)
                for g in range(5):
                    alpha = glow_pulse // (g + 1)
                    pygame.draw.rect(s, (*theme.ACCENT, alpha), (5-g, 5-g, rect.width+g*2, rect.height+g*2), width=1, border_radius=12)
                surf.blit(s, (rect.x - 5, rect.y - 5))
                
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=10)
                pygame.draw.rect(surf, theme.ACCENT, rect, width=2, border_radius=10)
            
            # Icon
            icon_draw_size = self.icon_size
            if selected:
                icon_draw_size += 6
                
            icon_x = x + (cell_w - icon_draw_size) // 2
            icon_y = y + 10
            
            if section.icon_img:
                scaled = pygame.transform.smoothscale(section.icon_img, (icon_draw_size, icon_draw_size))
                # Add a subtle frame to the icon
                if selected:
                    pygame.draw.rect(surf, theme.ACCENT, (icon_x-2, icon_y-2, icon_draw_size+4, icon_draw_size+4), 1, border_radius=4)
                surf.blit(scaled, (icon_x, icon_y))
            else:
                char = section.icon.strip("[]") if section.icon else "?"
                txt = title_font.render(char, True, theme.ACCENT if selected else theme.FG_DIM)
                surf.blit(txt, (x + (cell_w - txt.get_width()) // 2, icon_y + 10))
                
            # Label
            color = theme.ACCENT if selected else theme.FG_DIM
            label = label_font.render(section.title.upper(), True, color)
            surf.blit(label, (x + (cell_w - label.get_width()) // 2, y + cell_h - 22))

        # 4. Active Section Info (Bottom)
        cur = self.current
        info_rect = pygame.Rect(margin_x, theme.SCREEN_H - 110, available_w, 60)
        # Background for info
        s_info = pygame.Surface((info_rect.width, info_rect.height), pygame.SRCALPHA)
        s_info.fill((10, 12, 18, 200))
        surf.blit(s_info, (info_rect.x, info_rect.y))
        pygame.draw.rect(surf, theme.ACCENT_DIM, info_rect, width=1, border_radius=4)
        
        # Title in info box
        info_title = label_font.render(cur.title, True, theme.ACCENT)
        surf.blit(info_title, (info_rect.x + 15, info_rect.y + 10))
        
        # Subtitle/Description
        desc_text = f"DEPLOY {cur.title.upper()} MODULES"
        if cur.actions:
            desc_text = cur.actions[0].description if len(cur.actions) == 1 else f"{len(cur.actions)} modules available"
        
        desc_surf = desc_font.render(desc_text, True, theme.FG_DIM)
        surf.blit(desc_surf, (info_rect.x + 15, info_rect.y + 35))
        
        # 5. Live Activity Ticker (Bottom Right)
        from bigbox import activity
        ev = activity.latest()
        if ev:
            ticker_font = pygame.font.Font(None, 18)
            tick_text = f"SYS_LOG: {ev.message.upper()}"
            tick_surf = ticker_font.render(tick_text, True, theme.WARN)
            surf.blit(tick_surf, (theme.SCREEN_W - tick_surf.get_width() - 20, theme.SCREEN_H - 25))

        # 6. System Clock (Top Right)
        import datetime
        now = datetime.datetime.now()
        clock_str = now.strftime("%H:%M:%S")
        date_str = now.strftime("%Y-%m-%d")
        f_clock = pygame.font.Font(None, 24)
        f_date = pygame.font.Font(None, 16)
        
        cw = f_clock.size(clock_str)[0]
        surf.blit(f_clock.render(clock_str, True, theme.ACCENT), (theme.SCREEN_W - cw - 20, 15))
        dw = f_date.size(date_str)[0]
        surf.blit(f_date.render(date_str, True, theme.FG_DIM), (theme.SCREEN_W - dw - 20, 35))

    def _render_section(self, surf: pygame.Surface, font: pygame.font.Font, title_font: pygame.font.Font) -> None:
        rect = pygame.Rect(0, theme.STATUS_BAR_H, theme.SCREEN_W, theme.SCREEN_H - theme.STATUS_BAR_H)
        section = self.current
        slist = self.current_list

        # Page background
        if section.background_img is not None:
            # The background_img is already 800x412 (screen - status - tab)
            # We blit it at the top of our content area.
            surf.blit(section.background_img, (0, theme.STATUS_BAR_H + 44))
        else:
            pygame.draw.rect(surf, theme.BG, rect)

        # Header bar for the section
        head_h = 44
        head_rect = pygame.Rect(0, theme.STATUS_BAR_H, theme.SCREEN_W, head_h)
        pygame.draw.rect(surf, theme.BG_ALT, head_rect)
        pygame.draw.line(surf, theme.DIVIDER, (0, head_rect.bottom - 1), (theme.SCREEN_W, head_rect.bottom - 1))
        
        # Title + Icon
        tx = theme.PADDING
        if section.icon_img:
            # Use small version for header
            small_icon = pygame.transform.smoothscale(section.icon_img, (24, 24))
            surf.blit(small_icon, (tx, head_rect.y + (head_h - 24) // 2))
            tx += 32
            
        title = title_font.render(section.title, True, theme.ACCENT)
        surf.blit(title, (tx, head_rect.y + (head_h - title.get_height()) // 2))
        
        # Navigation hints
        small = pygame.font.Font(None, theme.FS_SMALL)
        hint = small.render("B BACK TO GRID · L/R SWITCH", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - theme.PADDING, head_rect.y + (head_h - hint.get_height()) // 2))

        # Content list
        list_rect = pygame.Rect(
            theme.PADDING,
            head_rect.bottom + 10,
            theme.SCREEN_W - 2 * theme.PADDING,
            theme.SCREEN_H - head_rect.bottom - 20
        )
        slist.render(surf, list_rect, font)
