# Moonraker Integration Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

_Changes in the next release_

---

## v0.1.0 - 2026-11-14
### Added
- Moonraker websocket client with live status subscription and automatic reconnect.
- Print view media player: camera snapshot artwork, file, state, ETA, layer, progress bar;
  pause/resume, cancel, camera switching.
- Camera snapshots downsized to ≤ 420 px, refreshed while printing; gcode thumbnail or
  printer icon when idle.
- Temperature sensors (selectable) and status sensors (state, progress, remaining, file,
  layer, finish time).
- Buttons and remote entity with control and macro pages; double-press confirmation for
  cancel, emergency stop, firmware restart and macros.
- Setup flow with mDNS discovery, API key support and reconfiguration.
