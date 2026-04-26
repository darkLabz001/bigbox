"""Logical input events.

The UI never sees raw GPIO or keyboard events — every input source translates
to one of these names and pushes it into the central queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from queue import Queue


class Button(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    A = "A"
    B = "B"
    X = "X"
    Y = "Y"
    START = "START"
    SELECT = "SELECT"
    HK = "HK"
    LL = "LL"
    RR = "RR"


@dataclass(frozen=True)
class ButtonEvent:
    button: Button
    pressed: bool   # False = released, also used for key-repeat ticks
    repeat: bool = False


class EventBus:
    """Thread-safe queue. Producers (GPIO/keyboard) put, the UI loop gets."""

    def __init__(self) -> None:
        self._q: Queue[ButtonEvent] = Queue()

    def put(self, ev: ButtonEvent) -> None:
        self._q.put(ev)

    def drain(self) -> list[ButtonEvent]:
        out: list[ButtonEvent] = []
        while not self._q.empty():
            out.append(self._q.get_nowait())
        return out
