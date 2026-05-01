"""Process-wide font cache for pygame.

Several views re-create `pygame.font.Font(None, N)` inside their
render() methods. On the GamePi43 we render at 30 fps, so a font is
parsed-from-disk 30+ times per second per call site. strace showed
~15 000 lseek/read syscalls per second against the TTF file — the
single biggest CPU hog after pygame's blit pipeline.

Importing this module monkey-patches pygame.font.Font so any call
with the same (name, size) hits an in-memory cache. Idempotent: if
the wrapper is already in place, importing again is a no-op. Complex
calls (with extra args/kwargs) bypass the cache and use the original
constructor unchanged.
"""
from __future__ import annotations

import pygame


def _install() -> None:
    if getattr(pygame.font.Font, "__bigbox_cached__", False):
        return

    _orig = pygame.font.Font
    _cache: dict[tuple[object, int], pygame.font.Font] = {}

    def _cached_font(name=None, size=12, *args, **kwargs):
        # Ensure pygame.font is initialized before attempting to create a Font.
        # This prevents "pygame.error: font not initialized" if a Font is 
        # instantiated before App._init_display() is called.
        if not pygame.font.get_init():
            pygame.font.init()

        # Only cache the common simple form. Anything fancier (custom
        # bold/italic args from a future pygame, BytesIO sources, etc.)
        # falls through to the real constructor.
        if not args and not kwargs:
            key = (name, int(size))
            cached = _cache.get(key)
            if cached is not None:
                return cached
            try:
                font = _orig(name, int(size))
            except Exception:
                # On any error, don't poison the cache.
                return _orig(name, int(size))
            _cache[key] = font
            return font
        return _orig(name, size, *args, **kwargs)

    _cached_font.__bigbox_cached__ = True  # type: ignore[attr-defined]
    pygame.font.Font = _cached_font  # type: ignore[assignment]


_install()
