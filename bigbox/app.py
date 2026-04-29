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
from bigbox.ui import Carousel, CCTVView, MenuView, ResultView, StatusBar, PingSweepView, KeyboardView, ARPScanView, FlockScannerView, WifiConnectView, CamScannerView, WifiAttackView, OfflineCrackerView, MediaPlayerView, InternetTVView, YouTubeView, TailscaleView, MailView, MessengerView, RagnarView, SignalScraperView, TrafficCamView, CameraInterceptorView, WifiteView, ChatView, SherlockView, DeadDropView, BBSView, BLEChatView, OnionChatView, BLESpamView, TerminalView, ThemeManagerView, UpdateView, WifiMultiToolView, WardriveView, EvilTwinView, GamesView, TrackerView, ProbeSnifferView, BeaconFloodView, KarmaLiteView


class App:
    def __init__(self) -> None:
        self.dev_mode = bool(os.environ.get("BIGBOX_DEV"))
        self.bus = EventBus()
        self.running = True
        self.update_checker = UpdateChecker(self)
        self.result_view: ResultView | None = None
        self.update_view: UpdateView | None = None
        self.menu_view: MenuView | None = None
        self.cctv_view: CCTVView | None = None
        self.ping_view: PingSweepView | None = None
        self.arp_view: ARPScanView | None = None
        self.kb_view: KeyboardView | None = None
        self.flock_view: FlockScannerView | None = None
        self.wifi_view: WifiConnectView | None = None
        self.cam_scan_view: CamScannerView | None = None
        self.wifi_attack_view: WifiAttackView | None = None
        self.wifi_multi_view: WifiMultiToolView | None = None
        self.cracker_view: OfflineCrackerView | None = None
        self.media_view: MediaPlayerView | None = None
        self.tv_view: InternetTVView | None = None
        self.youtube_view: YouTubeView | None = None
        self.tailscale_view: TailscaleView | None = None
        self.mail_view: MailView | None = None
        self.messenger_view: MessengerView | None = None
        self.ragnar_view: RagnarView | None = None
        self.scraper_view: SignalScraperView | None = None
        self.traffic_cam_view: TrafficCamView | None = None
        self.camera_view: CameraInterceptorView | None = None
        self.wifite_view: WifiteView | None = None
        self.chat_view: ChatView | None = None
        self.sherlock_view: SherlockView | None = None
        self.deaddrop_view: DeadDropView | None = None
        self.bbs_view: BBSView | None = None
        self.ble_view: BLEChatView | None = None
        self.onion_view: OnionChatView | None = None
        self.ble_spam_view: BLESpamView | None = None
        self.terminal_view: TerminalView | None = None
        self.theme_manager_view: ThemeManagerView | None = None
        self.wardrive_view: WardriveView | None = None
        self.eviltwin_view: EvilTwinView | None = None
        self.games_view: GamesView | None = None
        self.tracker_view: TrackerView | None = None
        self.probe_view: ProbeSnifferView | None = None
        self.beacon_view: BeaconFloodView | None = None
        self.karma_view: KarmaLiteView | None = None
        self.show_status = True
        self.held_buttons: set[Button] = set()
        self._last_vol_enforce = 0

        # Messaging background sync
        from bigbox.ui.messenger import MessengerSync
        self.msg_sync = MessengerSync(self)
        self.msg_sync.start()

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

    def show_media_player(self) -> None:
        self.media_view = MediaPlayerView()

    def show_tv(self) -> None:
        self.tv_view = InternetTVView()

    def show_youtube(self) -> None:
        self.youtube_view = YouTubeView()

    def show_tailscale(self) -> None:
        self.tailscale_view = TailscaleView()

    def show_mail(self) -> None:
        self.mail_view = MailView()

    def show_messenger(self) -> None:
        self.messenger_view = MessengerView()

    def show_ragnar(self) -> None:
        self.ragnar_view = RagnarView()

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
                    subprocess.run(["amixer", "sset", "PCM", "100%"], capture_output=True)
                except:
                    pass

            screen.fill(theme.BG)
            if self.kb_view is not None:
                self.kb_view.render(screen)
                if self.kb_view.dismissed:
                    self.kb_view = None
            elif self.cctv_view is not None:
                self.cctv_view.render(screen)
                if self.cctv_view.dismissed:
                    self.cctv_view = None
            elif self.ping_view is not None:
                self.ping_view.render(screen)
                if self.ping_view.dismissed:
                    self.ping_view = None
            elif self.arp_view is not None:
                self.arp_view.render(screen)
                if self.arp_view.dismissed:
                    self.arp_view = None
            elif self.flock_view is not None:
                self.flock_view.render(screen)
                if self.flock_view.dismissed:
                    self.flock_view = None
            elif self.wifi_view is not None:
                self.wifi_view.render(screen)
                if self.wifi_view.dismissed:
                    self.wifi_view = None
            elif self.cam_scan_view is not None:
                self.cam_scan_view.render(screen)
                if self.cam_scan_view.dismissed:
                    self.cam_scan_view = None
            elif self.wifi_attack_view is not None:
                self.wifi_attack_view.render(screen)
                if self.wifi_attack_view.dismissed:
                    self.wifi_attack_view = None
            elif self.wifi_multi_view is not None:
                self.wifi_multi_view.render(screen)
                if self.wifi_multi_view.dismissed:
                    self.wifi_multi_view = None
            elif self.cracker_view is not None:
                self.cracker_view.render(screen)
                if self.cracker_view.dismissed:
                    self.cracker_view = None
            elif self.media_view is not None:
                self.media_view.render(screen)
                if self.media_view.dismissed:
                    self.media_view = None
            elif self.tv_view is not None:
                self.tv_view.render(screen)
                if self.tv_view.dismissed:
                    self.tv_view = None
            elif self.youtube_view is not None:
                self.youtube_view.render(screen)
                if self.youtube_view.dismissed:
                    self.youtube_view = None
            elif self.tailscale_view is not None:
                self.tailscale_view.render(screen)
                if self.tailscale_view.dismissed:
                    self.tailscale_view = None
            elif self.mail_view is not None:
                self.mail_view.render(screen)
                if self.mail_view.dismissed:
                    self.mail_view = None
            elif self.messenger_view is not None:
                self.messenger_view.render(screen)
                if self.messenger_view.dismissed:
                    self.messenger_view = None
            elif self.ragnar_view is not None:
                self.ragnar_view.render(screen)
                if self.ragnar_view.dismissed:
                    self.ragnar_view = None
            elif self.scraper_view is not None:
                self.scraper_view.render(screen)
                if self.scraper_view.dismissed:
                    self.scraper_view = None
            elif self.traffic_cam_view is not None:
                self.traffic_cam_view.render(screen)
                if self.traffic_cam_view.dismissed:
                    self.traffic_cam_view = None
            elif self.camera_view is not None:
                self.camera_view.render(screen)
                if self.camera_view.dismissed:
                    self.camera_view = None
            elif self.wifite_view is not None:
                self.wifite_view.render(screen)
                if self.wifite_view.dismissed:
                    self.wifite_view = None
            elif self.chat_view is not None:
                self.chat_view.render(screen)
                if self.chat_view.dismissed:
                    self.chat_view = None
            elif self.sherlock_view is not None:
                self.sherlock_view.render(screen)
                if self.sherlock_view.dismissed:
                    self.sherlock_view = None
            elif self.deaddrop_view is not None:
                self.deaddrop_view.render(screen)
                if self.deaddrop_view.dismissed:
                    self.deaddrop_view = None
            elif self.bbs_view is not None:
                self.bbs_view.render(screen)
                if self.bbs_view.dismissed:
                    self.bbs_view = None
            elif self.ble_view is not None:
                self.ble_view.render(screen)
                if self.ble_view.dismissed:
                    self.ble_view = None
            elif self.onion_view is not None:
                self.onion_view.render(screen)
                if self.onion_view.dismissed:
                    self.onion_view = None
            elif self.ble_spam_view is not None:
                self.ble_spam_view.render(screen)
                if self.ble_spam_view.dismissed:
                    self.ble_spam_view = None
            elif self.terminal_view is not None:
                self.terminal_view.render(screen)
                if self.terminal_view.dismissed:
                    self.terminal_view = None
            elif self.theme_manager_view is not None:
                self.theme_manager_view.render(screen)
                if self.theme_manager_view.dismissed:
                    self.theme_manager_view = None
            elif self.wardrive_view is not None:
                self.wardrive_view.render(screen)
                if self.wardrive_view.dismissed:
                    self.wardrive_view = None
            elif self.eviltwin_view is not None:
                self.eviltwin_view.render(screen)
                if self.eviltwin_view.dismissed:
                    self.eviltwin_view = None
            elif self.games_view is not None:
                self.games_view.render(screen)
                if self.games_view.dismissed:
                    self.games_view = None
            elif self.tracker_view is not None:
                self.tracker_view.render(screen)
                if self.tracker_view.dismissed:
                    self.tracker_view = None
            elif self.probe_view is not None:
                self.probe_view.render(screen)
                if self.probe_view.dismissed:
                    self.probe_view = None
            elif self.beacon_view is not None:
                self.beacon_view.render(screen)
                if self.beacon_view.dismissed:
                    self.beacon_view = None
            elif self.karma_view is not None:
                self.karma_view.render(screen)
                if self.karma_view.dismissed:
                    self.karma_view = None
            elif self.update_view is not None:
                self.update_view.render(screen)
                if self.update_view.dismissed:
                    self.update_view = None
            elif self.result_view is not None:
                self.result_view.render(screen)
                if self.result_view.dismissed:
                    self.result_view = None
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

        # Specialized View Handling (Modal views)
        if self.menu_view is not None:
            self.menu_view.handle(bev)
            return

        if self.kb_view is not None:
            self.kb_view.handle(bev)
            return

        if self.cctv_view is not None:
            self.cctv_view.handle(bev)
            return

        if self.ping_view is not None:
            self.ping_view.handle(bev, self)
            return

        if self.arp_view is not None:
            self.arp_view.handle(bev, self)
            return

        if self.flock_view is not None:
            self.flock_view.handle(bev, self)
            return

        if self.wifi_view is not None:
            self.wifi_view.handle(bev, self)
            return

        if self.cam_scan_view is not None:
            self.cam_scan_view.handle(bev, self)
            return

        if self.wifi_attack_view is not None:
            self.wifi_attack_view.handle(bev, self)
            return

        if self.wifi_multi_view is not None:
            self.wifi_multi_view.handle(bev, self)
            return

        if self.cracker_view is not None:
            self.cracker_view.handle(bev, self)
            return

        if self.media_view is not None:
            self.media_view.handle(bev, self)
            return

        if self.tv_view is not None:
            try:
                self.tv_view.handle(bev, self)
            except Exception as e:
                self.show_result("TV Error", f"{type(e).__name__}: {e}")
            return

        if self.youtube_view is not None:
            self.youtube_view.handle(bev, self)
            return

        if self.tailscale_view is not None:
            self.tailscale_view.handle(bev, self)
            return

        if self.mail_view is not None:
            self.mail_view.handle(bev, self)
            return

        if self.messenger_view is not None:
            self.messenger_view.handle(bev, self)
            return

        if self.ragnar_view is not None:
            self.ragnar_view.handle(bev, self)
            return

        if self.scraper_view is not None:
            self.scraper_view.handle(bev, self)
            return

        if self.sherlock_view is not None:
            self.sherlock_view.handle(bev, self)
            return

        if self.deaddrop_view is not None:
            self.deaddrop_view.handle(bev, self)
            return

        if self.bbs_view is not None:
            self.bbs_view.handle(bev, self)
            return

        if self.ble_view is not None:
            self.ble_view.handle(bev, self)
            return

        if self.onion_view is not None:
            self.onion_view.handle(bev, self)
            return

        if self.ble_spam_view is not None:
            self.ble_spam_view.handle(bev, self)
            return

        if self.terminal_view is not None:
            self.terminal_view.handle(bev, self)
            return

        if self.theme_manager_view is not None:
            self.theme_manager_view.handle(bev, self)
            return

        if self.wardrive_view is not None:
            self.wardrive_view.handle(bev, self)
            return

        if self.eviltwin_view is not None:
            self.eviltwin_view.handle(bev, self)
            return

        if self.games_view is not None:
            self.games_view.handle(bev, self)
            return

        if self.tracker_view is not None:
            self.tracker_view.handle(bev, self)
            return

        if self.probe_view is not None:
            self.probe_view.handle(bev, self)
            return

        if self.beacon_view is not None:
            self.beacon_view.handle(bev, self)
            return

        if self.karma_view is not None:
            self.karma_view.handle(bev, self)
            return

        if self.update_view is not None:
            self.update_view.handle(bev)
            return

        if self.result_view is not None:
            self.result_view.handle(bev)
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
