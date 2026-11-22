"""Deny-by-default command allowlist (hard rule 2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Characters that could chain commands, start config mode, or smuggle control input.
_FORBIDDEN = re.compile(r"[\n\r;&`$<>\\\x00-\x1f\x7f]")
_FILTER = re.compile(r"^(include|exclude|begin|section|count) [^|]{1,80}$")


@dataclass
class Decision:
    allowed: bool
    command: str  # normalized command (what will actually be sent)
    reason: str = ""


def normalize(command: str) -> str:
    return " ".join(command.strip().split())


class Allowlist:
    def __init__(self, patterns_by_platform: dict[str, list[re.Pattern]]):
        self._patterns = patterns_by_platform

    @classmethod
    def load(cls, directory: Path) -> Allowlist:
        patterns: dict[str, list[re.Pattern]] = {}
        for file in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(file.read_text()) or {}
            compiled = [re.compile(p) for p in data.get("commands", [])]
            for bad in (p.pattern for p in compiled):
                if not (bad.startswith("^") and bad.endswith("$")):
                    raise ValueError(f"{file.name}: pattern must be anchored with ^...$: {bad}")
            for platform in data.get("platforms", [file.stem]):
                patterns.setdefault(platform, []).extend(compiled)
        return cls(patterns)

    def check(self, platform: str, command: str) -> Decision:
        cmd = normalize(command)
        if not cmd:
            return Decision(False, cmd, "empty command")
        if _FORBIDDEN.search(command):
            return Decision(False, cmd, "forbidden character (newline, ;, &, `, $, <, >)")
        patterns = self._patterns.get(platform)
        if not patterns:
            return Decision(False, cmd, f"no allowlist for platform {platform!r}")

        base, _, output_filter = cmd.partition(" | ")
        if "|" in base or "|" in output_filter:
            return Decision(False, cmd, "only one '| include/exclude/begin/section/count' allowed")
        if output_filter and not _FILTER.match(output_filter):
            return Decision(False, cmd, "output filter must be include/exclude/begin/section/count")

        base_lc = base.lower()
        if any(p.match(base_lc) for p in patterns):
            return Decision(True, cmd)
        return Decision(False, cmd, "not on the read-only allowlist (abbreviations not accepted)")
