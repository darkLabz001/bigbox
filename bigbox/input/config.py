from __future__ import annotations
import sys
from dataclasses import dataclass
from pathlib import Path
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from bigbox.events import Button
from bigbox.input.probe import get_best_config_path

@dataclass(frozen=True)
class ButtonConfig:
    pins: dict[Button, int]
    debounce_ms: int = 30
    repeat_delay_ms: int = 400
    repeat_interval_ms: int = 90

_ETC_OVERRIDE = Path("/etc/bigbox/buttons.toml")

def load_button_config(path: Path | None = None) -> ButtonConfig:
    if path:
        p = path
    elif _ETC_OVERRIDE.is_file():
        p = _ETC_OVERRIDE
    else:
        p = get_best_config_path()
        
    raw = tomllib.loads(p.read_text())
    pins_raw = raw.get("pins", {})
    pins: dict[Button, int] = {}
    for name, pin in pins_raw.items():
        try:
            pins[Button(name.upper())] = int(pin)
        except ValueError:
            continue
    behavior = raw.get("behavior", {})
    return ButtonConfig(
        pins=pins,
        debounce_ms=int(behavior.get("debounce_ms", 30)),
        repeat_delay_ms=int(behavior.get("repeat_delay_ms", 400)),
        repeat_interval_ms=int(behavior.get("repeat_interval_ms", 90)),
    )
