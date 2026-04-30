from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import pygame
import os
import shutil
import pty
import fcntl
import termios
import struct
import subprocess
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from bigbox.events import Button, ButtonEvent
from bigbox import wigle as wigle_mod
from bigbox import emulator as emu_mod
from bigbox import retroachievements as ra_mod

if TYPE_CHECKING:
    from bigbox.app import App

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MEDIA_DIR = Path("media")
ALLOWED_FOLDERS = ("movies", "tv")
MEDIA_DIR.mkdir(exist_ok=True)
for _sub in ALLOWED_FOLDERS:
    (MEDIA_DIR / _sub).mkdir(exist_ok=True)

# Global reference to the running Bigbox App
_bb_app: App | None = None

def set_app(bb_app: App):
    global _bb_app
    _bb_app = bb_app

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.websocket("/ws/terminal")
async def terminal_websocket(websocket: WebSocket):
    await websocket.accept()
    
    # Spawn bash in a PTY
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["/bin/bash"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=os.setsid,
        env={**os.environ, "TERM": "xterm-256color", "HOME": "/root"}
    )
    os.close(slave_fd)

    # Set non-blocking
    fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    async def pty_to_ws():
        try:
            while True:
                await asyncio.sleep(0.01)
                try:
                    data = os.read(master_fd, 1024)
                    if not data:
                        break
                    await websocket.send_bytes(data)
                except BlockingIOError:
                    continue
                except Exception:
                    break
        except Exception:
            pass

    task = asyncio.create_task(pty_to_ws())

    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "input":
                os.write(master_fd, data["data"].encode())
            elif data["type"] == "resize":
                buf = struct.pack("HHHH", data["rows"], data["cols"], 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, buf)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        task.cancel()
        if proc.poll() is None:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        os.close(master_fd)

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    folder: str = Form("movies"),
):
    if folder not in ALLOWED_FOLDERS:
        raise HTTPException(
            status_code=400,
            detail=f"folder must be one of {ALLOWED_FOLDERS}",
        )
    # Strip any path components from the client-supplied filename so an
    # upload can't escape MEDIA_DIR/<folder>/.
    safe_name = os.path.basename(file.filename or "")
    if not safe_name:
        raise HTTPException(status_code=400, detail="missing filename")

    target_dir = MEDIA_DIR / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / safe_name

    # Stream the file to disk in chunks instead of using shutil.copyfileobj
    # on the SpooledTemporaryFile, which can be slow/memory intensive for 1GB+
    try:
        with file_path.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024): # 1MB chunks
                buffer.write(chunk)
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    finally:
        await file.close()

    # Refresh the device-side player if it's open. refresh() handles both
    # the category screen and any open file list. Fall back to the legacy
    # _refresh_list() name if a stale build is somehow running.
    if _bb_app and _bb_app.media_view:
        try:
            if hasattr(_bb_app.media_view, "refresh"):
                _bb_app.media_view.refresh()
            else:
                _bb_app.media_view.list = _bb_app.media_view._refresh_list()
        except Exception as e:
            print(f"[web] media refresh failed: {e}")

    return {"filename": safe_name, "folder": folder, "status": "uploaded"}


@app.get("/media")
async def list_media():
    """Quick listing of what's in each folder, for the web UI to show."""
    out: dict[str, list[str]] = {}
    for sub in ALLOWED_FOLDERS:
        d = MEDIA_DIR / sub
        if d.is_dir():
            out[sub] = sorted(p.name for p in d.iterdir() if p.is_file())
        else:
            out[sub] = []
    return out

@app.get("/press/{button_name}")
async def press_button(button_name: str):
    if not _bb_app:
        return {"error": "App not initialized"}
    
    try:
        btn = Button(button_name.upper())
        # Inject press and release immediately for remote clicks
        _bb_app.bus.put(ButtonEvent(btn, pressed=True))
        await asyncio.sleep(0.05)
        _bb_app.bus.put(ButtonEvent(btn, pressed=False))
        return {"status": "ok", "button": button_name}
    except ValueError:
        return {"error": "Invalid button"}

async def frame_generator():
    import time as _time
    while True:
        if _bb_app:
            # Tell the main loop "yes, somebody is watching" so it keeps
            # encoding. Bumped per-iteration so the encode gate stays
            # open as long as a client keeps the connection.
            _bb_app.last_web_view_request = _time.time()
            if _bb_app.last_frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + _bb_app.last_frame + b'\r\n')
        await asyncio.sleep(0.1) # 10 FPS mirror is plenty for remote

@app.get("/video_feed")
async def video_feed():
    # Bump immediately so the first frame request kicks off encoding
    # even before frame_generator's first iteration.
    if _bb_app:
        import time as _time
        _bb_app.last_web_view_request = _time.time()
    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")


# ---------------- Wardrive / WiGLE -----------------------------------------

WARDRIVE_DIR = Path("loot/wardrive")


@app.get("/wigle/status")
async def wigle_status():
    creds = wigle_mod.load_creds()
    if not creds:
        return {"logged_in": False}
    return {"logged_in": True, "api_name": creds.api_name}


@app.post("/wigle/login")
async def wigle_login(api_name: str = Form(...), api_token: str = Form(...)):
    api_name = api_name.strip()
    api_token = api_token.strip()
    if not api_name or not api_token:
        raise HTTPException(status_code=400, detail="api_name and api_token required")
    creds = wigle_mod.WigleCreds(api_name=api_name, api_token=api_token)
    ok, msg = wigle_mod.validate_creds(creds)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)
    try:
        wigle_mod.save_creds(creds)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"could not persist creds: {e}")
    return {"logged_in": True, "api_name": api_name, "message": msg}


@app.post("/wigle/logout")
async def wigle_logout():
    wigle_mod.clear_creds()
    return {"logged_in": False}


@app.get("/wardrive/files")
async def wardrive_files():
    if not WARDRIVE_DIR.is_dir():
        return {"files": []}
    items = []
    for p in sorted(WARDRIVE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix in (".csv", ".gz"):
            items.append({
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": int(p.stat().st_mtime),
            })
    return {"files": items}


@app.post("/wardrive/upload/{name}")
async def wardrive_upload(name: str):
    safe = os.path.basename(name)
    target = WARDRIVE_DIR / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    creds = wigle_mod.load_creds()
    if not creds:
        raise HTTPException(status_code=401, detail="not signed in to WiGLE")
    ok, msg = wigle_mod.upload(target, creds)
    if not ok:
        raise HTTPException(status_code=502, detail=msg)
    return {"status": "uploaded", "message": msg, "filename": safe}


# ---------------- Games / ROMs ---------------------------------------------

@app.get("/roms")
async def list_roms():
    """Per-system rom listing (and the ps1-bios bucket)."""
    return emu_mod.list_all_roms()


@app.post("/roms/upload")
async def roms_upload(
    file: UploadFile = File(...),
    system: str = Form(...),
):
    if system not in emu_mod.WEB_SYSTEMS:
        raise HTTPException(
            status_code=400,
            detail=f"system must be one of {emu_mod.WEB_SYSTEMS}",
        )
    target_dir = emu_mod.upload_target_dir(system)
    if target_dir is None:
        raise HTTPException(status_code=400, detail="invalid system")
    safe_name = os.path.basename(file.filename or "")
    if not safe_name:
        raise HTTPException(status_code=400, detail="missing filename")

    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / safe_name
    try:
        with file_path.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                buffer.write(chunk)
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    finally:
        await file.close()

    if _bb_app and getattr(_bb_app, "games_view", None):
        try:
            _bb_app.games_view.refresh()
        except Exception as e:
            print(f"[web] games refresh failed: {e}")

    return {"status": "uploaded", "system": system, "filename": safe_name}


@app.delete("/roms/{system}/{name}")
async def roms_delete(system: str, name: str):
    target_dir = emu_mod.upload_target_dir(system)
    if target_dir is None:
        raise HTTPException(status_code=400, detail="invalid system")
    safe = os.path.basename(name)
    target = target_dir / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail="rom not found")
    try:
        target.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")
    if _bb_app and getattr(_bb_app, "games_view", None):
        try:
            _bb_app.games_view.refresh()
        except Exception:
            pass
    return {"status": "deleted", "system": system, "filename": safe}


# ---------------- RetroAchievements ----------------------------------------

@app.get("/retroachievements/status")
async def ra_status():
    creds = ra_mod.load_creds()
    if not creds:
        return {"logged_in": False}
    return {"logged_in": True, "username": creds.username}


@app.post("/retroachievements/login")
async def ra_login(username: str = Form(...), password: str = Form(...)):
    username = username.strip()
    if not username or not password:
        raise HTTPException(status_code=400,
                            detail="username and password required")
    ok, msg, creds = ra_mod.login(username, password)
    if not ok or not creds:
        raise HTTPException(status_code=401, detail=msg)
    try:
        ra_mod.save_creds(creds)
        ra_mod.apply_to_mgba_config(creds)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"persist failed: {e}")
    return {"logged_in": True, "username": creds.username, "message": msg}
from bigbox import webhooks as webhook_mod

if TYPE_CHECKING:
...
@app.post("/retroachievements/logout")
async def ra_logout():
    ra_mod.clear_creds()
    try:
        ra_mod.remove_from_mgba_config()
    except Exception:
        pass
    return {"logged_in": False}


# ---------------- Webhook --------------------------------------------------

@app.get("/webhook/status")
async def webhook_status():
    url = webhook_mod.load_webhook_url()
    return {"url": url or ""}


@app.post("/webhook/save")
async def webhook_save(url: str = Form(...)):
    url = url.strip()
    try:
        webhook_mod.save_webhook_url(url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"could not save webhook: {e}")
    return {"status": "ok", "url": url}

