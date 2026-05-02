__version__ = "0.1.0"

# Install the pygame.font.Font cache as soon as anything in this
# package is imported. Without this, scripts/tests that import a UI
# module before app.py runs get the un-monkey-patched constructor and
# silently regress to per-frame TTF reads.
from bigbox import _font_cache as _font_cache  # noqa: F401
