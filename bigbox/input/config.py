"""Loads config/buttons.toml into a typed config object."""
from __future__ import annotations

import sys
from dataclasses import dataclass
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


_ETC_OVERRIDE = Path("/etc/bigbox/buttons.toml")


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
    return ButtonConfig(
        pins=pins,
        debounce_ms=int(behavior.get("debounce_ms", 30)),
        repeat_delay_ms=int(behavior.get("repeat_delay_ms", 400)),
        repeat_interval_ms=int(behavior.get("repeat_interval_ms", 90)),
    )
