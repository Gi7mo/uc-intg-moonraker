"""
Moonraker integration driver for Unfolded Circle Remote Two/3.

Entry point: sets up logging, the device and config managers, discovery and
the setup flow, then starts the integration API.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os

from button import create_buttons
from const import PrinterConfig
from device import MoonrakerDevice
from discover import SERVICE_TYPE, MoonrakerDiscovery
from media_player import PrinterMediaPlayer
from remote import PrinterRemote
from sensor import create_sensors
from setup import MoonrakerSetupFlow
from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path

LOGGERS = (
    "driver",
    "device",
    "camera",
    "media_player",
    "sensor",
    "button",
    "remote",
    "setup",
    "discover",
)


async def main():
    """Start the Moonraker integration driver."""
    logging.basicConfig(format="%(asctime)s %(levelname)-8s %(name)-12s %(message)s")
    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    for name in LOGGERS:
        logging.getLogger(name).setLevel(level)

    driver = BaseIntegrationDriver(
        device_class=MoonrakerDevice,
        entity_classes=[
            PrinterMediaPlayer,
            PrinterRemote,
            create_sensors,
            create_buttons,
        ],  # type: ignore[list-item]
    )

    driver.config_manager = BaseConfigManager(
        get_config_path(driver.api.config_dir_path),
        driver.on_device_added,
        driver.on_device_removed,
        config_class=PrinterConfig,
    )

    await driver.register_all_configured_devices()

    discovery = MoonrakerDiscovery(service_type=SERVICE_TYPE, timeout=2)
    setup_handler = MoonrakerSetupFlow.create_handler(driver, discovery=discovery)

    await driver.api.init("driver.json", setup_handler)

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
