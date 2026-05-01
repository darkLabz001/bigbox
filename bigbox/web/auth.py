"""Web UI auth — single shared token, persisted under /etc/bigbox.

Before this existed, every endpoint on :8080 was unauthenticated,
including ``/ws/terminal`` (which spawns a root bash) and ``/upload``.
That's fine on a private LAN with nobody else on it; not fine on
Tailscale or any shared network.

Design:
- One token, stored at ``/etc/bigbox/web_token.txt`` so the OTA flow
  in /opt/bigbox doesn't wipe it (same pattern as wigle/RA creds).
- Generated on first call if missing.
- HTTP middleware checks for the token in a cookie, an
  ``Authorization: Bearer ...`` header, or a ``?token=...`` query
  param. Query-token requests get a cookie back so the next request
  is clean.
- WebSocket endpoints check cookies themselves (HTTP middleware
  doesn't run for WS upgrades).
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware


TOKEN_PATH = Path("/etc/bigbox/web_token.txt")
COOKIE_NAME = "bb_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# Paths that don't require auth — login form + its POST + favicon.
_PUBLIC_PATHS = {"/login", "/favicon.ico"}


_token_cache: str | None = None


def get_token() -> str:
    """Return the persistent web-UI token, creating one if missing."""
    global _token_cache
    if _token_cache:
        return _token_cache
    if TOKEN_PATH.is_file():
        try:
            t = TOKEN_PATH.read_text().strip()
            if t:
                _token_cache = t
                return t
        except Exception as e:
            print(f"[web_auth] read token failed: {e}")
    # Mint a fresh token
    new = secrets.token_urlsafe(24)
    try:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(new + "\n")
        # Tighten perms — readable by root only.
        try:
            os.chmod(TOKEN_PATH, 0o600)
        except Exception:
            pass
        print(f"[web_auth] generated new token at {TOKEN_PATH}")
    except Exception as e:
        print(f"[web_auth] could not persist token ({e}); using in-memory")
    _token_cache = new
    return new


def regenerate_token() -> str:
    """Wipe and re-mint. Existing sessions are invalidated."""
    global _token_cache
    _token_cache = None
    if TOKEN_PATH.is_file():
        try:
            TOKEN_PATH.unlink()
        except Exception:
            pass
    return get_token()


def presented_token(request: Request) -> str:
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie:
        return cookie
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token", "")


def is_authed(request: Request) -> bool:
    expected = get_token()
    return secrets.compare_digest(presented_token(request), expected)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)
        if not is_authed(request):
            return RedirectResponse("/login", status_code=302)
        response = await call_next(request)
        # Promote query-string token to cookie so subsequent requests
        # don't need to keep passing it.
        query_token = request.query_params.get("token", "")
        if query_token and not request.cookies.get(COOKIE_NAME):
            response.set_cookie(
                COOKIE_NAME, query_token,
                httponly=True, samesite="lax", max_age=COOKIE_MAX_AGE,
            )
        return response


def ws_authed(websocket) -> bool:
    """Cookie-based check for WebSocket upgrades (HTTP middleware
    doesn't run for these)."""
    cookie_header = websocket.headers.get("cookie", "")
    cookies = {}
    for part in cookie_header.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    presented = cookies.get(COOKIE_NAME, "")
    expected = get_token()
    return bool(presented) and secrets.compare_digest(presented, expected)
