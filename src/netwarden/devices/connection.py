"""Command runners: real SSH via Netmiko, or canned outputs from fixture files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

from netwarden.devices.inventory import Device
from netwarden.log import register_secret


class DeviceError(RuntimeError):
    pass


class Runner(Protocol):
    def run(self, device: Device, command: str) -> str: ...


class NetmikoRunner:
    """One SSH session per command - simple and stateless; pooling can come later."""

    def run(self, device: Device, command: str) -> str:
        from netmiko import ConnectHandler  # imported lazily: slow import, not needed in tests
        from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

        password = os.environ.get(device.password_env)
        if not password:
            raise DeviceError(f"env var {device.password_env} is not set (see .env.example)")
        register_secret(password)
        params = {
            "device_type": device.platform,
            "host": str(device.host),
            "username": device.username,
            "password": password,
            "port": device.port,
            "conn_timeout": device.timeout_s,
            "auth_timeout": device.timeout_s,
            "banner_timeout": device.timeout_s,
            "fast_cli": False,
        }
        try:
            with ConnectHandler(**params) as conn:
                return conn.send_command(command, read_timeout=device.timeout_s)
        except NetmikoTimeoutException as exc:
            raise DeviceError(f"timeout connecting to {device.name}: {exc}") from exc
        except NetmikoAuthenticationException as exc:
            raise DeviceError(f"authentication failed on {device.name}") from exc


def fixture_slug(command: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_")


class FixtureRunner:
    """Serve recorded output from <dir>/<platform>/<DEVICE>/<command_slug>.txt.

    Used by tests, by `--fixtures` demos, and later by the evaluation suite.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def run(self, device: Device, command: str) -> str:
        base = command.split(" | ")[0]
        path = self.directory / device.platform / device.name / f"{fixture_slug(base)}.txt"
        if not path.exists():
            raise DeviceError(f"no recorded output for {device.name}: {base!r}")
        return path.read_text()
