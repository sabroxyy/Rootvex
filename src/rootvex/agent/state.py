"""The agent's state: one typed dict passed between graph nodes."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from rootvex.agent.report import Report


class AgentState(TypedDict, total=False):
    question: str
    plan: str
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # tool calls executed so far
    started_at: float  # time.monotonic() at start
    executed: list[tuple[str, str]]  # (device, command) pairs that really ran
    outputs: dict[str, str]  # "device|command" (normalised) -> text the device returned
    limits_hit: str | None
    playbook: str  # title of the playbook that ran, "" if none
    findings: list  # rootvex.playbooks.Finding, best first
    cache: dict[str, str]  # "device|command" -> tool result the playbook already got
    report: Report
