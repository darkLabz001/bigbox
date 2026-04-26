---
name: Project bigbox
description: Pentesting firmware/tool for Raspberry Pi 4 + Waveshare GamePi43, built from scratch in this dir
type: project
---

**bigbox** is a pentesting handheld firmware running on Raspberry Pi 4 with the Waveshare GamePi43 (4.3" 800×480 DPI panel, GPIO buttons, 3.5mm audio).

**Stack chosen 2026-04-25:** Python 3 + Pygame (SDL2) running on KMS/DRM framebuffer (no X), gpiozero for buttons, systemd to autostart, subprocess to drive existing pentest CLIs (nmap, aircrack-ng, bluetoothctl, bettercap, etc.).

**UI model:** horizontal carousel of Section pages (Recon / Network / Wireless / Bluetooth / Settings / About). L/R shoulders or D-pad LEFT/RIGHT switch sections. D-pad UP/DOWN scrolls content within a section. A activates, B backs out, START opens menu.

**Why:** user wants a from-scratch tool, not a reskin of Kali NetHunter or Pwnagotchi.

**How to apply:** when adding features, prefer wrapping a battle-tested CLI tool over reimplementing protocols. Keep section modules self-contained so adding a new tool is one new file in `bigbox/sections/`. Button pin map lives in `config/buttons.toml` — never hardcode GPIO numbers in Python.
