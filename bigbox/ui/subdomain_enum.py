"""Subdomain enumerator — wraps subfinder (preferred) or amass.

Type a domain, get a list of discovered subdomains with HTTP status
checks. Saves to ``loot/osint/subs_<domain>_<ts>.json`` so it slots
into Scan History + the loot bundle.

Both tools are noisy (network-bound, can take minutes); the worker
runs in a background thread + registers with the bg-task tray so the
user can stop it from the Tasks view.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import background, theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


LOOT_DIR = Path("loot/osint")

PHASE_LANDING = "landing"
PHASE_RUNNING = "running"
PHASE_RESULT = "result"


def _resolve_binary() -> str | None:
    for b in ("subfinder", "amass"):
        if shutil.which(b):
            return b
    return None


class SubdomainEnumView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.domain = ""
        self.results: list[dict] = []
        self.status = ""
        self.error = ""
        self._proc: subprocess.Popen | None = None
        self._stop = False

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.small_font = pygame.font.Font(None, theme.FS_SMALL)
        self.scroll = 0

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._stop_scan()
            elif self.phase == PHASE_RESULT:
                self.phase = PHASE_LANDING
                self.results = []
                self.error = ""
                self.scroll = 0
            else:
                self.dismissed = True
            return
        if self.phase == PHASE_LANDING and ev.button is Button.A:
            def _cb(val):
                if val and "." in val:
                    self.domain = val.strip().lower()
                    self._start()
                elif val is not None:
                    ctx.toast("invalid domain")
            ctx.get_input("Domain (e.g. example.com)", _cb, self.domain)
        elif self.phase == PHASE_RESULT:
            if ev.button is Button.UP:
                self.scroll = max(0, self.scroll - 4)
            elif ev.button is Button.DOWN:
                self.scroll += 4
            elif ev.button is Button.X:
                self._send_to_webhook(ctx)

    def _start(self) -> None:
        binary = _resolve_binary()
        if binary is None:
            self.error = ("subfinder/amass not installed — "
                          "Toolbox → Verify Core Tools (or apt install subfinder)")
            self.phase = PHASE_RESULT
            return
        self.phase = PHASE_RUNNING
        self.status = f"running {binary} on {self.domain}..."
        self.results = []
        self.error = ""
        self._stop = False
        background.register("subdomain_enum",
                            f"Subdomain enum {self.domain}",
                            "Recon", stop=self._stop_scan)
        threading.Thread(target=self._worker, args=(binary,),
                         daemon=True).start()

    def _worker(self, binary: str) -> None:
        try:
            cmd = ([binary, "-d", self.domain, "-silent"]
                   if binary == "subfinder"
                   else [binary, "-d", self.domain, "-silent"])
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
        except Exception as e:
            self.error = f"launch failed: {e}"
            self.phase = PHASE_RESULT
            background.unregister("subdomain_enum")
            return

        subs: set[str] = set()
        try:
            for line in self._proc.stdout:
                if self._stop:
                    break
                sub = line.strip()
                if not sub or "." not in sub:
                    continue
                if sub in subs:
                    continue
                subs.add(sub)
                self.results.append({"sub": sub, "status": "?"})
                self.status = f"{len(subs)} found..."
        except Exception as e:
            self.error = f"read failed: {e}"

        # Light HTTP-status check on each, parallel.
        try:
            self._check_http(list(subs))
        except Exception:
            pass

        self.status = f"done — {len(self.results)} subdomain(s)"
        self.phase = PHASE_RESULT
        self._save()
        background.unregister("subdomain_enum")

    def _check_http(self, subs: list[str]) -> None:
        try:
            import requests
        except Exception:
            return
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def probe(s: str) -> tuple[str, str]:
            for scheme in ("https", "http"):
                try:
                    r = requests.head(f"{scheme}://{s}", timeout=4,
                                      allow_redirects=True)
                    return s, f"{scheme} {r.status_code}"
                except Exception:
                    continue
            return s, "down"

        index = {r["sub"]: r for r in self.results}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(probe, s) for s in subs]
            for fut in as_completed(futures):
                if self._stop:
                    break
                try:
                    s, status = fut.result()
                except Exception:
                    continue
                rec = index.get(s)
                if rec:
                    rec["status"] = status

    def _stop_scan(self) -> None:
        self._stop = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        background.unregister("subdomain_enum")

    def _save(self) -> None:
        try:
            LOOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = LOOT_DIR / f"subs_{self.domain}_{ts}.json"
            with out.open("w") as f:
                json.dump({"domain": self.domain,
                           "results": self.results, "queried_at": ts},
                          f, indent=2)
            try:
                from bigbox import activity
                activity.record(
                    f"subs {self.domain}: {len(self.results)} found")
            except Exception:
                pass
        except Exception as e:
            print(f"[subdomain_enum] save failed: {e}")

    def _send_to_webhook(self, ctx: "App") -> None:
        from bigbox import webhooks
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        text = self._render_text()
        out = Path(f"/tmp/bigbox-subs-{self.domain}-{ts}.txt")
        try:
            out.write_text(text)
        except Exception as e:
            ctx.toast(f"write failed: {e}")
            return
        def _send():
            ok, msg = webhooks.send_file(str(out))
            ctx.toast(msg if ok else f"failed: {msg}")
        threading.Thread(target=_send, daemon=True).start()

    def _render_text(self) -> str:
        lines = [f"subdomains for {self.domain}", ""]
        for r in self.results:
            lines.append(f"  {r['sub']:50}  {r['status']}")
        return "\n".join(lines)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title = self.title_font.render("OSINT :: SUBDOMAINS",
                                       True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)

        if self.phase == PHASE_LANDING:
            self.body_font.render(self.domain or "(none)", True, theme.FG)
            for i, line in enumerate([
                f"Domain: {self.domain or '(none)'}",
                "",
                "A: enter domain",
                "B: back",
            ]):
                ls = self.body_font.render(line, True, theme.FG)
                surf.blit(ls, (body.x + 16, body.y + 16 + i * 28))
        elif self.phase == PHASE_RUNNING:
            msg = self.body_font.render(self.status, True, theme.FG)
            surf.blit(msg, (body.centerx - msg.get_width() // 2,
                            body.centery - msg.get_height() // 2))
            shown = sorted(self.results, key=lambda r: r["sub"])[-10:]
            for i, r in enumerate(shown):
                ls = self.small_font.render(r["sub"][:60], True, theme.FG_DIM)
                surf.blit(ls, (body.x + 12, body.y + 12 + i * 18))
        else:  # RESULT
            if self.error:
                err = self.body_font.render(self.error[:80], True, theme.ERR)
                surf.blit(err, (body.x + 16, body.y + 16))
            else:
                hdr = self.body_font.render(
                    f"{self.domain} — {len(self.results)} subs",
                    True, theme.ACCENT)
                surf.blit(hdr, (body.x + 16, body.y + 12))
                row_y = body.y + 44
                row_h = 18
                visible = (body.height - 60) // row_h
                items = sorted(self.results, key=lambda r: r["sub"])
                items = items[self.scroll:self.scroll + visible]
                for r in items:
                    color = (theme.ACCENT if r["status"].startswith("https 2")
                             or r["status"].startswith("http 2")
                             else theme.FG_DIM)
                    line = f"{r['sub']:48}  {r['status']}"
                    ls = self.small_font.render(line[:80], True, color)
                    surf.blit(ls, (body.x + 16, row_y))
                    row_y += row_h
                    if row_y > body.bottom - 20:
                        break

        hint_text = ("UP/DOWN: Scroll · X: Send · B: Back"
                     if self.phase == PHASE_RESULT
                     else ("B: Stop" if self.phase == PHASE_RUNNING
                           else "A: Enter domain · B: Back"))
        hint = self.small_font.render(hint_text, True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
