"""The agent's state: one typed dict passed between graph nodes."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from netwarden.agent.report import Report


class AgentState(TypedDict, total=False):
    question: str
    plan: str
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # tool calls executed so far
    started_at: float  # time.monotonic() at start
    executed: list[tuple[str, str]]  # (device, command) pairs that really ran
    limits_hit: str | None
    report: Report
