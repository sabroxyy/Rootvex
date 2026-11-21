import pytest

from netwarden.config import PROJECT_ROOT
from netwarden.devices.allowlist import Allowlist


@pytest.fixture(scope="module")
def al():
    return Allowlist.load(PROJECT_ROOT / "config/allowlist")


@pytest.mark.parametrize(
    "cmd",
    [
        "show ip ospf neighbor",
        "  SHOW   ip   ospf neighbor  ",
        "show ip interface brief",
        "show interfaces GigabitEthernet0/1",
        "show ip route 10.0.12.0",
        "show running-config | section router ospf",
        "show logging | include OSPF",
        "ping 10.0.12.2 source GigabitEthernet0/1 repeat 5",
        "show crypto ipsec sa",
    ],
)
def test_allowed(al, cmd):
    assert al.check("cisco_ios", cmd).allowed, cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "configure terminal",
        "reload",
        "write memory",
        "copy running-config startup-config",
        "clear ip ospf process",
        "debug ip ospf adj",
        "sh run",  # abbreviations not accepted
        "show version\nreload",
        "show version ; reload",
        "show logging | include x | exclude y",
        "show logging | redirect flash:x",
        "show ip route `reload`",
        "",
        "ping 10.0.0.1 repeat 100000",
    ],
)
def test_denied(al, cmd):
    assert not al.check("cisco_ios", cmd).allowed, cmd


def test_unknown_platform_denied(al):
    assert not al.check("juniper_junos", "show version").allowed
