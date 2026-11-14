"""
Setup flow.

Step 1 asks for the Moonraker connection (or picks a printer found via mDNS)
and verifies it. Step 2 offers the discovered heaters and macros as
checkboxes plus the camera refresh interval. "Update" in the integration
settings re-runs both steps with the stored values pre-filled.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import (
    DEFAULT_CAM_INTERVAL,
    DEFAULT_PORT,
    PrinterConfig,
    heater_label,
    make_identifier,
    to_bool,
)
from device import MoonrakerDevice
from ucapi import IntegrationSetupError, RequestUserInput, SetupError
from ucapi_framework import BaseSetupFlow, DiscoveredDevice

_LOG = logging.getLogger(__name__)

MIN_CAM_INTERVAL = 1
MAX_CAM_INTERVAL = 60


def _label(field_id: str, text: str) -> dict[str, Any]:
    return {"id": field_id, "label": {"en": ""}, "field": {"label": {"value": {"en": text}}}}


def _api_key_field(value: str = "") -> dict[str, Any]:
    return {
        "id": "api_key",
        "label": {"en": "API key (leave empty for trusted clients)"},
        "field": {"password": {"value": value}},
    }


class MoonrakerSetupFlow(BaseSetupFlow[PrinterConfig]):
    """Setup flow for Moonraker printers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the flow."""
        super().__init__(*args, **kwargs)
        self._heaters: list[str] = []
        self._macros: list[str] = []

    def get_manual_entry_form(self) -> RequestUserInput:
        """Return the connection form, pre-filled when updating a printer."""
        existing = self.selected_config_entry
        return RequestUserInput(
            {"en": "Moonraker printer"},
            [
                _label(
                    "info",
                    "Enter the address of your Moonraker instance (Klipper). "
                    "The remote must be in trusted_clients, or provide an API key.",
                ),
                {
                    "id": "name",
                    "label": {"en": "Printer name"},
                    "field": {"text": {"value": existing.name if existing else ""}},
                },
                {
                    "id": "host",
                    "label": {"en": "IP address or hostname"},
                    "field": {"text": {"value": existing.host if existing else ""}},
                },
                {
                    "id": "port",
                    "label": {"en": "Port"},
                    "field": {
                        "number": {
                            "value": existing.port if existing else DEFAULT_PORT,
                            "min": 1,
                            "max": 65535,
                        }
                    },
                },
                _api_key_field(existing.api_key if existing else ""),
                {
                    "id": "ssl",
                    "label": {"en": "Use HTTPS"},
                    "field": {"checkbox": {"value": existing.ssl if existing else False}},
                },
            ],
        )

    async def discover_devices(self) -> list[DiscoveredDevice]:
        """Skip discovery when updating an existing printer."""
        if self.selected_config_id is not None:
            return []
        return await super().discover_devices()

    def get_additional_discovery_fields(self) -> list[dict]:
        """Ask for the API key together with the discovered printer."""
        return [_api_key_field()]

    async def prepare_input_from_discovery(
        self, discovered: DiscoveredDevice, additional_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Map a discovered printer onto the manual entry fields."""
        return {
            "name": discovered.name,
            "host": discovered.address,
            "port": (discovered.extra_data or {}).get("port", DEFAULT_PORT),
            "api_key": additional_input.get("api_key", ""),
            "ssl": False,
        }

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> PrinterConfig | SetupError | RequestUserInput:
        """Verify the connection and read available heaters and macros."""
        host = str(input_values.get("host", "")).strip()
        if not host:
            return self.get_manual_entry_form()
        try:
            port = int(float(input_values.get("port") or DEFAULT_PORT))
        except ValueError:
            port = DEFAULT_PORT
        api_key = str(input_values.get("api_key", "")).strip()
        ssl = to_bool(input_values.get("ssl", False))

        try:
            info = await MoonrakerDevice.probe(host, port, api_key, ssl)
        except PermissionError as err:
            _LOG.error("Moonraker at %s:%s rejected access: %s", host, port, err)
            return SetupError(IntegrationSetupError.AUTHORIZATION_ERROR)
        except ConnectionError as err:
            _LOG.error("Cannot reach Moonraker at %s:%s: %s", host, port, err)
            return SetupError(IntegrationSetupError.CONNECTION_REFUSED)

        existing = self.selected_config_entry
        selected = existing.macros if existing else []
        self._heaters = info["heaters"]
        # "_" marks internal helper macros in Klipper; keep previously selected ones visible.
        self._macros = [m for m in info["macros"] if not m.startswith("_") or m in selected]
        if not self._heaters and existing:
            self._heaters = list(existing.heaters)
        if not self._macros and existing:
            self._macros = list(existing.macros)

        name = str(input_values.get("name", "")).strip() or info["hostname"] or host
        return PrinterConfig(
            identifier=existing.identifier if existing else make_identifier(host, port),
            name=name,
            host=host,
            port=port,
            api_key=api_key,
            ssl=ssl,
            cam_interval=existing.cam_interval if existing else DEFAULT_CAM_INTERVAL,
            heaters=list(existing.heaters) if existing else [],
            macros=list(existing.macros) if existing else [],
        )

    async def get_additional_configuration_screen(
        self, device_config: PrinterConfig, previous_input: dict[str, Any]
    ) -> RequestUserInput | None:
        """Show heater and macro selection plus the camera interval."""
        updating = self.selected_config_id is not None
        fields: list[dict[str, Any]] = [
            {
                "id": "cam_interval",
                "label": {"en": "Camera refresh interval (seconds)"},
                "field": {
                    "number": {
                        "value": device_config.cam_interval,
                        "min": MIN_CAM_INTERVAL,
                        "max": MAX_CAM_INTERVAL,
                    }
                },
            }
        ]

        if self._heaters:
            fields.append(_label("heaters_info", "Temperatures to show:"))
            for index, heater in enumerate(self._heaters):
                default = heater in device_config.heaters if updating else True
                fields.append(
                    {
                        "id": f"heater_{index}",
                        "label": {"en": f"{heater_label(heater)} ({heater})"},
                        "field": {"checkbox": {"value": default}},
                    }
                )

        if self._macros:
            fields.append(_label("macros_info", "Macros as buttons (press twice to run):"))
            for index, macro in enumerate(self._macros):
                fields.append(
                    {
                        "id": f"macro_{index}",
                        "label": {"en": macro},
                        "field": {"checkbox": {"value": macro in device_config.macros}},
                    }
                )

        if not self._heaters and not self._macros:
            fields.append(
                _label(
                    "klippy_info",
                    "Klipper is not ready, so temperatures and macros cannot be listed. "
                    "Use 'Update' in the integration settings once Klipper is running.",
                )
            )

        return RequestUserInput({"en": "Printer options"}, fields)

    async def handle_additional_configuration_response(self, msg: Any) -> None:
        """Store the selected heaters, macros and camera interval."""
        config = self._pending_device_config
        values = msg.input_values
        try:
            interval = int(float(values.get("cam_interval", DEFAULT_CAM_INTERVAL)))
        except ValueError:
            interval = DEFAULT_CAM_INTERVAL
        config.cam_interval = max(MIN_CAM_INTERVAL, min(MAX_CAM_INTERVAL, interval))
        config.heaters = [
            h for i, h in enumerate(self._heaters) if to_bool(values.get(f"heater_{i}"))
        ]
        config.macros = [
            m for i, m in enumerate(self._macros) if to_bool(values.get(f"macro_{i}"))
        ]
        _LOG.info(
            "Printer %s: heaters=%s macros=%s camera every %ss",
            config.name,
            config.heaters,
            config.macros,
            config.cam_interval,
        )
        return None
