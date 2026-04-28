import pygame
import os
import time
import threading
import sys
try:
    from gpiozero import Button as GZButton
except ImportError:
    GZButton = None
import tomli

class PagerInputEvent:
    def __init__(self, button, type, timestamp):
        self.button = button
        self.type = type
        self.timestamp = timestamp

class Pager:
    BLACK = 0x0000
    WHITE = 0xFFFF
    RED = 0xF800
    GREEN = 0x07E0
    BLUE = 0x001F
    YELLOW = 0xFFE0
    CYAN = 0x07FF
    MAGENTA = 0xF81F
    ORANGE = 0xFD20
    PURPLE = 0x8010
    GRAY = 0x8410

    ROTATION_0 = 0
    ROTATION_90 = 90
    ROTATION_180 = 180
    ROTATION_270 = 270

    FONT_SMALL = 1
    FONT_MEDIUM = 2
    FONT_LARGE = 3

    BTN_UP = 0x01
    BTN_DOWN = 0x02
    BTN_LEFT = 0x04
    BTN_RIGHT = 0x08
    BTN_A = 0x10
    BTN_B = 0x20
    BTN_POWER = 0x40

    EVENT_NONE = 0
    EVENT_PRESS = 1
    EVENT_RELEASE = 2

    RTTTL_SOUND_ONLY = 0
    RTTTL_SOUND_VIBRATE = 1
    RTTTL_VIBRATE_ONLY = 2

    RTTTL_TETRIS = ""
    RTTTL_GAME_OVER = ""
    RTTTL_LEVEL_UP = ""

    def __init__(self):
        self._initialized = False
        self._w = 480
        self._h = 222
        self._scale = 1.0
        self._screen = None
        self._surf = None
        self._events = []
        self._fonts = {}
        self._buttons = {}
        self._lock = threading.Lock()
        self._b_held_since = 0

    def init(self):
        os.environ["SDL_VIDEODRIVER"] = "x11"
        os.environ["DISPLAY"] = ":0"
        pygame.init()
        pygame.mouse.set_visible(False)
        self._screen = pygame.display.set_mode((800, 480))
        self._surf = pygame.Surface((self._w, self._h))
        self._scale = min(800 / self._w, 480 / self._h)
        self._setup_gpio()
        self._initialized = True
        return 0

    def _setup_gpio(self):
        if not GZButton: return
        paths = ["/etc/bigbox/buttons.toml", "/opt/bigbox/config/buttons.toml"]
        pins = {}
        for p in paths:
            if os.path.exists(p):
                with open(p, "rb") as f:
                    try:
                        cfg = tomli.load(f)
                        pins = cfg.get("pins", {})
                        break
                    except: pass
        
        mapping = {
            "UP": self.BTN_UP, "DOWN": self.BTN_DOWN, "LEFT": self.BTN_LEFT, "RIGHT": self.BTN_RIGHT,
            "A": self.BTN_A, "B": self.BTN_B
        }

        for name, pin in pins.items():
            if name in mapping:
                gz = GZButton(pin, pull_up=True, bounce_time=0.03)
                gz.when_pressed = lambda d, b=mapping[name]: self._add_gpio_event(b, self.EVENT_PRESS)
                gz.when_released = lambda d, b=mapping[name]: self._add_gpio_event(b, self.EVENT_RELEASE)
                self._buttons[name] = gz

    def _add_gpio_event(self, btn, type):
        with self._lock:
            if btn == self.BTN_B:
                if type == self.EVENT_PRESS:
                    if self._b_held_since == 0: self._b_held_since = time.time()
                else:
                    self._b_held_since = 0
            self._events.append(PagerInputEvent(btn, type, self.get_ticks()))

    def cleanup(self):
        for btn in self._buttons.values():
            try: btn.close()
            except: pass
        pygame.quit()

    def set_rotation(self, rotation): pass
    def width(self): return self._w
    def height(self): return self._h

    def _rgb565_to_rgb(self, c):
        r = ((c >> 11) & 0x1F) * 255 // 31
        g = ((c >> 5) & 0x3F) * 255 // 63
        b = (c & 0x1F) * 255 // 31
        return (r, g, b)

    @staticmethod
    def rgb(r, g, b): return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    @staticmethod
    def hex_color(h):
        h = h.lstrip('#')
        rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return Pager.rgb(*rgb)

    def flip(self):
        if not self._initialized: return
        scaled = pygame.transform.scale(self._surf, (int(self._w * self._scale), int(self._h * self._scale)))
        x = (800 - scaled.get_width()) // 2
        y = (480 - scaled.get_height()) // 2
        self._screen.fill((0,0,0))
        self._screen.blit(scaled, (x, y))
        pygame.display.flip()

        now = time.time()
        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN or event.type == pygame.KEYUP:
                btn = None
                if event.key == pygame.K_UP: btn = self.BTN_UP
                elif event.key == pygame.K_DOWN: btn = self.BTN_DOWN
                elif event.key == pygame.K_LEFT: btn = self.BTN_LEFT
                elif event.key == pygame.K_RIGHT: btn = self.BTN_RIGHT
                elif event.key == pygame.K_z: btn = self.BTN_A
                elif event.key == pygame.K_x: btn = self.BTN_B
                elif event.key == pygame.K_RETURN: btn = self.BTN_A
                elif event.key == pygame.K_ESCAPE: btn = self.BTN_B
                if btn:
                    if btn == self.BTN_B:
                        if event.type == pygame.KEYDOWN:
                            if self._b_held_since == 0: self._b_held_since = now
                        else:
                            self._b_held_since = 0
                    with self._lock:
                        self._events.append(PagerInputEvent(btn, self.EVENT_PRESS if event.type == pygame.KEYDOWN else self.EVENT_RELEASE, self.get_ticks()))

        if self._b_held_since > 0 and (now - self._b_held_since) > 2.0:
            print("[pagerctl] Kill switch triggered! Exiting Loki.")
            self.cleanup()
            sys.exit(0)

    def clear(self, color=0): self._surf.fill(self._rgb565_to_rgb(color))
    def get_ticks(self): return pygame.time.get_ticks()
    def delay(self, ms): pygame.time.wait(ms)
    def frame_sync(self): return self.get_ticks()
    def pixel(self, x, y, color): self._surf.set_at((int(x), int(y)), self._rgb565_to_rgb(color))
    def fill_rect(self, x, y, w, h, color): pygame.draw.rect(self._surf, self._rgb565_to_rgb(color), (x, y, w, h))
    def rect(self, x, y, w, h, color): pygame.draw.rect(self._surf, self._rgb565_to_rgb(color), (x, y, w, h), 1)
    def hline(self, x, y, w, color): pygame.draw.line(self._surf, self._rgb565_to_rgb(color), (x, y), (x+w, y))
    def vline(self, x, y, h, color): pygame.draw.line(self._surf, self._rgb565_to_rgb(color), (x, y), (x, y+h))
    def line(self, x0, y0, x1, y1, color): pygame.draw.line(self._surf, self._rgb565_to_rgb(color), (x0, y0), (x1, y1))
    def fill_circle(self, cx, cy, r, color): pygame.draw.circle(self._surf, self._rgb565_to_rgb(color), (cx, cy), r)
    def circle(self, cx, cy, r, color): pygame.draw.circle(self._surf, self._rgb565_to_rgb(color), (cx, cy), r, 1)
    def draw_char(self, x, y, char, color, size=1): self.draw_text(x, y, char.decode() if isinstance(char, bytes) else char, color, size)
    
    def draw_text(self, x, y, text, color, size=1):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        f = pygame.font.Font(None, 12 * size)
        s = f.render(text, True, self._rgb565_to_rgb(color))
        self._surf.blit(s, (x, y))
        return x + s.get_width()

    def draw_text_centered(self, y, text, color, size=1):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        f = pygame.font.Font(None, 12 * size)
        s = f.render(text, True, self._rgb565_to_rgb(color))
        self._surf.blit(s, ((self._w - s.get_width()) // 2, y))

    def text_width(self, text, size=1):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        return pygame.font.Font(None, 12 * size).size(text)[0]

    def draw_number(self, x, y, num, color, size=1): return self.draw_text(x, y, str(num), color, size)

    def _get_font(self, font_path, size):
        if isinstance(font_path, bytes): font_path = font_path.decode()
        key = (font_path, size)
        if key not in self._fonts:
            try: self._fonts[key] = pygame.font.Font(font_path, int(size))
            except: self._fonts[key] = pygame.font.Font(None, int(size))
        return self._fonts[key]

    def draw_ttf(self, x, y, text, color, font_path, font_size):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        s = self._get_font(font_path, font_size).render(text, True, self._rgb565_to_rgb(color))
        self._surf.blit(s, (x, y))
        return x + s.get_width()

    def ttf_width(self, text, font_path, font_size):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        return self._get_font(font_path, font_size).size(text)[0]

    def ttf_height(self, font_path, font_size):
        return self._get_font(font_path, font_size).get_linesize()

    def draw_ttf_centered(self, y, text, color, font_path, font_size):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        s = self._get_font(font_path, font_size).render(text, True, self._rgb565_to_rgb(color))
        self._surf.blit(s, ((self._w - s.get_width()) // 2, y))

    def draw_ttf_right(self, y, text, color, font_path, font_size, padding=0):
        if isinstance(text, bytes): text = text.decode("utf-8", "replace")
        s = self._get_font(font_path, font_size).render(text, True, self._rgb565_to_rgb(color))
        self._surf.blit(s, (self._w - s.get_width() - padding, y))

    def play_rtttl(self, melody, mode=None): pass
    def stop_audio(self): pass
    def audio_playing(self): return 0
    def beep(self, freq, duration_ms): pass
    def play_rtttl_sync(self, melody, with_vibration=False): pass
    def vibrate(self, duration_ms=200): pass
    def vibrate_pattern(self, pattern): pass
    def led_set(self, name, brightness): pass
    def led_rgb(self, button, r, g, b): pass
    def led_dpad(self, direction, color): pass
    def led_all_off(self): pass
    def random(self, max_val): return random.randint(0, max_val-1) if max_val > 0 else 0
    def seed_random(self, seed): random.seed(seed)

    def wait_button(self):
        while True:
            ev = self.get_input_event()
            if ev and ev.type == self.EVENT_PRESS: return ev.button
            pygame.time.wait(10)
            self.flip()

    def poll_input(self): pass

    def get_input_event(self):
        self.flip()
        with self._lock:
            if self._events:
                ev = self._events.pop(0)
                class CEvent:
                    button = ev.button
                    type = ev.type
                    timestamp = ev.timestamp
                return CEvent()
        return None

    def has_input_events(self):
        with self._lock:
            return len(self._events) > 0
            
    def peek_buttons(self): return 0
    def clear_input_events(self):
        with self._lock: self._events = []
        
    def set_brightness(self, percent): return 1
    def get_brightness(self): return 100
    def get_max_brightness(self): return 100
    def screen_off(self): return 0
    def screen_on(self): return 0

    def load_image(self, filepath):
        if isinstance(filepath, bytes): filepath = filepath.decode()
        try: return pygame.image.load(filepath).convert_alpha()
        except: return None

    def free_image(self, handle): pass

    def draw_image(self, x, y, handle):
        if handle: self._surf.blit(handle, (x, y))

    def draw_image_scaled(self, x, y, w, h, handle):
        if handle: self._surf.blit(pygame.transform.scale(handle, (w, h)), (x, y))

    def draw_image_file(self, x, y, filepath):
        img = self.load_image(filepath)
        if img: self.draw_image(x, y, img); return 0
        return -1

    def draw_image_file_scaled(self, x, y, w, h, filepath):
        img = self.load_image(filepath)
        if img: self.draw_image_scaled(x, y, w, h, img); return 0
        return -1

    def get_image_info(self, filepath):
        img = self.load_image(filepath)
        if img: return (img.get_width(), img.get_height())
        return (0, 0)

    def draw_image_scaled_rotated(self, x, y, w, h, handle, rotation=0):
        if handle: self._surf.blit(pygame.transform.rotate(pygame.transform.scale(handle, (w, h)), rotation), (x, y))

    def draw_image_file_scaled_rotated(self, x, y, w, h, filepath, rotation=0):
        img = self.load_image(filepath)
        if img: self.draw_image_scaled_rotated(x, y, w, h, img, rotation); return 0
        return -1

    def screenshot(self, filepath, rotation=270): return 0

    def __enter__(self): self.init(); return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.cleanup()
