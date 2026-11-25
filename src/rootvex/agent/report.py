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
    # Exact line copied from that device's output for that command.
    quote: str = ""
    # Set by code, not the model: True only if the command ran on that device AND
    # the quote appears in that output. `check` says why when it is not verified.
    verified: bool = False
    check: str = ""


class Report(BaseModel):
    likely_cause: str
    evidence: list[Evidence] = Field(default_factory=list)
    proposed_fix: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    next_steps: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    limits_hit: str | None = None  # e.g. "step limit (12)" - partial report
    # Set by code: which playbook ran and what its code checks found (best first).
    playbook: str = ""
    code_findings: list[str] = Field(default_factory=list)

    def to_text(self) -> str:
        lines = [f"Likely cause: {self.likely_cause}", f"Confidence: {self.confidence}"]
        if self.limits_hit:
            lines.append(f"NOTE: stopped early - {self.limits_hit}; this report is partial.")
        if self.playbook:
            lines.append(f"Playbook: {self.playbook}")
        if self.code_findings:
            lines.append("Code checks found:")
            lines += [f"  - {c}" for c in self.code_findings]
        if self.evidence:
            lines.append("Evidence:")
            for e in self.evidence:
                mark = "" if e.verified else f" [UNVERIFIED - {e.check or 'not checked'}]"
                lines.append(f"  - {e.device} `{e.command}`: {e.finding}{mark}")
                if e.quote:
                    lines.append(f'      quote: "{e.quote}"')
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


def _squash(text: str) -> str:
    return " ".join(text.lower().split())


def verify_evidence(report: Report, outputs: dict[tuple[str, str], str]) -> None:
    """Mark each evidence item verified only if its quote is in that device's real output.

    outputs maps (device, command), both normalised, to the text the device returned.
    """
    for ev in report.evidence:
        key = (_squash(ev.device), _squash(ev.command))
        if key not in outputs:
            ev.verified, ev.check = False, "command was not run on this device"
        elif not ev.quote.strip():
            ev.verified, ev.check = False, "no quote from the output"
        elif _squash(ev.quote) not in _squash(outputs[key]):
            ev.verified, ev.check = False, "quote not found in this device's output"
        else:
            ev.verified, ev.check = True, "quote found in the output"
            lines = [ln.strip() for ln in ev.quote.splitlines() if ln.strip()]
            if len(lines) > 1:
                ev.quote = _best_line(lines, ev.finding)
                ev.check = f"quote found in the output (trimmed from {len(lines)} lines)"


def _best_line(lines: list[str], finding: str) -> str:
    """The quoted line sharing most words with the finding (later line wins a tie)."""

    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]\w+", text.lower()) if len(w) >= 4}

    want = words(finding)
    scored = [(len(want & words(ln)), i) for i, ln in enumerate(lines)]
    return lines[max(scored)[1]]


def add_code_findings(report: Report, playbook: str, findings: list) -> None:
    """Add the playbook's findings: their proof lines become evidence (checked later).

    If the model gave no usable cause, the best code finding becomes the cause.
    """
    report.playbook = playbook
    causes = [f for f in findings if not f.symptom]
    report.code_findings = [f.cause for f in findings]
    have = {(_squash(e.device), _squash(e.quote)) for e in report.evidence}
    for f in causes[:3] or findings[:2]:
        for p in f.proof:
            if (_squash(p.device), _squash(p.quote)) not in have:
                report.evidence.append(
                    Evidence(device=p.device, command=p.command, finding=f.cause, quote=p.quote)
                )
                have.add((_squash(p.device), _squash(p.quote)))
    weak = report.confidence == "low" or not report.likely_cause.strip()
    if causes and weak:
        report.likely_cause = causes[0].cause
        report.proposed_fix = report.proposed_fix or causes[0].fix
        report.confidence = "medium"
