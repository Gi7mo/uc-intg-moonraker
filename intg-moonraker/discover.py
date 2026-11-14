"""
Moonraker discovery.

Finds Moonraker instances that advertise themselves via mDNS. This requires
the ``[zeroconf]`` section in ``moonraker.conf``; manual entry always works.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import DEFAULT_PORT, make_identifier
from ucapi_framework import DiscoveredDevice
from ucapi_framework.discovery import MDNSDiscovery

_LOG = logging.getLogger(__name__)

SERVICE_TYPE = "_moonraker._tcp.local."


class MoonrakerDiscovery(MDNSDiscovery):
    """Discover Moonraker instances via mDNS."""

    def parse_mdns_service(self, service_info: Any) -> DiscoveredDevice | None:
        """Turn a zeroconf service info into a discovered printer."""
        addresses = service_info.parsed_addresses()
        if not addresses:
            return None
        address = addresses[0]
        port = service_info.port or DEFAULT_PORT
        name = service_info.name.removesuffix("." + SERVICE_TYPE) or address
        _LOG.debug("Discovered Moonraker %s at %s:%s", name, address, port)
        return DiscoveredDevice(
            identifier=make_identifier(address, port),
            name=name,
            address=address,
            extra_data={"port": port},
        )
