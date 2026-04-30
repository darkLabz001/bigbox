"""GamePi43 GPIO button driver.

Translates GPIO edge events into ButtonEvent and pushes them onto the EventBus.
Repeats are generated for D-pad UP/DOWN/LEFT/RIGHT only — face buttons never
auto-repeat (avoids accidental double-activations).
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from bigbox.events import Button, ButtonEvent, EventBus

if TYPE_CHECKING:
    from bigbox.input.config import ButtonConfig


_REPEATABLE = {Button.UP, Button.DOWN, Button.LEFT, Button.RIGHT}


class GPIOInput:
    def __init__(self, bus: EventBus, cfg: "ButtonConfig") -> None:
        self._bus = bus
        self._cfg = cfg
        self._buttons: dict[Button, object] = {}    # gpiozero.Button instances
        self._held_since: dict[Button, float] = {}
        self._repeater: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        # Force lgpio factory (modern, supports Pi 4/5 better on Kali/Debian)
        import os
        os.environ["GPIOZERO_PIN_FACTORY"] = "lgpio"
        
        # Imported here so dev mode (no Pi) doesn't need gpiozero at import time.
        from gpiozero import Button as GZButton  # type: ignore[import-not-found]
        from gpiozero import Device
        print(f"[input] GPIO starting (factory: {Device.pin_factory.__class__.__name__})")

        for btn, pin in self._cfg.pins.items():
            # Pin 2 (I2C SDA) and 3 (I2C SCL) have hardware pull-ups on the Pi.
            # gpiozero can fail or act weird if we try to set a software pull-up on them.
            use_pull_up = True
            if pin in (2, 3):
                use_pull_up = False
                
            gz = GZButton(
                pin,
                pull_up=use_pull_up,
                bounce_time=self._cfg.debounce_ms / 1000.0,
            )
            # We use a default-arg lambda to capture 'btn' from the loop scope.
            # We also take an optional 'device' arg to ignore the GZButton
            # instance that gpiozero passes to its callbacks.
            gz.when_pressed = lambda _d, b=btn: self._on_press(b)
            gz.when_released = lambda _d, b=btn: self._on_release(b)
            self._buttons[btn] = gz

        self._repeater = threading.Thread(target=self._repeat_loop, daemon=True)
        self._repeater.start()

    def stop(self) -> None:
        self._stop.set()
        for gz in self._buttons.values():
            try:
                gz.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._buttons.clear()

    def _on_press(self, b: Button) -> None:
        print(f"[input] GPIO press: {b}")
        self._held_since[b] = time.monotonic()
        self._bus.put(ButtonEvent(b, pressed=True))

    def _on_release(self, b: Button) -> None:
        print(f"[input] GPIO release: {b}")
        self._held_since.pop(b, None)
        self._bus.put(ButtonEvent(b, pressed=False))

    def _repeat_loop(self) -> None:
        delay = self._cfg.repeat_delay_ms / 1000.0
        interval = self._cfg.repeat_interval_ms / 1000.0
        last_fire: dict[Button, float] = {}
        while not self._stop.is_set():
            now = time.monotonic()
            for btn, t0 in list(self._held_since.items()):
                if btn not in _REPEATABLE:
                    continue
                if now - t0 < delay:
                    continue
                if now - last_fire.get(btn, 0.0) >= interval:
                    last_fire[btn] = now
                    self._bus.put(ButtonEvent(btn, pressed=True, repeat=True))
            time.sleep(0.01)
