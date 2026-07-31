"""Web API for Flipper Zero device control."""
from fastapi import APIRouter, WebSocket
from bigbox.flipper_zero_link import FliperZeroLink

router = APIRouter(prefix="/api/flipper", tags=["flipper"])

# Singleton instance
_flipper_link: FliperZeroLink | None = None


def get_flipper_link() -> FliperZeroLink:
    """Get or create Flipper Zero link."""
    global _flipper_link
    if _flipper_link is None:
        _flipper_link = FliperZeroLink()
        _flipper_link.start()
    return _flipper_link


@router.get("/status")
def get_status():
    """Get current Flipper Zero connection status."""
    link = get_flipper_link()
    st = link.snapshot()
    return {
        "connected": st.connected,
        "phase": st.phase,
        "device_name": st.device_name,
        "firmware_version": st.firmware_version,
        "battery": st.battery,
        "error": st.error,
    }


@router.get("/info")
def get_info():
    """Get detailed device information."""
    link = get_flipper_link()
    st = link.snapshot()
    return {
        "status": st.phase,
        "name": st.device_name,
        "firmware": st.firmware_version,
        "battery": st.battery,
        "last_update": st.last_update,
    }


@router.get("/apps")
def list_apps():
    """List installed apps on Flipper Zero."""
    link = get_flipper_link()
    return {"apps": link.list_apps()}


@router.post("/apps/{app_name}/launch")
def launch_app(app_name: str):
    """Launch an app on the Flipper Zero."""
    link = get_flipper_link()
    result = link.launch_app(app_name)
    return {"message": result}


@router.post("/command")
def send_command(command: str, args: list[str] | None = None):
    """Send a command to the Flipper Zero."""
    link = get_flipper_link()
    result = link.send_command(command, args or [])
    return {"result": result}


@router.post("/reboot")
def reboot_device():
    """Reboot the Flipper Zero."""
    link = get_flipper_link()
    result = link.send_command("reboot")
    return {"message": result}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time device monitoring."""
    await websocket.accept()
    link = get_flipper_link()

    try:
        while True:
            st = link.snapshot()
            await websocket.send_json({
                "type": "status",
                "connected": st.connected,
                "battery": st.battery,
                "phase": st.phase,
                "device_name": st.device_name,
            })
            import asyncio
            await asyncio.sleep(2)
    except Exception as e:
        await websocket.close(code=1000)
