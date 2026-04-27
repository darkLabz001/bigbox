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


ROMS_ROOT = Path("roms")
BIOS_ROOT = Path("bios")
EMULATOR_LOG = Path("/tmp/bigbox-emu.log")


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
        for cand in self.binary_candidates:
            if shutil.which(cand):
                return cand
        return None


SYSTEMS: dict[str, SystemDef] = {
    "gbc": SystemDef(
        key="gbc",
        label="GAME BOY / GBC",
        rom_subdir="gbc",
        extensions=(".gb", ".gbc", ".zip"),
        # mGBA handles both classic GB and GBC.
        binary_candidates=("mgba-sdl", "mgba-qt", "mgba"),
        extra_args=("-f",),  # fullscreen
    ),
    "gba": SystemDef(
        key="gba",
        label="GAME BOY ADVANCE",
        rom_subdir="gba",
        extensions=(".gba", ".zip"),
        binary_candidates=("mgba-sdl", "mgba-qt", "mgba"),
        extra_args=("-f",),
    ),
    "ps1": SystemDef(
        key="ps1",
        label="PLAYSTATION 1",
        rom_subdir="ps1",
        # .pbp is the eboot format; .chd is compressed. duckstation eats them all.
        extensions=(".bin", ".cue", ".iso", ".img", ".pbp", ".chd", ".ecm", ".m3u"),
        # duckstation-nogui (qt-less) preferred; pcsx-rearmed as fallback when
        # the user's distro doesn't have duckstation packaged.
        binary_candidates=("duckstation-nogui", "duckstation-qt",
                           "pcsx_rearmed", "pcsx-rearmed"),
        # DuckStation flag for fullscreen launch is `-fullscreen`; pcsx-rearmed
        # runs fullscreen by default. We append both — pcsx-rearmed ignores
        # unknown flags so it's safe.
        extra_args=("-fullscreen",),
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

    # Apply RetroAchievements creds to mGBA before launch (no-op if no
    # creds saved). Best-effort, never fatal.
    if binary.startswith("mgba"):
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

    cmd = [binary, *sd.extra_args, str(rom_path.resolve())]

    try:
        log_fd: int | object = open(EMULATOR_LOG, "w")
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


def read_emulator_log_tail(n: int = 8) -> list[str]:
    try:
        with EMULATOR_LOG.open("r") as f:
            lines = [ln.rstrip() for ln in f.readlines() if ln.strip()]
        return lines[-n:] if lines else []
    except Exception:
        return []
