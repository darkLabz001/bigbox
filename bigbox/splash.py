"""Animated boot splash — dark Arasaka-themed intro.

Plays in pygame with a parallel mpv audio thread. Designed to run from
App.run() *before* the main carousel loop, so the user sees this rather
than a flash of black or the carousel pre-render.

Stages, ~3.5s total:
  0.0 - 0.6 s   black with red horizontal sweep (CRT power-on)
  0.6 - 1.4 s   ARASAKA-style red diamond logo draws in
  1.4 - 2.4 s   "WELCOME TO DaRkb0x" types onto the screen
  2.4 - 3.5 s   hold + scanline shimmer + glitch tick
  exit          screen wiped, control returns to caller

Aesthetic: pure red on near-black, monospace, scanlines, mild jitter.
No external assets beyond the audio file at assets/boot.mp3 (copied from
the user's Downloads in scripts/install.sh).
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pygame


# Arasaka palette — red / blood / charcoal
ARA_BG = (8, 6, 8)
ARA_BG2 = (16, 8, 10)
ARA_RED = (220, 30, 40)
ARA_RED_DIM = (110, 14, 22)
ARA_RED_BRIGHT = (255, 80, 90)
ARA_BONE = (220, 220, 210)


# Resolved at runtime; works whether bigbox is in /opt/bigbox or a dev
# checkout, and never blocks startup if the asset is missing.
_ASSET_CANDIDATES = (
    Path("/opt/bigbox/assets/boot.mp3"),
    Path(__file__).resolve().parents[1] / "assets" / "boot.mp3",
)


def _find_audio() -> Path | None:
    for p in _ASSET_CANDIDATES:
        if p.exists():
            return p
    return None


def _play_audio_async(path: Path) -> subprocess.Popen | None:
    """Best-effort. Tries mpv first, falls back to aplay (which doesn't
    decode mp3) silently. Returns the Popen (or None) so the caller can
    terminate it if the splash gets cut short."""
    if shutil.which("mpv"):
        try:
            return subprocess.Popen(
                [
                    "mpv", "--no-config",
                    "--no-video",
                    "--ao=alsa,pulse,null",
                    "--really-quiet",
                    "--audio-display=no",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return None
    return None


def _draw_scanlines(surf: pygame.Surface) -> None:
    w, h = surf.get_size()
    line = pygame.Surface((w, 1), pygame.SRCALPHA)
    line.fill((0, 0, 0, 70))
    for y in range(0, h, 2):
        surf.blit(line, (0, y))


def _draw_arasaka_mark(surf: pygame.Surface,
                       cx: int, cy: int,
                       size: int,
                       progress: float,
                       color=ARA_RED) -> None:
    """Diamond + horizontal bars. progress 0..1 reveals it gradually."""
    progress = max(0.0, min(1.0, progress))
    half = int(size * progress)
    if half <= 0:
        return
    # outer diamond (filled with bg, outlined red)
    pts = [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)]
    pygame.draw.polygon(surf, ARA_BG2, pts)
    pygame.draw.polygon(surf, color, pts, 3)
    # inner diamond
    half2 = int(size * 0.55 * progress)
    if half2 > 0:
        pts2 = [(cx, cy - half2), (cx + half2, cy),
                (cx, cy + half2), (cx - half2, cy)]
        pygame.draw.polygon(surf, color, pts2, 2)
    # horizontal cross-bars (the trademark Arasaka look)
    bar_w = int(size * 1.6 * progress)
    pygame.draw.line(surf, color, (cx - bar_w // 2, cy), (cx + bar_w // 2, cy), 2)


def _draw_glitch_tick(surf: pygame.Surface, t: float) -> None:
    """Random horizontal slices shifted left/right for a CRT-glitch feel."""
    if random.random() > 0.18:
        return
    w, h = surf.get_size()
    for _ in range(random.randint(1, 3)):
        y = random.randint(0, h - 1)
        slice_h = random.randint(2, 6)
        shift = random.randint(-8, 8)
        if y + slice_h > h:
            slice_h = h - y
        sub = surf.subsurface((0, y, w, slice_h)).copy()
        surf.blit(sub, (shift, y))


def _typewriter(text: str, t: float, t_start: float, duration: float) -> str:
    if t < t_start:
        return ""
    elapsed = t - t_start
    frac = max(0.0, min(1.0, elapsed / duration))
    n = int(len(text) * frac)
    return text[:n]


# Cyberpunk-style boot log lines that scroll on after the title appears.
# Timed to fill the gap until the audio chime (~15s) ends. Each gets
# typewritten one at a time.
_BOOT_LOG = [
    "[OK]   kernel 6.12.75 / aarch64",
    "[OK]   gpio bus initialized",
    "[OK]   alsa: 3.5mm out / 100%",
    "[OK]   nl80211 wireless stack",
    "[OK]   bluez bluetooth stack",
    "[OK]   aircrack-ng suite ready",
    "[OK]   wigle uplink: provisioned",
    "[OK]   gps watchdog: armed",
    "[OK]   web ui: 0.0.0.0:8080",
    "[OK]   arasaka net: secure",
    "[INIT] handing control to user...",
]


def play(screen: pygame.Surface, total_seconds: float = 14.5) -> None:
    """Run the splash on `screen` for `total_seconds`, then return.

    Default is 14.5s to match the bundled boot.mp3 chime. The visible
    sequence is:
      0.0 - 0.6 s   CRT power-on sweep
      0.6 - 1.4 s   Arasaka diamond reveal
      1.4 - 2.4 s   "WELCOME TO DaRkb0x" typewriter
      2.0 - 2.5 s   "BOOTSTRAPPING SECURE NODE..." subtitle
      3.0 - 13.0 s  boot log lines roll on, one every ~0.9 s
      13.0 +        title + log hold, fade out
    """
    audio_path = _find_audio()
    audio_proc = _play_audio_async(audio_path) if audio_path else None

    w, h = screen.get_size()
    cx, cy = w // 2, h // 2

    try:
        f_title = pygame.font.Font(None, 56)
    except Exception:
        f_title = pygame.font.SysFont("monospace", 48)
    try:
        f_sub = pygame.font.Font(None, 22)
    except Exception:
        f_sub = pygame.font.SysFont("monospace", 18)
    try:
        f_corp = pygame.font.Font(None, 18)
    except Exception:
        f_corp = pygame.font.SysFont("monospace", 14)
    try:
        f_log = pygame.font.Font(None, 18)
    except Exception:
        f_log = pygame.font.SysFont("monospace", 14)

    clock = pygame.time.Clock()
    start = time.time()
    title_text = "WELCOME TO DaRkb0x"
    sub_text = "BOOTSTRAPPING SECURE NODE..."

    # Boot log timing: first line appears at t=3.0, one every 0.9s, with
    # a per-line typewrite of 0.3s.
    log_start = 3.0
    log_step = 0.9
    log_typewrite = 0.3

    while True:
        t = time.time() - start
        if t >= total_seconds:
            break

        screen.fill(ARA_BG)

        # Stage 1 — CRT power-on red sweep (0.0..0.6s)
        if t < 0.6:
            sweep_y = int((t / 0.6) * h)
            pygame.draw.rect(screen, ARA_RED_DIM, (0, sweep_y - 4, w, 8))
            pygame.draw.line(screen, ARA_RED_BRIGHT, (0, sweep_y), (w, sweep_y), 2)
        else:
            # Faint full-screen vignette for stages 2..4
            for ring in range(0, 80, 8):
                alpha = max(0, 30 - ring // 4)
                pygame.draw.rect(screen, (alpha, 0, 0),
                                 (ring, ring, w - 2 * ring, h - 2 * ring), 1)

        # Stage 2 — Arasaka mark (0.6..1.4s, then holds)
        if t >= 0.6:
            mark_progress = min(1.0, (t - 0.6) / 0.8)
            # After the mark fully draws, sit it slightly higher so the
            # boot log has room beneath the title.
            mark_y = cy - 80 if t > 3.0 else cy - 40
            _draw_arasaka_mark(screen, cx, mark_y, 60,
                               mark_progress, color=ARA_RED)

        # Stage 3 — Title typewriter (1.4..2.4s)
        title_y = cy + 10 if t > 3.0 else cy + 50
        if t >= 1.4:
            shown = _typewriter(title_text, t, 1.4, 1.0)
            ts = f_title.render(shown, True, ARA_RED_BRIGHT)
            screen.blit(ts, (cx - ts.get_width() // 2, title_y))

            # Subtitle below title
            if t >= 2.0:
                sub_shown = _typewriter(sub_text, t, 2.0, 0.5)
                ss = f_sub.render(sub_shown, True, ARA_RED)
                screen.blit(ss, (cx - ss.get_width() // 2, title_y + 60))

        # Stage 4 — Boot log roll (3.0s onwards)
        if t >= log_start:
            log_x = 60
            log_y = cy + 100
            line_h = f_log.get_linesize() + 2
            for i, line in enumerate(_BOOT_LOG):
                line_t = log_start + i * log_step
                if t < line_t:
                    break
                shown = _typewriter(line, t, line_t, log_typewrite)
                # First two chars get a brighter color (the [OK]/[INIT] tag)
                tag_end = shown.find("]")
                if tag_end != -1 and tag_end < len(shown):
                    tag = shown[: tag_end + 1]
                    rest = shown[tag_end + 1:]
                    color = (ARA_RED_BRIGHT if "INIT" in tag or "ERR" in tag
                             else ARA_BONE)
                    tag_surf = f_log.render(tag, True, color)
                    rest_surf = f_log.render(rest, True, ARA_RED_DIM)
                    screen.blit(tag_surf, (log_x, log_y + i * line_h))
                    screen.blit(rest_surf,
                                (log_x + tag_surf.get_width(),
                                 log_y + i * line_h))
                else:
                    ls = f_log.render(shown, True, ARA_RED_DIM)
                    screen.blit(ls, (log_x, log_y + i * line_h))

        # Bottom-left version stamp
        stamp = f_corp.render("[ARASAKA::DaRkb0x::v0.1]", True, ARA_RED_DIM)
        screen.blit(stamp, (12, h - 26))

        # Top-right blinking cursor
        if int(t * 4) % 2 == 0:
            block = f_sub.render(">", True, ARA_RED_BRIGHT)
            screen.blit(block, (w - 20, 8))

        _draw_scanlines(screen)
        _draw_glitch_tick(screen, t)

        pygame.display.flip()
        clock.tick(60)

    # Fade-out: brief darken before returning, so the carousel doesn't
    # snap-cut in.
    fade = pygame.Surface((w, h))
    fade.fill((0, 0, 0))
    for alpha in range(0, 255, 25):
        fade.set_alpha(alpha)
        screen.fill(ARA_BG)
        screen.blit(fade, (0, 0))
        pygame.display.flip()
        pygame.time.wait(20)

    # If audio is still playing, let it run — it's short (psx.mp3 is the
    # PlayStation chime, ~3-4s). Don't terminate it; just let the OS
    # reap the process when it finishes.
    if audio_proc and audio_proc.poll() is None:
        # Detach: don't wait, but if the user smashes a key we still
        # want to be able to kill it. App.run() doesn't track this, so
        # we leak the Popen handle by design — kernel reaps on exit.
        pass
