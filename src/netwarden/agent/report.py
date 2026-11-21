"""The structured report every run ends with."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class Evidence(BaseModel):
    device: str
    command: str
    finding: str
    # Set by the agent, not the model: True only if this command really ran this session.
    verified: bool = False


class Report(BaseModel):
    likely_cause: str
    evidence: list[Evidence] = Field(default_factory=list)
    proposed_fix: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    next_steps: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    limits_hit: str | None = None  # e.g. "step limit (12)" - partial report

    def to_text(self) -> str:
        lines = [f"Likely cause: {self.likely_cause}", f"Confidence: {self.confidence}"]
        if self.limits_hit:
            lines.append(f"NOTE: stopped early - {self.limits_hit}; this report is partial.")
        if self.evidence:
            lines.append("Evidence:")
            for e in self.evidence:
                mark = "" if e.verified else " [UNVERIFIED - command was not run]"
                lines.append(f"  - {e.device} `{e.command}`: {e.finding}{mark}")
        if self.proposed_fix:
            lines.append(f"Proposed fix (not applied): {self.proposed_fix}")
        if self.next_steps:
            lines.append("Next steps:")
            lines += [f"  - {s}" for s in self.next_steps]
        if self.sources:
            lines.append("Sources: " + ", ".join(self.sources))
        return "\n".join(lines)


def parse_report(text: str) -> Report:
    """Parse the model's JSON answer. Small models are messy, so fall back gracefully."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return Report.model_validate(json.loads(match.group(0)))
        except (json.JSONDecodeError, ValidationError):
            pass
    return Report(
        likely_cause=text.strip()[:2000] or "No answer from model.",
        confidence="low",
        next_steps=["Model did not return valid JSON; review the raw answer above."],
    )
