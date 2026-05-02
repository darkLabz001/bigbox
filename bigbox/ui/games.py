"""Games — pick a system, pick a ROM, hand off to the emulator.

Phases mirror MediaPlayerView so the UX is consistent:
  PICK_SYSTEM  - GBC / GBA / PS1 with rom counts
  PICK_ROM     - file list for the selected system
  RUNNING      - emulator subprocess; B kills it; render polls proc.poll()
  RESULT       - last 8 lines of /tmp/bigbox-emu.log so failures are visible
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import TYPE_CHECKING

import pygame

from bigbox import emulator as _emu
from bigbox import games_state, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


PHASE_SYSTEM = "system"
PHASE_ROM = "rom"
PHASE_RUNNING = "running"
PHASE_RESULT = "result"
PHASE_PYTHON = "python"


# Audio volume save/restore lives in bigbox.emulator now (it
# auto-detects whether to use pactl-vs-amixer based on what daemon
# owns the cards). See save_audio_volume / restore_audio_volume there.


class GamesView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_SYSTEM
        self.trigger_internal = False
        self.current_game_module = None

        # System picker
        self.systems: list[tuple[str, str, int]] = []  # (key, label, rom_count)
        self.sys_cursor = 0

        # Rom picker
        self.current_system: str | None = None
        self.list: ScrollList = ScrollList([])

        # Running emulator
        self.proc: subprocess.Popen | None = None
        self.playing_rom: str | None = None
        self._launch_time: float = 0.0
        self.injector = None

        # Result screen
        self.last_result: list[str] | None = None
        self.last_result_rc: int | None = None

        # Cache fonts
        try:
            self.title_font = pygame.font.Font(None, theme.FS_TITLE)
            self.body_font = pygame.font.Font(None, theme.FS_BODY)
            self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
        except Exception:
            self.title_font = self.body_font = self.hint_font = \
                pygame.font.SysFont("monospace", 20)

        self._refresh_systems()
        # If only one system (e.g. GBA), skip the picker
        if len(self.systems) == 1:
            self.current_system = self.systems[0][0]
            self.list = self._build_rom_list()
            self.phase = PHASE_ROM

    # ---------- refresh ----------
    def _refresh_systems(self) -> None:
        out: list[tuple[str, str, int]] = []
        # Add internal Classics
        out.append(("classics", "CLASSIC TERMINAL PAYLOADS", 2))

        # Show every system the emulator module knows about. Empty rom
        # dirs render as "0 roms" — they're a hint for the user that
        # the system is wired up and ready for an upload.
        for key, sd in _emu.SYSTEMS.items():
            out.append((key, sd.label, len(sd.list_roms())))
        self.systems = out
        if self.sys_cursor >= len(out):
            self.sys_cursor = max(0, len(out) - 1)

    def _build_rom_list(self) -> ScrollList:
        if self.current_system == "classics":
            actions = [
                Action("Wargames", lambda ctx: self._launch_python("wargames")),
                Action("Oregon Trail", lambda ctx: self._launch_python("oregon_trail")),
            ]
            return ScrollList(actions)
            
        sd = _emu.SYSTEMS.get(self.current_system or "")
        if not sd:
            return ScrollList([Action("[ no system ]", None)])
        roms = games_state.sorted_roms(self.current_system or "", sd.list_roms())
        actions: list[Action] = []
        for r in roms:
            def make_handler(rom_name: str):
                return lambda ctx: self._launch(rom_name)
            count = games_state.play_count(self.current_system or "", r)
            desc = f"played {count}x" if count else ""
            actions.append(Action(r, make_handler(r), desc))
        if not actions:
            actions.append(Action("[ Empty — upload via Web UI ]", None))
        return ScrollList(actions)

    def refresh(self) -> None:
        """External hook (web upload calls this)."""
        self._refresh_systems()
        if self.phase == PHASE_ROM:
            self.list = self._build_rom_list()

    # ---------- launch ----------
    def _launch(self, rom_filename: str) -> None:
        if not self.current_system:
            return
        self.playing_rom = rom_filename
        self.last_result = None
        self.last_result_rc = None

        # Save current audio volume so we can restore it on emulator
        # exit. emulator.launch bumps to 100% so games are audible
        # without reaching for the device, but stealing the user's
        # volume silently is rude; we put it back on the way out.
        self._saved_audio = _emu.save_audio_volume()

        proc, msg = _emu.launch(self.current_system, rom_filename)
        if proc is None:
            self.last_result = [msg]
            self.last_result_rc = -1
            self.phase = PHASE_RESULT
            return
        self.proc = proc
        self.injector = _emu.InputInjector()
        self._launch_time = time.time()
        games_state.record_play(self.current_system, rom_filename)
        self.phase = PHASE_RUNNING

    def _launch_python(self, game_name: str) -> None:
        self.phase = PHASE_PYTHON
        if game_name == "wargames":
            from bigbox.games import wargames
            self.current_game_module = wargames
        elif game_name == "oregon_trail":
            from bigbox.games import oregon_trail
            self.current_game_module = oregon_trail
        
        self.trigger_internal = True

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.injector:
            self.injector.close()
            self.injector = None
        self.proc = None
        self.playing_rom = None
        # Restore the volume the user had before we launched.
        _emu.restore_audio_volume(getattr(self, "_saved_audio", None))
        # Bounce back to the rom list of the same system
        self.phase = PHASE_ROM if self.current_system else PHASE_SYSTEM
        # Refresh so the just-played rom floats to the top.
        if self.current_system:
            self.list = self._build_rom_list()

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: App) -> bool:
        if self.phase == PHASE_PYTHON:
            return True

        if self.phase == PHASE_RUNNING:
            # Hotkey combo to stop: START + SELECT (either order)
            if ev.pressed and ev.button in (Button.START, Button.SELECT):
                if Button.START in ctx.held_buttons and Button.SELECT in ctx.held_buttons:
                    self._stop()
                    return True
            
            if self.injector:
                self.injector.inject(ev.button, ev.pressed)
            return True  # Consume all events during gameplay

        if not ev.pressed:
            return False

        if self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.B, Button.START, Button.SELECT):
                self.last_result = None
                self.last_result_rc = None
                self.phase = PHASE_ROM if self.current_system else PHASE_SYSTEM
            return True

        if self.phase == PHASE_SYSTEM:
            if ev.button is Button.B:
                self.dismissed = True
            elif not self.systems:
                pass
            elif ev.button is Button.UP:
                self.sys_cursor = (self.sys_cursor - 1) % len(self.systems)
            elif ev.button is Button.DOWN:
                self.sys_cursor = (self.sys_cursor + 1) % len(self.systems)
            elif ev.button is Button.A:
                self.current_system = self.systems[self.sys_cursor][0]
                self.list = self._build_rom_list()
                self.phase = PHASE_ROM
            return True

        if self.phase == PHASE_ROM:
            if ev.button is Button.B:
                self.current_system = None
                self._refresh_systems()
                self.phase = PHASE_SYSTEM
                return True
            action = self.list.handle(ev)
            if action and action.handler:
                action.handler(ctx)
            return True
            
        return False

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        if self.phase == PHASE_PYTHON:
            if getattr(self, "trigger_internal", False):
                self.trigger_internal = False
                from bigbox.app import App
                import gc
                apps = [obj for obj in gc.get_objects() if isinstance(obj, App)]
                if apps:
                    app = apps[0]
                    self.current_game_module.run(surf, app.bus)
                    self.phase = PHASE_ROM
                return

        # Detect emulator exit
        if self.phase == PHASE_RUNNING and self.proc:
            rc = self.proc.poll()
            if rc is not None:
                self.last_result = _emu.read_emulator_log_tail(8) or [
                    f"emulator exited (code {rc})"
                ]
                self.last_result_rc = rc
                self.proc = None
                self.playing_rom = None
                self.phase = PHASE_RESULT

        surf.fill(theme.BG)

        # Header
        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        if self.phase == PHASE_RESULT:
            t = "GAMES :: RESULT"
        elif self.phase == PHASE_RUNNING:
            t = f"PLAYING: {self.playing_rom or ''}"
        elif self.phase == PHASE_ROM:
            sd = _emu.SYSTEMS.get(self.current_system or "")
            t = f"GAMES :: {(sd.label if sd else '')}"
        else:
            t = "GAMES"
        title = self.title_font.render(t, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING,
                          (head_h - title.get_height()) // 2))

        if self.phase == PHASE_RESULT:
            self._render_result(surf, head_h)
        elif self.phase == PHASE_RUNNING:
            self._render_running(surf, head_h)
        elif self.phase == PHASE_ROM:
            self._render_rom_list(surf, head_h)
        else:
            self._render_systems(surf, head_h)

    def _render_systems(self, surf: pygame.Surface, head_h: int) -> None:
        list_x = theme.PADDING
        list_y = head_h + theme.PADDING
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - head_h - 2 * theme.PADDING - 40
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        f_main = pygame.font.Font(None, 32)
        f_meta = pygame.font.Font(None, 20)
        row_h = 64

        for i, (key, label, count) in enumerate(self.systems):
            y = list_y + i * row_h
            if y + row_h > list_y + list_h:
                break
            rect = pygame.Rect(list_x + 4, y + 4, list_w - 8, row_h - 8)
            if i == self.sys_cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=6)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=6)
                color = theme.ACCENT
            else:
                color = theme.FG
            ls = f_main.render(label, True, color)
            surf.blit(ls, (rect.x + 14, rect.y + 10))
            cs = f_meta.render(f"{count} rom{'s' if count != 1 else ''}",
                               True, theme.FG_DIM)
            surf.blit(cs, (rect.right - cs.get_width() - 14, rect.y + 22))

        hint = self.hint_font.render(
            "UP/DOWN: Navigate  A: Open  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_rom_list(self, surf: pygame.Surface, head_h: int) -> None:
        list_rect = pygame.Rect(
            theme.PADDING,
            head_h + theme.PADDING,
            theme.SCREEN_W - 2 * theme.PADDING,
            theme.SCREEN_H - head_h - 2 * theme.PADDING - 40,
        )
        self.list.render(surf, list_rect, self.body_font)
        hint = self.hint_font.render(
            "UP/DOWN: Navigate  A: Play  B: Systems",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_running(self, surf: pygame.Surface, head_h: int) -> None:
        center_x = theme.SCREEN_W // 2
        f_big = pygame.font.Font(None, 36)
        f_med = pygame.font.Font(None, 22)
        f_chord = pygame.font.Font(None, 20)

        # Title block
        msg = f_big.render("emulator running", True, theme.ACCENT)
        surf.blit(msg, (center_x - msg.get_width() // 2, head_h + 16))

        sub = f_med.render(self.playing_rom or "", True, theme.FG_DIM)
        surf.blit(sub, (center_x - sub.get_width() // 2,
                        head_h + 16 + msg.get_height() + 4))

        # Control map — bigbox GPIO buttons are wired into a virtual
        # gamepad via uinput (see bigbox/emulator.py InputInjector).
        # Show the actual mapping so users don't think they need a
        # second controller.
        mappings = [
            ("D-Pad",     "UP / DOWN / LEFT / RIGHT"),
            ("A button",  "A"),
            ("B button",  "B"),
            ("L / R",     "X / Y  (or LL / RR)"),
            ("START",     "START"),
            ("SELECT",    "SELECT"),
        ]
        box_w = 540
        box_h = 30 + len(mappings) * 26 + 10
        box_x = (theme.SCREEN_W - box_w) // 2
        box_y = head_h + 90
        pygame.draw.rect(surf, (5, 5, 10), (box_x, box_y, box_w, box_h))
        pygame.draw.rect(surf, theme.DIVIDER, (box_x, box_y, box_w, box_h), 1)
        header = f_chord.render("CONTROLS", True, theme.ACCENT)
        surf.blit(header, (box_x + 12, box_y + 6))
        for i, (game, btn) in enumerate(mappings):
            y = box_y + 30 + i * 26
            game_s = f_chord.render(game, True, theme.FG_DIM)
            btn_s = f_chord.render(btn, True, theme.FG)
            surf.blit(game_s, (box_x + 16, y))
            surf.blit(btn_s, (box_x + 200, y))

        hint = self.hint_font.render(
            "HK to exit emulator   (or SELECT + START)",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_result(self, surf: pygame.Surface, head_h: int) -> None:
        rc = self.last_result_rc if self.last_result_rc is not None else 0
        ok = (rc == 0)

        accent = theme.ACCENT if ok else theme.ERR
        sub_y = head_h + theme.PADDING
        sub = self.body_font.render(
            f"{self.playing_rom or ''}  (exit {rc})", True, accent)
        surf.blit(sub, (theme.PADDING, sub_y))

        log_y = sub_y + sub.get_height() + 8
        log_h = theme.SCREEN_H - log_y - 40
        log_rect = pygame.Rect(theme.PADDING, log_y,
                               theme.SCREEN_W - 2 * theme.PADDING, log_h)
        pygame.draw.rect(surf, (0, 0, 0), log_rect)
        pygame.draw.rect(surf, theme.DIVIDER, log_rect, 1)

        f = pygame.font.Font(None, 18)
        line_h = f.get_linesize()
        max_lines = max(1, (log_rect.height - 16) // line_h)
        lines = (self.last_result or [])[-max_lines:]
        max_w = log_rect.width - 16
        for i, raw in enumerate(lines):
            text = raw
            while text and f.size(text)[0] > max_w:
                text = text[:-1]
            color = theme.ERR if any(k in raw.lower() for k in
                                     ("error", "fail", "cannot", "could not")) \
                else theme.FG_DIM
            ls = f.render(text, True, color)
            surf.blit(ls, (log_rect.x + 8, log_rect.y + 8 + i * line_h))

        hint = self.hint_font.render(
            "B: dismiss   /tmp/bigbox-emu.log has full output",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
