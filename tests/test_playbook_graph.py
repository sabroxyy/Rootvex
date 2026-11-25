"""The playbook node inside the graph: checks run in code, findings reach the report."""

import ipaddress

from langchain_core.messages import AIMessage

from rootvex.agent.graph import run_agent
from rootvex.config import PROJECT_ROOT, AgentSettings
from rootvex.devices.allowlist import Allowlist
from rootvex.devices.connection import FixtureRunner
from rootvex.devices.inventory import load_inventory
from rootvex.playbooks import load_playbooks, playbook_dir
from rootvex.tools.device_tools import make_device_tools
from tests.fake_llm import ScriptedChatModel


def tools_for(scenario):
    inv = load_inventory(
        PROJECT_ROOT / "config/inventory.example.yaml", [ipaddress.IPv4Network("10.99.0.0/24")]
    )
    al = Allowlist.load(PROJECT_ROOT / "config/allowlist")
    runner = FixtureRunner(PROJECT_ROOT / "tests/fixtures/scenarios" / scenario)
    return make_device_tools(inv, al, runner), inv.names()


def test_weak_model_answer_is_replaced_by_the_code_finding():
    tools, names = tools_for("ospf_passive")
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="1. check OSPF"),
            AIMessage(content="I am not sure."),  # no tool call: straight to report
            AIMessage(content='{"likely_cause": "unknown", "confidence": "low"}'),
        ],
        calls=[],
    )
    events = []
    state = run_agent(
        "OSPF neighbor R1 R2 is down",
        llm,
        tools,
        AgentSettings(),
        names,
        on_event=events.append,
        playbooks=load_playbooks(playbook_dir()),
    )
    report = state["report"]
    assert report.playbook == "OSPF neighbor down"
    assert "passive" in report.likely_cause and report.confidence == "medium"
    assert "no passive-interface" in report.proposed_fix
    assert report.evidence and all(e.verified for e in report.evidence)
    # The model saw the code findings before it answered.
    collect_prompt = llm.calls[1][-1].content
    assert "passive OSPF interface" in collect_prompt
    assert any(e["type"] == "findings" for e in events)
    # Playbook checks do not use the model's step budget.
    assert state["steps"] == 0


def test_strong_model_answer_is_kept():
    tools, names = tools_for("ospf_timers")
    good = (
        '{"likely_cause": "Hello/dead timers differ on R1-R2", "confidence": "high",'
        ' "evidence": [{"device": "R2", "command": "show ip ospf interface",'
        ' "finding": "hello 5", "quote": "Timer intervals configured, Hello 5, Dead 20"}]}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=good)],
        calls=[],
    )
    state = run_agent(
        "OSPF R1 R2 down",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    report = state["report"]
    assert report.likely_cause.startswith("Hello/dead") and report.confidence == "high"
    assert report.evidence[0].verified
    assert any("timer mismatch" in c for c in report.code_findings)


def test_no_playbook_for_unrelated_question():
    tools, names = tools_for("ospf_timers")
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="plan"),
            AIMessage(content="done"),
            AIMessage(content='{"likely_cause": "n/a", "confidence": "low"}'),
        ],
        calls=[],
    )
    state = run_agent(
        "what version is R1",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    assert state["report"].playbook == "" and state["report"].likely_cause == "n/a"
