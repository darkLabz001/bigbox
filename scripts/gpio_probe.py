#!/usr/bin/env python3
"""Watch every general-purpose BCM GPIO pin and log every transition.

Run as root. While it's running, press each button in turn — its BCM pin
appears in the log at the moment of the press. Used to map GamePi43 button
revs against an unknown layout.

Output format (one event per line, fields space-separated):
    SECONDS_SINCE_START  GPIO<n>  PRESS|RELEASE
"""
from __future__ import annotations

import sys
import time

import lgpio

# Pins to skip:
#   0, 1   I2C0 ID EEPROM (HAT detection) — claiming breaks future detection.
#   14, 15 default UART; if console is on, claiming kicks the console off.
SKIP = {0, 1}

CANDIDATES = [p for p in range(28) if p not in SKIP]


def main() -> int:
    chip = lgpio.gpiochip_open(0)
    opened: list[int] = []
    for p in CANDIDATES:
        try:
            lgpio.gpio_claim_input(chip, p, lgpio.SET_PULL_UP)
            opened.append(p)
        except lgpio.error as e:
            print(f"# skip GPIO{p}: {e}", file=sys.stderr, flush=True)

    states = {p: lgpio.gpio_read(chip, p) for p in opened}
    print(f"# watching {len(opened)} pins: {opened}", flush=True)
    print("# press buttons now; Ctrl-C to stop", flush=True)

    t0 = time.monotonic()
    try:
        while True:
            for p in opened:
                cur = lgpio.gpio_read(chip, p)
                if cur != states[p]:
                    ev = "PRESS" if cur == 0 else "RELEASE"
                    print(f"{time.monotonic() - t0:7.3f}  GPIO{p:02d}  {ev}", flush=True)
                    states[p] = cur
            time.sleep(0.003)
    except KeyboardInterrupt:
        pass
    finally:
        for p in opened:
            try:
                lgpio.gpio_free(chip, p)
            except lgpio.error:
                pass
        lgpio.gpiochip_close(chip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
