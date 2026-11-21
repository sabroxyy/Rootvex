import json

from langchain_core.messages import AIMessage, ToolMessage

from netwarden.agent.graph import run_agent
from netwarden.agent.report import parse_report
from netwarden.config import AgentSettings
from netwarden.tools.fake import make_fake_tools
from tests.fake_llm import ScriptedChatModel, tool_call

GOOD_REPORT = json.dumps(
    {
        "likely_cause": "R1 Gi0/1 is administratively down.",
        "evidence": [
            {"device": "R1", "command": "show ip interface brief", "finding": "Gi0/1 admin down"},
            {"device": "R2", "command": "show ip ospf neighbor", "finding": "made up"},
        ],
        "proposed_fix": "interface Gi0/1 / no shutdown on R1",
        "confidence": "high",
        "next_steps": ["Confirm OSPF adjacency comes up"],
    }
)


def test_full_loop_with_fake_tool():
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="1. Check interfaces on R1"),
            tool_call("get_fake_interface_status", {"device": "R1"}),
            AIMessage(content="R1 Gi0/1 is admin down."),
            AIMessage(content=GOOD_REPORT),
        ],
        calls=[],
    )
    state = run_agent("OSPF R1-R2 down", llm, make_fake_tools(), AgentSettings())
    report = state["report"]

    assert state["steps"] == 1
    assert report.confidence == "high"
    assert report.limits_hit is None
    # Evidence from a command that really ran is verified; invented evidence is flagged.
    assert report.evidence[0].verified is True
    assert report.evidence[1].verified is False
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert "administratively down" in tool_msgs[0].content
    assert "<untrusted_device_output" in tool_msgs[0].content


def test_step_limit_produces_partial_report():
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="plan"),
            tool_call("get_fake_interface_status", {"device": "R1"}, "a"),
            tool_call("get_fake_interface_status", {"device": "R2"}, "b"),
            tool_call("get_fake_interface_status", {"device": "R1"}, "c"),  # over the limit
            AIMessage(content='{"likely_cause": "unsure", "confidence": "low"}'),
        ],
        calls=[],
    )
    state = run_agent("x", llm, make_fake_tools(), AgentSettings(max_steps=2))
    assert state["steps"] == 2
    assert state["report"].limits_hit == "step limit (2)"


def test_unknown_tool_and_bad_device_do_not_crash():
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="plan"),
            tool_call("format_flash", {}, "a"),
            tool_call("get_fake_interface_status", {"device": "CORE9"}, "b"),
            AIMessage(content="done"),
            AIMessage(content="not json at all"),
        ],
        calls=[],
    )
    state = run_agent("x", llm, make_fake_tools(), AgentSettings())
    contents = [m.content for m in state["messages"] if isinstance(m, ToolMessage)]
    assert "unknown tool" in contents[0]
    assert "unknown device" in contents[1]
    assert state["report"].confidence == "low"  # fallback report


def test_parse_report_handles_code_fences():
    r = parse_report('```json\n{"likely_cause": "x", "confidence": "medium"}\n```')
    assert r.likely_cause == "x" and r.confidence == "medium"
