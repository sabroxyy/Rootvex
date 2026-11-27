# Planting faults in the lab (Phase 4)

Step-by-step version with copy-ready console commands: [`docs/PHASE4_RUNBOOK.md`](../docs/PHASE4_RUNBOOK.md) (PDF: `docs/Rootvex-Phase4-Runbook.pdf`).

How to break the GNS3 lab on purpose, ask Rootvex, and put it back. **Lab only.**
You type the config commands yourself on the router console (GNS3 → right-click →
Console). Rootvex never changes config: it only runs read-only `show` commands.

Interface names on the c7200 lab: `if1` = Gi1/0, `if2` = Gi2/0, `if3` = Gi3/0,
`mgmt` = Fa0/0 (VRF MGMT, never touch it).

Each fault also exists as a recorded scenario in `tests/fixtures/scenarios/<name>/`,
so `uv run pytest tests/test_playbooks.py` proves the playbook without the lab.

## How to run one test

1. Check the lab is healthy: `uv run rootvex ask "OSPF neighbor between R1 and R2 is down"`
   should say no fault (code checks find nothing abnormal).
2. Plant the fault (commands below), wait about 40 seconds (OSPF dead timer).
3. Ask the question from the table. Look at:
   - `Playbook:` - the right one was chosen
   - `Code checks found:` - first line is the planted cause
   - `Evidence:` - every line `verified`, each with a quote from the router
4. Undo the fault, wait, ask again: it should be healthy.

Replay a recorded fault without the lab: `uv run rootvex ask "OSPF neighbor R1 R2 is down" --scenario ospf_passive --steps`

## Faults

| # | Scenario (recorded) | Plant (config mode) | Ask | Expected top finding | Undo |
|---|---|---|---|---|---|
| 1 | `ospf_passive` | R1: `router ospf 1` / `passive-interface Gi1/0` | OSPF neighbor between R1 and R2 is down | R1 Gi1/0 is a passive OSPF interface | `no passive-interface Gi1/0` |
| 2 | `ospf_timers` | R2: `interface Gi1/0` / `ip ospf hello-interval 5` | OSPF adjacency R1 R2 not forming | hello/dead timer mismatch 10/40 vs 5/20 | `no ip ospf hello-interval` |
| 3 | (original fixtures) | R2: `interface Gi1/0` / `ip ospf 1 area 1` | OSPF neighbor R1 R2 is down | OSPF area mismatch area 0 vs area 1 | `ip ospf 1 area 0` |
| 4 | `ospf_mtu` | R2: `interface Gi1/0` / `ip mtu 1400` | OSPF neighbor R1 R2 stuck | stuck in EXSTART/EXCHANGE: MTU mismatch | `no ip mtu` |
| 5 | `interface_shutdown` | R1: `interface Gi1/0` / `shutdown` | interface on R1 is down | R1 Gi1/0 is administratively shut down | `no shutdown` |
| 6 | `interface_flap` | R1: `interface Gi1/0` / `shutdown`, `no shutdown` three times (flap R1 itself; GNS3 links do not always carry a far-end shutdown) | R1 link is flapping | R1 Gi1/0 is flapping | (nothing; `clear logging` on R1 to reset) |
| 7 | `bgp_wrong_as` | R2: `router bgp 65001` / `neighbor 203.0.113.2 remote-as 65101` | BGP peer between R2 and ISP1 is down | bad / wrong remote AS | `neighbor 203.0.113.2 remote-as 65100` |
| 8 | `bgp_admin_shut` | R2: `router bgp 65001` / `neighbor 203.0.113.2 shutdown` | BGP neighbor on R2 is idle | neighbor administratively shut down | `no neighbor 203.0.113.2 shutdown` |
| 9 | `ipsec_psk` | BR1: `no crypto isakmp key <key> address 203.0.113.1` / `crypto isakmp key WRONG-KEY address 203.0.113.1`, then `clear crypto session` (exec) | IPsec VPN tunnel on R2 is down | pre-shared key mismatch | put the real key back (LAB_VPN_PSK from `.env`), `clear crypto session` |
| 10 | `website_no_default` | R1: `no ip route 0.0.0.0 0.0.0.0 10.0.12.2` | user behind R1 can't reach a website | R1 has no default route | `ip route 0.0.0.0 0.0.0.0 10.0.12.2` |
| 11 | `website_acl` | R2: `ip access-list extended BLOCK_WEB` / `deny tcp any any eq www` / `permit ip any any`, then `interface Gi1/0` / `ip access-group BLOCK_WEB in`; from PC1 run `curl http://192.0.2.80` | user behind R2 cannot browse the web site | access-list deny entry has N hits | `interface Gi1/0` / `no ip access-group BLOCK_WEB in` |

Notes

- The IPsec tunnel is a VTI (`tunnel protection ipsec profile`), so `show crypto
  ipsec sa` shows `interface: Tunnel0`. Traffic must cross the tunnel (ping from PC1
  to PC2) for counters to move.
- Fault 9 shows the wrong key only on BR1. Run the question naming both routers
  ("between R2 and BR1") to collect both sides.
- `show logging` only holds what is in the buffer (`logging buffered 16384`). After a
  router reload the old messages are gone.
- If a recorded scenario and the live lab disagree, the live output wins: save it
  under `tests/fixtures/scenarios/<name>/cisco_ios/<DEVICE>/` and fix the playbook.
