"""Tests for mDNS discovery parsing."""

from discover import SERVICE_TYPE, MoonrakerDiscovery
from zeroconf import IPVersion


class FakeServiceInfo:
    def __init__(self, name, addresses, port):
        self.name = name
        self.port = port
        self._addresses = addresses

    def parsed_addresses(self, version=IPVersion.All):
        if version == IPVersion.V4Only:
            return [a for a in self._addresses if ":" not in a]
        return self._addresses


def parse(info):
    return MoonrakerDiscovery.parse_mdns_service(None, info)


def test_parses_ipv4_service():
    device = parse(FakeServiceInfo("voron." + SERVICE_TYPE, ["fe80::1", "192.168.1.50"], 7125))
    assert (device.name, device.address, device.identifier) == ("voron", "192.168.1.50", "192_168_1_50_7125")
    assert device.extra_data == {"port": 7125}


def test_default_port_and_name():
    device = parse(FakeServiceInfo(SERVICE_TYPE, ["10.0.0.5"], 0))
    assert device.extra_data == {"port": 7125}
    assert device.name


def test_ipv6_only_service_is_skipped():
    assert parse(FakeServiceInfo("x." + SERVICE_TYPE, ["fe80::1"], 7125)) is None
