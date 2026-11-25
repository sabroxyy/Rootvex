import json

import pytest

from rootvex import log
from rootvex.config import load_settings
from rootvex.router import RouterConfigError, Session

MATERIAL = """\
<untrusted_device_output device="FW-HQ-01" command="show full-configuration">
config system global
    set hostname "FW-HQ-01"
end
config vpn ipsec phase1-interface
    edit "to-branch"
        set remote-gw 203.0.113.10
        set psksecret ENC dGhpc2lzYXZlcnlsb25nc2VjcmV0dmFsdWU=
    next
end
devname="FW-HQ-01" srcip=172.16.10.20 dstip=203.0.113.10 msg="negotiation failure"
</untrusted_device_output>
"""


class FakeChat:
    """Records every call; answers per step in whatever values it was given."""

    def __init__(self, reason=None, review=None):
        self.calls = []
        self.reason = reason
        self.review = review

    def __call__(self, ep, system, user):
        self.calls.append({"ep": ep, "system": system, "user": user})
        if "strict reviewer" in system:
            return json.dumps(self.review or {"agree": True, "issues": []})
        if "JSON only" in system:
            if self.reason:
                return json.dumps(self.reason)
            ip = next(w.split("=")[1] for w in user.split() if w.startswith("dstip="))
            return (
                "```json\n"
                + json.dumps(
                    {
                        "root_cause": f"Phase 1 proposal mismatch with peer {ip}",
                        "confidence": "medium",
                        "evidence": [],
                        "verify_commands": ["diagnose vpn ike gateway list name to-branch"],
                        "fix_commands": [f"set remote-gw {ip}"],
                        "rollback_commands": [],
                        "risk": "low",
                    }
                )
                + "\n```"
            )
        return user.split("Material:\n", 1)[-1]  # "summary" = the evidence itself


@pytest.fixture
def cloud_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test-key-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test-key-456")


def session(monkeypatch, mode, **fake_kw):
    monkeypatch.setenv("ROOTVEX_ROUTER__MODE", mode)
    fake = FakeChat(**fake_kw)
    s = Session(load_settings(), chat=fake, user="tester")
    return s, fake


def audit_lines(s):
    return [json.loads(line) for line in s.audit_file.read_text().splitlines()]


def test_default_mode_is_local():
    assert load_settings().router.mode == "local"


def test_local_mode_never_leaves_the_network(monkeypatch):
    s, fake = session(monkeypatch, "local")
    s.troubleshoot("Why is the VPN to the branch down?", MATERIAL)
    assert {c["ep"].name for c in fake.calls} == {"local"}
    assert all(not c["ep"].cloud for c in fake.calls)
    assert all(not line["left_network"] for line in audit_lines(s))


def test_cloud_mode_without_keys_fails_clearly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RouterConfigError, match="GEMINI_API_KEY"):
        session(monkeypatch, "cloud")


def test_cloud_mode_sends_only_redacted_text(monkeypatch, cloud_keys):
    s, fake = session(monkeypatch, "cloud")
    result = s.troubleshoot("Why is the VPN to the branch down?", MATERIAL)
    sent = json.dumps([[c["system"], c["user"]] for c in fake.calls])
    for leaked in ("203.0.113.10", "172.16.10.20", "FW-HQ-01", "dGhpc2lz"):
        assert leaked not in sent, leaked
    # the engineer still sees real values
    assert result["finding"]["fix_commands"] == ["set remote-gw 203.0.113.10"]
    assert result["status"] == "ready_for_approval"
    lines = audit_lines(s)
    assert len(lines) == 3 and all(x["left_network"] and x["redacted"] for x in lines)
    assert "203.0.113.10" not in s.audit_file.read_text()


def test_hybrid_reads_device_output_locally(monkeypatch, cloud_keys):
    s, fake = session(monkeypatch, "hybrid")
    s.troubleshoot("Why is the VPN to the branch down?", MATERIAL)
    assert [c["ep"].cloud for c in fake.calls] == [False, True, True]
    assert "203.0.113.10" not in fake.calls[1]["user"]


def test_api_keys_are_registered_as_log_secrets(monkeypatch, cloud_keys):
    session(monkeypatch, "cloud")
    assert "gem-test-key-123" in log._known_secrets
    assert "gem-test-key-123" not in log.redact_text("key=gem-test-key-123")


def test_lab_secrets_never_reach_the_cloud(monkeypatch, cloud_keys):
    log.register_secret("LabPass123")
    s, fake = session(monkeypatch, "cloud")
    s.troubleshoot("q", MATERIAL + "\nlogin used LabPass123\n")
    assert "LabPass123" not in json.dumps([c["user"] for c in fake.calls])


def test_reviewer_disagreement_needs_engineer(monkeypatch, cloud_keys):
    s, _ = session(monkeypatch, "cloud", review={"agree": False, "issues": ["wrong peer"]})
    result = s.troubleshoot("VPN down?", MATERIAL)
    assert result["status"] == "needs_engineer_review"
    assert "reviewer disagreed" in result["reasons"]


def _finding(*cmds):
    return {
        "root_cause": "x",
        "confidence": "high",
        "evidence": [],
        "verify_commands": [],
        "fix_commands": list(cmds),
        "rollback_commands": [],
        "risk": "x",
    }


@pytest.mark.parametrize(
    "cmd",
    [
        "execute reboot",
        "write erase",
        "reload",
        "shutdown",
        "execute factoryreset",
        "request system restart",
    ],
)
def test_dangerous_commands_need_engineer(monkeypatch, cmd):
    s, _ = session(monkeypatch, "local", reason=_finding(cmd))
    assert s.troubleshoot("q", "m")["status"] == "needs_engineer_review"


def test_no_shutdown_is_not_flagged(monkeypatch):
    s, _ = session(monkeypatch, "local", reason=_finding("interface Gi1/0/1", "no shutdown"))
    assert s.troubleshoot("q", "m")["status"] == "ready_for_approval"


def test_unparsable_answer_needs_engineer(monkeypatch):
    s, fake = session(monkeypatch, "local")
    fake.reason = None
    s.chat = lambda ep, system, user: "I think it's the PSK, not sure."
    result = s.troubleshoot("q", "m")
    assert result["status"] == "needs_engineer_review"
