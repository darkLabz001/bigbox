"""Username pivot — parallel HEAD-request check across ~50 platforms.

Faster sherlock-alt: instead of running the upstream sherlock binary
(which hits sites sequentially and takes minutes), spawn a thread
pool that fires HEAD requests concurrently. Live progress: how many
platforms checked vs found. Saves to ``loot/osint/user_<name>_<ts>.json``.

Platform list is intentionally curated — the highest-signal ~50
sites for everyday OSINT, not the full sherlock 400+ which is mostly
noise.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


LOOT_DIR = Path("loot/osint")
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) bigbox-osint"

PHASE_LANDING = "landing"
PHASE_RUNNING = "running"
PHASE_RESULT = "result"


# (platform_label, url_template, signal_strategy)
# signal_strategy:
#   "status_2xx"     — claim found if 2xx response
#   "not_404"        — claim found if status != 404 (some sites 200 a sign-in
#                      wall on missing users; we treat that as ambiguous)
#   ("text_negative", "string") — claim found if the marker isn't in body
PLATFORMS: list[tuple[str, str, object]] = [
    ("github",       "https://github.com/{u}",                     "status_2xx"),
    ("gitlab",       "https://gitlab.com/{u}",                     "status_2xx"),
    ("twitter/x",    "https://x.com/{u}",                          "status_2xx"),
    ("instagram",    "https://www.instagram.com/{u}/",             "status_2xx"),
    ("tiktok",       "https://www.tiktok.com/@{u}",                "status_2xx"),
    ("reddit",       "https://www.reddit.com/user/{u}",            "status_2xx"),
    ("youtube",      "https://www.youtube.com/@{u}",               "status_2xx"),
    ("twitch",       "https://www.twitch.tv/{u}",                  "status_2xx"),
    ("medium",       "https://medium.com/@{u}",                    "status_2xx"),
    ("dev.to",       "https://dev.to/{u}",                         "status_2xx"),
    ("stackoverflow","https://stackoverflow.com/users/{u}",        "status_2xx"),
    ("hashnode",     "https://hashnode.com/@{u}",                  "status_2xx"),
    ("keybase",      "https://keybase.io/{u}",                     "status_2xx"),
    ("npm",          "https://www.npmjs.com/~{u}",                 "status_2xx"),
    ("pypi",         "https://pypi.org/user/{u}/",                 "status_2xx"),
    ("docker hub",   "https://hub.docker.com/u/{u}",               "status_2xx"),
    ("bitbucket",    "https://bitbucket.org/{u}/",                 "status_2xx"),
    ("codeberg",     "https://codeberg.org/{u}",                   "status_2xx"),
    ("hackernews",   "https://news.ycombinator.com/user?id={u}",   "status_2xx"),
    ("hackerone",    "https://hackerone.com/{u}",                  "status_2xx"),
    ("bugcrowd",     "https://bugcrowd.com/{u}",                   "status_2xx"),
    ("kaggle",       "https://www.kaggle.com/{u}",                 "status_2xx"),
    ("about.me",     "https://about.me/{u}",                       "status_2xx"),
    ("linktree",     "https://linktr.ee/{u}",                      "status_2xx"),
    ("lichess",      "https://lichess.org/@/{u}",                  "status_2xx"),
    ("chess.com",    "https://www.chess.com/member/{u}",           "status_2xx"),
    ("steam",        "https://steamcommunity.com/id/{u}",          "status_2xx"),
    ("itch.io",      "https://{u}.itch.io",                        "status_2xx"),
    ("wikipedia",    "https://en.wikipedia.org/wiki/User:{u}",     "status_2xx"),
    ("flickr",       "https://www.flickr.com/people/{u}",          "status_2xx"),
    ("vimeo",        "https://vimeo.com/{u}",                      "status_2xx"),
    ("soundcloud",   "https://soundcloud.com/{u}",                 "status_2xx"),
    ("bandcamp",     "https://{u}.bandcamp.com",                   "status_2xx"),
    ("patreon",      "https://www.patreon.com/{u}",                "status_2xx"),
    ("ko-fi",        "https://ko-fi.com/{u}",                      "status_2xx"),
    ("buymeacoffee", "https://www.buymeacoffee.com/{u}",           "status_2xx"),
    ("substack",     "https://{u}.substack.com",                   "status_2xx"),
    ("wordpress",    "https://{u}.wordpress.com",                  "status_2xx"),
    ("blogger",      "https://{u}.blogspot.com",                   "status_2xx"),
    ("disqus",       "https://disqus.com/by/{u}/",                 "status_2xx"),
    ("gravatar",     "https://en.gravatar.com/{u}",                "status_2xx"),
    ("mastodon.social", "https://mastodon.social/@{u}",            "status_2xx"),
    ("infosec.exchange","https://infosec.exchange/@{u}",           "status_2xx"),
    ("bsky",         "https://bsky.app/profile/{u}.bsky.social",   "status_2xx"),
    ("threads",      "https://www.threads.net/@{u}",               "status_2xx"),
    ("pinterest",    "https://www.pinterest.com/{u}/",             "status_2xx"),
    ("ebay",         "https://www.ebay.com/usr/{u}",               "status_2xx"),
    ("etsy",         "https://www.etsy.com/people/{u}",            "status_2xx"),
    ("vsco",         "https://vsco.co/{u}",                        "status_2xx"),
    ("last.fm",      "https://www.last.fm/user/{u}",               "status_2xx"),
]


class UserPivotView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.username = ""
        self.results: list[dict] = []
        self._stop = False
        self.scroll = 0

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.small_font = pygame.font.Font(None, theme.FS_SMALL)

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._stop = True
            elif self.phase == PHASE_RESULT:
                self.phase = PHASE_LANDING
                self.results = []
                self.scroll = 0
            else:
                self.dismissed = True
            return
        if self.phase == PHASE_LANDING and ev.button is Button.A:
            def _cb(val):
                if val and val.strip():
                    self.username = val.strip()
                    self._start()
            ctx.get_input("Username to search", _cb, self.username)
        elif self.phase == PHASE_RESULT:
            if ev.button is Button.UP:
                self.scroll = max(0, self.scroll - 4)
            elif ev.button is Button.DOWN:
                self.scroll += 4
            elif ev.button is Button.X:
                self._send_to_webhook(ctx)

    def _start(self) -> None:
        self.phase = PHASE_RUNNING
        self.results = [
            {"platform": p[0], "url": p[1].format(u=self.username),
             "found": None, "status": ""}
            for p in PLATFORMS
        ]
        self._stop = False
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            import requests
        except Exception:
            for r in self.results:
                r["found"] = False
                r["status"] = "no requests"
            self.phase = PHASE_RESULT
            return

        sess = requests.Session()
        sess.headers["User-Agent"] = USER_AGENT

        def probe(idx: int) -> None:
            if self._stop:
                return
            r = self.results[idx]
            url = r["url"]
            try:
                resp = sess.head(url, timeout=6, allow_redirects=True)
                code = resp.status_code
                # Some servers reject HEAD; retry with GET on 405.
                if code in (400, 403, 405):
                    resp = sess.get(url, timeout=6, allow_redirects=True,
                                    stream=True)
                    code = resp.status_code
                    resp.close()
                r["status"] = f"HTTP {code}"
                r["found"] = 200 <= code < 300
            except Exception as e:
                r["found"] = False
                r["status"] = type(e).__name__

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(probe, i)
                       for i in range(len(self.results))]
            for _ in as_completed(futures):
                if self._stop:
                    break
        self.phase = PHASE_RESULT
        self._save()

    def _save(self) -> None:
        try:
            LOOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = self.username.replace("/", "_")[:32]
            out = LOOT_DIR / f"user_{safe}_{ts}.json"
            found = [r for r in self.results if r["found"]]
            with out.open("w") as f:
                json.dump({"username": self.username,
                           "results": self.results,
                           "queried_at": ts}, f, indent=2)
            try:
                from bigbox import activity
                activity.record(
                    f"user pivot {self.username}: {len(found)}/{len(self.results)} hit")
            except Exception:
                pass
        except Exception as e:
            print(f"[user_pivot] save failed: {e}")

    def _send_to_webhook(self, ctx: "App") -> None:
        from bigbox import webhooks
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        text = self._render_text()
        out = Path(f"/tmp/bigbox-user-{self.username}-{ts}.txt")
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
        lines = [f"username: {self.username}", ""]
        for r in self.results:
            mark = "+" if r["found"] else "-"
            lines.append(f"  {mark} {r['platform']:20}  {r['url']}")
        return "\n".join(lines)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title_text = "OSINT :: USER_PIVOT"
        if self.phase != PHASE_LANDING:
            done = sum(1 for r in self.results if r["found"] is not None)
            found = sum(1 for r in self.results if r["found"])
            title_text = f"USER_PIVOT · {found}/{done}"
        title = self.title_font.render(title_text, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)

        if self.phase == PHASE_LANDING:
            for i, line in enumerate([
                f"Username: {self.username or '(none)'}",
                f"{len(PLATFORMS)} platforms checked in parallel",
                "",
                "A: enter username",
                "B: back",
            ]):
                ls = self.body_font.render(line, True, theme.FG)
                surf.blit(ls, (body.x + 16, body.y + 16 + i * 26))
        else:
            row_h = 18
            visible = (body.height - 16) // row_h
            items = self.results[self.scroll:self.scroll + visible]
            for i, r in enumerate(items):
                if r["found"] is True:
                    color, mark = theme.ACCENT, "+"
                elif r["found"] is False:
                    color, mark = theme.FG_DIM, "-"
                else:
                    color, mark = theme.WARN, "?"
                line = f"{mark} {r['platform']:20}  {r['status']:12}  {r['url']}"
                ls = self.small_font.render(line[:90], True, color)
                surf.blit(ls, (body.x + 12, body.y + 8 + i * row_h))

        hint_text = ("UP/DOWN: Scroll · X: Send · B: Back"
                     if self.phase == PHASE_RESULT
                     else ("B: Stop" if self.phase == PHASE_RUNNING
                           else "A: Enter username · B: Back"))
        hint = self.small_font.render(hint_text, True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
