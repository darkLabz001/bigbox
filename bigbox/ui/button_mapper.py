"""Button Mapper — Settings → System → Button Mapper.

Lets the user re-bind any pygame keysym to any logical Button without
editing TOML by hand. Persists to /etc/bigbox/buttons.toml and reloads
the live KEYMAP in-process so changes take effect immediately.

Controls:
    UP / DOWN        navigate rows (one per logical Button)
    A                bind a key to the highlighted Button (next keypress
                     wins; ESC cancels the capture)
    X                clear ALL keys bound to the highlighted Button
    Y                reset the ENTIRE keymap to bundled defaults
    B                back to Settings

Implementation notes:
    Raw-key capture: bigbox.app.run() peels off pygame.KEYDOWN before
    kbd_translate when self.app.raw_capture_callback is non-None — that's
    what lets us read keysyms the user might have un-bound (otherwise no
    Button event would fire and the view would never know).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.input import keyboard as _kbd
from bigbox.input.config import save_keymap

if TYPE_CHECKING:
    from bigbox.app import App


# Display order matches the README controls table.
_BUTTONS_ORDER: tuple[Button, ...] = (
    Button.UP, Button.DOWN, Button.LEFT, Button.RIGHT,
    Button.A, Button.B, Button.X, Button.Y,
    Button.START, Button.SELECT,
    Button.LL, Button.RR,
    Button.HK,
)


def _keysym_label(keysym: int) -> str:
    """Pretty name for a pygame keysym, e.g. K_LSHIFT → 'L Shift'."""
    name = pygame.key.name(keysym) or f"keysym-{keysym}"
    # pygame returns "left shift" / "return" / "space" / "j" — capitalize.
    return " ".join(w.capitalize() for w in name.split())


class ButtonMapperView:
    def __init__(self) -> None:
        self.idx = 0
        self.scroll = 0
        self._toast: str = ""
        self._toast_until: float = 0.0
        # capture_for: when non-None, we're waiting for the next raw key
        # press to bind to this Button. Set by handle(A) below.
        self.capture_for: Button | None = None

    # ---------- event handling ----------
    def handle(self, ev: ButtonEvent, app: App) -> None:
        if not ev.pressed:
            return
        # If we're already in capture mode, ignore further Button events
        # entirely — the raw-key callback will resolve it. (Without this,
        # the held-A that opened capture mode could repeat-fire and
        # re-arm capture on every tick.)
        if self.capture_for is not None:
            return

        if ev.button is Button.B and not ev.repeat:
            app.go_back()
            return
        if ev.button is Button.UP:
            self.idx = (self.idx - 1) % len(_BUTTONS_ORDER)
        elif ev.button is Button.DOWN:
            self.idx = (self.idx + 1) % len(_BUTTONS_ORDER)
        elif ev.button is Button.A and not ev.repeat:
            self._begin_capture(app, _BUTTONS_ORDER[self.idx])
        elif ev.button is Button.X and not ev.repeat:
            self._clear_button(_BUTTONS_ORDER[self.idx])
        elif ev.button is Button.Y and not ev.repeat:
            self._reset_all()

    def _begin_capture(self, app: App, btn: Button) -> None:
        self.capture_for = btn

        def _on_key(keysym: int | None) -> None:
            # Always clear our local capture flag first so a failed save
            # doesn't leave the UI stuck in "press a key…" forever.
            target = self.capture_for
            self.capture_for = None
            if target is None:
                return
            if keysym is None:
                self._show_toast("Cancelled")
                return
            # Bind this keysym → target Button. If the keysym was already
            # bound to a different Button, the new binding overrides it
            # (a keysym can only emit one Button at a time).
            _kbd.KEYMAP[keysym] = target
            if save_keymap(dict(_kbd.KEYMAP)):
                self._show_toast(f"{_keysym_label(keysym)} → {target.value}")
            else:
                self._show_toast("Save failed (perms?)")

        app.raw_capture_callback = _on_key

    def _clear_button(self, btn: Button) -> None:
        before = len(_kbd.KEYMAP)
        for k in [k for k, v in list(_kbd.KEYMAP.items()) if v is btn]:
            del _kbd.KEYMAP[k]
        removed = before - len(_kbd.KEYMAP)
        if removed == 0:
            self._show_toast(f"{btn.value}: nothing bound")
            return
        if save_keymap(dict(_kbd.KEYMAP)):
            self._show_toast(f"Cleared {removed} key(s) from {btn.value}")
        else:
            self._show_toast("Save failed (perms?)")

    def _reset_all(self) -> None:
        _kbd.set_keymap(_kbd.default_keymap())
        if save_keymap(dict(_kbd.KEYMAP)):
            self._show_toast("Reset to bundled defaults")
        else:
            self._show_toast("Reset (in-memory only — save failed)")

    def _show_toast(self, msg: str) -> None:
        self._toast = msg
        self._toast_until = time.monotonic() + 2.5

    # ---------- rendering ----------
    def _bindings(self) -> dict[Button, list[int]]:
        """Group the live KEYMAP by Button for display."""
        out: dict[Button, list[int]] = {b: [] for b in _BUTTONS_ORDER}
        for keysym, btn in _kbd.KEYMAP.items():
            if btn in out:
                out[btn].append(keysym)
        for k in out:
            out[k].sort(key=lambda ks: pygame.key.name(ks))
        return out

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        # Header.
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        pygame.draw.line(surf, theme.DIVIDER, (0, head.bottom - 1), (head.right, head.bottom - 1))
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        title = title_font.render("BUTTON MAPPER", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))

        hint_font = pygame.font.Font(None, theme.FS_SMALL)
        hint = hint_font.render(
            "A=bind  X=clear  Y=reset all  B=back", True, theme.FG_DIM
        )
        surf.blit(
            hint,
            (head.right - hint.get_width() - theme.PADDING,
             (head.height - hint.get_height()) // 2),
        )

        # Rows.
        body_font = pygame.font.Font(None, theme.FS_BODY)
        small_font = pygame.font.Font(None, theme.FS_SMALL)
        row_top = head.bottom + theme.PADDING
        row_h = theme.ROW_H
        max_visible = max(1, (theme.SCREEN_H - row_top - 80) // row_h)

        # Keep highlighted row in view.
        if self.idx < self.scroll:
            self.scroll = self.idx
        elif self.idx >= self.scroll + max_visible:
            self.scroll = self.idx - max_visible + 1

        bindings = self._bindings()
        # Reserve a fixed left column for the Button label so the keysym
        # column starts at a consistent x — easier to scan.
        label_col_w = 180
        for i in range(max_visible):
            row_i = self.scroll + i
            if row_i >= len(_BUTTONS_ORDER):
                break
            btn = _BUTTONS_ORDER[row_i]
            y = row_top + i * row_h
            selected = (row_i == self.idx)

            if selected:
                row_rect = pygame.Rect(
                    theme.PADDING - 6, y - 4,
                    theme.SCREEN_W - 2 * (theme.PADDING - 6), row_h,
                )
                pygame.draw.rect(surf, theme.SELECTION_BG, row_rect)

            label_color = theme.SELECTION if selected else theme.FG
            label_surf = body_font.render(btn.value, True, label_color)
            surf.blit(label_surf, (theme.PADDING, y + (row_h - label_surf.get_height()) // 2))

            keysyms = bindings.get(btn, [])
            if keysyms:
                keys_text = ", ".join(_keysym_label(k) for k in keysyms)
            else:
                keys_text = "(unbound)"
            keys_color = theme.FG if keysyms else theme.WARN
            if not keysyms and selected:
                keys_color = theme.ERR
            keys_surf = body_font.render(keys_text, True, keys_color)
            surf.blit(keys_surf,
                      (theme.PADDING + label_col_w,
                       y + (row_h - keys_surf.get_height()) // 2))

        # Scrollbar (only when there's overflow).
        if len(_BUTTONS_ORDER) > max_visible:
            sb_w = 4
            track = pygame.Rect(theme.SCREEN_W - sb_w - 4, row_top,
                                sb_w, max_visible * row_h)
            pygame.draw.rect(surf, theme.DIVIDER, track)
            thumb_h = max(20, int(track.height * max_visible / len(_BUTTONS_ORDER)))
            thumb_y = track.y + int(track.height * self.scroll / max(1, len(_BUTTONS_ORDER)))
            pygame.draw.rect(surf, theme.ACCENT_DIM,
                             pygame.Rect(track.x, thumb_y, sb_w, thumb_h))

        # Capture overlay — modal, blocks the rest of the UI until the
        # user presses a key (or ESC).
        if self.capture_for is not None:
            self._render_capture_overlay(surf, body_font, title_font)
            return

        # Footer toast (auto-fades after 2.5s).
        if self._toast and time.monotonic() < self._toast_until:
            toast = small_font.render(self._toast, True, theme.ACCENT)
            surf.blit(toast,
                      (theme.PADDING, theme.SCREEN_H - toast.get_height() - theme.PADDING))

    def _render_capture_overlay(
        self, surf: pygame.Surface,
        body_font: pygame.font.Font, title_font: pygame.font.Font,
    ) -> None:
        overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        surf.blit(overlay, (0, 0))

        box_w, box_h = 640, 240
        box = pygame.Rect(
            (theme.SCREEN_W - box_w) // 2,
            (theme.SCREEN_H - box_h) // 2,
            box_w, box_h,
        )
        pygame.draw.rect(surf, theme.BG_ALT, box)
        pygame.draw.rect(surf, theme.ACCENT, box, width=2)

        prompt = title_font.render(
            f"Press any key to bind to {self.capture_for.value}",  # type: ignore[union-attr]
            True, theme.ACCENT,
        )
        surf.blit(
            prompt,
            (box.x + (box.width - prompt.get_width()) // 2,
             box.y + theme.PADDING),
        )

        sub = body_font.render(
            "ESC = cancel · the key you press will be bound now",
            True, theme.FG_DIM,
        )
        surf.blit(
            sub,
            (box.x + (box.width - sub.get_width()) // 2,
             box.y + box.height - sub.get_height() - theme.PADDING),
        )
