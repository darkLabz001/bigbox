from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import pygame
from fastapi import FastAPI, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os
import shutil

from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MEDIA_DIR = Path("media")
MEDIA_DIR.mkdir(exist_ok=True)

# Global reference to the running Bigbox App
_bb_app: App | None = None

def set_app(bb_app: App):
    global _bb_app
    _bb_app = bb_app

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    file_path = MEDIA_DIR / file.filename
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # If media view is active, refresh it
    if _bb_app and _bb_app.media_view:
        _bb_app.media_view.list = _bb_app.media_view._refresh_list()
        
    return {"filename": file.filename, "status": "uploaded"}

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
