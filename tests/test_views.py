"""Smoke tests for bigbox views — catches the "device crashes when
you open X" class before it ships.

Two checks per registered view:

1. The owning module imports cleanly. Catches things like a stale
   import name, a module-level call to a missing symbol, or a syntax
   error introduced in passing.
2. (Best-effort) The view instantiates with no args and ``render()``
   returns without raising on a dummy 800×480 surface. Views that
   need runtime context (KeyboardView callback, ResultView text,
   RagnarView phase, …) get import-only coverage instead.

Also asserts that every entry in ``App._VIEWS`` has a test case so the
next view that lands gets caught here too.

Run:
    python -m tests.test_views          # standalone, exit code = #fails
    pytest -q tests/test_views.py       # inside CI

Must run on (or with) access to the device's filesystem — many views
poke at /usr/share/wordlists, /etc/bigbox, /sys/class/net etc. on init.
"""
from __future__ import annotations

import importlib
import os
import sys
import traceback


# Pygame needs SOMETHING to draw to. The dummy SDL drivers don't open
# a window or audio device — perfect for headless smoke tests.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("BIGBOX_DEV", "1")

import pygame  # noqa: E402

pygame.display.init()
pygame.font.init()
pygame.display.set_mode((800, 480))


# (view_attr, module_path, class_name, ctor)
# ctor is a callable taking the class and returning an instance, or
# None to skip instantiation (import-only). Order matches App._VIEWS.
VIEW_CASES: list[tuple[str, str, str, object]] = [
    ("kb_view",            "bigbox.ui.keyboard",         "KeyboardView",          None),
    ("cctv_view",          "bigbox.ui.cctv",             "CCTVView",              lambda C: C()),
    ("ping_view",          "bigbox.ui.pingsweep",        "PingSweepView",         lambda C: C()),
    ("arp_view",           "bigbox.ui.arpscan",          "ARPScanView",           lambda C: C()),
    ("flock_view",         "bigbox.ui.flock",            "FlockScannerView",      lambda C: C()),
    ("wifi_view",          "bigbox.ui.wifi_connect",     "WifiConnectView",       lambda C: C()),
    ("cam_scan_view",      "bigbox.ui.cam_scanner",      "CamScannerView",        lambda C: C()),
    ("wifi_attack_view",   "bigbox.ui.wifi_attack",      "WifiAttackView",        None),  # iface picker → None safe
    ("wifi_multi_view",    "bigbox.ui.wifi_multi_tool",  "WifiMultiToolView",     None),
    ("cracker_view",       "bigbox.ui.cracker",          "OfflineCrackerView",    lambda C: C()),
    ("data_sniper_view",   "bigbox.ui.data_sniper",      "DataSniperView",        None),
    ("pmkid_sniper_view",  "bigbox.ui.pmkid_sniper",     "PMKIDSniperView",       lambda C: C()),
    ("media_view",         "bigbox.ui.media_player",     "MediaPlayerView",       lambda C: C()),
    ("tv_view",            "bigbox.ui.tv",               "InternetTVView",        None),
    ("youtube_view",       "bigbox.ui.youtube",          "YouTubeView",           None),
    ("tailscale_view",     "bigbox.ui.tailscale",        "TailscaleView",         None),
    ("anon_surf_view",     "bigbox.ui.anonsurf",         "AnonSurfView",          None),
    ("vault_view",         "bigbox.ui.vault",            "VaultView",             None),
    ("bettercap_view",     "bigbox.ui.bettercap",        "BettercapView",         None),
    ("mail_view",          "bigbox.ui.mail",             "MailView",              None),
    ("messenger_view",     "bigbox.ui.messenger",        "MessengerView",         None),
    ("ragnar_view",        "bigbox.ui.ragnar",           "RagnarView",            None),  # takes phase
    ("scraper_view",       "bigbox.ui.signal_scraper",   "SignalScraperView",     None),
    ("traffic_cam_view",   "bigbox.ui.traffic_cam",      "TrafficCamView",        None),
    ("camera_view",        "bigbox.ui.camera_interceptor", "CameraInterceptorView", None),
    ("wifite_view",        "bigbox.ui.wifite",           "WifiteView",            None),
    ("chat_view",          "bigbox.ui.chat",             "ChatView",              None),
    ("sherlock_view",      "bigbox.ui.sherlock",         "SherlockView",          None),  # takes username
    ("deaddrop_view",      "bigbox.ui.deaddrop",         "DeadDropView",          None),
    ("bbs_view",           "bigbox.ui.bbs",              "BBSView",               None),
    ("ble_view",           "bigbox.ui.ble_chat",         "BLEChatView",           None),
    ("onion_view",         "bigbox.ui.onion_chat",       "OnionChatView",         None),
    ("ble_spam_view",      "bigbox.ui.ble_spam",         "BLESpamView",           None),
    ("terminal_view",      "bigbox.ui.terminal",         "TerminalView",          None),
    ("theme_manager_view", "bigbox.ui.theme_manager",    "ThemeManagerView",      None),
    ("wardrive_view",      "bigbox.ui.wardrive",         "WardriveView",          None),
    ("eviltwin_view",      "bigbox.ui.eviltwin",         "EvilTwinView",          None),
    ("honeypot_view",      "bigbox.ui.honeypot",         "HoneypotView",          lambda C: C()),
    ("captures_view",      "bigbox.ui.captures",         "CapturesView",          lambda C: C()),
    ("scan_history_view",  "bigbox.ui.scan_history",     "ScanHistoryView",       lambda C: C()),
    ("web_access_view",    "bigbox.ui.web_access",       "WebAccessView",         lambda C: C()),
    ("phone_camera_view",  "bigbox.ui.phone_camera",     "PhoneCameraView",       lambda C: C()),
    ("diagnostics_view",   "bigbox.ui.diagnostics",      "DiagnosticsView",       lambda C: C()),
    ("bg_tasks_view",      "bigbox.ui.background_tasks", "BackgroundTasksView",   lambda C: C()),
    ("tracker_history_view","bigbox.ui.tracker_history", "TrackerHistoryView",    lambda C: C()),
    ("breach_check_view",  "bigbox.ui.breach_check",     "BreachCheckView",       lambda C: C()),
    ("subdomain_enum_view","bigbox.ui.subdomain_enum",   "SubdomainEnumView",     lambda C: C()),
    ("user_pivot_view",    "bigbox.ui.user_pivot",       "UserPivotView",         lambda C: C()),
    ("exif_inspector_view","bigbox.ui.exif_inspector",   "ExifInspectorView",     lambda C: C()),
    ("games_view",         "bigbox.ui.games",            "GamesView",             None),
    ("tracker_view",       "bigbox.ui.trackers",         "TrackerView",           None),
    ("probe_view",         "bigbox.ui.wifi_lite",        "ProbeSnifferView",      None),
    ("beacon_view",        "bigbox.ui.wifi_lite",        "BeaconFloodView",       None),
    ("karma_view",         "bigbox.ui.wifi_lite",        "KarmaLiteView",         None),
    ("update_view",        "bigbox.ui.update",           "UpdateView",            None),  # takes argv
    ("result_view",        "bigbox.ui.widgets",          "ResultView",            lambda C: C("Test", "Body")),
]


def run() -> int:
    surf = pygame.Surface((800, 480))
    passes: list[str] = []
    failures: list[str] = []

    for attr, modname, classname, ctor in VIEW_CASES:
        try:
            mod = importlib.import_module(modname)
            cls = getattr(mod, classname)
        except Exception as e:
            traceback.print_exc()
            failures.append(f"{attr}: import error ({modname}.{classname}): "
                            f"{type(e).__name__}: {e}")
            continue

        if ctor is None:
            passes.append(f"{attr} (import only)")
            continue

        try:
            v = ctor(cls)
            v.render(surf)
        except Exception as e:
            traceback.print_exc()
            failures.append(f"{attr}: instantiate/render: "
                            f"{type(e).__name__}: {e}")
            continue
        passes.append(f"{attr}")

    # Cross-check: every entry in App._VIEWS must appear in VIEW_CASES,
    # and vice-versa. Catches "added a view, forgot the test case."
    try:
        from bigbox.app import _VIEWS
    except Exception as e:
        failures.append(f"could not import bigbox.app._VIEWS: {e}")
    else:
        cased = {c[0] for c in VIEW_CASES}
        registered = {n for n, _ in _VIEWS}
        missing = registered - cased
        extra = cased - registered
        if missing:
            failures.append(f"missing test cases for: {sorted(missing)}")
        if extra:
            failures.append(f"test cases for views not in _VIEWS: "
                            f"{sorted(extra)}")

    print()
    print("=" * 64)
    print(f"{len(passes)} passed · {len(failures)} failed")
    for line in passes:
        print(f"  PASS  {line}")
    for line in failures:
        print(f"  FAIL  {line}")
    return 1 if failures else 0


# pytest discovery
def test_views():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
