"""Phase 2 fake tool: proves the agent loop without any devices."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from netwarden.tools.sanitize import wrap_untrusted

_FAKE_STATUS = {
    "R1": """Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     10.99.0.11      YES manual up                    up
GigabitEthernet0/1     10.0.12.1       YES manual administratively down down
GigabitEthernet0/2     10.10.10.1      YES manual up                    up""",
    "R2": """Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     10.99.0.12      YES manual up                    up
GigabitEthernet0/1     10.0.12.2       YES manual up                    down
GigabitEthernet0/2     203.0.113.1     YES manual up                    up""",
}


def get_fake_interface_status(device: str) -> str:
    """Return `show ip interface brief` for a lab device (fake data). Devices: R1, R2."""
    output = _FAKE_STATUS.get(device.upper())
    if output is None:
        return f"ERROR: unknown device {device!r}. Known devices: {', '.join(_FAKE_STATUS)}"
    return wrap_untrusted(
        output, device=device.upper(), command="show ip interface brief", max_chars=6000
    )


def make_fake_tools() -> list[StructuredTool]:
    return [StructuredTool.from_function(get_fake_interface_status)]
