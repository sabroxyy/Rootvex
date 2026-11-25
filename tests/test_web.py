"""Web prototype: login, a full run with live steps, decisions, restart, failures."""

import ipaddress
import json
import time

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from rootvex.config import PROJECT_ROOT, WebSettings, load_settings
from rootvex.devices.allowlist import Allowlist
from rootvex.devices.connection import FixtureRunner
from rootvex.devices.inventory import load_inventory
from rootvex.tools.device_tools import make_device_tools
from rootvex.web.app import create_app
from rootvex.web.store import RunStore
from tests.fake_llm import ScriptedChatModel, tool_call

AUTH = ("engineer", "web-pass")
REPORT = json.dumps(
    {
        "likely_cause": "OSPF area mismatch: R1 Gi0/1 area 0, R2 Gi0/1 area 1.",
        "evidence": [
            {
                "device": "R2",
                "command": "show ip protocols",
                "finding": "10.0.12.0/30 area 1",
                "quote": "10.0.12.0 0.0.0.3 area 1",
            }
        ],
        "proposed_fix": "On R2: network 10.0.12.0 0.0.0.3 area 0",
        "confidence": "high",
    }
)


def fixture_tools(mode):
    inv = load_inventory(
        PROJECT_ROOT / "config/inventory.example.yaml", [ipaddress.IPv4Network("10.99.0.0/24")]
    )
    al = Allowlist.load(PROJECT_ROOT / "config/allowlist")
    return make_device_tools(inv, al, FixtureRunner(PROJECT_ROOT / "tests/fixtures")), inv.names()


def make_client(tmp_path, script, password="web-pass"):
    settings = load_settings(
        web=WebSettings(user="engineer", password=password, db_file=tmp_path / "rv.db")
    )
    llm = ScriptedChatModel(script=script, calls=[])
    app = create_app(settings, llm=llm, tools_factory=fixture_tools)
    return TestClient(app), app


def wait_done(client, run_id, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = client.get(f"/api/runs/{run_id}", auth=AUTH).json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_refuses_to_start_without_a_password(tmp_path):
    with pytest.raises(SystemExit, match="ROOTVEX_WEB__PASSWORD"):
        make_client(tmp_path, [], password=None)


def test_login_required(tmp_path):
    client, _ = make_client(tmp_path, [])
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("engineer", "wrong")).status_code == 401
    page = client.get("/", auth=AUTH)
    assert page.status_code == 200 and "Rootvex" in page.text
    assert client.get("/api/runs", auth=("x", "web-pass")).status_code == 401


def test_full_run_steps_report_and_decision(tmp_path):
    client, _ = make_client(
        tmp_path,
        [
            AIMessage(content="1. Check OSPF config on R2"),
            tool_call("run_show_command", {"device": "R2", "command": "show ip protocols"}),
            AIMessage(content="Area mismatch."),
            AIMessage(content=REPORT),
        ],
    )
    run_id = client.post(
        "/api/runs", json={"question": "OSPF R1-R2 down", "mode": "recorded"}, auth=AUTH
    ).json()["id"]
    run = wait_done(client, run_id)

    assert run["status"] == "done" and run["user"] == "engineer" and run["mode"] == "recorded"
    assert run["plan"].startswith("1. Check OSPF")
    tools = [e for e in run["events"] if e["type"] == "tool"]
    assert tools[0]["args"]["command"] == "show ip protocols" and "area 1" in tools[0]["result"]
    assert run["report"]["confidence"] == "high"
    assert run["report"]["evidence"][0]["verified"] is True

    decided = client.post(
        f"/api/runs/{run_id}/decision", json={"decision": "approved", "note": "lab"}, auth=AUTH
    ).json()
    assert decided["decision"] == "approved" and decided["decided_by"] == "engineer"
    # A decision is final, and only recorded: no second one.
    again = client.post(f"/api/runs/{run_id}/decision", json={"decision": "rejected"}, auth=AUTH)
    assert again.status_code == 409
    history = client.get("/api/runs", auth=AUTH).json()
    assert history[0]["id"] == run_id and history[0]["decision"] == "approved"


def test_config_command_is_denied_in_the_web_flow(tmp_path):
    client, _ = make_client(
        tmp_path,
        [
            AIMessage(content="plan"),
            tool_call("run_show_command", {"device": "R1", "command": "configure terminal"}),
            AIMessage(content="done"),
            AIMessage(content='{"likely_cause": "nothing changed", "confidence": "low"}'),
        ],
    )
    run_id = client.post(
        "/api/runs", json={"question": "shut Gi1/0 on R1", "mode": "recorded"}, auth=AUTH
    ).json()["id"]
    run = wait_done(client, run_id)
    tool = [e for e in run["events"] if e["type"] == "tool"][0]
    assert tool["result"].startswith("DENIED")


def test_bad_input_is_rejected(tmp_path):
    client, _ = make_client(tmp_path, [])
    assert client.post("/api/runs", json={"question": "x"}, auth=AUTH).status_code == 422
    bad_mode = client.post("/api/runs", json={"question": "OSPF down", "mode": "rm -rf"}, auth=AUTH)
    assert bad_mode.status_code == 422
    assert client.get("/api/runs/nope", auth=AUTH).status_code == 404


def test_model_failure_is_shown_not_hidden(tmp_path):
    client, _ = make_client(tmp_path, [])  # empty script: the model "fails" at once
    run_id = client.post(
        "/api/runs", json={"question": "OSPF down", "mode": "recorded"}, auth=AUTH
    ).json()["id"]
    run = wait_done(client, run_id)
    assert run["status"] == "failed" and "ran out of replies" in run["error"]
    # A failed run cannot be approved.
    no = client.post(f"/api/runs/{run_id}/decision", json={"decision": "approved"}, auth=AUTH)
    assert no.status_code == 409


def test_runs_and_decisions_survive_a_restart(tmp_path):
    db = tmp_path / "rv.db"
    store = RunStore(db)
    store.create("a1", "engineer", "q1", "live")
    store.finish("a1", {"likely_cause": "x", "confidence": "low"})
    store.decide("a1", "rejected", "engineer", "not convinced")
    store.create("b2", "engineer", "q2", "live")  # still running when the server stops

    reopened = RunStore(db)
    assert reopened.get("a1")["decision"] == "rejected"
    assert reopened.get("a1")["decision_note"] == "not convinced"
    assert reopened.get("b2")["status"] == "interrupted"
