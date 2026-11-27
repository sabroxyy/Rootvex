"""Playbooks against recorded faults: right cause first, every proof line is real."""

import ipaddress

import pytest

from rootvex.agent.report import Evidence, Report, verify_evidence
from rootvex.config import PROJECT_ROOT
from rootvex.devices.allowlist import Allowlist
from rootvex.devices.connection import DeviceError, FixtureRunner
from rootvex.devices.inventory import load_inventory
from rootvex.playbooks import analyze, choose, collect, devices_in, load_playbooks, playbook_dir

BOOKS = load_playbooks(playbook_dir())
INV = load_inventory(
    PROJECT_ROOT / "config/inventory.example.yaml", [ipaddress.IPv4Network("10.99.0.0/24")]
)
SCEN = PROJECT_ROOT / "tests/fixtures/scenarios"

# scenario, question, expected playbook, text that must be in the top cause
CASES = [
    ("ospf_passive", "OSPF neighbor R1 R2 is down", "ospf_neighbor_down", "passive"),
    ("ospf_timers", "OSPF adjacency R1 R2 not forming", "ospf_neighbor_down", "timer mismatch"),
    ("ospf_mtu", "OSPF neighbor R1 R2 stuck", "ospf_neighbor_down", "MTU"),
    ("interface_shutdown", "interface on R1 is down", "interface_down", "administratively shut"),
    ("interface_flap", "R1 link is flapping", "interface_down", "flapping"),
    ("bgp_wrong_as", "BGP peer on R1 is down", "bgp_neighbor_down", "remote AS"),
    ("bgp_admin_shut", "BGP neighbor on R1 is idle", "bgp_neighbor_down", "administratively"),
    ("ipsec_psk", "IPsec VPN tunnel on R1 is down", "ipsec_vpn_down", "pre-shared key"),
    ("ipsec_oneway", "IPsec VPN on R1 not passing traffic", "ipsec_vpn_down", "decrypts none"),
    (
        "website_no_default",
        "user behind R1 can't reach a website",
        "website_unreachable",
        "default",
    ),
    ("website_acl", "user behind R1 cannot browse the web site", "website_unreachable", "deny"),
]


def run_scenario(name, question):
    book = choose(BOOKS, question)
    runner = FixtureRunner(SCEN / name)
    devs = devices_in(question, INV.names())

    def run(device, command):
        try:
            return runner.run(INV.get(device), command)
        except DeviceError:
            return None

    return book, collect(book, devs, run)


@pytest.mark.parametrize(("scenario", "question", "book_id", "expect"), CASES)
def test_playbook_finds_planted_fault(scenario, question, book_id, expect):
    book, outputs = run_scenario(scenario, question)
    assert book.id == book_id
    findings = analyze(book, outputs)
    assert findings, "no findings"
    top = findings[0]
    assert expect.lower() in top.cause.lower(), [f.cause for f in findings]
    assert top.tags, "a cause needs tags so a model answer can agree with it"
    # Every proof line must pass the same quote check the model's evidence gets.
    report = Report(
        likely_cause=top.cause,
        evidence=[
            Evidence(device=p.device, command=p.command, finding=f.cause, quote=p.quote)
            for f in findings
            for p in f.proof
        ],
    )
    verify_evidence(report, {(d.lower(), c.lower()): t for (d, c), t in outputs.items()})
    assert all(e.verified for e in report.evidence), [e.check for e in report.evidence]


def test_healthy_ospf_has_no_cause():
    book, outputs = run_scenario("ospf_healthy", "OSPF neighbor R1 R2 is down")
    assert [f for f in analyze(book, outputs) if not f.symptom] == []


def test_area_mismatch_in_original_fixtures():
    book = choose(BOOKS, "OSPF R1 R2 down")
    runner = FixtureRunner(PROJECT_ROOT / "tests/fixtures")
    outputs = {}
    for c in book.checks:
        for d in ("R1", "R2"):
            try:
                outputs[(d, c.command)] = runner.run(INV.get(d), c.command)
            except DeviceError:
                pass
    top = analyze(book, outputs)[0]
    assert "area mismatch" in top.cause and "area 0 vs area 1" in top.cause


def test_every_playbook_command_is_allowlisted():
    al = Allowlist.load(PROJECT_ROOT / "config/allowlist")
    for book in BOOKS:
        for check in book.checks:
            assert al.check("cisco_ios", check.command).allowed, (book.id, check.command)


def test_no_playbook_for_unrelated_question():
    assert choose(BOOKS, "what is the weather") is None


def test_ospf_init_on_a_tunnel_points_to_ipsec():
    # Live fault #9: a wrong pre-shared key on BR1 showed up on R2 as OSPF INIT on Tunnel0.
    from rootvex.playbooks.analyzers import ospf_neighbors

    out = (
        "Neighbor ID     Pri   State           Dead Time   Address         Interface\n"
        "3.3.3.3           0   INIT/  -        00:00:35    172.16.0.2      Tunnel0\n"
    )
    f = ospf_neighbors({("R2", "show ip ospf neighbor"): out})[0]
    assert "IPsec" in f.cause and "show crypto isakmp sa" in f.fix
    assert f.proof[0].quote.startswith("3.3.3.3") and "Tunnel0" in f.proof[0].quote


def test_old_downs_do_not_count_as_flapping():
    # Boot-time downs hours before, plus one recent down: not flapping.
    from rootvex.playbooks.analyzers import interface_flaps

    log = (
        "*Nov 27 13:51:15.191: %LINEPROTO-5-UPDOWN: Line protocol on Interface "
        "GigabitEthernet1/0, changed state to down\n"
        "*Nov 27 15:00:00.000: %LINEPROTO-5-UPDOWN: Line protocol on Interface "
        "GigabitEthernet1/0, changed state to down\n"
        "*Nov 27 17:22:15.515: %LINEPROTO-5-UPDOWN: Line protocol on Interface "
        "GigabitEthernet1/0, changed state to down\n"
    )
    assert interface_flaps({("R1", "show logging | include UPDOWN"): log}) == []


def test_unused_port_is_not_reported_as_down():
    book, outputs = run_scenario("interface_flap", "R1 link is flapping")
    assert not any("GigabitEthernet3/0" in f.cause for f in analyze(book, outputs))


def test_flap_proof_is_the_newest_matching_line():
    _, outputs = run_scenario("interface_flap", "R1 link is flapping")
    from rootvex.playbooks.analyzers import interface_flaps

    f = interface_flaps(outputs)[0]
    assert f.proof[0].quote.startswith("*Nov 27 17:22:36.595")


def test_stale_qm_idle_row_is_not_reported_as_up():
    # Live 27 Nov (#9): during a failed rekey IOS lists the old QM_IDLE SA next to
    # the new MM_NO_STATE one. Report the failure, not "up".
    book, outputs = run_scenario("ipsec_rekey_fail", "IPsec VPN tunnel between R2 and BR1 is down")
    findings = analyze(book, outputs)
    assert "MM_NO_STATE" in findings[0].cause and "203.0.113.1 -> 198.51.100.2" in findings[0].cause
    assert not any(f.ok for f in findings)
