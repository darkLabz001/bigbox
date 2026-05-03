"""Audio control — pipewire-pulse and ALSA paths in one API.

bigbox.service runs as root; pipewire-pulse runs under the desktop
user. :func:`_pulse_env` (mirrored from bigbox.emulator) resolves
that mismatch so pactl can talk to the right socket.

Public API:
    list_sinks() -> list[Sink]
    current_sink() -> str | None
    set_default_sink(name) -> bool
    get_volume_percent() -> int | None
    set_volume_percent(pct: int) -> bool
    nudge_volume(delta_pct: int) -> int | None
    toggle_mute() -> bool | None        # returns new mute state

Each function autodetects the running daemon. ALSA fallback uses
amixer on Card 1 (Headphones), matching the previous bigbox behavior.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class Sink:
    name: str          # pulse sink internal name
    description: str   # human label
    is_default: bool


def _audio_daemon_running() -> bool:
    try:
        out = subprocess.check_output(
            ["pgrep", "-x", "-f", "pipewire-pulse|pulseaudio"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        )
        return bool(out.strip())
    except Exception:
        try:
            for d in os.listdir("/run/user"):
                if os.path.exists(f"/run/user/{d}/pulse/native"):
                    return True
        except OSError:
            pass
    return False


def _pulse_env() -> dict:
    out = {}
    try:
        for d in sorted(os.listdir("/run/user")):
            sock = f"/run/user/{d}/pulse/native"
            if os.path.exists(sock):
                out["PULSE_SERVER"] = f"unix:{sock}"
                out["XDG_RUNTIME_DIR"] = f"/run/user/{d}"
                break
    except OSError:
        pass
    return out


def _pactl(args: list[str]) -> tuple[bool, str]:
    env = {**os.environ, **_pulse_env()}
    try:
        out = subprocess.check_output(
            ["pactl", *args], text=True, stderr=subprocess.STDOUT,
            timeout=2, env=env,
        )
        return True, out
    except FileNotFoundError:
        return False, "pactl not installed"
    except subprocess.CalledProcessError as e:
        return False, e.output or str(e)
    except Exception as e:
        return False, str(e)


# ---------- discovery --------------------------------------------------------

def list_sinks() -> list[Sink]:
    """Return every pulse sink with its description and whether it's
    currently the default. Empty list on ALSA-only systems."""
    if not _audio_daemon_running():
        return []
    default = current_sink() or ""
    out: list[Sink] = []
    ok, txt = _pactl(["list", "short", "sinks"])
    if not ok:
        return []
    # Each line: "<id>\t<name>\t<driver>\t<format>\t<state>"
    sink_names = [line.split("\t")[1] for line in txt.splitlines()
                  if "\t" in line]
    # Pull descriptions from `pactl list sinks` (long form). Pipewire
    # often gives every Pi sink the same generic "Built-in Audio
    # Stereo" Description; alsa.card_name is the useful field
    # ("bcm2835 HDMI 1" vs "bcm2835 Headphones") so we prefer it.
    desc_by_name: dict[str, str] = {}
    ok2, longtxt = _pactl(["list", "sinks"])
    if ok2:
        cur_name: Optional[str] = None
        cur_desc: Optional[str] = None
        cur_card: Optional[str] = None
        def _commit() -> None:
            if cur_name:
                desc_by_name[cur_name] = cur_card or cur_desc or cur_name
        for line in longtxt.splitlines():
            stripped = line.strip()
            if stripped.startswith("Name: "):
                _commit()
                cur_name = stripped[len("Name: "):]
                cur_desc = None
                cur_card = None
            elif stripped.startswith("Description: "):
                cur_desc = stripped[len("Description: "):]
            elif stripped.startswith("alsa.card_name = "):
                cur_card = stripped[len("alsa.card_name = "):].strip('"')
        _commit()
    for name in sink_names:
        out.append(Sink(name=name,
                        description=desc_by_name.get(name, name),
                        is_default=(name == default)))
    return out


def current_sink() -> Optional[str]:
    if not _audio_daemon_running():
        return None
    ok, txt = _pactl(["get-default-sink"])
    if not ok:
        return None
    return txt.strip() or None


def set_default_sink(name: str) -> bool:
    if not _audio_daemon_running():
        return False
    ok, _ = _pactl(["set-default-sink", name])
    return ok


def has_real_pulse_sink() -> bool:
    """True iff pipewire-pulse has at least one non-auto_null sink.
    Pipewire's ALSA monitor sometimes hasn't loaded the cards (or has
    crashed); when only auto_null exists, every pulse stream silently
    goes to /dev/null. Use this to decide whether to fall back to
    direct-ALSA paths."""
    return any(s.name != "auto_null" for s in list_sinks())


def preferred_sink_for(role: str) -> Optional[str]:
    """Pick the sink best suited for ``role``. Currently:
      "emulator" / "media" → Headphones if present (handheld bigbox
                              normally uses the 3.5 mm jack), else
                              first real sink.
    Returns the sink name or None if no real sink exists."""
    if not _audio_daemon_running():
        return None
    sinks = list_sinks()
    if not sinks:
        return None
    real = [s for s in sinks if s.name != "auto_null"]
    pool = real or sinks
    if role in ("emulator", "media"):
        chosen = next(
            (s for s in pool if "headphone" in s.description.lower()),
            pool[0],
        )
        return chosen.name
    return (real[0].name if real else sinks[0].name)


def ensure_real_sink() -> Optional[str]:
    """If the default sink is pipewire's ``auto_null`` (the virtual
    "no real output" fallback — selected when bigbox starts before
    the ALSA cards register), switch to a real one. Prefers the
    Headphones jack so emulators are audible without HDMI plugged in;
    falls back to whatever real sink exists.

    Returns the name of the active default sink after this call (or
    None if nothing real exists)."""
    if not _audio_daemon_running():
        return None
    cur = current_sink()
    sinks = list_sinks()
    real = [s for s in sinks if s.name != "auto_null"]
    if not real:
        return cur
    # If the current default is already a real sink, keep it.
    if cur and cur != "auto_null":
        return cur
    # Pick Headphones over HDMI — handheld bigbox is normally used
    # with the 3.5 mm jack, not external display audio.
    chosen = next(
        (s for s in real if "headphone" in s.description.lower()),
        real[0],
    )
    if set_default_sink(chosen.name):
        return chosen.name
    return cur


# ---------- volume -----------------------------------------------------------

def get_volume_percent() -> Optional[int]:
    if _audio_daemon_running():
        ok, txt = _pactl(["get-sink-volume", "@DEFAULT_SINK@"])
        if ok:
            m = re.search(r"(\d+)%", txt)
            if m:
                return int(m.group(1))
        return None
    # ALSA fallback
    try:
        out = subprocess.check_output(
            ["amixer", "-c", "1", "sget", "PCM"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        )
        m = re.search(r"\[(\d+)%\]", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def set_volume_percent(pct: int) -> bool:
    pct = max(0, min(150, pct))
    if _audio_daemon_running():
        ok, _ = _pactl(["set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
        return ok
    try:
        subprocess.run(
            ["amixer", "-c", "1", "sset", "PCM", f"{pct}%"],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def nudge_volume(delta_pct: int) -> Optional[int]:
    cur = get_volume_percent()
    if cur is None:
        return None
    new = max(0, min(150, cur + delta_pct))
    if set_volume_percent(new):
        return new
    return None


def toggle_mute() -> Optional[bool]:
    """Returns the new mute state (True=muted) or None on failure."""
    if _audio_daemon_running():
        ok, _ = _pactl(["set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        if not ok:
            return None
        ok2, txt = _pactl(["get-sink-mute", "@DEFAULT_SINK@"])
        if ok2:
            return "yes" in txt.lower()
        return None
    try:
        subprocess.run(
            ["amixer", "-c", "1", "sset", "PCM", "toggle"],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Best-effort: read back the new state.
        out = subprocess.check_output(
            ["amixer", "-c", "1", "sget", "PCM"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        )
        return "[off]" in out
    except Exception:
        return None
