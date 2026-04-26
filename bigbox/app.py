"""Main application loop.

- Initializes pygame for the GamePi43's 800x480 panel (or windowed in dev mode).
- Starts an input source: GPIO buttons on real hardware, keyboard in dev mode.
- Builds the carousel from `bigbox.sections`.
- Runs at 60 FPS, draining events and dispatching them to the active screen
  (the carousel by default, a ResultView when a tool is running).
"""
from __future__ import annotations

import os
import subprocess
import time

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent, EventBus
from bigbox.input import load_button_config
from bigbox.input.keyboard import translate as kbd_translate
from bigbox.runner import run_streaming
from bigbox.sections import build_sections
from bigbox.ui.widgets import MenuView, ResultView, StatusBar
from bigbox.ui.cctv import CCTVView


class App:
    def __init__(self) -> None:
        self.dev_mode = bool(os.environ.get("BIGBOX_DEV"))
        self.bus = EventBus()
        self.running = True
        self.result_view: ResultView | None = None
        self.menu_view: MenuView | None = None
        self.cctv_view: CCTVView | None = None
        self.show_status = True
        self.held_buttons: set[Button] = set()

    # ---------- lifecycle ----------
    def _init_display(self) -> pygame.Surface:
        # Pick a video driver. Prefer KMS DRM if /dev/dri exists; fall back to
        # the legacy fbdev otherwise (Waveshare's stock GamePi43 image uses
        # /dev/fb0 with no DRM device). Either can be overridden by an
        # explicit SDL_VIDEODRIVER env var.
        if not self.dev_mode and not os.environ.get("DISPLAY"):
            if "SDL_VIDEODRIVER" not in os.environ:
                if os.path.exists("/dev/dri/card0") or os.path.exists("/dev/dri/card1"):
                    os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
                else:
                    os.environ["SDL_VIDEODRIVER"] = "fbcon"
                    os.environ.setdefault("SDL_FBDEV", "/dev/fb0")

        # Don't try to open ALSA — most handheld builds have no configured
        # sound card on first boot, and pygame's audio init is noisy when it
        # fails. Sound is opt-in via SDL_AUDIODRIVER if/when we add it.
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

        # Init only the subsystems we use, so a missing audio device or
        # joystick doesn't sink the whole startup.
        pygame.display.init()
        pygame.font.init()

        flags = 0 if self.dev_mode else pygame.FULLSCREEN
        screen = pygame.display.set_mode((theme.SCREEN_W, theme.SCREEN_H), flags)
        try:
            pygame.mouse.set_visible(False)
        except pygame.error:
            pass    # some drivers don't support cursor control; harmless.
        return screen

    def _start_input(self) -> None:
        if self.dev_mode:
            return    # keyboard events are pulled via pygame's event queue in run()
        cfg = load_button_config()
        from bigbox.input.gpio import GPIOInput
        self._gpio = GPIOInput(self.bus, cfg)
        try:
            self._gpio.start()
        except Exception as e:
            # If GPIO can't init (wrong perms, not on a Pi), fall back to keyboard
            # so the device is still recoverable via a USB keyboard.
            print(f"[bigbox] GPIO init failed ({e}); keyboard input only")
            self._gpio = None

    # ---------- SectionContext implementation ----------
    def show_result(self, title: str, text: str) -> None:
        self.result_view = ResultView(title, text)

    def run_streaming(self, title: str, argv: list[str]) -> None:
        view = ResultView(title, "")
        self.result_view = view
        run_streaming(argv, view.append)

    def show_cctv(self) -> None:
        self.cctv_view = CCTVView()

    def go_back(self) -> None:
        self.result_view = None
        self.cctv_view = None

    def toast(self, msg: str) -> None:
        # Lightweight: just print for now; could become an on-screen toast widget.
        print(f"[toast] {msg}")

    # ---------- main loop ----------
    def run(self) -> int:
        screen = self._init_display()
        pygame.display.set_caption("bigbox")
        self._start_input()

        carousel = Carousel(build_sections())
        statusbar = StatusBar()
        body_font = pygame.font.Font(None, theme.FS_BODY)
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        clock = pygame.time.Clock()

        while self.running:
            # 1. Pump pygame events. In dev mode, translate keys -> ButtonEvents.
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                    if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                        self.running = False
                    if self.dev_mode:
                        kbd_translate(ev, self.bus)

            # 2. Drain logical button events; route to the foreground screen.
            for bev in self.bus.drain():
                self._dispatch(bev, carousel)

            # 3. Render.
            screen.fill(theme.BG)
            if self.cctv_view is not None:
                self.cctv_view.render(screen)
                if self.cctv_view.dismissed:
                    self.cctv_view = None
            elif self.result_view is not None:
                self.result_view.render(screen)
                if self.result_view.dismissed:
                    self.result_view = None
            else:
                if self.show_status:
                    statusbar.render(screen)
                carousel.render(screen, body_font, title_font)

            if self.menu_view is not None:
                self.menu_view.render(screen)
                if self.menu_view.dismissed:
                    self.menu_view = None

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()
        return 0

    def _dispatch(self, bev: ButtonEvent, carousel: Carousel) -> None:
        if bev.pressed:
            self.held_buttons.add(bev.button)
        else:
            self.held_buttons.discard(bev.button)

        if not bev.pressed:
            return

        # Hotkey combos (checked before single-button actions)
        if Button.HK in self.held_buttons and not bev.repeat:
            if bev.button is Button.START:
                self.running = False  # Emergency exit
                return
            if bev.button is Button.B:
                self.go_back()
                return

        # Global hotkeys.
        if not bev.repeat:
            if bev.button is Button.START:
                self._open_system_menu()
                return
            if bev.button is Button.SELECT:
                print(f"[bigbox] section={carousel.current.title}")
                return
            if bev.button is Button.X:
                self.show_status = not self.show_status
                return
            if bev.button is Button.Y:
                self._take_screenshot()
                return

        if self.menu_view is not None:
            self.menu_view.handle(bev)
            return

        if self.cctv_view is not None:
            self.cctv_view.handle(bev)
            return

        if self.result_view is not None:
            self.result_view.handle(bev)
            return

        action = carousel.handle(bev, self)   # self satisfies SectionContext
        if action and action.handler:
            try:
                action.handler(self)
            except Exception as e:
                self.show_result("error", f"{type(e).__name__}: {e}")

    def _open_system_menu(self) -> None:
        actions = [
            ("Back to Tool", lambda: None),
            ("Reboot", lambda: subprocess.run(["sudo", "reboot"])),
            ("Power Off", lambda: subprocess.run(["sudo", "poweroff"])),
        ]
        if self.dev_mode:
            actions.append(("Exit bigbox", lambda: setattr(self, "running", False)))
        self.menu_view = MenuView("System", actions)

    def _take_screenshot(self) -> None:
        import os
        from datetime import datetime
        if not os.path.exists("screenshots"):
            os.makedirs("screenshots")
        fname = f"screenshots/shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        pygame.image.save(pygame.display.get_surface(), fname)
        self.toast(f"Saved {fname}")
