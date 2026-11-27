"""Write recorded fault scenarios (IOS 12.4(24)T5 on c7200, lab naming).

Each scenario is what the lab shows after one planted fault (see lab/FAULTS.md).
Run once to (re)create tests/fixtures/scenarios/; the files are committed.
"""

from pathlib import Path

from rootvex.devices.connection import fixture_slug

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios"

BRIEF_HEAD = (
    "Interface                  IP-Address      OK? Method Status                Protocol\n"
)


def brief(*rows):
    return BRIEF_HEAD + "".join(
        f"{i:<27}{ip:<16}YES NVRAM  {st:<22}{pr}\n" for i, ip, st, pr in rows
    )


def ospf_if(iface, ip, area, rid, hello=10, dead=40, passive=False, nettype="BROADCAST"):
    text = (
        f"{iface} is up, line protocol is up \n"
        f"  Internet Address {ip}/30, Area {area} \n"
        f"  Process ID 1, Router ID {rid}, Network Type {nettype}, Cost: 1\n"
        "  Transmit Delay is 1 sec, State DR, Priority 1 \n"
        f"  Timer intervals configured, Hello {hello}, Dead {dead}, Wait {dead}, Retransmit 5\n"
        "    oob-resync timeout 40\n"
    )
    if passive:
        text += "    No Hellos (Passive interface)\n"
    else:
        text += "    Hello due in 00:00:04\n"
    return text


NBR_HEAD = "Neighbor ID     Pri   State           Dead Time   Address         Interface\n"


def nbr(*rows):
    return NBR_HEAD + "".join(
        f"{rid:<15}{1:>4}   {st:<16}00:00:3{n}    {ip:<16}{i}\n"
        for n, (rid, st, ip, i) in enumerate(rows)
    )


R1_UP = ("FastEthernet0/0", "10.99.0.11", "up", "up")
R2_UP = ("FastEthernet0/0", "10.99.0.12", "up", "up")
G1 = "GigabitEthernet1/0"


def ospf_pair(r1_kw=None, r2_kw=None, r1_nbr=(), r2_nbr=(), r1_log="", r2_log=""):
    r1_kw, r2_kw = r1_kw or {}, r2_kw or {}
    return {
        "R1": {
            "show ip ospf neighbor": nbr(*r1_nbr),
            "show ip ospf interface": ospf_if(G1, "10.0.12.1", 0, "1.1.1.1", **r1_kw),
            "show ip interface brief": brief(R1_UP, (G1, "10.0.12.1", "up", "up")),
            "show logging": r1_log,
        },
        "R2": {
            "show ip ospf neighbor": nbr(*r2_nbr),
            "show ip ospf interface": ospf_if(G1, "10.0.12.2", 0, "2.2.2.2", **r2_kw),
            "show ip interface brief": brief(R2_UP, (G1, "10.0.12.2", "up", "up")),
            "show logging": r2_log,
        },
    }


def log(*lines):
    return "".join(f"*Nov 25 10:0{i}:1{i}.123: {ln}\n" for i, ln in enumerate(lines))


BGP_HEAD = (
    "BGP router identifier 1.1.1.1, local AS number 65001\n"
    "Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
)

SCENARIOS = {
    "ospf_healthy": ospf_pair(
        r1_nbr=[("2.2.2.2", "FULL/BDR", "10.0.12.2", G1)],
        r2_nbr=[("1.1.1.1", "FULL/DR", "10.0.12.1", G1)],
    ),
    "ospf_passive": ospf_pair(r1_kw={"passive": True}),
    "ospf_timers": ospf_pair(r2_kw={"hello": 5, "dead": 20}),
    "ospf_mtu": ospf_pair(
        r1_nbr=[("2.2.2.2", "EXSTART/BDR", "10.0.12.2", G1)],
        r2_nbr=[("1.1.1.1", "EXCHANGE/DR", "10.0.12.1", G1)],
    ),
    "interface_shutdown": {
        "R1": {
            "show ip interface brief": brief(
                R1_UP, (G1, "10.0.12.1", "administratively down", "down")
            ),
            "show logging": log(
                f"%LINK-5-CHANGED: Interface {G1}, changed state to administratively down"
            ),
        }
    },
    # interface_flap: real R1 output from the live lab (27 Nov), kept as recorded files;
    # see tests/fixtures/scenarios/interface_flap/. Not generated here.
    "bgp_wrong_as": {
        "R1": {
            "show ip bgp summary": BGP_HEAD
            + "10.0.12.2       4 65003       0       0        0    0    0 never    Active\n",
            "show ip bgp neighbors": "BGP neighbor is 10.0.12.2,  remote AS 65003, external link\n"
            "  BGP state = Active\n",
            "show ip interface brief": brief(R1_UP, (G1, "10.0.12.1", "up", "up")),
            "show logging": log(
                "%BGP-3-NOTIFICATION: received from neighbor 10.0.12.2 2/2 "
                "(peer in wrong AS) 2 bytes FDEB"
            ),
        }
    },
    "bgp_admin_shut": {
        "R1": {
            "show ip bgp summary": BGP_HEAD
            + "10.0.12.2       4 65002       0       0        0    0    0 00:01:12 Idle (Admin)\n",
            "show ip bgp neighbors": "BGP neighbor is 10.0.12.2,  remote AS 65002, external link\n"
            "  BGP state = Idle, Administratively shut down\n",
            "show ip interface brief": brief(R1_UP, (G1, "10.0.12.1", "up", "up")),
            "show logging": "",
        }
    },
    "ipsec_psk": {
        "R1": {
            "show crypto isakmp sa": "IPv4 Crypto ISAKMP SA\n"
            "dst             src             state          conn-id slot status\n"
            "10.0.13.3       10.0.13.1       MM_KEY_EXCH          0    0 ACTIVE\n",
            "show crypto ipsec sa": "interface: GigabitEthernet2/0\n"
            "    Crypto map tag: VPN, local addr 10.0.13.1\n"
            "    #pkts encaps: 0, #pkts encrypt: 0, #pkts digest: 0\n"
            "    #pkts decaps: 0, #pkts decrypt: 0, #pkts verify: 0\n",
            "show logging": log(
                "%CRYPTO-4-IKMP_BAD_MESSAGE: IKE message from 10.0.13.3 failed its "
                "sanity check or is malformed"
            ),
        }
    },
    "ipsec_oneway": {
        "R1": {
            "show crypto isakmp sa": "IPv4 Crypto ISAKMP SA\n"
            "dst             src             state          conn-id slot status\n"
            "10.0.13.3       10.0.13.1       QM_IDLE           1001    0 ACTIVE\n",
            "show crypto ipsec sa": "interface: GigabitEthernet2/0\n"
            "    Crypto map tag: VPN, local addr 10.0.13.1\n"
            "    #pkts encaps: 25, #pkts encrypt: 25, #pkts digest: 25\n"
            "    #pkts decaps: 0, #pkts decrypt: 0, #pkts verify: 0\n",
            "show logging": "",
        }
    },
    "website_no_default": {
        "R1": {
            "show ip route": "Codes: C - connected, S - static, O - OSPF\n\n"
            "Gateway of last resort is not set\n\n"
            "     10.0.0.0/30 is subnetted, 1 subnets\n"
            f"C       10.0.12.0 is directly connected, {G1}\n",
            "show ip interface brief": brief(R1_UP, (G1, "10.0.12.1", "up", "up")),
            "show ip nat translations": "",
            "show ip access-lists": "",
        }
    },
    "website_acl": {
        "R1": {
            "show ip route": "Gateway of last resort is 10.0.12.2 to network 0.0.0.0\n\n"
            "S*   0.0.0.0/0 [1/0] via 10.0.12.2\n",
            "show ip interface brief": brief(R1_UP, (G1, "10.0.12.1", "up", "up")),
            "show ip nat translations": "",
            "show ip access-lists": "Extended IP access list BLOCK_WEB\n"
            "    10 deny tcp any any eq www (152 matches)\n"
            "    20 permit ip any any (3020 matches)\n",
        }
    },
}


def main() -> None:
    for name, devices in SCENARIOS.items():
        for dev, cmds in devices.items():
            folder = OUT / name / "cisco_ios" / dev
            folder.mkdir(parents=True, exist_ok=True)
            for cmd, text in cmds.items():
                (folder / f"{fixture_slug(cmd)}.txt").write_text(text)
    print(f"wrote {len(SCENARIOS)} scenarios to {OUT}")


if __name__ == "__main__":
    main()
