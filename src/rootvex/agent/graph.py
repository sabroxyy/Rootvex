"""The LangGraph agent:  plan -> agent <-> tools -> report.

    plan    : model writes a short plan of read-only checks (no tools)
    agent   : model calls tools to collect evidence, or says it is done
    tools   : run the requested tool calls (each counts as one step)
    report  : model writes the final JSON report; we mark which evidence is verified

The loop stops when the model stops calling tools, the step limit is hit, or the
wall-clock timeout fires. Hitting a limit still produces a (partial) report.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence

import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from rootvex.agent import prompts
from rootvex.agent.report import parse_report
from rootvex.agent.state import AgentState
from rootvex.config import AgentSettings
from rootvex.log import get_logger, new_run_id

_OUTPUT_TAG = re.compile(r'<untrusted_device_output device="([^"]*)" command="([^"]*)">')

log = get_logger("rootvex.agent")


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _call_llm(llm: BaseChatModel, messages: list, node: str) -> AIMessage:
    """Invoke the model and log model name, latency and token usage."""
    start = time.monotonic()
    reply = llm.invoke(messages)
    usage = getattr(reply, "usage_metadata", None) or {}
    log.info(
        "model_call",
        node=node,
        model=getattr(llm, "model_name", type(llm).__name__),
        latency_ms=round((time.monotonic() - start) * 1000),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        tool_calls=[tc["name"] for tc in getattr(reply, "tool_calls", []) or []],
    )
    return reply


def build_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    cfg: AgentSettings,
    devices: Sequence[str] = (),
):
    tools_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(list(tools)) if tools else llm

    def time_left(state: AgentState) -> bool:
        return time.monotonic() - state["started_at"] < cfg.timeout_s

    # --- nodes -------------------------------------------------------------

    def plan_node(state: AgentState) -> dict:
        msg = prompts.PLAN.format(
            question=state["question"], devices=", ".join(devices) or "(see tools)"
        )
        reply = _call_llm(llm, [SystemMessage(prompts.SYSTEM), HumanMessage(msg)], "plan")
        plan = str(reply.content).strip()
        log.info("plan", plan=plan)
        collect = prompts.COLLECT.format(question=state["question"], plan=plan)
        return {"plan": plan, "messages": [SystemMessage(prompts.SYSTEM), HumanMessage(collect)]}

    def agent_node(state: AgentState) -> dict:
        reply = _call_llm(llm_with_tools, state["messages"], "agent")
        return {"messages": [reply]}

    def tools_node(state: AgentState) -> dict:
        last = state["messages"][-1]
        steps = state.get("steps", 0)
        executed = list(state.get("executed", []))
        out: list[ToolMessage] = []
        for call in last.tool_calls:
            if steps >= cfg.max_steps:
                result = f"ERROR: step limit ({cfg.max_steps}) reached; no more tool calls."
            elif call["name"] not in tools_by_name:
                result = f"ERROR: unknown tool {call['name']!r}."
            else:
                steps += 1
                try:
                    result = str(tools_by_name[call["name"]].invoke(call["args"]))
                except Exception as exc:  # a tool failure must not crash the run
                    result = f"ERROR: tool failed: {type(exc).__name__}: {exc}"
            for device, command in _OUTPUT_TAG.findall(result):
                executed.append((device, command))
            log.info(
                "tool_call", tool=call["name"], args=call["args"], step=steps, result=result[:2000]
            )
            out.append(ToolMessage(content=result, tool_call_id=call["id"], name=call["name"]))
        return {"messages": out, "steps": steps, "executed": executed}

    def report_node(state: AgentState) -> dict:
        limit = state.get("limits_hit")
        note = f"\nNOTE: evidence collection stopped early ({limit})." if limit else ""
        messages = list(state["messages"]) + [HumanMessage(prompts.REPORT.format(limit_note=note))]
        reply = _call_llm(llm, messages, "report")
        report = parse_report(str(reply.content))
        ran = {(_norm(d), _norm(c)) for d, c in state.get("executed", [])}
        for ev in report.evidence:
            ev.verified = (_norm(ev.device), _norm(ev.command)) in ran
        report.limits_hit = limit
        log.info("report", report=report.model_dump())
        return {"report": report}

    # --- routing -----------------------------------------------------------

    def after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return "report"
        if state.get("steps", 0) >= cfg.max_steps:
            return "limit"
        if not time_left(state):
            return "timeout"
        return "tools"

    def mark_step_limit(state: AgentState) -> dict:
        return {"limits_hit": f"step limit ({cfg.max_steps})"}

    def mark_timeout(state: AgentState) -> dict:
        return {"limits_hit": f"timeout ({cfg.timeout_s}s)"}

    def after_tools(state: AgentState) -> str:
        return "agent" if time_left(state) else "timeout"

    g = StateGraph(AgentState)
    g.add_node("plan", plan_node)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("report", report_node)
    g.add_node("step_limit", mark_step_limit)
    g.add_node("timeout", mark_timeout)
    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_conditional_edges(
        "agent",
        after_agent,
        {"tools": "tools", "report": "report", "limit": "step_limit", "timeout": "timeout"},
    )
    g.add_conditional_edges("tools", after_tools, {"agent": "agent", "timeout": "timeout"})
    g.add_edge("step_limit", "report")
    g.add_edge("timeout", "report")
    g.add_edge("report", END)
    return g.compile()


def run_agent(
    question: str,
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    cfg: AgentSettings,
    devices: Sequence[str] = (),
):
    """Run one troubleshooting session and return the final state (with `report`)."""
    run_id = new_run_id()
    structlog.contextvars.bind_contextvars(run_id=run_id)
    log.info("run_start", question=question, max_steps=cfg.max_steps, timeout_s=cfg.timeout_s)
    try:
        graph = build_graph(llm, tools, cfg, devices)
        # Each step is ~2 graph hops; give LangGraph headroom above our own limit.
        state = graph.invoke(
            {"question": question, "steps": 0, "started_at": time.monotonic(), "executed": []},
            config={"recursion_limit": cfg.max_steps * 3 + 10},
        )
        state["run_id"] = run_id
        log.info("run_end", steps=state.get("steps"), limits_hit=state.get("limits_hit"))
        return state
    finally:
        structlog.contextvars.unbind_contextvars("run_id")
