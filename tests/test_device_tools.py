import ipaddress
import json

import pytest
from langchain_core.messages import AIMessage

from netwarden.agent.graph import run_agent
from netwarden.config import PROJECT_ROOT, AgentSettings
from netwarden.devices.allowlist import Allowlist
from netwarden.devices.connection import FixtureRunner
from netwarden.devices.inventory import load_inventory
from netwarden.tools.device_tools import make_device_tools
from tests.fake_llm import ScriptedChatModel, tool_call

LAB = [ipaddress.IPv4Network("10.99.0.0/24")]


class SpyRunner(FixtureRunner):
    def __init__(self, directory):
        super().__init__(directory)
        self.sent = []

    def run(self, device, command):
        self.sent.append((device.name, command))
        return super().run(device, command)


@pytest.fixture
def setup():
    inv = load_inventory(PROJECT_ROOT / "config/inventory.example.yaml", LAB)
    al = Allowlist.load(PROJECT_ROOT / "config/allowlist")
    runner = SpyRunner(PROJECT_ROOT / "tests/fixtures")
    tools = {t.name: t for t in make_device_tools(inv, al, runner)}
    return tools, runner


def test_allowed_command_returns_wrapped_output(setup):
    tools, runner = setup
    out = tools["run_show_command"].invoke({"device": "r2", "command": "show ip protocols"})
    assert '<untrusted_device_output device="R2" command="show ip protocols">' in out
    assert "area 1" in out
    assert runner.sent == [("R2", "show ip protocols")]


def test_denied_command_never_reaches_device(setup):
    tools, runner = setup
    out = tools["run_show_command"].invoke({"device": "R1", "command": "configure terminal"})
    assert out.startswith("DENIED")
    assert runner.sent == []


def test_unknown_device(setup):
    tools, runner = setup
    out = tools["run_show_command"].invoke({"device": "10.0.0.1", "command": "show version"})
    assert out.startswith("ERROR: unknown device")
    assert runner.sent == []


def test_output_filter_and_secret_redaction(setup):
    tools, _ = setup
    out = tools["run_show_command"].invoke(
        {"device": "R1", "command": "show running-config | include secret|key"}
    )
    # Two pipes would be denied; check the single-filter path separately.
    assert out.startswith("DENIED")
    out = tools["run_show_command"].invoke({"device": "R1", "command": "show running-config"})
    assert "0822455D0A16" not in out and "$9$abcdefghijklmn" not in out
    assert "router-id 1.1.1.1" in out
    out = tools["run_show_command"].invoke(
        {"device": "R1", "command": "show logging | include OSPF"}
    )
    assert "mismatched area ID" in out and "LINEPROTO" not in out


def test_missing_fixture_is_an_error_not_a_crash(setup):
    tools, _ = setup
    out = tools["run_show_command"].invoke({"device": "R1", "command": "show version"})
    assert out.startswith("ERROR: no recorded output")


def test_agent_diagnoses_ospf_area_mismatch_end_to_end(setup):
    tools, _ = setup
    report_json = json.dumps(
        {
            "likely_cause": "OSPF area mismatch on the R1-R2 link (R1 area 0, R2 area 1).",
            "evidence": [
                {"device": "R1", "command": "show ip ospf neighbor", "finding": "no neighbors"},
                {"device": "R2", "command": "show ip protocols", "finding": "10.0.12.0/30 area 1"},
            ],
            "proposed_fix": "On R2: router ospf 1 / network 10.0.12.0 0.0.0.3 area 0",
            "confidence": "high",
        }
    )
    llm = ScriptedChatModel(
        script=[
            AIMessage(content="1. neighbors 2. protocols on both"),
            tool_call("run_show_command", {"device": "R1", "command": "show ip ospf neighbor"}),
            tool_call("run_show_command", {"device": "R2", "command": "show ip protocols"}, "c2"),
            AIMessage(content="Area mismatch."),
            AIMessage(content=report_json),
        ],
        calls=[],
    )
    state = run_agent("OSPF R1-R2 down", llm, list(tools.values()), AgentSettings(), ["R1", "R2"])
    assert state["steps"] == 2
    assert all(e.verified for e in state["report"].evidence)
    assert "area" in state["report"].likely_cause
