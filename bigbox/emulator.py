"""Emulator launcher + per-system definitions for the Games section.

Three systems supported in v1:
  gbc - Game Boy Color (and Game Boy)  -> mgba-sdl
  gba - Game Boy Advance               -> mgba-sdl
  ps1 - PlayStation 1                  -> duckstation-nogui, fallback pcsx-rearmed

mGBA's RetroAchievements integration is configured by writing to
~/.config/mgba/config.ini before launch (see bigbox/retroachievements.py).
DuckStation has its own config; v1 just launches it without RA.

ROMs live under /opt/bigbox/roms/<system>/. PS1 BIOS (user-supplied,
copyrighted, not shipped) lives under /opt/bigbox/bios/ps1/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import evdev
    from evdev import UInput, ecodes as e
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False


ROMS_ROOT = Path("roms")
BIOS_ROOT = Path("bios")
EMULATOR_LOG = Path("/tmp/bigbox-emu.log")

def _build_keymaps():
    """Per-system bigbox-button → evdev-key tables. Built lazily so
    the module can be imported when evdev isn't available."""
    if not HAS_EVDEV:
        return {}
    from bigbox.events import Button
    return {
        # PSX: 4 distinct face + 2 shoulder + d-pad + start/select.
        "ps1": {
            Button.UP: e.KEY_UP,
            Button.DOWN: e.KEY_DOWN,
            Button.LEFT: e.KEY_LEFT,
            Button.RIGHT: e.KEY_RIGHT,
            Button.A: e.KEY_X,            # Cross
            Button.B: e.KEY_Z,            # Circle
            Button.X: e.KEY_C,            # Triangle
            Button.Y: e.KEY_V,            # Square
            Button.LL: e.KEY_Q,           # L1
            Button.RR: e.KEY_W,           # R1
            Button.START: e.KEY_ENTER,
            Button.SELECT: e.KEY_BACKSPACE,
        },
        # GB / GBC / GBA: 2 face + 2 shoulder, with X/Y doubling as L/R
        # (matches mGBA's defaults).
        "gba": {
            Button.UP: e.KEY_UP,
            Button.DOWN: e.KEY_DOWN,
            Button.LEFT: e.KEY_LEFT,
            Button.RIGHT: e.KEY_RIGHT,
            Button.A: e.KEY_X,
            Button.B: e.KEY_Z,
            Button.X: e.KEY_A,
            Button.Y: e.KEY_S,
            Button.LL: e.KEY_A,
            Button.RR: e.KEY_S,
            Button.START: e.KEY_ENTER,
            Button.SELECT: e.KEY_BACKSPACE,
        },
    }


class InputInjector:
    """Single long-lived uinput device, reused across emulator launches.

    Used to be created/destroyed per-launch — that's too late for X11:
    the device appears at /dev/input/eventN microseconds before mednafen
    starts SDL, and Xorg/libinput often misses the udev hot-add. By
    keeping one device alive for the entire bigbox process lifetime,
    udev sees it at boot and X11 has it enumerated long before any
    emulator window opens.

    set_system(key) flips which keymap inject() uses; the underlying
    uinput device pre-declares the union of every possible key so the
    device's capabilities never change at runtime."""

    def __init__(self):
        self.ui: Optional[UInput] = None
        self.system_key = "gba"   # safe default
        self._keymaps = _build_keymaps()
        if not HAS_EVDEV or not self._keymaps:
            return
        # Union of all keys across every system's keymap.
        all_keys = set()
        for km in self._keymaps.values():
            all_keys.update(km.values())
        try:
            self.ui = UInput({e.EV_KEY: sorted(all_keys)},
                             name="bigbox-virtual-gamepad")
        except Exception as ex:
            print(f"[emulator] Failed to create UInput: {ex}")

    def set_system(self, system_key: str) -> None:
        """Pick which keymap inject() will use until the next call."""
        self.system_key = system_key if system_key in self._keymaps else "gba"

    @property
    def keymap(self) -> dict:
        return self._keymaps.get(self.system_key, {})

    def inject(self, btn, pressed: bool) -> None:
        if not self.ui:
            return
        key = self.keymap.get(btn)
        if key is None:
            return
        try:
            self.ui.write(e.EV_KEY, key, 1 if pressed else 0)
            self.ui.syn()
        except Exception:
            pass

    def close(self) -> None:
        if self.ui:
            try:
                self.ui.close()
            except Exception:
                pass
            self.ui = None


@dataclass
class SystemDef:
    key: str                   # url-safe identifier: "gbc", "gba", "ps1"
    label: str                 # display label for the UI
    rom_subdir: str            # subdir under roms/
    extensions: tuple[str, ...]  # allowed extensions (lowercase, with dot)
    binary_candidates: tuple[str, ...]  # in priority order
    extra_args: tuple[str, ...] = ()    # static args appended before the rom

    def rom_dir(self) -> Path:
        return ROMS_ROOT / self.rom_subdir

    def list_roms(self) -> list[str]:
        d = self.rom_dir()
        if not d.is_dir():
            return []
        try:
            return sorted(p.name for p in d.iterdir()
                          if p.is_file() and p.suffix.lower() in self.extensions)
        except OSError:
            return []

    def find_binary(self) -> str | None:
        # Search PATH first, then Debian's /usr/games/ (which isn't on
        # bigbox.service's PATH — apt installs emulators there). Returns
        # an absolute path when found via /usr/games so subprocess.Popen
        # doesn't have to know about it.
        for cand in self.binary_candidates:
            found = shutil.which(cand)
            if found:
                return found
            for prefix in ("/usr/games/", "/usr/local/games/"):
                p = prefix + cand
                if os.access(p, os.X_OK):
                    return p
        return None


SYSTEMS: dict[str, SystemDef] = {
    "gbc": SystemDef(
        key="gbc",
        label="GAME BOY / GBC",
        rom_subdir="gbc",
        extensions=(".gb", ".gbc", ".zip"),
        # mGBA handles both classic GB and GBC. Debian's `mgba-sdl` package
        # installs the binary as plain `mgba` (not `mgba-sdl`); check both
        # to support upstream + distro builds.
        binary_candidates=("mgba", "mgba-sdl", "mgba-qt"),
        extra_args=("-f",),  # fullscreen
    ),
    "gba": SystemDef(
        key="gba",
        label="GAME BOY ADVANCE",
        rom_subdir="gba",
        extensions=(".gba", ".zip"),
        binary_candidates=("mgba", "mgba-sdl", "mgba-qt"),
        extra_args=("-f", "-3"),  # Fullscreen + 3x scale
    ),
    "ps1": SystemDef(
        key="ps1",
        label="PLAYSTATION 1",
        rom_subdir="ps1",
        # .pbp is the eboot format; .chd is compressed. duckstation eats them all.
        extensions=(".bin", ".cue", ".iso", ".img", ".pbp", ".chd", ".ecm", ".m3u"),
        # mednafen is the most reliable PS1 path on Debian/Kali — single
        # binary, ALSA audio works out of the box, fullscreen via -fs 1.
        # DuckStation is preferred when present (RA support); pcsxr is a
        # last resort because its plugin audio defaults are flaky.
        binary_candidates=("duckstation-nogui", "duckstation-qt",
                           "mednafen",
                           "pcsxr", "pcsx_rearmed", "pcsx-rearmed"),
        # extra_args is empty here — PS1 emus take incompatible flags
        # (mednafen errors out on unknown ones), so the actual flags
        # are picked per-binary in launch() via _ps1_args_for_binary.
        extra_args=(),
    ),
}


# Allowed system keys for the web /upload endpoint. Includes a special
# "ps1-bios" key that targets bios/ps1/ instead of roms/ps1/.
WEB_SYSTEMS = ("gbc", "gba", "ps1", "ps1-bios")


def upload_target_dir(system_key: str) -> Path | None:
    """Resolve a web upload `system` field to a writable directory, or
    None if the key isn't allowed."""
    if system_key in SYSTEMS:
        return ROMS_ROOT / SYSTEMS[system_key].rom_subdir
    if system_key == "ps1-bios":
        return BIOS_ROOT / "ps1"
    return None


def list_all_roms() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, sd in SYSTEMS.items():
        out[key] = sd.list_roms()
    # PS1 BIOS as its own bucket so the user can verify they've uploaded it.
    bios = BIOS_ROOT / "ps1"
    if bios.is_dir():
        out["ps1-bios"] = sorted(p.name for p in bios.iterdir() if p.is_file())
    else:
        out["ps1-bios"] = []
    return out


def _ps1_args_for_binary(bin_name: str) -> tuple[str, ...]:
    """Per-binary CLI flags for PS1 emulators. mednafen rejects
    unknown args (errors out — not silent); duckstation takes
    --fullscreen; pcsxr is GUI-only and takes no useful CLI."""
    name = bin_name.lower()
    if "mednafen" in name:
        return ("-fs", "1")
    if "duckstation" in name:
        return ("-fullscreen",)
    return ()


# PS1 BIOS files mednafen looks for (priority order, scph5501 is US)
_MEDNAFEN_BIOS_NAMES = ("scph5501.bin", "scph5500.bin", "scph5502.bin")


def _configure_mednafen_psx_bios() -> None:
    """Mednafen needs psx.bios_{na,jp,eu} pointing to a real BIOS file
    or it refuses to run PS1 games. Users typically drop scph1001.bin
    or similar into /opt/bigbox/bios/ps1/; mednafen wants the *5500
    series by default. Sidestep the filename mismatch by writing the
    paths into mednafen.cfg directly. Idempotent — re-runs are no-ops.

    Also disables psx.bios_sanity so older BIOS dumps with non-canonical
    SHA1s still work."""
    bios_dir = Path("/opt/bigbox/bios/ps1")
    if not bios_dir.is_dir():
        return
    candidates = [p for p in bios_dir.iterdir()
                  if p.is_file() and p.suffix.lower() == ".bin"]
    if not candidates:
        return
    # Prefer scph5501 if present (NTSC-U canonical), else first .bin.
    chosen = next(
        (p for p in candidates if p.name.lower() in _MEDNAFEN_BIOS_NAMES),
        candidates[0],
    )

    cfg_path = Path("/root/.mednafen/mednafen.cfg")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    # Audio: pin mednafen to direct ALSA on the Headphones card
    # rather than going through SDL → pulse. Pulse on this image
    # frequently has only auto_null loaded (the ALSA monitor doesn't
    # consistently see the cards), and mednafen's own ALSA driver is
    # rock-solid. plughw lets ALSA convert rate/format on the fly.
    # SDL scancodes that match what InputInjector emits in PS1 mode.
    # Format: `keyboard 0x0 <decimal_scancode>` — verified against
    # the existing md.input.* bindings already in mednafen.cfg.
    # Bigbox button → SDL scancode → PSX action:
    #   UP/DOWN/LEFT/RIGHT  →  82/81/80/79  →  D-Pad
    #   A (KEY_X = SDL X)   →  27          →  Cross
    #   B (KEY_Z = SDL Z)   →  29          →  Circle
    #   X (KEY_C = SDL C)   →  6           →  Triangle
    #   Y (KEY_V = SDL V)   →  25          →  Square
    #   LL (KEY_Q = SDL Q)  →  20          →  L1
    #   RR (KEY_W = SDL W)  →  26          →  R1
    #   START (KEY_ENTER)   →  40          →  Start
    #   SELECT (BACKSPACE)  →  42          →  Select
    # L2/R2 left at scancode 0 (unbound) — bigbox has no extra
    # shoulders to spare. Most PSX titles work without them.
    settings = {
        "psx.bios_jp": str(chosen),
        "psx.bios_na": str(chosen),
        "psx.bios_eu": str(chosen),
        "psx.bios_sanity": "0",
        "video.driver": "opengl",
        "video.fs": "1",
        "sound.driver": "alsa",
        "sound.device": "plughw:1,0",
        "sound.rate": "48000",
        "sound.volume": "100",
        "psx.input.port1": "gamepad",
        "psx.input.port1.gamepad.up":       "keyboard 0x0 82",
        "psx.input.port1.gamepad.down":     "keyboard 0x0 81",
        "psx.input.port1.gamepad.left":     "keyboard 0x0 80",
        "psx.input.port1.gamepad.right":    "keyboard 0x0 79",
        "psx.input.port1.gamepad.cross":    "keyboard 0x0 27",
        "psx.input.port1.gamepad.circle":   "keyboard 0x0 29",
        "psx.input.port1.gamepad.triangle": "keyboard 0x0 6",
        "psx.input.port1.gamepad.square":   "keyboard 0x0 25",
        "psx.input.port1.gamepad.l1":       "keyboard 0x0 20",
        "psx.input.port1.gamepad.r1":       "keyboard 0x0 26",
        "psx.input.port1.gamepad.l2":       "keyboard 0x0 0",
        "psx.input.port1.gamepad.r2":       "keyboard 0x0 0",
        "psx.input.port1.gamepad.start":    "keyboard 0x0 40",
        "psx.input.port1.gamepad.select":   "keyboard 0x0 42",
    }

    existing: dict[str, str] = {}
    if cfg_path.is_file():
        for line in cfg_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                existing[parts[0]] = parts[1]
    existing.update(settings)
    body = "\n".join(f"{k} {v}" for k, v in existing.items()) + "\n"
    cfg_path.write_text(body)


def _pulse_env() -> dict:
    """Env vars that let root talk to a user's pipewire-pulse session.
    bigbox.service runs as root, but pipewire-pulse runs under the
    desktop user (typically uid 1000) and its native socket lives at
    /run/user/<uid>/pulse/native — root needs PULSE_SERVER and
    XDG_RUNTIME_DIR pointed at that path or libpulse can't find it."""
    try:
        for d in sorted(os.listdir("/run/user")):
            sock = f"/run/user/{d}/pulse/native"
            if os.path.exists(sock):
                return {
                    "PULSE_SERVER": f"unix:{sock}",
                    "XDG_RUNTIME_DIR": f"/run/user/{d}",
                }
    except OSError:
        pass
    return {}


def save_audio_volume() -> dict:
    """Snapshot the current audio volume so the caller can restore it
    after launch() bumps it to 100%. Returns an opaque dict; pass it
    to restore_audio_volume()."""
    if _audio_daemon_running():
        env = {**os.environ, **_pulse_env()}
        try:
            out = subprocess.check_output(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                text=True, stderr=subprocess.DEVNULL, timeout=2, env=env,
            )
            import re
            m = re.search(r"(\d+)%", out)
            if m:
                return {"kind": "pulse", "volume": int(m.group(1))}
        except Exception:
            pass
        return {"kind": "pulse"}
    try:
        out = subprocess.check_output(
            ["amixer", "-c", "1", "sget", "PCM"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        )
        import re
        m = re.search(r"\[(\d+)%\]", out)
        if m:
            return {"kind": "alsa", "volume": int(m.group(1))}
    except Exception:
        pass
    return {"kind": "none"}


def restore_audio_volume(ctx: dict | None) -> None:
    if not ctx:
        return
    kind = ctx.get("kind")
    vol = ctx.get("volume")
    if vol is None:
        return
    try:
        if kind == "pulse":
            env = {**os.environ, **_pulse_env()}
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{vol}%"],
                check=False, timeout=2, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif kind == "alsa":
            subprocess.run(
                ["amixer", "-c", "1", "sset", "PCM", f"{vol}%"],
                check=False, timeout=2,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


def _audio_daemon_running() -> bool:
    """True if pipewire-pulse or PulseAudio is running. When either
    owns the audio cards, SDL apps must go through the pulse driver —
    direct-ALSA paths silently fail for shared devices."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-x", "-f", "pipewire-pulse|pulseaudio"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        )
        return bool(out.strip())
    except Exception:
        # Fallback: check for the user runtime socket, which both
        # daemons create at /run/user/<uid>/pulse/native.
        try:
            for d in os.listdir("/run/user"):
                if os.path.exists(f"/run/user/{d}/pulse/native"):
                    return True
        except OSError:
            pass
    return False


def launch(system_key: str, rom_filename: str) -> tuple[subprocess.Popen | None, str]:
    """Spawn the emulator subprocess for `system_key` and `rom_filename`.

    Returns (proc, msg). On failure proc is None and msg explains why.

    Caller is responsible for monitoring proc.poll() and killing it on
    user request.
    """
    sd = SYSTEMS.get(system_key)
    if not sd:
        return None, f"unknown system: {system_key}"

    rom_path = sd.rom_dir() / rom_filename
    if not rom_path.is_file():
        return None, f"rom not found: {rom_path}"

    binary = sd.find_binary()
    if not binary:
        candidates = ", ".join(sd.binary_candidates)
        return None, f"no emulator installed (tried {candidates})"

    # Per-system pre-launch setup. mGBA wants its own config file
    # written; PS1 emulators want per-binary CLI args + (for mednafen)
    # BIOS paths injected into mednafen.cfg.
    bin_name = os.path.basename(binary)
    sys_extra_args: tuple[str, ...] = sd.extra_args
    if sd.key == "ps1":
        sys_extra_args = _ps1_args_for_binary(bin_name)
        if "mednafen" in bin_name.lower():
            try:
                _configure_mednafen_psx_bios()
            except Exception as e:
                print(f"[emulator] mednafen bios config failed: {e}")
    if "mgba" in bin_name:
        try:
            _write_mgba_display_config()
        except Exception:
            pass
        try:
            from bigbox import retroachievements as _ra
            creds = _ra.load_creds()
            if creds:
                _ra.apply_to_mgba_config(creds)
        except Exception:
            pass

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", "/root/.Xauthority")

    # Audio routing. Detect what's actually managing the cards:
    #
    #   - If pipewire-pulse / pulseaudio is running, go through the
    #     pulse compatibility layer. Direct-to-ALSA via plughw:1,0
    #     loses to the daemon for the device and produces silence
    #     (this was the original bug — bigbox forced ALSA + plughw
    #     even though Pipewire owned the cards).
    #   - Otherwise fall back to direct ALSA on the Headphones card.
    #
    # mGBA / pcsxr both honour SDL_AUDIODRIVER, so this single env
    # decides for every supported emulator.
    # Audio routing strategy:
    #
    #   1. If pipewire-pulse is running AND has a real (non-auto_null)
    #      sink loaded → route SDL through pulse, pinned to Headphones.
    #   2. Otherwise (pulse missing OR pulse only has auto_null because
    #      its ALSA monitor hasn't loaded cards) → fall back to direct
    #      ALSA on the Headphones card. We've verified `aplay -D
    #      plughw:1,0` works even when pulse can't see the device.
    #
    # Without (2), bigbox would route to pulse, pulse would route to
    # auto_null, and the user would hear silence even though the
    # underlying ALSA hardware was fine.
    use_pulse = False
    target_sink: Optional[str] = None
    try:
        from bigbox import audio as _audio
        if _audio_daemon_running() and _audio.has_real_pulse_sink():
            target_sink = _audio.preferred_sink_for("emulator")
            use_pulse = bool(target_sink and target_sink != "auto_null")
    except Exception:
        pass

    if use_pulse and target_sink:
        env.setdefault("SDL_AUDIODRIVER", "pulse")
        for k, v in _pulse_env().items():
            env.setdefault(k, v)
        env["PULSE_SINK"] = target_sink
        for cmd_args in (
            ["pactl", "set-sink-mute", target_sink, "0"],
            ["pactl", "set-sink-volume", target_sink, "100%"],
        ):
            try:
                subprocess.run(cmd_args, check=False, timeout=2,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               env=env)
            except Exception:
                pass
    else:
        # Direct ALSA on the Headphones card. plughw lets ALSA do
        # rate/format conversion so SDL doesn't have to match the
        # card's native settings exactly.
        env.setdefault("SDL_AUDIODRIVER", "alsa")
        env.setdefault("AUDIODEV", "plughw:1,0")
        env.setdefault("ALSA_PCM_DEVICE", "plughw:1,0")
        env.setdefault("ALSA_CARD", "Headphones")
        try:
            subprocess.run(
                ["amixer", "-c", "1", "sset", "PCM", "100%", "unmute"],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass

    cmd = [binary, *sys_extra_args, str(rom_path.resolve())]

    try:
        log_fd: int | object = open(EMULATOR_LOG, "w")
        log_fd.write(f"# command: {' '.join(cmd)}\n")  # type: ignore
        log_fd.flush()  # type: ignore
    except Exception:
        log_fd = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            env=env,
        )
        return proc, f"launched {binary}"
    except Exception as e:
        return None, f"launch failed: {type(e).__name__}: {e}"
    finally:
        if log_fd is not subprocess.DEVNULL:
            try:
                log_fd.close()  # type: ignore[union-attr]
            except Exception:
                pass


def _write_mgba_display_config() -> None:
    """Pre-write mgba config.ini display + audio settings. Idempotent.
    Writes to both ~/.config/mgba/ and ~/.mgba/ for maximum compatibility."""
    import configparser
    
    # Paths to try writing to
    paths = [
        Path("/root/.config/mgba/config.ini"),
        Path("/root/.mgba/config.ini")
    ]

    for cfg_path in paths:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)

        cp = configparser.ConfigParser()
        cp.optionxform = str
        if cfg_path.exists():
            try:
                cp.read(cfg_path)
            except Exception:
                cp = configparser.ConfigParser()
                cp.optionxform = str

        # Apply to both qt and sdl ports
        for section in ("ports.qt", "ports.sdl"):
            if not cp.has_section(section):
                cp.add_section(section)
            cp.set(section, "fullscreen", "1")
            cp.set(section, "videoScale", "3")
            cp.set(section, "lockAspectRatio", "1")
            cp.set(section, "lockIntegerScaling", "0")
            cp.set(section, "resampleVideo", "1")
            cp.set(section, "audioBuffers", "2048")
            cp.set(section, "sampleRate", "44100")
            cp.set(section, "volume", "256")
            cp.set(section, "fastForwardVolume", "256")
            cp.set(section, "mute", "0")

            # Key mappings (SDL scancodes)
            cp.set(section, "keyB", "122")       # 'z' -> GBA B
            cp.set(section, "keyA", "120")       # 'x' -> GBA A
            cp.set(section, "keySelect", "8")    # backspace -> Select
            cp.set(section, "keyStart", "13")    # enter -> Start
            cp.set(section, "keyRight", "1073741903") # right arrow
            cp.set(section, "keyLeft", "1073741904")  # left arrow
            cp.set(section, "keyUp", "1073741906")    # up arrow
            cp.set(section, "keyDown", "1073741905")  # down arrow
            cp.set(section, "keyR", "115")       # 's' -> GBA R
            cp.set(section, "keyL", "97")        # 'a' -> GBA L

        with cfg_path.open("w") as f:
            cp.write(f, space_around_delimiters=False)


def read_emulator_log_tail(n: int = 8) -> list[str]:
    try:
        with EMULATOR_LOG.open("r") as f:
            lines = [ln.rstrip() for ln in f.readlines() if ln.strip()]
        return lines[-n:] if lines else []
    except Exception:
        return []
