"""EXIF inspector — pick an image from media/captures or loot/osint/images,
show camera/timestamp/GPS metadata.

Uses Pillow if available (already a soft dep on this image), with
``exiftool`` as a stdlib-free fallback. Renders a tiny static OSM
preview URL for any image with GPS coords so the user can paste it.

For dropping new images: web UI's existing /upload endpoint hands
files to media/<folder>; user can also scp into ``loot/osint/images/``
which the view also scans.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


SOURCES = (
    Path("loot/osint/images"),
    Path("media/captures"),
    Path("media/movies"),
    Path("media/tv"),
)
IMG_EXT = (".jpg", ".jpeg", ".png", ".tiff", ".heic", ".webp")

PHASE_LIST = "list"
PHASE_DETAIL = "detail"


def _list_images() -> list[Path]:
    out: list[Path] = []
    for d in SOURCES:
        if not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() in IMG_EXT:
                    out.append(p)
        except OSError:
            continue
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _gps_decimal(coord: tuple, ref: str) -> float:
    """Convert EXIF rational GPS coord (deg, min, sec) + N/S/E/W to
    signed decimal degrees."""
    try:
        d, m, s = (float(c) for c in coord)
        val = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            val = -val
        return val
    except Exception:
        return 0.0


def extract_exif(path: Path) -> dict:
    """Return a flat dict of interesting EXIF fields. Tries Pillow
    first; if that fails or isn't installed, shells out to exiftool."""
    out: dict = {"_path": str(path), "_size": path.stat().st_size}
    # --- Pillow path ---
    try:
        from PIL import Image, ExifTags
        with Image.open(path) as im:
            raw = im._getexif() or {}
        tag_map = {v: k for k, v in ExifTags.TAGS.items()}
        gps_map = {v: k for k, v in ExifTags.GPSTAGS.items()}
        wanted = ("Make", "Model", "DateTime", "DateTimeOriginal",
                  "Software", "ExposureTime", "FNumber", "ISOSpeedRatings",
                  "FocalLength", "Orientation", "Artist", "Copyright")
        for name in wanted:
            tag = tag_map.get(name)
            if tag in raw:
                out[name] = str(raw[tag])
        gps_block = raw.get(tag_map.get("GPSInfo", -1))
        if gps_block:
            named = {ExifTags.GPSTAGS.get(k, str(k)): v
                     for k, v in gps_block.items()}
            lat = named.get("GPSLatitude")
            lat_ref = named.get("GPSLatitudeRef", "N")
            lon = named.get("GPSLongitude")
            lon_ref = named.get("GPSLongitudeRef", "E")
            alt = named.get("GPSAltitude")
            if lat and lon:
                out["GPSLatitude"] = round(_gps_decimal(lat, lat_ref), 6)
                out["GPSLongitude"] = round(_gps_decimal(lon, lon_ref), 6)
            if alt:
                try:
                    out["GPSAltitude"] = round(float(alt), 1)
                except Exception:
                    pass
        return out
    except ImportError:
        pass
    except Exception as e:
        out["_pillow_error"] = str(e)
    # --- exiftool fallback ---
    if shutil.which("exiftool"):
        try:
            r = subprocess.run(
                ["exiftool", "-S", "-G", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                if ": " not in line:
                    continue
                key, value = line.split(": ", 1)
                key = key.strip().replace(" ", "")
                if any(w in key for w in ("Model", "Make", "Date", "GPS",
                                          "Software", "Artist")):
                    out[key] = value.strip()
            return out
        except Exception as e:
            out["_exiftool_error"] = str(e)
    out["_error"] = "no EXIF backend available (pip install pillow OR apt install libimage-exiftool-perl)"
    return out


def map_url(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}"


class ExifInspectorView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LIST
        self.list: ScrollList = ScrollList([])
        self.images: list[Path] = []
        self.selected: Path | None = None
        self.exif: dict = {}
        self.scroll = 0

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.small_font = pygame.font.Font(None, theme.FS_SMALL)
        self.mono_font = pygame.font.Font(None, 18)

        self._refresh()

    def _refresh(self) -> None:
        self.images = _list_images()
        actions: list[Action] = []
        for p in self.images:
            label = f"{p.parent.name}/{p.name}"
            size_kb = p.stat().st_size // 1024
            def make_handler(path=p):
                return lambda ctx: self._open(path)
            actions.append(Action(label, make_handler(),
                                  f"{size_kb} KB"))
        if not actions:
            actions.append(Action("[ no images found ]", None,
                                  "drop in loot/osint/images/ or media/captures/"))
        self.list = ScrollList(actions)

    def _open(self, path: Path) -> None:
        self.selected = path
        self.scroll = 0
        self.exif = {"_status": "parsing..."}
        self.phase = PHASE_DETAIL
        threading.Thread(target=self._parse_in_bg, args=(path,),
                         daemon=True).start()

    def _parse_in_bg(self, path: Path) -> None:
        try:
            self.exif = extract_exif(path)
            try:
                from bigbox import activity
                activity.record(f"EXIF parsed: {path.name}")
            except Exception:
                pass
        except Exception as e:
            self.exif = {"_error": str(e)}

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if self.phase == PHASE_LIST:
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.X:
                self._refresh()
            else:
                action = self.list.handle(ev)
                if action and action.handler:
                    action.handler(ctx)
            return
        # PHASE_DETAIL
        if ev.button is Button.B:
            self.phase = PHASE_LIST
            self.selected = None
            self.exif = {}
            self.scroll = 0
        elif ev.button is Button.UP:
            self.scroll = max(0, self.scroll - 4)
        elif ev.button is Button.DOWN:
            self.scroll += 4

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title_text = "OSINT :: EXIF"
        if self.phase == PHASE_DETAIL and self.selected is not None:
            title_text = f"EXIF · {self.selected.name[:50]}"
        title = self.title_font.render(title_text, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - 50)

        if self.phase == PHASE_LIST:
            self.list.render(surf, body, self.body_font)
            hint = self.small_font.render(
                "UP/DOWN: Navigate · A: Open · X: Refresh · B: Back",
                True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
            return

        # DETAIL
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)

        # Render the exif dict, prioritising GPS + camera info.
        pri_keys = ("Make", "Model", "DateTimeOriginal", "DateTime",
                    "GPSLatitude", "GPSLongitude", "GPSAltitude",
                    "Software", "Artist")
        rendered: list[tuple[str, str]] = []
        for k in pri_keys:
            if k in self.exif:
                rendered.append((k, str(self.exif[k])))
        for k, v in self.exif.items():
            if k.startswith("_") or k in pri_keys:
                continue
            rendered.append((k, str(v)))

        # Map URL for any GPS-bearing image.
        if "GPSLatitude" in self.exif and "GPSLongitude" in self.exif:
            rendered.append(("OSM URL",
                             map_url(self.exif["GPSLatitude"],
                                     self.exif["GPSLongitude"])))

        if not rendered:
            msg = self.body_font.render(
                self.exif.get("_status") or
                self.exif.get("_error") or
                "no EXIF data in this image.",
                True, theme.FG_DIM)
            surf.blit(msg, (body.x + 16, body.y + 16))
        else:
            row_h = 22
            x = body.x + 12
            y = body.y + 10 - self.scroll
            for k, v in rendered:
                if y > body.bottom:
                    break
                if y + row_h >= body.y:
                    ks = self.mono_font.render(f"{k:18}", True, theme.ACCENT)
                    vs = self.mono_font.render(str(v)[:80], True, theme.FG)
                    surf.blit(ks, (x, y))
                    surf.blit(vs, (x + 180, y))
                y += row_h

        hint = self.small_font.render(
            "UP/DOWN: Scroll · B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
