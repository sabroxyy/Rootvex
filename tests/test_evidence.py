"""Quote-checked evidence: a finding counts only if its quote is in THAT device's output."""

from rootvex.agent.report import Evidence, Report, verify_evidence

OUTPUTS = {
    ("r1", "show ip ospf interface"): "Tunnel0 is up\n  Internet Address 10.0.12.1/30, Area 0",
    ("r2", "show ip ospf interface"): "Tunnel0 is up, line protocol is up\n"
    "  Internet Address 172.16.0.1/30, Area 0",
}


def check(device, command, quote):
    r = Report(
        likely_cause="x",
        evidence=[Evidence(device=device, command=command, finding="f", quote=quote)],
    )
    verify_evidence(r, OUTPUTS)
    return r.evidence[0]


def test_quote_from_the_right_device_is_verified():
    e = check("R2", "show ip ospf interface", "Internet Address 172.16.0.1/30, Area 0")
    assert e.verified and e.check == "quote found in the output"


def test_quote_ignores_case_and_spacing():
    assert check(
        "R2", "show  ip ospf interface", "internet address   172.16.0.1/30, area 0"
    ).verified


def test_r2_line_credited_to_r1_is_rejected():
    # The live-lab mistake: R2's tunnel address reported as R1's.
    e = check("R1", "show ip ospf interface", "Internet Address 172.16.0.1/30, Area 0")
    assert not e.verified and e.check == "quote not found in this device's output"


def test_missing_quote_or_command_is_rejected():
    assert check("R2", "show ip ospf interface", "").check == "no quote from the output"
    assert check("R2", "show ip route", "anything").check == "command was not run on this device"


def test_multi_line_quote_is_trimmed_to_the_relevant_line():
    out = (
        "Gi1/0 is up, line protocol is up\n"
        "  Internet Address 10.0.12.1/30, Area 0\n"
        "    No Hellos (Passive interface)\n"
    )
    report = Report(
        likely_cause="x",
        evidence=[
            Evidence(
                device="R1",
                command="show ip ospf interface",
                finding="Gi1/0 is a passive interface",
                quote=out,
            )
        ],
    )
    verify_evidence(report, {("r1", "show ip ospf interface"): out})
    ev = report.evidence[0]
    assert ev.verified and ev.quote == "No Hellos (Passive interface)"
    assert "trimmed from 3 lines" in ev.check
