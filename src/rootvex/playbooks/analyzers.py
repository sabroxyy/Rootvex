"""Code that reads show-command output and compares both ends of a link.

Each analyzer takes {(device, command): output} and returns Findings whose proof
lines are copied from that output, so the quote check can verify them.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict

from rootvex.playbooks import Finding, Proof, base

_IFACE_HEAD = re.compile(r"^(\S+) is (up|down|administratively down), line protocol is (\w+)", re.M)
_ADDR = re.compile(r"Internet Address (\d+\.\d+\.\d+\.\d+)/(\d+), Area ([\d.]+)")
_TIMERS = re.compile(r"Timer intervals configured, Hello (\d+), Dead (\d+)")
_AUTH = re.compile(r"(Simple password|Message digest) authentication enabled")
_NETTYPE = re.compile(r"Network Type (\S+),")
_BRIEF = re.compile(r"^(\S+)\s+\d+\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+/\d+)\s+\d+", re.M)
_NBR = re.compile(
    r"^(\d+\.\d+\.\d+\.\d+)\s+\d+\s+([A-Z0-9]+)/\s*\S*\s+\S+\s+(\S+)\s+(\S.*?)\s*$", re.M
)
_IP_BRIEF = re.compile(r"^(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+\w+\s+\w+\s+(.+?)\s+(up|down)\s*$", re.M)


def _outputs_for(outputs: dict, command: str) -> dict[str, str]:
    return {d: t for (d, c), t in outputs.items() if base(c) == command}


def _line(text: str, needle_rx: re.Pattern | str, start: int = 0) -> str:
    rx = re.compile(needle_rx) if isinstance(needle_rx, str) else needle_rx
    m = rx.search(text, start)
    if not m:
        return ""
    s = text.rfind("\n", 0, m.start()) + 1
    e = text.find("\n", m.start())
    return text[s : e if e != -1 else len(text)].strip()


def _ospf_interfaces(outputs: dict) -> dict[tuple[str, str], dict]:
    """(device, iface) -> details, from 'show ip ospf interface' (full) or its brief form."""
    found: dict[tuple[str, str], dict] = {}
    for device, text in _outputs_for(outputs, "show ip ospf interface").items():
        heads = list(_IFACE_HEAD.finditer(text))
        for i, h in enumerate(heads):
            block = text[h.start() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            addr = _ADDR.search(block)
            if not addr:
                continue
            net = ipaddress.ip_interface(f"{addr[1]}/{addr[2]}").network
            timers = _TIMERS.search(block)
            auth = _AUTH.search(block)
            nettype = _NETTYPE.search(block)
            found[(device, h[1])] = {
                "net": net,
                "area": addr[3],
                "area_line": _line(block, _ADDR),
                "hello": timers[1] if timers else None,
                "dead": timers[2] if timers else None,
                "timer_line": _line(block, _TIMERS) if timers else "",
                "passive": "Passive interface" in block,
                "passive_line": _line(block, r"No Hellos \(Passive interface\)"),
                "auth": auth[1] if auth else "none",
                "auth_line": _line(block, _AUTH) if auth else _line(block, _ADDR),
                "nettype": nettype[1] if nettype else None,
                "nettype_line": _line(block, _NETTYPE) if nettype else "",
                "command": "show ip ospf interface",
            }
    for device, text in _outputs_for(outputs, "show ip ospf interface brief").items():
        for m in _BRIEF.finditer(text):
            key = (device, m[1])
            if key in found:
                continue
            found[key] = {
                "net": ipaddress.ip_interface(m[3]).network,
                "area": m[2],
                "area_line": _line(text, re.escape(m[0].strip())),
                "command": "show ip ospf interface brief",
            }
    return found


def ospf_compare(outputs: dict) -> list[Finding]:
    """Compare both ends of every OSPF link: area, timers, passive, auth, network type."""
    by_net: dict = defaultdict(list)
    for (device, iface), info in _ospf_interfaces(outputs).items():
        by_net[info["net"]].append((device, iface, info))
    findings: list[Finding] = []
    for net, ends in by_net.items():
        devices = {d for d, _, _ in ends}
        if len(devices) < 2:
            continue
        (d1, i1, a), (d2, i2, b) = ends[0], next(e for e in ends if e[0] != ends[0][0])
        pair = f"{d1} {i1} and {d2} {i2} ({net})"

        def proof(key: str, d1=d1, a=a, d2=d2, b=b) -> list[Proof]:
            return [
                Proof(d1, a["command"], a[key]),
                Proof(d2, b["command"], b[key]),
            ]

        if a["area"] != b["area"]:
            findings.append(
                Finding(
                    cause=f"OSPF area mismatch on {pair}: area {a['area']} vs area {b['area']}",
                    fix=f"Put both ends of {net} in the same area (change the network/area "
                    f"statement on the side that is wrong)",
                    proof=proof("area_line"),
                    weight=95,
                    tags=["area"],
                )
            )
        if a.get("hello") and b.get("hello") and (a["hello"], a["dead"]) != (b["hello"], b["dead"]):
            findings.append(
                Finding(
                    cause=f"OSPF hello/dead timer mismatch on {pair}: "
                    f"{a['hello']}/{a['dead']} vs {b['hello']}/{b['dead']}",
                    fix="Set the same 'ip ospf hello-interval' and 'ip ospf dead-interval' on both",
                    proof=proof("timer_line"),
                    weight=90,
                    tags=["hello", "dead", "timer"],
                )
            )
        for dev, iface, info in ((d1, i1, a), (d2, i2, b)):
            if info.get("passive"):
                findings.append(
                    Finding(
                        cause=f"{dev} {iface} is a passive OSPF interface, so it sends no hellos "
                        f"to its neighbor on {net}",
                        fix=f"On {dev}: router ospf <pid> / no passive-interface {iface}",
                        proof=[Proof(dev, info["command"], info["passive_line"])],
                        weight=92,
                        tags=["passive"],
                    )
                )
        if "auth" in a and "auth" in b and a["auth"] != b["auth"]:
            findings.append(
                Finding(
                    cause=f"OSPF authentication mismatch on {pair}: {a['auth']} vs {b['auth']}",
                    fix="Use the same OSPF authentication type and key on both ends",
                    proof=proof("auth_line"),
                    weight=88,
                    tags=["auth"],
                )
            )
        if a.get("nettype") and b.get("nettype") and a["nettype"] != b["nettype"]:
            findings.append(
                Finding(
                    cause=f"OSPF network type mismatch on {pair}: {a['nettype']} vs {b['nettype']} "
                    "(adjacency may form but routes can be missing)",
                    fix="Use the same 'ip ospf network' type on both ends",
                    proof=proof("nettype_line"),
                    weight=70,
                    tags=["network type"],
                )
            )
    return findings


def ospf_neighbors(outputs: dict) -> list[Finding]:
    """Neighbor state: none at all, stuck in INIT, or stuck in EXSTART/EXCHANGE."""
    findings: list[Finding] = []
    for device, text in _outputs_for(outputs, "show ip ospf neighbor").items():
        rows = list(_NBR.finditer(text))
        if not rows:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            quote = _line(text, r"Neighbor ID") or first
            findings.append(
                Finding(
                    cause=f"{device} has no OSPF neighbors",
                    proof=[Proof(device, "show ip ospf neighbor", quote)],
                    weight=10,
                    symptom=True,
                )
            )
        for m in rows:
            state, iface = m[2], m[4]
            line = m[0].strip()
            if state == "INIT" and iface.lower().startswith("tunnel"):
                # Seen live (fault #9): a wrong IPsec key on a VTI shows up as OSPF INIT.
                findings.append(
                    Finding(
                        cause=f"{device} sees neighbor {m[1]} only in INIT on {iface}: "
                        "traffic over the tunnel works one way, usually an IPsec "
                        "problem (pre-shared key, proposal) on the tunnel",
                        fix="Check IPsec on both tunnel ends: 'show crypto isakmp sa' "
                        "and 'show crypto ipsec sa'; ask the IPsec VPN playbook",
                        proof=[Proof(device, "show ip ospf neighbor", line)],
                        weight=75,
                        tags=["ipsec", "isakmp", "crypto", "pre-shared", "tunnel"],
                    )
                )
            elif state == "INIT":
                findings.append(
                    Finding(
                        cause=f"{device} sees neighbor {m[1]} only in INIT on {iface}: "
                        "hellos arrive one way (ACL, filtered multicast, or the peer "
                        "does not hear this router)",
                        fix="Check access lists on both interfaces for OSPF "
                        "(protocol 89, 224.0.0.5)",
                        proof=[Proof(device, "show ip ospf neighbor", line)],
                        weight=75,
                        tags=["init", "one-way", "one way", "access-list", "acl"],
                    )
                )
            elif state in ("EXSTART", "EXCHANGE"):
                findings.append(
                    Finding(
                        cause=f"{device} is stuck in {state} with {m[1]} on {iface}: typical of an "
                        "interface MTU mismatch",
                        fix="Compare 'MTU' in 'show interfaces' on both ends and make them equal "
                        "(or use 'ip ospf mtu-ignore')",
                        proof=[Proof(device, "show ip ospf neighbor", line)],
                        weight=85,
                        tags=["mtu"],
                    )
                )
    return findings


def ospf_not_enabled(outputs: dict) -> list[Finding]:
    """A link subnet runs OSPF on one end but the other end's interface is not in OSPF."""
    ospf = _ospf_interfaces(outputs)
    nets_by_device = defaultdict(set)
    for (device, _), info in ospf.items():
        nets_by_device[device].add(info["net"])
    findings = []
    ospf_seen = {d for (d, c) in outputs if base(c).startswith("show ip ospf interface")}
    for device, text in _outputs_for(outputs, "show ip interface brief").items():
        if device not in ospf_seen:
            continue  # no OSPF output from this device: cannot tell, so say nothing
        for m in _IP_BRIEF.finditer(text):
            iface, ip, status, proto = m[1], m[2], m[3].strip(), m[4]
            if status != "up" or proto != "up":
                continue
            for peer, nets in nets_by_device.items():
                if peer == device:
                    continue
                for net in nets:
                    if ipaddress.ip_address(ip) in net and net not in nets_by_device.get(
                        device, set()
                    ):
                        findings.append(
                            Finding(
                                cause=f"{device} {iface} ({ip}) is up but not running OSPF, while "
                                f"{peer} runs OSPF on {net}",
                                fix=f"On {device}: add the {net} network to OSPF "
                                "(network statement or 'ip ospf <pid> area <area>')",
                                proof=[Proof(device, "show ip interface brief", m[0].strip())],
                                weight=80,
                                tags=[
                                    "not running ospf",
                                    "not in ospf",
                                    "network statement",
                                    "not enabled",
                                ],
                            )
                        )
    return findings


_UPDOWN = re.compile(
    r"LINEPROTO-5-UPDOWN: Line protocol on Interface (\S+), changed state to down", re.M
)
_LOG_TIME = re.compile(r"^\*?(\w{3}) +(\d+) (\d+):(\d+):(\d+)", re.M)
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
FLAP_DOWNS = 2  # downs of one interface within FLAP_WINDOW_S of the newest log line
FLAP_WINDOW_S = 15 * 60


def _log_seconds(line: str) -> int | None:
    """Seconds since start of year from an IOS log timestamp (*Nov 27 17:22:15.515)."""
    m = _LOG_TIME.match(line.strip())
    if not m or m[1] not in _MONTHS:
        return None
    month, day, h, mi, sec = _MONTHS.index(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5])
    return ((month * 31 + day) * 24 + h) * 3600 + mi * 60 + sec


def interface_flaps(outputs: dict) -> list[Finding]:
    """An interface whose line protocol went down 2+ times in the last 15 minutes of the
    log is flapping. Older events (boot, earlier tests) are ignored."""
    findings = []
    for device, text in _outputs_for(outputs, "show logging").items():
        stamps = [t for ln in text.splitlines() if (t := _log_seconds(ln)) is not None]
        newest = max(stamps) if stamps else None
        downs = defaultdict(list)
        for m in _UPDOWN.finditer(text):
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.start())
            line = text[start : end if end != -1 else len(text)].strip()  # this line, not a twin
            t = _log_seconds(line)
            if newest is not None and t is not None and newest - t > FLAP_WINDOW_S:
                continue
            downs[m[1]].append(line)
        for iface, lines in downs.items():
            if len(lines) >= FLAP_DOWNS:
                findings.append(
                    Finding(
                        cause=f"{device} {iface} is flapping: line protocol went down "
                        f"{len(lines)} times in the last 15 minutes of the log",
                        fix="Check the physical link, duplex/speed and errors on both ends "
                        "(in the lab: someone or something is shutting the port)",
                        proof=[Proof(device, "show logging", lines[-1])],
                        weight=88,
                        tags=["flap"],
                    )
                )
    return findings


_BGP_ROW = re.compile(
    r"^(\d+\.\d+\.\d+\.\d+)\s+4\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\S+)\s+(\S.*?)\s*$", re.M
)


def bgp_sessions(outputs: dict) -> list[Finding]:
    """Neighbors not Established (State/PfxRcd column is a word, not a number)."""
    findings = []
    for device, text in _outputs_for(outputs, "show ip bgp summary").items():
        for m in _BGP_ROW.finditer(text):
            state = m[4]
            if state.isdigit():
                continue
            line = m[0].strip()
            if "admin" in state.lower():
                cause = f"{device}: BGP neighbor {m[1]} is administratively shut down"
                fix = f"On {device}: router bgp <as> / no neighbor {m[1]} shutdown"
                weight, symptom, tags = 95, False, ["shut"]
            else:
                cause = (
                    f"{device}: BGP neighbor {m[1]} (AS {m[2]}) is {state}, not Established: "
                    "TCP 179 not completing (no route to peer, wrong peer address/AS, ACL)"
                )
                fix = "Check reachability to the peer, the neighbor address and remote-as"
                weight, symptom, tags = 30, True, ["active", "idle", "established"]
            findings.append(
                Finding(
                    cause,
                    fix,
                    [Proof(device, "show ip bgp summary", line)],
                    weight,
                    symptom,
                    tags,
                )
            )
    return findings


_ISAKMP_ROW = re.compile(
    r"^(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+\d+\s+(\S+)", re.M
)


def ipsec_state(outputs: dict) -> list[Finding]:
    """Phase 1 stuck, or phase 2 encrypting with no decrypts (one-way traffic)."""
    findings = []
    for device, text in _outputs_for(outputs, "show crypto isakmp sa").items():
        rows = list(_ISAKMP_ROW.finditer(text))
        if not rows:
            findings.append(
                Finding(
                    f"{device} has no IKE phase 1 SA: the tunnel never started "
                    "(no interesting traffic, crypto map not applied, or peer unreachable)",
                    "Check the crypto map is on the outside interface and the peer is reachable",
                    [Proof(device, "show crypto isakmp sa", _line(text, r"dst\s+src") or "")],
                    35,
                    True,
                    tags=["phase 1", "isakmp", "crypto map", "never start"],
                )
            )
        for m in rows:
            if m[3] != "QM_IDLE":
                findings.append(
                    Finding(
                        f"{device}: IKE phase 1 with {m[1]} is stuck in {m[3]}: the peer does "
                        "not answer or rejects the proposal / key",
                        "Compare isakmp policy and pre-shared key on both ends",
                        [Proof(device, "show crypto isakmp sa", m[0].strip())],
                        60,
                        tags=["phase 1", "mm_", "pre-shared", "proposal", "key"],
                    )
                )
    for device, text in _outputs_for(outputs, "show crypto ipsec sa").items():
        enc = _line(text, r"#pkts encaps: (\d+)")
        dec = _line(text, r"#pkts decaps: (\d+)")
        e = re.search(r"#pkts encaps: (\d+)", text)
        d = re.search(r"#pkts decaps: (\d+)", text)
        if e and d and int(e[1]) > 0 and int(d[1]) == 0:
            findings.append(
                Finding(
                    f"{device} encrypts traffic ({e[1]} packets) but decrypts none: the far "
                    "end does not send back (its crypto ACL/route does not match, or a "
                    "firewall drops ESP)",
                    "Check that both crypto ACLs are mirror images and ESP (protocol 50) "
                    "is allowed",
                    [Proof(device, "show crypto ipsec sa", enc or dec)],
                    80,
                    tags=["decrypt", "one-way", "one way", "crypto acl", "esp"],
                )
            )
    return findings


def route_default(outputs: dict) -> list[Finding]:
    """No default route: traffic to the internet has nowhere to go."""
    findings = []
    for device, text in _outputs_for(outputs, "show ip route").items():
        if "Gateway of last resort is not set" in text:
            findings.append(
                Finding(
                    f"{device} has no default route (gateway of last resort is not set), so "
                    "traffic to internet addresses is dropped",
                    f"On {device}: add or restore the default route (ip route 0.0.0.0 0.0.0.0 "
                    "<next hop>) or the routing protocol that provides it",
                    [Proof(device, "show ip route", "Gateway of last resort is not set")],
                    85,
                    tags=["default route", "gateway of last resort"],
                )
            )
    return findings


_ACL_DENY = re.compile(r"^\s*(\d+ )?deny (.+?) \((\d+) match(?:es)?\)\s*$", re.M)


def acl_denies(outputs: dict) -> list[Finding]:
    """ACL deny entries with hits: something is being blocked."""
    findings = []
    for device, text in _outputs_for(outputs, "show ip access-lists").items():
        for m in _ACL_DENY.finditer(text):
            findings.append(
                Finding(
                    f"{device}: an access-list deny entry has {m[3]} hits ({m[2].strip()}): "
                    "it may be blocking this traffic",
                    "Check whether this deny entry matches the user's traffic; if so, move or "
                    "change it",
                    [Proof(device, "show ip access-lists", m[0].strip())],
                    60,
                    tags=["access-list", "acl", "deny"],
                )
            )
    return findings
