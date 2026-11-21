"""Read-only device tools the agent can call: list_devices and run_show_command."""

from __future__ import annotations

import re
import time

from langchain_core.tools import StructuredTool

from netwarden.devices.allowlist import Allowlist
from netwarden.devices.connection import DeviceError, Runner
from netwarden.devices.inventory import Inventory, InventoryError
from netwarden.log import get_logger, redact_text
from netwarden.tools.sanitize import wrap_untrusted

log = get_logger("netwarden.devices")


def _apply_filter(output: str, command: str) -> str:
    """Apply '| include/exclude/begin/section/count' locally (same result, works on fixtures)."""
    _, _, flt = command.partition(" | ")
    if not flt:
        return output
    kind, _, text = flt.partition(" ")
    lines = output.splitlines()
    try:
        rx = re.compile(text, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(text), re.IGNORECASE)
    if kind == "include":
        lines = [ln for ln in lines if rx.search(ln)]
    elif kind == "exclude":
        lines = [ln for ln in lines if not rx.search(ln)]
    elif kind == "begin":
        idx = next((i for i, ln in enumerate(lines) if rx.search(ln)), len(lines))
        lines = lines[idx:]
    elif kind == "section":
        keep, out = False, []
        for ln in lines:
            if not ln.startswith(" "):
                keep = bool(rx.search(ln))
            if keep:
                out.append(ln)
        lines = out
    elif kind == "count":
        return f"Number of lines which match regexp = {sum(1 for ln in lines if rx.search(ln))}"
    return "\n".join(lines)


def make_device_tools(
    inventory: Inventory, allowlist: Allowlist, runner: Runner, max_output_chars: int = 6000
) -> list[StructuredTool]:
    def list_devices() -> str:
        """List the lab devices you may query, with their platform."""
        return "\n".join(f"{d.name} ({d.platform})" for d in inventory)

    def run_show_command(device: str, command: str) -> str:
        """Run ONE read-only show/diagnostic command on a lab device, e.g.
        device="R1", command="show ip ospf neighbor". Full command words only
        (no abbreviations). Config commands are always denied."""
        try:
            dev = inventory.get(device)
        except InventoryError as exc:
            return f"ERROR: {exc}"
        decision = allowlist.check(dev.platform, command)
        if not decision.allowed:
            log.warning("command_denied", device=dev.name, command=command, reason=decision.reason)
            return f"DENIED: {decision.command!r} - {decision.reason}."
        start = time.monotonic()
        try:
            base = decision.command.split(" | ")[0]
            output = _apply_filter(runner.run(dev, base), decision.command)
        except DeviceError as exc:
            log.error("command_failed", device=dev.name, command=decision.command, error=str(exc))
            return f"ERROR: {exc}"
        log.info(
            "device_command",
            device=dev.name,
            command=decision.command,
            duration_ms=round((time.monotonic() - start) * 1000),
            output_chars=len(output),
        )
        return wrap_untrusted(
            redact_text(output),
            device=dev.name,
            command=decision.command,
            max_chars=max_output_chars,
        )

    return [
        StructuredTool.from_function(list_devices),
        StructuredTool.from_function(run_show_command),
    ]
