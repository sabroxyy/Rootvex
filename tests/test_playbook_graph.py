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
    assert "passive" in report.likely_cause and report.confidence == "high"
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


def test_model_repeating_a_playbook_command_reuses_the_output():
    from tests.fake_llm import tool_call

    tools, names = tools_for("ospf_passive")
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="plan"),
            tool_call("run_show_command", {"device": "R1", "command": "show ip ospf neighbor"}),
            AIMessage(content="done"),
            AIMessage(content='{"likely_cause": "passive", "confidence": "high"}'),
        ],
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
    assert state["steps"] == 0  # reused, not run again
    tool_msg = [m for m in state["messages"] if m.type == "tool"][0]
    assert "already collected by the playbook" in tool_msg.content


def test_confident_but_wrong_model_is_overruled_by_proven_code_finding():
    # Seen on the workstation: the model said "both routers are DR" with high
    # confidence while the code had proof of a passive interface.
    tools, names = tools_for("ospf_passive")
    wrong = (
        '{"likely_cause": "Both routers are DR but have not exchanged hellos",'
        ' "proposed_fix": "check ACLs", "confidence": "high"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=wrong)],
        calls=[],
    )
    state = run_agent(
        "OSPF neighbor R1 R2 is down",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    r = state["report"]
    assert "passive" in r.likely_cause and r.confidence == "high"
    assert "no passive-interface" in r.proposed_fix
    assert r.model_view.startswith("Both routers are DR")
    assert r.evidence[0].quote == "No Hellos (Passive interface)" and r.evidence[0].verified
    assert "not supported by the code checks" in r.to_text()


def test_model_naming_the_wrong_interface_does_not_agree():
    # Live fault #6: the model said "flapping because Gi3/0 is admin down" while code
    # proved Gi1/0 flapping. "flapping" alone must not count as agreement.
    tools, names = tools_for("interface_flap")
    wrong = (
        '{"likely_cause": "The link is flapping because GigabitEthernet3/0 is '
        'administratively down", "proposed_fix": "interface Gi3/0 / no shutdown",'
        ' "confidence": "high"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=wrong)],
        calls=[],
    )
    state = run_agent(
        "R1 link is flapping",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    r = state["report"]
    assert r.likely_cause.startswith("R1 GigabitEthernet1/0 is flapping")
    assert "GigabitEthernet3/0" in r.model_view
    assert "physical link" in r.proposed_fix


def test_model_naming_the_same_interface_agrees():
    tools, names = tools_for("interface_flap")
    right = (
        '{"likely_cause": "Gi1/0 on R1 is flapping", "proposed_fix": "check the cable on Gi1/0",'
        ' "confidence": "high"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=right)],
        calls=[],
    )
    state = run_agent(
        "R1 link is flapping",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    assert state["report"].likely_cause == "Gi1/0 on R1 is flapping"
    assert state["report"].model_view == ""


def test_healthy_ipsec_overrules_an_invented_fault():
    # Live 27 Nov: tunnel up (QM_IDLE, 1910/1903 packets) but the model said
    # "IPsec SA not established". Code proof of health must win.
    tools, names = tools_for("ipsec_healthy")
    invented = (
        '{"likely_cause": "The IPsec tunnel is down because the IPsec SA is not '
        'established", "proposed_fix": "check crypto maps", "confidence": "medium"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=invented)],
        calls=[],
    )
    state = run_agent(
        "IPsec VPN tunnel between R2 and BR1 is down",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    r = state["report"]
    assert r.playbook == "IPsec VPN down"
    assert r.likely_cause.startswith("No fault found by the code checks")
    assert "QM_IDLE" in r.likely_cause and "both ways" in r.likely_cause
    assert r.model_view.startswith("The IPsec tunnel is down")
    assert r.proposed_fix == ""
    assert r.evidence and all(e.verified for e in r.evidence)
    assert any(c.startswith("OK: ") for c in r.code_findings)


def test_acl_beats_wrong_default_route_answer_on_a_bgp_router():
    # Live 27 Nov (#11): R2 learns 192.0.2.0/24 by BGP and has no default route by
    # design; the model blamed the missing default route while ACL BLOCK_WEB had hits.
    tools, names = tools_for("website_acl_bgp")
    wrong = (
        '{"likely_cause": "R2 lacks a default route, causing packets to be dropped before '
        'reaching the ACL that blocks outbound HTTP traffic", "confidence": "high"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=wrong)],
        calls=[],
    )
    state = run_agent(
        "user behind R2 cannot browse the web site",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    r = state["report"]
    assert r.likely_cause.startswith("R2: access list BLOCK_WEB denies tcp any any eq www")
    assert r.model_view.startswith("R2 lacks a default route")
    assert "BLOCK_WEB" in r.proposed_fix
    assert not any("traffic to internet addresses is dropped" in c for c in r.code_findings)


def test_healthy_lab_with_old_fault_logs_reports_no_fault():
    # Live 27 Nov health check after the fault tests: neighbors FULL, but the log
    # still held "FULL to DOWN" lines from earlier tests. That is history.
    tools, names = tools_for("ospf_healthy_oldlogs")
    story = (
        '{"likely_cause": "OSPF adjacency issues due to physical connectivity problems",'
        ' "confidence": "medium"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=story)],
        calls=[],
    )
    state = run_agent(
        "OSPF neighbor R1 R2 is down",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    r = state["report"]
    assert r.likely_cause.startswith("No fault found by the code checks")
    assert "FULL" in r.likely_cause
    assert not any("lost OSPF neighbor" in c for c in r.code_findings)
    assert r.model_view.startswith("OSPF adjacency issues")


def test_model_agreeing_the_lab_is_healthy_is_not_flagged():
    tools, names = tools_for("ospf_healthy_oldlogs")
    healthy = (
        '{"likely_cause": "OSPF adjacencies are in the FULL state on all routers, '
        'indicating healthy connections.", "confidence": "high"}'
    )
    llm = ScriptedChatModel(
        script=[AIMessage(content="plan"), AIMessage(content="done"), AIMessage(content=healthy)],
        calls=[],
    )
    state = run_agent(
        "OSPF neighbor R1 R2 is down",
        llm,
        tools,
        AgentSettings(),
        names,
        playbooks=load_playbooks(playbook_dir()),
    )
    r = state["report"]
    assert r.likely_cause.startswith("No fault found by the code checks")
    assert r.model_view == ""
