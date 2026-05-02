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
import threading
import time
from pathlib import Path
from typing import Callable

import pygame

# Install the process-wide pygame.font.Font cache *before* anything
# else imports pygame.font in this process. Several views re-create
# fonts inside render() at 30 fps, which strace showed re-reading the
# TTF file ~15k times/sec — the single biggest CPU hog on this Pi.
from bigbox import _font_cache  # noqa: F401  (side-effect import)

from bigbox import theme
from bigbox.events import Button, ButtonEvent, EventBus
from bigbox.input import load_button_config
from bigbox.input.keyboard import translate as kbd_translate
from bigbox.runner import run_streaming
from bigbox.sections import build_sections
from bigbox.update_checker import UpdateChecker
from bigbox.ui import Carousel, CCTVView, MenuView, ResultView, StatusBar, PingSweepView, KeyboardView, ARPScanView, FlockScannerView, WifiConnectView, CamScannerView, WifiAttackView, OfflineCrackerView, MediaPlayerView, InternetTVView, YouTubeView, TailscaleView, AnonSurfView, VaultView, BettercapView, MailView, MessengerView, RagnarView, SignalScraperView, TrafficCamView, CameraInterceptorView, WifiteView, ChatView, SherlockView, DeadDropView, BBSView, BLEChatView, OnionChatView, BLESpamView, TerminalView, ThemeManagerView, UpdateView, WifiMultiToolView, WardriveView, EvilTwinView, GamesView, TrackerView, ProbeSnifferView, BeaconFloodView, KarmaLiteView


# Foreground-view registry. Render and input both walk this in order;
# the first attribute on `App` whose value is non-None wins. Order
# matches the historical elif chain so behavior is preserved.
#
# handle_arity:
#   1 = view.handle(bev)            (kb / cctv / update / result)
#   2 = view.handle(bev, app)       (most everything else)
#   0 = input is handled outside the registry (games_view's pre-step
#       in _dispatch consumes events including releases)
#
# menu_view is *not* in the registry — it's an overlay rendered on top
# of whatever's behind it and special-cased at the input top-level.
_VIEWS: tuple[tuple[str, int], ...] = (
    ("kb_view", 1),
    ("cctv_view", 1),
    ("ping_view", 2),
    ("arp_view", 2),
    ("flock_view", 2),
    ("wifi_view", 2),
    ("cam_scan_view", 2),
    ("wifi_attack_view", 2),
    ("wifi_multi_view", 2),
    ("cracker_view", 2),
    ("pmkid_sniper_view", 2),
    ("media_view", 2),
    ("tv_view", 2),
    ("youtube_view", 2),
    ("tailscale_view", 2),
    ("anon_surf_view", 2),
    ("vault_view", 2),
    ("bettercap_view", 2),
    ("mail_view", 2),
    ("messenger_view", 2),
    ("ragnar_view", 2),
    ("scraper_view", 2),
    ("traffic_cam_view", 2),
    ("camera_view", 2),
    ("wifite_view", 2),
    ("chat_view", 2),
    ("sherlock_view", 2),
    ("deaddrop_view", 2),
    ("bbs_view", 2),
    ("ble_view", 2),
    ("onion_view", 2),
    ("ble_spam_view", 2),
    ("terminal_view", 2),
    ("theme_manager_view", 2),
    ("wardrive_view", 2),
    ("eviltwin_view", 2),
    ("captures_view", 2),
    ("scan_history_view", 2),
    ("web_access_view", 2),
    ("phone_camera_view", 2),
    ("diagnostics_view", 2),
    ("bg_tasks_view", 2),
    ("games_view", 0),
    ("tracker_view", 2),
    ("probe_view", 2),
    ("beacon_view", 2),
    ("karma_view", 2),
    ("update_view", 1),
    ("result_view", 1),
)

# Method names to try when forcibly stopping a view that crashed,
# checked in order. First one that exists is invoked.
_STOP_METHODS = ("_stop_run", "_stop_crack", "_stop_capture",
                 "_stop_stream", "_stop_snipe", "_stop", "_shutdown")


class App:
    def __init__(self) -> None:
        self.dev_mode = bool(os.environ.get("BIGBOX_DEV"))
        self.bus = EventBus()
        self.running = True
        self.update_checker = UpdateChecker(self)
        # Foreground views — all initialized to None here via _VIEWS so adding
        # a new view is a one-line registry update. menu_view is the
        # only foreground attribute outside _VIEWS (it is an overlay).
        self.menu_view: MenuView | None = None
        for _vname, _ in _VIEWS:
            setattr(self, _vname, None)
        self.show_status = True
        self.held_buttons: set[Button] = set()
        self.hk_used = False
        self._last_vol_enforce = 0
        
        # Screen recording state
        self.recording_proc: subprocess.Popen | None = None
        self.recording_start_time: float = 0.0

        # Messaging background sync
        from bigbox.ui.messenger import MessengerSync
        self.msg_sync = MessengerSync(self)
        self.msg_sync.start()

        # SD card silently fills over time (captures + recordings +
        # handshakes + scans + wardrive); start a slow sweep that
        # auto-rotates the oldest files when free space dips below
        # the soft threshold. See bigbox/disk.py.
        from bigbox import disk as _disk
        _disk.start_sweeper()

        # Persistent journal storage — without this, journalctl only
        # sees the current boot, which means the Diagnostics view loses
        # everything across crashes/reboots. Idempotent: skips if the
        # directory already exists.
        try:
            jdir = Path("/var/log/journal")
            if not jdir.is_dir():
                jdir.mkdir(parents=True, exist_ok=True)
                # systemd picks up the new dir on next restart.
                subprocess.run(
                    ["systemctl", "restart", "systemd-journald"],
                    timeout=5, check=False,
                )
                print("[journal] enabled persistent storage at /var/log/journal")
        except Exception as e:
            print(f"[journal] could not enable persistent storage: {e}")

        # Web UI state.
        # last_frame: most recent JPEG of the screen for /video_feed.
        # last_web_view_request: monotonic-ish timestamp updated by the
        # web server every time /video_feed is hit. We use it to skip the
        # screen-capture JPEG encode entirely when nobody's watching —
        # default state on a handheld used standalone.
        self.last_frame: bytes | None = None
        self.last_web_view_request: float = 0.0
        self._frame_counter = 0

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
            self._start_web_server() # Still start web server in dev mode
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
        
        self._start_web_server()

    def _start_web_server(self) -> None:
        """Starts the FastAPI web server in a background thread."""
        try:
            import uvicorn
            from bigbox.web.server import app, set_app
            set_app(self)
            
            def run_server():
                # Increase timeouts for large movie uploads (1GB can take a while over WiFi)
                uvicorn.run(
                    app, 
                    host="0.0.0.0", 
                    port=8080, 
                    log_level="error",
                    timeout_keep_alive=60,
                    loop="asyncio"
                )
            
            t = threading.Thread(target=run_server, daemon=True)
            t.start()
            print("[bigbox] Web UI started at http://0.0.0.0:8080")
        except ImportError:
            print("[bigbox] uvicorn not found; Web UI disabled")

    # ---------- SectionContext implementation ----------
    def show_result(self, title: str, text: str) -> None:
        self.result_view = ResultView(title, text)

    def run_streaming(self, title: str, argv: list[str]) -> None:
        view = ResultView(title, "")
        self.result_view = view
        run_streaming(argv, view.append)

    def show_cctv(self) -> None:
        self.cctv_view = CCTVView()

    def show_pingsweep(self) -> None:
        self.ping_view = PingSweepView()

    def show_arpscan(self) -> None:
        self.arp_view = ARPScanView()

    def show_flock(self) -> None:
        self.flock_view = FlockScannerView()

    def show_wifi(self) -> None:
        self.wifi_view = WifiConnectView()

    def show_camscan(self) -> None:
        self.cam_scan_view = CamScannerView()

    def show_wifi_attack(self) -> None:
        self.wifi_attack_view = WifiAttackView()

    def show_wifi_multi_tool(self) -> None:
        self.wifi_multi_view = WifiMultiToolView()

    def show_cracker(self) -> None:
        self.cracker_view = OfflineCrackerView()

    def show_pmkid_sniper(self) -> None:
        from bigbox.ui.pmkid_sniper import PMKIDSniperView
        self.pmkid_sniper_view = PMKIDSniperView()

    def show_media_player(self) -> None:
        self.media_view = MediaPlayerView()

    def show_tv(self) -> None:
        self.tv_view = InternetTVView()

    def show_youtube(self) -> None:
        self.youtube_view = YouTubeView()

    def show_tailscale(self) -> None:
        self.tailscale_view = TailscaleView()

    def show_anonsurf(self) -> None:
        self.anon_surf_view = AnonSurfView()

    def show_vault(self) -> None:
        self.vault_view = VaultView()

    def show_bettercap(self) -> None:
        self.bettercap_view = BettercapView()

    def show_mail(self) -> None:
        self.mail_view = MailView()

    def show_messenger(self) -> None:
        self.messenger_view = MessengerView()

    def show_ragnar(self, phase: str = "landing") -> None:
        self.ragnar_view = RagnarView(phase)

    def show_signal_scraper(self) -> None:
        self.scraper_view = SignalScraperView()

    def show_traffic_cam(self) -> None:
        self.traffic_cam_view = TrafficCamView()

    def show_camera_interceptor(self) -> None:
        self.camera_view = CameraInterceptorView()

    def show_wifite(self) -> None:
        self.wifite_view = WifiteView()

    def show_wardrive(self) -> None:
        self.wardrive_view = WardriveView()

    def show_eviltwin(self) -> None:
        self.eviltwin_view = EvilTwinView()

    def show_captures(self) -> None:
        from bigbox.ui.captures import CapturesView
        self.captures_view = CapturesView()

    def show_scan_history(self) -> None:
        from bigbox.ui.scan_history import ScanHistoryView
        self.scan_history_view = ScanHistoryView()

    def show_web_access(self) -> None:
        from bigbox.ui.web_access import WebAccessView
        self.web_access_view = WebAccessView()

    def show_phone_camera(self) -> None:
        from bigbox.ui.phone_camera import PhoneCameraView
        self.phone_camera_view = PhoneCameraView()

    def show_diagnostics(self) -> None:
        from bigbox.ui.diagnostics import DiagnosticsView
        self.diagnostics_view = DiagnosticsView()

    def show_background_tasks(self) -> None:
        from bigbox.ui.background_tasks import BackgroundTasksView
        self.bg_tasks_view = BackgroundTasksView()

    def show_games(self) -> None:
        self.games_view = GamesView()

    def show_trackers(self) -> None:
        self.tracker_view = TrackerView()

    def show_probe_sniffer(self) -> None:
        self.probe_view = ProbeSnifferView()

    def show_beacon_flood(self) -> None:
        self.beacon_view = BeaconFloodView()

    def show_karma_lite(self) -> None:
        self.karma_view = KarmaLiteView()

    def show_chat(self) -> None:
        self.chat_view = ChatView()

    def show_sherlock(self, username: str) -> None:
        self.sherlock_view = SherlockView(username)

    def show_deaddrop(self) -> None:
        self.deaddrop_view = DeadDropView()

    def show_bbs(self) -> None:
        self.bbs_view = BBSView()

    def show_ble_chat(self) -> None:
        self.ble_view = BLEChatView()

    def show_onion_chat(self) -> None:
        self.onion_view = OnionChatView()

    def show_ble_spam(self) -> None:
        self.ble_spam_view = BLESpamView()

    def show_terminal(self) -> None:
        self.terminal_view = TerminalView()

    def show_theme_manager(self) -> None:
        self.theme_manager_view = ThemeManagerView()

    def show_update(self, title: str, argv: list[str]) -> None:
        view = UpdateView(title, "")
        self.update_view = view
        run_streaming(argv, view.append)

    def show_menu(self, title: str, actions: list[tuple[str, Callable[[], None]]]) -> None:
        self.menu_view = MenuView(title, actions)

    def get_input(self, title: str, callback: Callable[[str | None], None], initial: str = "") -> None:
        self.kb_view = KeyboardView(title, callback, initial)

    def go_back(self) -> None:
        self.result_view = None
        self.update_view = None
        self.cctv_view = None
        self.ping_view = None
        self.arp_view = None
        self.kb_view = None
        self.flock_view = None
        self.wifi_view = None
        self.cam_scan_view = None
        self.wifi_attack_view = None
        self.wifi_multi_view = None
        self.cracker_view = None
        self.media_view = None
        self.tv_view = None
        self.youtube_view = None
        self.tailscale_view = None
        self.anon_surf_view = None
        self.vault_view = None
        self.bettercap_view = None
        self.mail_view = None
        self.messenger_view = None
        self.ragnar_view = None
        self.scraper_view = None
        self.traffic_cam_view = None
        self.camera_view = None
        self.wifite_view = None
        self.chat_view = None
        self.sherlock_view = None
        self.deaddrop_view = None
        self.bbs_view = None
        self.ble_view = None
        self.onion_view = None
        self.ble_spam_view = None
        self.terminal_view = None
        self.theme_manager_view = None
        self.wardrive_view = None
        self.eviltwin_view = None
        self.games_view = None
        self.captures_view = None
        self.tracker_view = None
        self.probe_view = None
        self.beacon_view = None
        self.karma_view = None

    def toast(self, msg: str) -> None:
        # Lightweight: just print for now; could become an on-screen toast widget.
        print(f"[toast] {msg}")

    # ---------- target FPS ----------
    def _target_fps(self) -> int:
        """Cap the main loop FPS to whatever the foreground view needs.

        Saves a measurable chunk of CPU + battery on a handheld. Most
        views are static menus that don't need 60 fps; live-video views
        get 30; when an external fullscreen subprocess is on-screen
        (mpv, emulator, hostapd) pygame is hidden underneath and we
        only need to keep the event pump alive.
        """
        # External fullscreen subprocesses own the display.
        if self.media_view is not None and getattr(self.media_view, "proc", None) is not None:
            return 5
        if self.tv_view is not None and getattr(self.tv_view, "playing_proc", None) is not None:
            return 5
        if self.games_view is not None and getattr(self.games_view, "proc", None) is not None:
            return 5
        if self.eviltwin_view is not None:
            sess = getattr(self.eviltwin_view, "session", None)
            if sess is not None and getattr(sess, "is_running", lambda: False)():
                return 5
        # Live video / animation views — keep them smooth.
        if self.cctv_view is not None:
            return 30
        if self.tv_view is not None:
            return 30
        if self.traffic_cam_view is not None:
            return 30
        if self.camera_view is not None:
            return 30
        if self.flock_view is not None:
            return 30
        # Default: menus and most modals.
        return 30

    # ---------- main loop ----------
    def run(self) -> int:
        screen = self._init_display()
        
        # Pre-load shared fonts used by views that take a SectionContext.
        # Must happen AFTER pygame.font.init() inside _init_display().
        self.fonts = {
            "base": pygame.font.Font(None, theme.FS_BODY),
            "bold": pygame.font.Font(None, theme.FS_BODY + 4),
            "small": pygame.font.Font(None, theme.FS_BODY - 4),
        }

        pygame.display.set_caption("bigbox")
        # Play the Arasaka boot splash (red diamond + "WELCOME TO DaRkb0x" +
        # psx.mp3 chime) before anything else hits the screen. Skipped in
        # dev mode so we don't sit through it on every restart.
        if not self.dev_mode and not os.environ.get("BIGBOX_NO_SPLASH"):
            try:
                from bigbox import splash as _splash
                _splash.play(screen)
            except Exception as e:
                print(f"[bigbox] splash failed: {e}")
        
        self._start_input()
        self.update_checker.start()

        carousel = Carousel(build_sections())
        statusbar = StatusBar()
        body_font = pygame.font.Font(None, theme.FS_BODY)
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        clock = pygame.time.Clock()

        while self.running:
            # 1. Pump pygame events. Translate keys -> ButtonEvents for external keyboards.
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                    if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                        self.running = False
                    # Always translate keyboard events (supports USB/BLE keyboards on device)
                    kbd_translate(ev, self.bus)

            # 2. Drain logical button events; route to the foreground screen.
            for bev in self.bus.drain():
                self._dispatch(bev, carousel)

            # 3. Render.
            now = time.time()
            if now - self._last_vol_enforce > 10:
                self._last_vol_enforce = now
                try:
                    # Force Card 1 (Headphones) to 100% and unmute
                    subprocess.run(["amixer", "-c", "1", "sset", "PCM", "100%", "unmute"], capture_output=True)
                except:
                    pass

            screen.fill(theme.BG)
            if self._dispatch_view_render(screen):
                pass
            else:
                if self.show_status:
                    statusbar.render(screen, self)
                carousel.render(screen, body_font, title_font)

            if self.menu_view is not None:
                self.menu_view.render(screen)
                if self.menu_view.dismissed:
                    self.menu_view = None

            # 4. Web UI screen capture — only encode when somebody's
            #    actually watching. The web server bumps
            #    last_web_view_request on every /video_feed hit; if it's
            #    been quiet for >5s, skip the encode entirely. Big
            #    battery save when the device is being used standalone.
            self._frame_counter += 1
            if self._frame_counter >= 6:
                self._frame_counter = 0
                if time.time() - self.last_web_view_request < 5.0:
                    try:
                        # pygame.image.save accepts the display surface
                        # directly — drop the old tostring/fromstring
                        # round-trip that copied a full RGB buffer.
                        import io
                        buf = io.BytesIO()
                        pygame.image.save(screen, buf, "jpg")
                        self.last_frame = buf.getvalue()
                    except Exception:
                        pass

            pygame.display.flip()
            clock.tick(self._target_fps())

        if self.recording_proc:
            try:
                self.recording_proc.terminate()
            except:
                pass

        pygame.quit()
        return 0

    # ---------- view registry helpers --------------------------------------
    def _active_view(self) -> tuple[str, object, int]:
        """Return ``(attr_name, view, handle_arity)`` of the highest-
        priority active view, or ``("", None, 0)`` if none."""
        for name, arity in _VIEWS:
            v = getattr(self, name, None)
            if v is not None:
                return name, v, arity
        return "", None, 0

    def _force_stop_view(self, view: object) -> None:
        """Best-effort: call any plausible stop method on a crashed
        view so its subprocess/thread doesn't leak."""
        for stop_name in _STOP_METHODS:
            stop_fn = getattr(view, stop_name, None)
            if callable(stop_fn):
                try:
                    stop_fn()
                except Exception:
                    pass
                return

    def _dispatch_view_render(self, screen: pygame.Surface) -> bool:
        """Render the foreground view if any. Returns True if a view
        was rendered. A crashing view is logged + dropped instead of
        propagating, so one broken view can't take down bigbox."""
        name, view, _ = self._active_view()
        if view is None:
            return False
        try:
            view.render(screen)
            if getattr(view, "dismissed", False):
                setattr(self, name, None)
        except Exception as e:
            import traceback
            print(f"[render] {name} crashed: {type(e).__name__}: {e}")
            traceback.print_exc()
            self._force_stop_view(view)
            setattr(self, name, None)
        return True

    def _dispatch_view_input(self, bev: ButtonEvent) -> bool:
        """Hand input to the active view if any. Returns True if a
        view consumed it (caller should skip global hotkeys)."""
        name, view, arity = self._active_view()
        if view is None or arity == 0:
            return False
        try:
            if arity == 2:
                view.handle(bev, self)
            else:
                view.handle(bev)
        except Exception as e:
            import traceback
            print(f"[input] {name} crashed: {type(e).__name__}: {e}")
            traceback.print_exc()
            self._force_stop_view(view)
            setattr(self, name, None)
        return True

    def _dispatch(self, bev: ButtonEvent, carousel: Carousel) -> None:
        if bev.pressed:
            print(f"[app] Button press: {bev.button}")
            self.held_buttons.add(bev.button)
            if bev.button is Button.HK:
                self.hk_used = False
        else:
            print(f"[app] Button release: {bev.button}")
            self.held_buttons.discard(bev.button)
            if bev.button is Button.HK and not self.hk_used:
                self._open_hk_menu()
                return

        # Allow GamesView to receive key releases for the emulator
        if self.games_view is not None:
            if self.games_view.handle(bev, self):
                return
        
        if not bev.pressed:
            return

        # Hotkey combos (checked before single-button actions)
        if Button.HK in self.held_buttons and not bev.repeat:
            if bev.button is not Button.HK:
                self.hk_used = True
            
            if bev.button is Button.START:
                self.running = False  # Emergency exit
                return
            if bev.button is Button.B:
                self.go_back()
                return
            if bev.button is Button.A:
                self._open_hk_menu()
                return

        # Fallback for HK menu: SELECT + START (if not already handled)
        if not bev.repeat and Button.SELECT in self.held_buttons and Button.START in self.held_buttons:
            self._open_hk_menu()
            return

        # Specialized View Handling (Modal views)
        if self.menu_view is not None:
            self.menu_view.handle(bev)
            return

        # Foreground view (registry-driven, with try/except so a
        # crashing view drops itself instead of taking down bigbox).
        if self._dispatch_view_input(bev):
            return

        # Global hotkeys (low priority/contextual).
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

        action = carousel.handle(bev, self)   # self satisfies SectionContext
        if action and action.handler:
            try:
                action.handler(self)
            except Exception as e:
                self.show_result("error", f"{type(e).__name__}: {e}")

    def _open_system_menu(self) -> None:
        actions = [
            ("Back to Tool", lambda: None),
            ("Reboot", lambda: subprocess.run(["systemctl", "reboot"])),
            ("Power Off", lambda: subprocess.run(["systemctl", "poweroff"])),
        ]
        if self.dev_mode:
            actions.append(("Exit bigbox", lambda: setattr(self, "running", False)))
        self.menu_view = MenuView("System", actions)

    def _open_hk_menu(self) -> None:
        # Resolve update script path
        from pathlib import Path
        update_script = Path(__file__).resolve().parents[1] / "scripts" / "update.sh"
        
        actions = [
            ("Screenshot (Y)", self._take_screenshot),
            ("Record Screen (Toggle)", self._toggle_screen_record),
            ("OTA Update", lambda: self.show_update("OTA update", [str(update_script)])),
            ("Reboot System", lambda: subprocess.run(["systemctl", "reboot"])),
        ]
        self.menu_view = MenuView("HOTKEYS", actions)

    # ---------- captures ---------------------------------------------------
    # Both screenshots and recordings land here so the View Captures screen
    # has a single directory to scan.
    CAPTURES_DIR = Path("media/captures")

    def _has_v4l2m2m_encoder(self) -> bool:
        """Pi 4 has hardware h264 encode via /dev/video11. Detect once and
        cache. Falls back to mjpeg if v4l2m2m isn't there."""
        if hasattr(self, "_v4l2m2m_cache"):
            return self._v4l2m2m_cache
        ok = os.path.exists("/dev/video11")
        if ok:
            # Also confirm ffmpeg has the encoder compiled in
            try:
                out = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-encoders"],
                    capture_output=True, text=True, timeout=3,
                )
                ok = "h264_v4l2m2m" in out.stdout
            except Exception:
                ok = False
        self._v4l2m2m_cache = ok
        return ok

    def _toggle_screen_record(self) -> None:
        if self.recording_proc:
            # Stop recording — ffmpeg -f mpegts wants 'q' on stdin for a
            # clean trailer, but SIGTERM on most modern ffmpeg is fine.
            try:
                if self.recording_proc.stdin:
                    try:
                        self.recording_proc.stdin.write(b"q\n")
                        self.recording_proc.stdin.flush()
                    except Exception:
                        pass
                self.recording_proc.terminate()
                self.recording_proc.wait(timeout=3)
            except Exception:
                try:
                    self.recording_proc.kill()
                except Exception:
                    pass
            self.recording_proc = None
            from bigbox import background as _bg
            _bg.unregister("screen_record")
            self.toast("Recording saved to media/captures/")
            return

        # Start recording.
        from datetime import datetime
        self.CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        fname = self.CAPTURES_DIR / (
            f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        )

        if not self.dev_mode:
            # fbdev source — direct framebuffer read, no X handshake.
            in_args = ["-f", "fbdev", "-i", "/dev/fb0"]
        else:
            display = os.environ.get("DISPLAY", ":0")
            in_args = ["-f", "x11grab", "-s", "800x480", "-i", display]

        # Encoder choice: hardware first, MJPEG fallback. Both run at
        # ~10% CPU on a Pi 4 instead of the ~120% libx264 was costing,
        # which is why bigbox was freezing during record.
        if self._has_v4l2m2m_encoder():
            enc_args = [
                "-c:v", "h264_v4l2m2m",
                "-b:v", "1500k",
                "-pix_fmt", "yuv420p",
            ]
        else:
            enc_args = [
                "-c:v", "mjpeg",
                "-q:v", "5",        # 1=best, 31=worst
                "-pix_fmt", "yuvj420p",
            ]

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            *in_args,
            "-r", "12",             # 12 fps captures bigbox's adaptive 30fps fine
            *enc_args,
            str(fname),
        ]
        try:
            self.recording_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.PIPE,
            )
            self.recording_start_time = time.time()
            from bigbox import background as _bg
            _bg.register(
                "screen_record",
                f"Screen recording → {fname.name}",
                "Capture",
                stop=self._toggle_screen_record,
            )
            self.toast(f"Recording -> {fname.name}")
        except FileNotFoundError:
            self.toast("ffmpeg not installed")
        except Exception as e:
            self.toast(f"Record failed: {e}")

    def _take_screenshot(self) -> None:
        from datetime import datetime
        self.CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        fname = self.CAPTURES_DIR / (
            f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        # Snapshot the surface synchronously (cheap), then encode + write
        # to disk in a background thread. PNG encode of 800x480 takes
        # ~100ms on a Pi 4 — synchronous it visibly stutters the UI.
        try:
            surf = pygame.display.get_surface().copy()
        except Exception as e:
            self.toast(f"Screenshot grab failed: {e}")
            return

        def _save():
            try:
                pygame.image.save(surf, str(fname))
            except Exception as e:
                print(f"[screenshot] save failed: {e}")

        threading.Thread(target=_save, daemon=True).start()
        self.toast(f"Saved {fname.name}")
