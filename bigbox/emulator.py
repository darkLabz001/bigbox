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

class InputInjector:
    """Creates a virtual keyboard via uinput to feed bigbox ButtonEvents into the emulator."""
    
    def __init__(self):
        self.ui: Optional[UInput] = None
        if not HAS_EVDEV:
            return
            
        # Map bigbox buttons to standard emulator keys
        from bigbox.events import Button
        self.keymap = {
            Button.UP: e.KEY_UP,
            Button.DOWN: e.KEY_DOWN,
            Button.LEFT: e.KEY_LEFT,
            Button.RIGHT: e.KEY_RIGHT,
            Button.A: e.KEY_X,          # GBA A -> KEY_X (matches RetroArch default)
            Button.B: e.KEY_Z,          # GBA B -> KEY_Z
            Button.X: e.KEY_A,          # GBA L -> KEY_A
            Button.Y: e.KEY_S,          # GBA R -> KEY_S
            Button.LL: e.KEY_A,
            Button.RR: e.KEY_S,
            Button.START: e.KEY_ENTER,
            Button.SELECT: e.KEY_BACKSPACE,
        }
        
        events = {e.EV_KEY: list(self.keymap.values())}
        try:
            self.ui = UInput(events, name="bigbox-virtual-gamepad")
        except Exception as ex:
            print(f"[emulator] Failed to create UInput: {ex}")

    def inject(self, btn, pressed: bool):
        if not self.ui or btn not in self.keymap:
            return
        
        try:
            self.ui.write(e.EV_KEY, self.keymap[btn], 1 if pressed else 0)
            self.ui.syn()
        except Exception:
            pass

    def close(self):
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
        # mednafen takes "-fs 1" (two args) for fullscreen; DuckStation
        # uses "-fullscreen". Each of these is harmless to the others
        # because mednafen ignores unknown flags and DuckStation accepts
        # extras.
        extra_args=("-fs", "1", "-fullscreen"),
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

    # mGBA setup: pre-write display + audio config, then layer in any
    # RetroAchievements creds. Both are best-effort.
    bin_name = os.path.basename(binary)
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
    # Force the emulator's ALSA usage onto the Headphones card (Card 1).
    env.setdefault("SDL_AUDIODRIVER", "alsa")
    env.setdefault("AUDIODEV", "hw:1,0")
    try:
        # Pre-bump system volume for card 1 (Headphones)
        subprocess.run(["amixer", "-c", "1", "sset", "Headphones", "100%"], capture_output=True)
        subprocess.run(["amixer", "-c", "1", "sset", "PCM", "100%"], capture_output=True)
    except:
        pass

    cmd = [binary, *sd.extra_args, str(rom_path.resolve())]

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
