"""Offline handshake cracker — feeds a captured .cap into aircrack-ng.

Companion to wifi_attack.py: pick one of the .cap files we wrote, pick a
wordlist (plain or gzipped), watch live progress, save cracked creds to
loot/cracked.txt.

aircrack-ng reads the wordlist either from a path (`-w file`) or stdin
(`-w -`), so .gz wordlists are streamed through `zcat`. Stdin gets `1\\n`
piped into it so aircrack auto-selects the first network in captures
that have multiple BSSIDs.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


PHASE_PICK_CAP = "cap"
PHASE_PICK_WORDLIST = "wordlist"
PHASE_CRACKING = "crack"
PHASE_RESULT = "result"


CAP_DIR = Path("loot/handshakes")
CRACKED_LOG = Path("loot/cracked.txt")
WORDLIST_DIRS = [
    Path("/opt/bigbox/wordlists"),
    Path("wordlists"),
    Path("/usr/share/wordlists"),
    Path("/usr/share/wordlists/rockyou"),
]


@dataclass
class CapFile:
    path: Path
    size: int
    mtime: float

    @property
    def display(self) -> str:
        return self.path.name

    @property
    def size_str(self) -> str:
        if self.size < 1024:
            return f"{self.size}B"
        if self.size < 1024 * 1024:
            return f"{self.size // 1024}KB"
        return f"{self.size // (1024 * 1024)}MB"


@dataclass
class Wordlist:
    path: Path
    size: int

    @property
    def display(self) -> str:
        return self.path.name

    @property
    def is_gz(self) -> bool:
        return self.path.suffix == ".gz"

    @property
    def size_str(self) -> str:
        if self.size < 1024 * 1024:
            return f"{self.size // 1024}KB"
        if self.size < 1024 * 1024 * 1024:
            return f"{self.size // (1024 * 1024)}MB"
        return f"{self.size / (1024 * 1024 * 1024):.1f}GB"


def _list_caps() -> list[CapFile]:
    out: list[CapFile] = []
    for d in [CAP_DIR, Path("/opt/bigbox") / CAP_DIR]:
        if not d.exists():
            continue
        for p in d.glob("*.cap"):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(CapFile(p, st.st_size, st.st_mtime))
    out.sort(key=lambda c: c.mtime, reverse=True)
    return out


def _list_wordlists() -> list[Wordlist]:
    seen: set[Path] = set()
    out: list[Wordlist] = []
    for d in WORDLIST_DIRS:
        if not d.exists():
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file():
                continue
            if p.suffix not in (".txt", ".lst", ".gz", ".dic", ""):
                continue
            real = p.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append(Wordlist(p, size))
    out.sort(key=lambda w: w.size, reverse=True)
    return out


class OfflineCrackerView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_CAP
        self.status_msg = ""

        self.caps = _list_caps()
        self.cap_cursor = 0
        self.cap_scroll = 0

        self.wordlists: list[Wordlist] = []
        self.wl_cursor = 0
        self.wl_scroll = 0

        self.selected_cap: CapFile | None = None
        self.selected_wl: Wordlist | None = None

        # Cracking state
        self._proc: subprocess.Popen | None = None
        self._zcat: subprocess.Popen | None = None
        self._stop = False
        self._reader_thread: threading.Thread | None = None

        self.keys_tested = 0
        self.keys_total = 0
        self.kps = 0.0
        self.current_key = ""
        self.bssid = ""
        self.essid = ""
        self.start_time: float = 0.0

        self.found_password: str | None = None
        self.failed = False

    # ---------- aircrack lifecycle ----------
    def _start_crack(self) -> None:
        if not self.selected_cap or not self.selected_wl:
            return
        cap = str(self.selected_cap.path)
        wl = self.selected_wl

        self.keys_tested = 0
        self.keys_total = 0
        self.kps = 0.0
        self.current_key = ""
        self.found_password = None
        self.failed = False
        self.start_time = time.time()
        self.status_msg = f"Cracking {self.selected_cap.display}"
        self._stop = False

        # Pi 4 has 4 cores. Letting aircrack default to all of them pegs
        # the system and either freezes the UI thread or trips an
        # undervoltage brown-out (USB wifi + 4 hot cores draws enough
        # current to dip below 4.85 V on marginal supplies, which the
        # Pi treats as a soft reset — looks exactly like bigbox crashed).
        # `-p 2` caps aircrack at 2 threads; `nice -n 19` lowers
        # scheduler priority so the UI render thread always wins.
        AIRCRACK = ["nice", "-n", "19", "aircrack-ng", "-p", "2"]
        try:
            if wl.is_gz:
                self._zcat = subprocess.Popen(
                    ["nice", "-n", "19", "zcat", str(wl.path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self._proc = subprocess.Popen(
                    AIRCRACK + ["-a", "2", "-w", "-", cap],
                    stdin=self._zcat.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    preexec_fn=os.setsid,
                )
                # We don't need our handle; let aircrack read from zcat directly.
                if self._zcat.stdout:
                    self._zcat.stdout.close()
            else:
                self._proc = subprocess.Popen(
                    AIRCRACK + ["-a", "2", "-w", str(wl.path), cap],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    preexec_fn=os.setsid,
                )
                # Auto-select network 1 in case the cap has multiple BSSIDs.
                try:
                    if self._proc.stdin:
                        self._proc.stdin.write(b"1\n")
                        self._proc.stdin.flush()
                except Exception:
                    pass
        except FileNotFoundError as e:
            self.status_msg = f"missing tool: {e.filename}"
            self.failed = True
            self.phase = PHASE_RESULT
            return

        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def _stop_crack(self) -> None:
        self._stop = True
        for proc in (self._proc, self._zcat):
            if proc and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._proc = None
        self._zcat = None

    def _reader(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        # aircrack updates the progress line with carriage returns; read raw
        # bytes and split on \r or \n so we can parse it.
        buf = b""
        while not self._stop:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf += chunk
            while True:
                idx = -1
                for sep in (b"\r", b"\n"):
                    j = buf.find(sep)
                    if j != -1 and (idx == -1 or j < idx):
                        idx = j
                if idx == -1:
                    break
                line = buf[:idx].decode("utf-8", errors="replace")
                buf = buf[idx + 1:]
                self._parse_line(line)
        # drain any final bytes
        if buf:
            self._parse_line(buf.decode("utf-8", errors="replace"))
        # Wait for child to exit and decide success/fail if not already known.
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        if not self._stop and self.found_password is None and not self.failed:
            self.failed = True
            self.status_msg = "Wordlist exhausted — key not found"
            self.phase = PHASE_RESULT

    _RE_PROGRESS = re.compile(
        r"(\d+)\s*/\s*(\d+)\s+keys tested\s*\(\s*([\d.]+)\s*k/s\s*\)"
    )
    _RE_CURRENT = re.compile(r"Current passphrase:\s*(.+?)\s*$")
    _RE_FOUND = re.compile(r"KEY FOUND!\s*\[\s*(.+?)\s*\]")
    _RE_BSSID = re.compile(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b")

    def _parse_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        m = self._RE_FOUND.search(line)
        if m:
            self.found_password = m.group(1)
            self.status_msg = "KEY FOUND"
            self._save_cracked(self.found_password)
            self.phase = PHASE_RESULT
            return

        if "KEY NOT FOUND" in line:
            self.failed = True
            self.status_msg = "Wordlist exhausted — key not found"
            self.phase = PHASE_RESULT
            return

        m = self._RE_PROGRESS.search(line)
        if m:
            try:
                self.keys_tested = int(m.group(1))
                self.keys_total = int(m.group(2))
                self.kps = float(m.group(3))
            except ValueError:
                pass
            return

        m = self._RE_CURRENT.search(line)
        if m:
            self.current_key = m.group(1)[:48]
            return

        # Capture BSSID/ESSID hints from the network-list section.
        if not self.bssid:
            m = self._RE_BSSID.search(line)
            if m and "WPA" in line:
                self.bssid = m.group(1)
                # ESSID is after the BSSID in the row layout
                tail = line.split(m.group(1), 1)[-1]
                parts = tail.strip().split()
                if parts and parts[-1] not in ("WPA", "WPA2", "(0", "handshake)"):
                    self.essid = parts[-1]

    def _save_cracked(self, password: str) -> None:
        try:
            CRACKED_LOG.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cap = self.selected_cap.display if self.selected_cap else ""
            line = (
                f"{ts}\t{cap}\t{self.bssid or '?'}\t"
                f"{self.essid or '?'}\t{password}\n"
            )
            with CRACKED_LOG.open("a") as f:
                f.write(line)
        except Exception:
            pass

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if self.phase == PHASE_PICK_CAP:
            if ev.button is Button.B:
                self.dismissed = True
            elif not self.caps:
                return
            elif ev.button is Button.UP:
                self.cap_cursor = (self.cap_cursor - 1) % len(self.caps)
                self._adjust_scroll(self.cap_cursor, "cap")
            elif ev.button is Button.DOWN:
                self.cap_cursor = (self.cap_cursor + 1) % len(self.caps)
                self._adjust_scroll(self.cap_cursor, "cap")
            elif ev.button is Button.A:
                self.selected_cap = self.caps[self.cap_cursor]
                self.wordlists = _list_wordlists()
                self.wl_cursor = 0
                self.wl_scroll = 0
                self.phase = PHASE_PICK_WORDLIST
            return

        if self.phase == PHASE_PICK_WORDLIST:
            if ev.button is Button.B:
                self.phase = PHASE_PICK_CAP
            elif not self.wordlists:
                return
            elif ev.button is Button.UP:
                self.wl_cursor = (self.wl_cursor - 1) % len(self.wordlists)
                self._adjust_scroll(self.wl_cursor, "wl")
            elif ev.button is Button.DOWN:
                self.wl_cursor = (self.wl_cursor + 1) % len(self.wordlists)
                self._adjust_scroll(self.wl_cursor, "wl")
            elif ev.button is Button.A:
                self.selected_wl = self.wordlists[self.wl_cursor]
                self.phase = PHASE_CRACKING
                self._start_crack()
            return

        if self.phase == PHASE_CRACKING:
            if ev.button is Button.B:
                self._stop_crack()
                self.status_msg = "Stopped"
                self.failed = True
                self.phase = PHASE_RESULT
            return

        if self.phase == PHASE_RESULT:
            if ev.button is Button.B:
                # Back to cap selection so the user can chain another run.
                self._stop_crack()
                self.found_password = None
                self.failed = False
                self.phase = PHASE_PICK_CAP

    def _adjust_scroll(self, cursor: int, kind: str) -> None:
        visible = 8
        if kind == "cap":
            if cursor < self.cap_scroll:
                self.cap_scroll = cursor
            elif cursor >= self.cap_scroll + visible:
                self.cap_scroll = cursor - visible + 1
        else:
            if cursor < self.wl_scroll:
                self.wl_scroll = cursor
            elif cursor >= self.wl_scroll + visible:
                self.wl_scroll = cursor - visible + 1

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("WIRELESS :: CRACK", True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        surf.blit(f_small.render(self.status_msg[:60], True, theme.ACCENT),
                  (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        hint = self._hint()
        hint_surf = f_small.render(hint, True, theme.FG_DIM)
        surf.blit(hint_surf,
                  (theme.SCREEN_W - hint_surf.get_width() - theme.PADDING,
                   theme.SCREEN_H - foot_h + 8))

        if self.phase == PHASE_PICK_CAP:
            self._render_caps(surf, head_h, foot_h)
        elif self.phase == PHASE_PICK_WORDLIST:
            self._render_wordlists(surf, head_h, foot_h)
        elif self.phase == PHASE_CRACKING:
            self._render_cracking(surf, head_h, foot_h)
        elif self.phase == PHASE_RESULT:
            self._render_result(surf, head_h, foot_h)

    def _hint(self) -> str:
        if self.phase == PHASE_PICK_CAP:
            return "A: Pick  B: Back"
        if self.phase == PHASE_PICK_WORDLIST:
            return "A: Start  B: Back"
        if self.phase == PHASE_CRACKING:
            return "B: Stop"
        if self.phase == PHASE_RESULT:
            return "B: Done"
        return "B: Back"

    def _render_list(self, surf: pygame.Surface, head_h: int, foot_h: int,
                     items: list, cursor: int, scroll: int,
                     primary, secondary) -> None:
        list_x = theme.PADDING
        list_y = head_h + 8
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - head_h - foot_h - 16
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        row_h = 44
        f_main = pygame.font.Font(None, 24)
        f_meta = pygame.font.Font(None, 18)
        visible = list_h // row_h

        for i in range(visible):
            idx = scroll + i
            if idx >= len(items):
                break
            it = items[idx]
            y = list_y + i * row_h
            rect = pygame.Rect(list_x + 1, y + 1, list_w - 2, row_h - 2)
            if idx == cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2)
                color = theme.ACCENT
            else:
                color = theme.FG
            label = f_main.render(primary(it), True, color)
            surf.blit(label, (rect.x + 10, rect.y + 4))
            meta = secondary(it)
            if meta:
                ms = f_meta.render(meta, True, theme.FG_DIM)
                surf.blit(ms, (rect.right - ms.get_width() - 10, rect.y + 14))

        if len(items) > visible:
            bar_x = list_x + list_w - 4
            thumb_h = max(20, int(list_h * visible / len(items)))
            thumb_y = list_y + int(list_h * scroll / len(items))
            pygame.draw.rect(surf, theme.DIVIDER, (bar_x, list_y, 3, list_h))
            pygame.draw.rect(surf, theme.ACCENT, (bar_x, thumb_y, 3, thumb_h))

    def _render_caps(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        if not self.caps:
            f = pygame.font.Font(None, 26)
            msg = f.render("No .cap files in loot/handshakes/.",
                           True, theme.FG_DIM)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2 - 20))
            sub = pygame.font.Font(None, 22).render(
                "Capture a handshake first (Wireless > Handshake/Deauth).",
                True, theme.FG_DIM)
            surf.blit(sub, (theme.SCREEN_W // 2 - sub.get_width() // 2,
                            theme.SCREEN_H // 2 + 14))
            return
        self._render_list(
            surf, head_h, foot_h,
            self.caps, self.cap_cursor, self.cap_scroll,
            primary=lambda c: c.display,
            secondary=lambda c: f"{c.size_str}  {datetime.fromtimestamp(c.mtime).strftime('%Y-%m-%d %H:%M')}",
        )

    def _render_wordlists(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        if not self.wordlists:
            f = pygame.font.Font(None, 26)
            msg = f.render("No wordlists found.", True, theme.FG_DIM)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2 - 20))
            sub = pygame.font.Font(None, 20).render(
                "Looked in: /opt/bigbox/wordlists, /usr/share/wordlists",
                True, theme.FG_DIM)
            surf.blit(sub, (theme.SCREEN_W // 2 - sub.get_width() // 2,
                            theme.SCREEN_H // 2 + 14))
            return
        self._render_list(
            surf, head_h, foot_h,
            self.wordlists, self.wl_cursor, self.wl_scroll,
            primary=lambda w: w.display,
            secondary=lambda w: f"{w.size_str}{'  gz' if w.is_gz else ''}",
        )

    def _render_cracking(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 30)
        f_med = pygame.font.Font(None, 24)
        f_small = pygame.font.Font(None, 20)

        cap = self.selected_cap.display if self.selected_cap else ""
        wl = self.selected_wl.display if self.selected_wl else ""

        y = head_h + 16
        surf.blit(f_med.render(f"cap:  {cap}", True, theme.FG),
                  (theme.PADDING, y))
        y += 28
        surf.blit(f_med.render(f"list: {wl}", True, theme.FG),
                  (theme.PADDING, y))
        y += 36

        # Progress bar
        bar_x = theme.PADDING
        bar_y = y
        bar_w = theme.SCREEN_W - 2 * theme.PADDING
        bar_h = 28
        pygame.draw.rect(surf, theme.BG_ALT, (bar_x, bar_y, bar_w, bar_h))
        pygame.draw.rect(surf, theme.DIVIDER, (bar_x, bar_y, bar_w, bar_h), 1)
        if self.keys_total > 0:
            ratio = max(0.0, min(1.0, self.keys_tested / self.keys_total))
            fill = max(0, int(bar_w * ratio) - 2)
            if fill > 0:
                pygame.draw.rect(surf, theme.ACCENT_DIM,
                                 (bar_x + 1, bar_y + 1, fill, bar_h - 2))
            pct = f"{ratio * 100:.2f}%"
        else:
            # marquee while we wait
            pos = (time.time() * 220) % (bar_w + 80) - 80
            pygame.draw.rect(surf, theme.ACCENT_DIM,
                             (bar_x + max(0, int(pos)), bar_y + 1, 80, bar_h - 2))
            pct = "..."
        ps = f_med.render(pct, True, theme.ACCENT)
        surf.blit(ps, (bar_x + bar_w // 2 - ps.get_width() // 2, bar_y + 2))

        y += bar_h + 24

        # Stats grid
        keys_label = f"{self.keys_tested:,} / {self.keys_total:,}" if self.keys_total else "..."
        stats = [
            ("KEYS", keys_label),
            ("SPEED", f"{self.kps:.1f} k/s"),
            ("ELAPSED", _format_seconds(time.time() - self.start_time)),
            ("ETA", _eta(self.keys_tested, self.keys_total, self.kps)),
        ]
        col_w = (theme.SCREEN_W - 2 * theme.PADDING) // 4
        for i, (lab, val) in enumerate(stats):
            cx = theme.PADDING + i * col_w
            surf.blit(f_small.render(lab, True, theme.FG_DIM), (cx, y))
            surf.blit(f_med.render(val, True, theme.FG), (cx, y + 22))

        y += 70

        # Current key
        if self.current_key:
            surf.blit(f_small.render("trying:", True, theme.FG_DIM),
                      (theme.PADDING, y))
            ck = f_big.render(self.current_key, True, theme.WARN)
            surf.blit(ck, (theme.PADDING, y + 18))

    def _render_result(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 56)
        f_med = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 22)

        if self.found_password:
            title = "KEY FOUND"
            color = theme.ACCENT
        else:
            title = "KEY NOT FOUND"
            color = theme.ERR

        ts = f_big.render(title, True, color)
        surf.blit(ts, (theme.SCREEN_W // 2 - ts.get_width() // 2, head_h + 30))

        y = head_h + 110
        if self.selected_cap:
            ct = f_small.render(f"cap: {self.selected_cap.display}",
                                True, theme.FG_DIM)
            surf.blit(ct, (theme.SCREEN_W // 2 - ct.get_width() // 2, y))
            y += 24
        if self.essid or self.bssid:
            net = f"{self.essid or '?'}  ({self.bssid or '?'})"
            nt = f_small.render(net, True, theme.FG_DIM)
            surf.blit(nt, (theme.SCREEN_W // 2 - nt.get_width() // 2, y))
            y += 24

        y += 20
        if self.found_password:
            box_w = 540
            box_h = 80
            bx = (theme.SCREEN_W - box_w) // 2
            pygame.draw.rect(surf, theme.SELECTION_BG, (bx, y, box_w, box_h),
                             border_radius=8)
            pygame.draw.rect(surf, theme.ACCENT, (bx, y, box_w, box_h),
                             2, border_radius=8)
            pw = f_med.render(self.found_password, True, theme.ACCENT)
            surf.blit(pw, (theme.SCREEN_W // 2 - pw.get_width() // 2,
                           y + box_h // 2 - pw.get_height() // 2))
            saved = f_small.render("Saved to loot/cracked.txt",
                                   True, theme.FG_DIM)
            surf.blit(saved, (theme.SCREEN_W // 2 - saved.get_width() // 2,
                              y + box_h + 12))
        else:
            stats = f_small.render(
                f"{self.keys_tested:,} keys tested in "
                f"{_format_seconds(time.time() - self.start_time)}",
                True, theme.FG_DIM)
            surf.blit(stats, (theme.SCREEN_W // 2 - stats.get_width() // 2, y))


def _format_seconds(s: float) -> str:
    s = int(max(0, s))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        return f"{h}h{m:02d}m"
    return f"{m:02d}:{sec:02d}"


def _eta(tested: int, total: int, kps: float) -> str:
    if total <= 0 or kps <= 0 or tested >= total:
        return "..."
    remaining = total - tested
    secs = remaining / (kps * 1000)
    return _format_seconds(secs)
