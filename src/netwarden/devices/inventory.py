"""Load the lab inventory and refuse any device outside the allowed lab networks (hard rule 1)."""

from __future__ import annotations

import ipaddress
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class InventoryError(ValueError):
    pass


class Device(BaseModel):
    name: str
    host: ipaddress.IPv4Address  # IP only - no hostnames, so no DNS tricks
    platform: str  # Netmiko device_type, e.g. cisco_ios
    username: str
    password_env: str  # NAME of the env var holding the password
    port: int = 22
    timeout_s: int = Field(20, ge=1, le=300)


class Inventory:
    def __init__(self, devices: dict[str, Device]):
        self._devices = devices

    def names(self) -> list[str]:
        return sorted(self._devices)

    def get(self, name: str) -> Device:
        """Look up by inventory key (case-insensitive). The model can only name devices this way."""
        for key, dev in self._devices.items():
            if key.lower() == name.strip().lower():
                return dev
        raise InventoryError(f"unknown device {name!r}; known: {', '.join(self.names())}")

    def __iter__(self):
        return iter(self._devices.values())


def load_inventory(path: Path, allowed_networks: list[ipaddress.IPv4Network]) -> Inventory:
    if not path.exists():
        raise InventoryError(
            f"inventory file not found: {path} "
            "(copy config/inventory.example.yaml to config/inventory.local.yaml)"
        )
    raw = yaml.safe_load(path.read_text()) or {}
    devices: dict[str, Device] = {}
    for name, fields in (raw.get("devices") or {}).items():
        if "password" in fields:
            raise InventoryError(f"{name}: put passwords in .env and use password_env")
        dev = Device(name=name, **fields)
        if not any(dev.host in net for net in allowed_networks):
            nets = ", ".join(str(n) for n in allowed_networks)
            raise InventoryError(f"{name}: {dev.host} is outside the lab networks ({nets})")
        devices[name] = dev
    if not devices:
        raise InventoryError(f"no devices in {path}")
    return Inventory(devices)
