"""Settings — system controls."""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _vol_up(ctx: SectionContext) -> None:
    from bigbox import audio
    new = audio.nudge_volume(5)
    ctx.toast(f"volume {new}%" if new is not None else "vol +: no audio backend")


def _vol_down(ctx: SectionContext) -> None:
    from bigbox import audio
    new = audio.nudge_volume(-5)
    ctx.toast(f"volume {new}%" if new is not None else "vol -: no audio backend")


def _vol_mute(ctx: SectionContext) -> None:
    from bigbox import audio
    state = audio.toggle_mute()
    if state is None:
        ctx.toast("mute toggle: no audio backend")
    else:
        ctx.toast("muted" if state else "unmuted")


def _emulator_audio_card(ctx: SectionContext) -> None:
    """Pick which ALSA card emulators should send audio to. Persists
    to /etc/bigbox/emulator_audio.json so it survives bigbox restarts
    and OTA updates."""
    from bigbox import emulator as _emu
    current = _emu._emulator_audio_card()
    options = [
        ("HDMI (Card 0)",       0),
        ("Headphones (Card 1)", 1),
    ]
    actions = []
    for label, card in options:
        marker = " ●" if card == current else ""
        def make_handler(c=card, lbl=label):
            def _set():
                ok = _emu.set_emulator_audio_card(c)
                ctx.toast(f"Emulator audio → {lbl}" if ok
                          else "Could not save")
                ctx.go_back()
            return _set
        actions.append((f"{label}{marker}", make_handler()))
    ctx.show_menu("Emulator Audio Card", actions)


def _audio_test(ctx: SectionContext) -> None:
    """Play a short tone on each ALSA card in turn so the user can
    identify which one actually drives the GamePi43's built-in
    speaker. Runs in a background thread so the UI stays responsive
    while aplay blocks on each playback."""
    import subprocess
    import threading
    from pathlib import Path

    candidates = [
        ("HDMI (Card 0)",       "plughw:0,0"),
        ("Headphones (Card 1)", "plughw:1,0"),
    ]
    wav = Path("/usr/share/sounds/alsa/Front_Center.wav")
    if not wav.is_file():
        ctx.toast(f"missing test wav: {wav}")
        return

    def _worker():
        # Pre-bump both cards so a quiet mixer doesn't mask a working
        # output. Best-effort.
        for c in (0, 1):
            try:
                subprocess.run(
                    ["amixer", "-c", str(c), "sset", "PCM", "100%", "unmute"],
                    capture_output=True, timeout=2,
                )
            except Exception:
                pass
        for label, dev in candidates:
            ctx.toast(f"Audio test: {label}")
            try:
                subprocess.run(
                    ["aplay", "-D", dev, str(wav)],
                    timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                ctx.toast(f"{label} failed: {e}")
        ctx.toast("Audio test done — use Audio Output to set the working one")

    threading.Thread(target=_worker, daemon=True).start()


def _audio_output(ctx: SectionContext) -> None:
    from bigbox import audio
    sinks = audio.list_sinks()
    if not sinks:
        ctx.show_result("Audio Output",
                        "No pulse/pipewire sinks found.\n\n"
                        "Either pipewire-pulse isn't running, or "
                        "pulseaudio-utils isn't installed (Toolbox → "
                        "Verify Core Tools).")
        return
    actions = []
    for s in sinks:
        marker = " ●" if s.is_default else ""
        label = f"{s.description}{marker}"
        def make_handler(name=s.name, desc=s.description):
            def _set():
                ok = audio.set_default_sink(name)
                ctx.toast(f"output → {desc}" if ok else "set sink failed")
                ctx.go_back()
            return _set
        actions.append((label, make_handler()))
    ctx.show_menu("Audio Output", actions)


def _reboot(ctx: SectionContext) -> None:
    # bigbox runs as root under bigbox.service, so call systemctl
    # directly. Avoids the sudo round-trip and won't hang on a
    # missing sudoers entry.
    ctx.run_streaming("reboot", ["systemctl", "reboot"])


def _poweroff(ctx: SectionContext) -> None:
    ctx.run_streaming("poweroff", ["systemctl", "poweroff"])


def _view_loot(ctx: SectionContext) -> None:
    import os
    fname = "loot/flock_intel.txt"
    if not os.path.exists(fname):
        ctx.show_result("Flock Loot", "No loot captured yet.\n\nRun FlockSeeker to gather intel.")
        return
        
    try:
        with open(fname, "r") as f:
            content = f.read()
            if not content.strip():
                content = "Loot file is empty."
            ctx.show_result("Flock Loot", content)
    except Exception as e:
        ctx.show_result("Error", f"Could not read loot: {e}")


def _wifi_connect(ctx: SectionContext) -> None:
    ctx.show_wifi()


def _terminal(ctx: SectionContext) -> None:
    ctx.show_terminal()


def _theme_manager(ctx: SectionContext) -> None:
    ctx.show_theme_manager()


def _tailscale(ctx: SectionContext) -> None:
    ctx.show_tailscale()


def _web_access(ctx: SectionContext) -> None:
    ctx.show_web_access()


def _diagnostics(ctx: SectionContext) -> None:
    ctx.show_diagnostics()


def _background_tasks(ctx: SectionContext) -> None:
    ctx.show_background_tasks()


def _update(ctx: SectionContext) -> None:
    # Always resolve the script via the package layout, never via cwd.
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    ctx.show_update("OTA update", [str(script)])


def _toolbox_menu(ctx: SectionContext) -> None:
    from pathlib import Path
    script_dir = Path(__file__).resolve().parents[2] / "scripts"

    def fix_deps():
        ctx.show_update("Fixing Dependencies", [str(script_dir / "fix-deps.sh")])

    def install_osint():
        ctx.show_update("Installing OSINT Suite", [str(script_dir / "install-osint.sh")])

    def install_ragnar():
        ctx.show_update("Installing Ragnar", [str(script_dir / "install_ragnar.sh")])

    def setup_webhook():
        from bigbox import webhooks
        current = webhooks.load_webhook_url() or ""
        def save_cb(val):
            if val is not None:
                webhooks.save_webhook_url(val)
                ctx.toast("Webhook URL saved")
            ctx.go_back()
        ctx.get_input("Webhook URL", save_cb, current)

    actions = [
        ("Verify Core Tools", fix_deps),
        ("Install OSINT Suite", install_osint),
        ("Install Ragnar", install_ragnar),
        ("Webhook Setup", setup_webhook),
    ]
    ctx.show_menu("Toolbox", actions)


def _network_menu(ctx: SectionContext) -> None:
    ctx.show_menu("Network", [
        ("Web UI Access (QR)", lambda: ctx.show_web_access()),
        ("Connect to Wi-Fi",   lambda: ctx.show_wifi()),
        ("Tailscale VPN",      lambda: ctx.show_tailscale()),
    ])


def _diagnostics_menu(ctx: SectionContext) -> None:
    ctx.show_menu("Diagnostics", [
        ("Running Tasks",      lambda: ctx.show_background_tasks()),
        ("Recent Crashes",     lambda: ctx.show_diagnostics()),
        ("Send Loot Bundle",   lambda: _send_loot_bundle(ctx)),
        ("View Flock Loot",    lambda: _view_loot(ctx)),
    ])


def _send_loot_bundle(ctx: SectionContext) -> None:
    """Bundle every loot dir + captures and ship via the configured
    webhook. Toasts result. Whole pipeline runs in a background thread
    since gz over many MB can take a couple seconds."""
    import threading
    from bigbox import loot_export, webhooks

    def _worker():
        ctx.toast("Bundling loot...")
        path = loot_export.bundle()
        if path is None:
            ctx.toast("No loot to send (loot/ + captures/ are empty)")
            return
        size_mb = loot_export.bundle_size_mb(path)
        ctx.toast(f"Bundle: {size_mb} MB — uploading...")
        ok, msg = webhooks.send_file(str(path))
        ctx.toast(f"Loot bundle: {msg}" if ok else f"Upload failed: {msg}")

    threading.Thread(target=_worker, daemon=True).start()


def _power_menu(ctx: SectionContext) -> None:
    ctx.show_menu("Power & Audio", [
        ("Volume up",     lambda: _vol_up(ctx)),
        ("Volume down",   lambda: _vol_down(ctx)),
        ("Mute toggle",   lambda: _vol_mute(ctx)),
        ("Audio Output",        lambda: _audio_output(ctx)),
        ("Audio Test",          lambda: _audio_test(ctx)),
        ("Emulator Audio Card", lambda: _emulator_audio_card(ctx)),
        ("Reboot",        lambda: _reboot(ctx)),
        ("Power off",     lambda: _poweroff(ctx)),
    ])


def _system_menu(ctx: SectionContext) -> None:
    ctx.show_menu("System", [
        ("Bash Terminal",          lambda: ctx.show_terminal()),
        ("Theme Manager",          lambda: ctx.show_theme_manager()),
        ("Toolbox",                lambda: _toolbox_menu(ctx)),
        ("Check for updates (OTA)", lambda: _update(ctx)),
    ])


def build() -> Section:
    return Section(
        title="Settings",
        icon="[=]",
        icon_img=load_icon("settings"),
        background_img=load_background("settings"),
        actions=[
            # Top-level: most-used at the top, submenus for the rest.
            Action("Web UI Access", _web_access, "Scan a QR with your phone — auto login"),
            Action("Network",       _network_menu, "Wi-Fi, Tailscale, Web UI access"),
            Action("Diagnostics",   _diagnostics_menu, "Running tasks, crash log, loot"),
            Action("System",        _system_menu, "Terminal, themes, toolbox, OTA"),
            Action("Power & Audio", _power_menu, "Volume, reboot, power off"),
        ],
    )
