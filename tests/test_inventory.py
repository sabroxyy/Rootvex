import ipaddress

import pytest

from netwarden.config import PROJECT_ROOT
from netwarden.devices.inventory import InventoryError, load_inventory

LAB = [ipaddress.IPv4Network("10.99.0.0/24")]


def write(tmp_path, body):
    p = tmp_path / "inv.yaml"
    p.write_text(body)
    return p


def test_example_inventory_loads():
    inv = load_inventory(PROJECT_ROOT / "config/inventory.example.yaml", LAB)
    assert inv.names() == ["BR1", "ISP1", "R1", "R2"]
    assert inv.get("r1").host == ipaddress.IPv4Address("10.99.0.11")


def test_host_outside_lab_is_refused(tmp_path):
    p = write(
        tmp_path,
        """devices:
  HOME:
    host: 192.168.1.1
    platform: cisco_ios
    username: x
    password_env: X
""",
    )
    with pytest.raises(InventoryError, match="outside the lab"):
        load_inventory(p, LAB)


def test_hostnames_not_allowed(tmp_path):
    p = write(
        tmp_path,
        """devices:
  R9:
    host: core.example.com
    platform: cisco_ios
    username: x
    password_env: X
""",
    )
    with pytest.raises(ValueError):
        load_inventory(p, LAB)


def test_inline_password_refused(tmp_path):
    p = write(
        tmp_path,
        """devices:
  R1:
    host: 10.99.0.11
    platform: cisco_ios
    username: x
    password: hunter2
    password_env: X
""",
    )
    with pytest.raises(InventoryError, match="password_env"):
        load_inventory(p, LAB)


def test_unknown_device(tmp_path):
    inv = load_inventory(PROJECT_ROOT / "config/inventory.example.yaml", LAB)
    with pytest.raises(InventoryError, match="unknown device"):
        inv.get("CORE1")
