# Moonraker (Klipper) integration for Unfolded Circle Remote 3 / Remote Two

Monitor and control Klipper 3D printers through [Moonraker](https://moonraker.readthedocs.io/)
from your Unfolded Circle remote: webcam, print progress, temperatures, pause / resume,
cancel, emergency stop, firmware restart and your own Klipper macros.

Built on [ucapi-framework](https://github.com/JackJPowell/ucapi-framework) and
[uc-intg-template](https://github.com/JackJPowell/uc-intg-template).

## Features

- **Print view** (media player entity): camera snapshot as artwork, file name, state,
  progress, time remaining and layer, with a progress bar.
  - ⏯ Play/Pause pauses and resumes the print.
  - ⏹ Stop cancels it (press twice).
  - ⏭ / ⏮ or the source list switch between webcams.
- **Camera**: snapshots from all webcams configured in Moonraker (Mainsail/Fluidd/crowsnest).
  - The driver downsizes each snapshot to ≤ 420 px.
  - It refreshes every *N* seconds while a print is running or paused.
  - When idle it shows the slicer thumbnail of the last file, or a printer icon.
- **Temperatures**: one sensor per heater or temperature sensor, shown as `215 / 220 °C`.
  You choose which ones appear (default: all).
- **Status sensors**: state, progress %, time remaining, file, layer `current / total` and
  estimated finish time.
- **Controls** (button entities and a remote entity with ready-made pages):
  - pause / resume, cancel, emergency stop, firmware restart;
  - one button per selected Klipper macro.
- **Safety**: cancel, emergency stop, firmware restart and macros run only on a
  **second press within 3 seconds**. After the first press the status shows
  *"Press … again to confirm"*.
- **Multiple printers**: add each printer via the integration setup.
- **Live updates** via the Moonraker websocket (no polling), with automatic reconnect.
- **Discovery** via mDNS when Moonraker's `[zeroconf]` is enabled; manual entry always works.
- Stops all network traffic and camera refreshes while the remote is in standby.

## Platform limits

An integration can only provide *entities*. You arrange the pages on the remote yourself
in the web configurator. As a result:

- There is **no video stream**. The camera is a still image refreshed every few seconds
  (configurable, 1–60 s).
- The integration cannot force a page order or an "endless" swipe loop. The
  [recommended layout](#recommended-activity-layout) below gets close.
- Temperature sensors show text (`215 / 220 °C`), so the remote's automatic °C/°F
  conversion does not apply.
- The print view needs remote UI **0.73.4 or newer** (Settings → About). Older UI versions
  leave a media widget empty if its media player hasn't been loaded elsewhere yet, e.g. by
  opening it once from the entity list.

## Moonraker configuration

The remote (or the Docker host running this integration) must be allowed to access Moonraker.
You have two options:

**A) Trusted client** in `moonraker.conf`. Use your remote's IP or its subnet:

```ini
[authorization]
trusted_clients:
    192.168.1.0/24
```

**B) API key**: enter it during setup. You can read it in Mainsail/Fluidd or with
`curl http://<printer>:7125/access/api_key` from a trusted machine.

Optional:

- `[zeroconf]` in `moonraker.conf` enables automatic discovery.
- Webcams are read from Moonraker's webcam configuration, the same one Mainsail/Fluidd use.
  - Relative snapshot URLs like `/webcam/?action=snapshot` are fetched from the printer
    host on port 80/443, where nginx serves them.
  - Rotation and flip settings are applied.

## Installation

### On the remote (recommended)

1. Download `uc-intg-moonraker-<version>-aarch64.tar.gz` from the
   [releases](../../releases).
2. In the web configurator, go to *Integrations* → *Add new* → *Install custom* and upload the file.
3. Start the setup and enter the printer's host, port (default `7125`) and an optional API key.
4. On the second page, choose temperatures (default: all), macros and the camera interval.
   Only user-facing macros are listed (Klipper hides `_`-prefixed helpers), and none is selected by default.

To add more printers, or to change settings later, open the integration →
*Configure* → *Add a new device* / *Update information for selected device*.

### Docker

```bash
docker run -d --name uc-intg-moonraker --network host \
  -v $(pwd)/config:/config ghcr.io/gi7mo/uc-intg-moonraker:latest
```

The remote finds the integration via mDNS. Otherwise add it manually as
`ws://<docker-host>:9090`.

## Recommended activity layout

Create an activity (for example *"3D printer"*), add the printer's entities and build these pages:

| Page | Content |
|------|---------|
| 1 – Print | **Media player widget, full page (4×6)**: camera, file, state, ETA and progress bar. Tap it for the large artwork view. |
| 2 – Controls | Sensors *Status*, *Progress*, *Time remaining*, *Finish time* and your temperatures in the top half. Buttons *Pause / Resume*, *Cancel*, *Emergency stop* and *Firmware restart* in the bottom half. |
| 3 – Camera | Media player widget again, full page, for a large camera view while you work on page 2. |
| 4 – Macros | The macro buttons, or the remote entity's *Macros* page. |

The remote entity *"<printer> Controls"* also brings two ready-made pages, *Control* and
*Macros*, and every command as a simple command for physical button mapping.
Suggested mapping: Play/Pause → `PAUSE_RESUME`, Next/Prev → `NEXT_CAMERA` / `PREVIOUS_CAMERA`.

## Entities

Entity ids use the printer identifier `<host>_<port>`, for example `192_168_1_50_7125`.

| Entity | Id |
|--------|----|
| Print view | `media_player.<printer>` |
| Controls (remote) | `remote.<printer>` |
| Status sensors | `sensor.<printer>.status`, `.progress`, `.remaining`, `.file`, `.layer`, `.finish` |
| Temperatures | `sensor.<printer>.temp_<object>`, e.g. `temp_extruder`, `temp_heater_bed` |
| Buttons | `button.<printer>.pause_resume`, `.cancel`, `.emergency_stop`, `.firmware_restart`, `.macro_<name>` |

Remote entity simple commands: `PAUSE_RESUME`, `CANCEL`, `EMERGENCY_STOP`,
`FIRMWARE_RESTART`, `NEXT_CAMERA`, `PREVIOUS_CAMERA` and `M_<MACRO>` per selected macro.
Long macro names are shortened with a hash suffix to fit the 20-character limit.

## Development

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r test-requirements.txt
python -m pytest            # unit and integration tests against a fake Moonraker
python intg-moonraker/driver.py   # run locally; config is stored in ./config
```

`dev/docker-compose.yml` starts the integration with auto-reload, together with the
UC core simulator.

### Releasing

1. Bump `version` in `driver.json` and `pyproject.toml`, and add a `CHANGELOG.md` entry.
2. Push a tag `v<version>`. GitHub Actions builds the aarch64 tarball (PyInstaller) and the
   Docker image, then creates the release.

## License

Mozilla Public License 2.0, see [LICENSE](LICENSE).
