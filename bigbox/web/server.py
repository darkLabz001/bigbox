from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import pygame
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os
import shutil

from bigbox.events import Button, ButtonEvent
from bigbox import wigle as wigle_mod

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

    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

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
    while True:
        if _bb_app and _bb_app.last_frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + _bb_app.last_frame + b'\r\n')
        await asyncio.sleep(0.1) # 10 FPS mirror is plenty for remote

@app.get("/video_feed")
async def video_feed():
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
