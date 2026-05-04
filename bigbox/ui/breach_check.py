"""HIBP breach checker — type an email, list known breaches.

Wraps the v3 ``haveibeenpwned.com/api/v3/breachedaccount/<email>``
endpoint. That endpoint requires a paid API key (~$3.50/mo); the
key lives at ``/etc/bigbox/hibp.json`` (``{"api_key": "..."}``) so it
survives OTA. The view shows a clear "set key in Toolbox" message
when the key is missing instead of failing silently.

Results persist to ``loot/osint/hibp_<email>_<ts>.json`` so they
show up in webhook bundles + the loot gallery.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


CONFIG_PATH = Path("/etc/bigbox/hibp.json")
LOOT_DIR = Path("loot/osint")
USER_AGENT = "bigbox-osint/1.0"
HIBP_API = "https://haveibeenpwned.com/api/v3/breachedaccount/{email}"


PHASE_LANDING = "landing"
PHASE_RUNNING = "running"
PHASE_RESULT = "result"


def _load_api_key() -> str:
    try:
        with CONFIG_PATH.open() as f:
            return (json.load(f).get("api_key") or "").strip()
    except Exception:
        return ""


def save_api_key(key: str) -> bool:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w") as f:
            json.dump({"api_key": key.strip()}, f)
        return True
    except Exception as e:
        print(f"[hibp] save key failed: {e}")
        return False


class BreachCheckView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.email = ""
        self.status = ""
        self.breaches: list[dict] = []
        self.error = ""

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.small_font = pygame.font.Font(None, theme.FS_SMALL)

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            if self.phase != PHASE_LANDING:
                self.phase = PHASE_LANDING
                self.breaches = []
                self.error = ""
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                def _cb(val):
                    if val and "@" in val:
                        self.email = val.strip()
                        self._start()
                    elif val is not None:
                        ctx.toast("invalid email")
                ctx.get_input("Email to check", _cb, self.email)
            elif ev.button is Button.X:
                # Configure API key
                def _cb(val):
                    if val is not None:
                        save_api_key(val)
                        ctx.toast("HIBP key saved" if val.strip() else "key cleared")
                ctx.get_input("HIBP API Key (paid)", _cb, _load_api_key())
        elif self.phase == PHASE_RESULT:
            if ev.button is Button.X:
                self._send_to_webhook(ctx)

    def _start(self) -> None:
        self.phase = PHASE_RUNNING
        self.status = f"Querying HIBP for {self.email}..."
        self.error = ""
        self.breaches = []
        threading.Thread(target=self._query, daemon=True).start()

    def _query(self) -> None:
        key = _load_api_key()
        if not key:
            self.error = ("No HIBP API key configured. Press X on the "
                          "landing screen to set one (paid: ~$3.50/mo).")
            self.phase = PHASE_RESULT
            return
        try:
            import requests
            r = requests.get(
                HIBP_API.format(email=self.email),
                headers={"hibp-api-key": key, "User-Agent": USER_AGENT},
                params={"truncateResponse": "false"},
                timeout=15,
            )
        except Exception as e:
            self.error = f"request failed: {type(e).__name__}: {e}"
            self.phase = PHASE_RESULT
            return

        if r.status_code == 404:
            # HIBP returns 404 when the email has no known breaches.
            self.breaches = []
            self.status = "Clean — no known breaches."
            self.phase = PHASE_RESULT
            self._save()
            return
        if r.status_code == 401:
            self.error = "HIBP rejected the API key (401)."
            self.phase = PHASE_RESULT
            return
        if r.status_code != 200:
            self.error = f"HTTP {r.status_code}: {r.text[:120]}"
            self.phase = PHASE_RESULT
            return
        try:
            self.breaches = r.json()
        except Exception:
            self.breaches = []
        self.status = f"Found {len(self.breaches)} breach(es)."
        self.phase = PHASE_RESULT
        self._save()

    def _save(self) -> None:
        try:
            LOOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_email = self.email.replace("@", "_at_").replace("/", "_")
            out = LOOT_DIR / f"hibp_{safe_email}_{ts}.json"
            with out.open("w") as f:
                json.dump(
                    {"email": self.email, "breaches": self.breaches,
                     "queried_at": ts}, f, indent=2)
            try:
                from bigbox import activity
                activity.record(f"HIBP {self.email}: {len(self.breaches)} breach(es)")
            except Exception:
                pass
        except Exception as e:
            print(f"[hibp] save failed: {e}")

    def _send_to_webhook(self, ctx: "App") -> None:
        from bigbox import webhooks
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        text = self._render_text()
        out = Path(f"/tmp/bigbox-hibp-{ts}.txt")
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
        lines = [f"HIBP report for {self.email}", ""]
        if not self.breaches:
            lines.append("No known breaches.")
        else:
            for b in self.breaches:
                name = b.get("Name", "?")
                date = b.get("BreachDate", "?")
                pwned = b.get("PwnCount", 0)
                classes = ", ".join(b.get("DataClasses", []))
                lines.append(f"  {name}  ({date})  pwned={pwned:,}")
                if classes:
                    lines.append(f"      data: {classes}")
        return "\n".join(lines)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title = self.title_font.render("OSINT :: HIBP", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)

        if self.phase == PHASE_LANDING:
            lines = [
                ("Email:",         self.email or "(none)"),
                ("API key:",       "set" if _load_api_key() else "MISSING"),
                ("",               ""),
                ("A: enter email", ""),
                ("X: configure API key", ""),
                ("B: back", ""),
            ]
            for i, (k, v) in enumerate(lines):
                if not k and not v:
                    continue
                ks = self.body_font.render(k, True, theme.ACCENT)
                vs = self.body_font.render(v, True, theme.FG)
                surf.blit(ks, (body.x + 16, body.y + 16 + i * 28))
                surf.blit(vs, (body.x + 200, body.y + 16 + i * 28))
        elif self.phase == PHASE_RUNNING:
            msg = self.body_font.render(self.status, True, theme.FG)
            surf.blit(msg, (body.centerx - msg.get_width() // 2,
                            body.centery - msg.get_height() // 2))
        else:  # RESULT
            if self.error:
                err = self.body_font.render(self.error[:80], True, theme.ERR)
                surf.blit(err, (body.x + 16, body.y + 16))
                hint = self.small_font.render(
                    "B to retry · X to send report",
                    True, theme.FG_DIM)
                surf.blit(hint, (body.x + 16, body.y + 50))
            else:
                hdr = self.body_font.render(
                    f"{self.email} — {len(self.breaches)} breach(es)",
                    True, theme.ACCENT if not self.breaches else theme.WARN)
                surf.blit(hdr, (body.x + 16, body.y + 12))
                row_y = body.y + 44
                for b in self.breaches[:8]:
                    name = b.get("Name", "?")
                    date = b.get("BreachDate", "?")
                    pwned = b.get("PwnCount", 0)
                    line = f"{name}  ({date})  pwned={pwned:,}"
                    ls = self.small_font.render(line[:70], True, theme.FG)
                    surf.blit(ls, (body.x + 16, row_y))
                    row_y += 22

        hint = self.small_font.render(
            "B: Back   X: Send to webhook" if self.phase == PHASE_RESULT
            else "B: Back",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
