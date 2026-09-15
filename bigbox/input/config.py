"""Loads config/buttons.toml into a typed config object."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:
    import tomli as tomllib  # type: ignore[import-not-found]

from bigbox.events import Button


@dataclass(frozen=True)
class ButtonConfig:
    pins: dict[Button, int]            # Button -> BCM pin
    debounce_ms: int = 30
    repeat_delay_ms: int = 400
    repeat_interval_ms: int = 90
    keyboard_mode: str = "default"     # "default" | "pocketterm"
    gpio_enabled: bool = True
    keymap: dict[int, Button] = field(default_factory=dict)  # keysym int -> Button


_ETC_OVERRIDE = Path("/etc/bigbox/buttons.toml")


def _resolve_keysym(name: str) -> int | None:
    """Turn a pygame keysym name (e.g. "j", "return", "lshift") into the
    pygame.K_* int. Returns None for unknown names."""
    import pygame
    try:
        return pygame.key.key_code(name)
    except Exception:
        pass
    attr = f"K_{name.lower()}"
    return getattr(pygame, attr, None)


def _bundled_path() -> Path:
    # config/buttons.toml relative to repo root (two levels up from this file).
    return Path(__file__).resolve().parents[2] / "config" / "buttons.toml"


def _resolve_path() -> Path:
    """Pick the active config file. /etc/bigbox/buttons.toml wins if present
    so a user's hand-tuned pin map survives OTA git resets that overwrite the
    bundled default."""
    if _ETC_OVERRIDE.is_file():
        return _ETC_OVERRIDE
    return _bundled_path()


def load_button_config(path: Path | None = None) -> ButtonConfig:
    p = path or _resolve_path()
    raw = tomllib.loads(p.read_text())
    pins_raw = raw.get("pins", {})
    pins: dict[Button, int] = {}
    for name, pin in pins_raw.items():
        try:
            pins[Button(name.upper())] = int(pin)
        except ValueError:
            # Unknown button name in the TOML — ignore so user typos don't crash.
            continue
    behavior = raw.get("behavior", {})
    inp = raw.get("input", {})

    keymap_raw = raw.get("keymap", {})
    keymap: dict[int, Button] = {}
    for key_name, btn_name in keymap_raw.items():
        keysym = _resolve_keysym(str(key_name))
        if keysym is None:
            continue
        try:
            keymap[keysym] = Button(str(btn_name).upper())
        except ValueError:
            continue

    return ButtonConfig(
        pins=pins,
        debounce_ms=int(behavior.get("debounce_ms", 30)),
        repeat_delay_ms=int(behavior.get("repeat_delay_ms", 400)),
        repeat_interval_ms=int(behavior.get("repeat_interval_ms", 90)),
        keyboard_mode=str(inp.get("keyboard_mode", "default")),
        gpio_enabled=bool(inp.get("gpio_enabled", True)),
        keymap=keymap,
    )


def save_keymap(keymap: dict[int, Button]) -> bool:
    """Atomically write a full keysym→Button keymap to /etc/bigbox/buttons.toml.

    Preserves any [pins]/[input]/[behavior] sections from the existing file
    (or the bundled default if no /etc copy exists yet). Returns True on
    success, False on any IO/permissions error."""
    import pygame

    target = _ETC_OVERRIDE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[input/config] cannot create {target.parent}: {e}")
        return False

    existing: dict = {}
    for src in (target, _bundled_path()):
        if src.is_file():
            try:
                existing = tomllib.loads(src.read_text())
                break
            except Exception:
                continue

    pins = existing.get("pins", {})
    behavior = existing.get("behavior", {})
    inp = existing.get("input", {})

    lines: list[str] = [
        "# bigbox button mapper — written by Settings → System → Button Mapper.",
        "# A non-empty [keymap] here REPLACES the bundled defaults in",
        "# bigbox/input/keyboard.py. Edit by hand or wipe to restore defaults.",
        "",
        "[keymap]",
    ]
    for keysym in sorted(keymap.keys(), key=lambda k: pygame.key.name(k)):
        name = pygame.key.name(keysym).replace('"', '\\"')
        btn = keymap[keysym].value
        lines.append(f'"{name}" = "{btn}"')
    lines.append("")

    if pins:
        lines.append("[pins]")
        for k, v in pins.items():
            lines.append(f"{k} = {int(v)}")
        lines.append("")

    lines.append("[behavior]")
    lines.append(f"debounce_ms        = {int(behavior.get('debounce_ms', 30))}")
    lines.append(f"repeat_delay_ms    = {int(behavior.get('repeat_delay_ms', 400))}")
    lines.append(f"repeat_interval_ms = {int(behavior.get('repeat_interval_ms', 90))}")
    lines.append("")

    if inp:
        lines.append("[input]")
        for k, v in inp.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            else:
                lines.append(f'{k} = "{v}"')
        lines.append("")

    content = "\n".join(lines)
    tmp = target.with_suffix(".toml.tmp")
    try:
        tmp.write_text(content)
        import os
        os.replace(tmp, target)
        return True
    except Exception as e:
        print(f"[input/config] save_keymap write failed: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def pocketterm_keyboard_present(base: Path | None = None) -> bool:
    """True when a Waveshare PocketTerm35 is detected.

    The handheld's controls are an RP2040 that enumerates as a USB HID
    keyboard with vendor 1209 / product 0001. When it's on the bus we switch
    to the "pocketterm" keyboard profile and disable the (absent, pin-
    clashing) GPIO driver automatically — no config file required.
    """
    base = base or Path("/sys/bus/usb/devices")
    if not base.is_dir():
        return False
    for vf in base.glob("*/idVendor"):
        try:
            if vf.read_text().strip() != "1209":
                continue
            pf = vf.parent / "idProduct"
            if pf.exists() and pf.read_text().strip() == "0001":
                return True
        except OSError:
            continue
    return False
