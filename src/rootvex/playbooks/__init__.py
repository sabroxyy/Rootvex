"""Troubleshooting playbooks: ordered read-only checks plus code that reads the results.

A playbook (config/playbooks/*.yaml) says which commands to run, in order, and
which known output lines prove a cause. Analyzers (rootvex.playbooks.analyzers)
compare both ends of a link. Every Finding carries the exact lines that prove it,
so the report can quote them and the quote check can verify them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Proof:
    device: str
    command: str
    quote: str  # exact line from that device's output


@dataclass
class Finding:
    cause: str
    fix: str = ""
    proof: list[Proof] = field(default_factory=list)
    weight: int = 50  # higher = more specific / more likely the root cause
    symptom: bool = False  # True: describes what is broken, not why


@dataclass
class Check:
    command: str
    why: str = ""
    devices: str = "named"  # named | all


@dataclass
class Signature:
    command: str  # base command whose output is searched
    regex: str
    cause: str  # may use {1}, {name} from the regex groups, and {device}
    fix: str = ""
    weight: int = 50
    symptom: bool = False


@dataclass
class Playbook:
    id: str
    title: str
    keywords: list[str]
    checks: list[Check]
    signatures: list[Signature] = field(default_factory=list)
    analyzers: list[str] = field(default_factory=list)

    def score(self, question: str) -> int:
        q = question.lower()
        return sum(1 for k in self.keywords if re.search(rf"\b{re.escape(k.lower())}\b", q))


def load_playbooks(directory: Path) -> list[Playbook]:
    books = []
    for f in sorted(directory.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        books.append(
            Playbook(
                id=d["id"],
                title=d["title"],
                keywords=d.get("keywords", []),
                checks=[Check(**c) for c in d.get("checks", [])],
                signatures=[Signature(**s) for s in d.get("signatures", [])],
                analyzers=d.get("analyzers", []),
            )
        )
    return books


def choose(books: list[Playbook], question: str) -> Playbook | None:
    scored = sorted(((b.score(question), b.id, b) for b in books), reverse=True)
    return scored[0][2] if scored and scored[0][0] > 0 else None


def devices_in(question: str, inventory_names: list[str]) -> list[str]:
    """Inventory devices named in the question, in inventory order."""
    found = [n for n in inventory_names if re.search(rf"\b{re.escape(n)}\b", question, re.I)]
    return found


def base(command: str) -> str:
    return command.split(" | ")[0].strip().lower()


def find_signatures(book: Playbook, outputs: dict[tuple[str, str], str]) -> list[Finding]:
    """Apply the playbook's regex signatures to every output of the matching command."""
    found = []
    for sig in book.signatures:
        rx = re.compile(sig.regex, re.I | re.M)
        for (device, command), text in outputs.items():
            if base(command) != base(sig.command):
                continue
            for m in rx.finditer(text):
                line = _line_at(text, m.start())
                # {1} = first regex group ({0} is unused), {name} = named group
                args = ("", *(g or "" for g in m.groups()))
                named = {"device": device, **m.groupdict()}
                found.append(
                    Finding(
                        cause=sig.cause.format(*args, **named),
                        fix=sig.fix.format(*args, **named),
                        proof=[Proof(device, command, line)],
                        weight=sig.weight,
                        symptom=sig.symptom,
                    )
                )
    return found


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end != -1 else len(text)].strip()


def analyze(book: Playbook, outputs: dict[tuple[str, str], str]) -> list[Finding]:
    """All findings for these outputs, most likely root cause first, duplicates removed."""
    from rootvex.playbooks import analyzers

    findings = find_signatures(book, outputs)
    for name in book.analyzers:
        findings += getattr(analyzers, name)(outputs)
    # Analyzers name the base command; point each proof at the command really run
    # (e.g. "show logging | include OSPF"), so the quote check finds that output.
    ran = {(d, base(c)): c for d, c in outputs}
    for f in findings:
        for p in f.proof:
            p.command = ran.get((p.device, base(p.command)), p.command)
    seen, unique = set(), []
    for f in sorted(findings, key=lambda f: (f.symptom, -f.weight)):
        key = f.cause  # the same cause seen twice (e.g. repeated log lines) is one finding
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def collect(
    book: Playbook, devices: list[str], run: Callable[[str, str], str | None]
) -> dict[tuple[str, str], str]:
    """Run every check on every device, in order. run() returns None when it failed."""
    outputs = {}
    for check in book.checks:
        for device in devices:
            text = run(device, check.command)
            if text is not None:
                outputs[(device, check.command)] = text
    return outputs


def playbook_dir() -> Path:
    from rootvex.config import PROJECT_ROOT

    return PROJECT_ROOT / "config" / "playbooks"
