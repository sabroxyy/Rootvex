"""Treat device output as untrusted data before the model sees it (hard rule 4)."""

from __future__ import annotations

import re

# Control characters except tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TAG = "untrusted_device_output"


def wrap_untrusted(text: str, *, device: str, command: str, max_chars: int) -> str:
    """Strip control chars, truncate, and wrap output in tags the model must not obey."""
    text = _CONTROL.sub("", text.replace("\r\n", "\n"))
    # Stop output from closing our tag early and "escaping" into instructions.
    text = re.sub(rf"</?\s*{_TAG}", "[tag removed]", text, flags=re.IGNORECASE)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + f"\n[... truncated, {len(text) - max_chars} more chars ...]"
    safe_device = re.sub(r"[^\w.-]", "_", device)
    safe_command = command.replace('"', "'")
    return f'<{_TAG} device="{safe_device}" command="{safe_command}">\n{text}\n</{_TAG}>'
