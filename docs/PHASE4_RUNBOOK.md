# Rootvex Phase 4 Runbook — proving the playbooks on the live lab

**Version 1 — 27 November 2026.** Branch `phase4`. Progress: **9 of 11 faults proven live**
(#1–#9). #10–#11 to do.

This runbook is the exact procedure for planting each lab fault, asking Rootvex,
checking the answer, and putting the lab back. **Lab only.** Rootvex never changes
a router; **you** type every config command, by hand, in the router console.

---

## 1. Before you start

### 1.1 Where each command goes

| Prompt you see | Window | What you type there |
|---|---|---|
| `sabroxyy@Sabroxyy:~/projects/rootvex$` | Ubuntu terminal | `uv run rootvex …`, `ping`, `nmcli`, `git` |
| `R1#`, `R2#`, `BR1#`, `ISP1#` | GNS3 router console (right-click router → **Console**) | `configure terminal`, `interface …`, `end` |
| `R1>` | Router console, not privileged | type `enable`, then the password from `.env` (`LAB_DEVICE_PASSWORD`) |

Router commands typed in Ubuntu fail with `command not found` — nothing changes.

### 1.2 Bring the lab up (once per session)

```bash
cd ~/projects/rootvex
git switch phase4 && git pull && uv sync
git log --oneline -1           # must match the latest commit on GitHub
nmcli connection up tap-nw
ip -br addr show tap-nw        # tap-nw UP 10.99.0.1/24
```

In GNS3: open project `rootvex-lab`, press ▶ (start all), wait ~2 minutes.

```bash
ping -c 2 10.99.0.11          # R1
ping -c 2 10.99.0.12          # R2
ping -c 2 10.99.0.13          # BR1
ping -c 2 10.99.0.14          # ISP1
```

| Symptom | Fix |
|---|---|
| `tap-nw` missing or DOWN | `nmcli connection up tap-nw` |
| Ping fails, tap-nw UP | Routers not started in GNS3; start them, wait 2 min |
| Ping OK, Rootvex "timeout connecting" | `ssh rootvex@10.99.0.11`; if refused: `uv run python lab/build_gns3_lab.py --ssh` |
| "No matching key exchange" | `~/.ssh/config` entry for `10.99.0.*` (see `lab/README.md`) |

### 1.3 Health check (before every fault)

```bash
uv run rootvex ask "OSPF neighbor R1 R2 is down"
```

Pass: no root cause in **Code checks found**, R1–R2 FULL. If a cause shows up,
a previous fault was not undone — undo it first.

### 1.4 How to run and save each test

```bash
uv run rootvex ask "<question>" --steps | tee logs/fault-NN.txt
```

`logs/` is git-ignored, so the saved file stays on your machine. Keep it if the
answer is wrong: the raw output is what fixes the playbook.

### 1.5 How to judge a result

**Use the exact question from the fault card.** Pasting a log line as the
question can pick a different playbook (on 27 Nov an IPsec fault was asked as
OSPF log lines, so the OSPF playbook ran).

**Judge from Rootvex's own output, not an assistant's summary.** Open the full
output (Ctrl+O in Gemini) and read the `Likely cause`, `Code checks found` and
`Proposed fix` lines. On 27 Nov an assistant summary added fixes Rootvex never printed.


| Check | Pass when |
|---|---|
| **Playbook** | The one named in the fault card |
| **Likely cause** | Says the same thing as "Expected" |
| **Evidence** | Every line verified; the quote is a real line from that router |
| **Proposed fix** | Matches the "Undo" commands (it is not applied) |
| **Model's view** | Allowed. It only means the model disagreed and code won |

---

## 2. The faults

One card per fault: **plant → wait → ask → expect → undo**. Do them one at a time
and run the health check in between.

### #1 OSPF passive interface — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R1) | `configure terminal` / `router ospf 1` / `passive-interface GigabitEthernet1/0` / `end` |
| Wait | none (IOS drops the neighbor at once) |
| Ask | `OSPF neighbor R1 R2 is down` |
| Expect | Playbook *OSPF neighbor down*; `R1 GigabitEthernet1/0 is a passive OSPF interface`; quote `No Hellos (Passive interface)` |
| Undo (R1) | `configure terminal` / `router ospf 1` / `no passive-interface GigabitEthernet1/0` / `end` |

### #2 OSPF hello/dead timer mismatch — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `ip ospf hello-interval 5` / `end` |
| Wait | ~40 s, until `FULL to DOWN … Dead timer expired` |
| Ask | `OSPF adjacency R1 R2 not forming` |
| Expect | `OSPF hello/dead timer mismatch … 10/40 vs 5/20`; quotes `Hello 10, Dead 40` (R1) and `Hello 5, Dead 20` (R2) |
| Undo (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `no ip ospf hello-interval` / `end` |

### #3 OSPF area mismatch — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `ip ospf 1 area 1` / `end` |
| Wait | ~10 s |
| Ask | `OSPF neighbor R1 R2 is down` |
| Expect | `OSPF area mismatch … area 0 vs area 1`; also possible: log line `mismatched area ID` |
| Undo (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `ip ospf 1 area 0` / `end` |

### #4 OSPF MTU mismatch — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `ip mtu 1400` / `end`, then `clear ip ospf process` and answer `yes` |
| Wait | ~30 s |
| Ask | `OSPF neighbor R1 R2 stuck` |
| Expect | `stuck in EXSTART` (or EXCHANGE) `… MTU mismatch` |
| Undo (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `no ip mtu` / `end`, then `clear ip ospf process`, `yes` |
| Note | **Save the output** (`tee`). IOS 12.4 may show this state differently from the recording. |

### #5 Interface administratively down — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R1) | `configure terminal` / `interface GigabitEthernet1/0` / `shutdown` / `end` |
| Ask | `interface on R1 is down` |
| Expect | Playbook *Interface down or flapping*; `R1 GigabitEthernet1/0 (10.0.12.1) is administratively shut down` |
| Undo (R1) | `configure terminal` / `interface GigabitEthernet1/0` / `no shutdown` / `end` |

### #6 Interface flapping — ✅ proven 27 Nov (after the flap fix)

| | |
|---|---|
| Plant (R1) | Two or three times: `configure terminal` / `interface GigabitEthernet1/0` / `shutdown` / `no shutdown` / `end` (wait ~5 s between rounds). Ask within 15 minutes. |
| Ask | `R1 link is flapping` |
| Expect | `R1 GigabitEthernet1/0 is flapping: line protocol went down N times in the last 15 minutes of the log` (N ≥ 2). Gi3/0 (`unassigned`, spare port) must not be the answer. |
| Undo (R1) | Nothing to undo. `clear logging` (answer `yes`) resets the log for the next test |
| Note | Flap R1 itself: in GNS3 a shutdown on R2 does not always take R1's line down. |

### #7 BGP wrong remote AS — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R2) | `configure terminal` / `router bgp 65001` / `neighbor 203.0.113.2 remote-as 65101` / `end` |
| Wait | up to ~60 s (BGP retries) |
| Ask | `BGP peer between R2 and ISP1 is down` |
| Expect | Playbook *BGP neighbor down*; `neighbor 203.0.113.2 reports a bad remote AS`; quote from `%BGP-3-NOTIFICATION … (peer in wrong AS)` |
| Undo (R2) | `configure terminal` / `router bgp 65001` / `neighbor 203.0.113.2 remote-as 65100` / `end` |

### #8 BGP neighbor shut down — ✅ proven 27 Nov

| | |
|---|---|
| Plant (R2) | `configure terminal` / `router bgp 65001` / `neighbor 203.0.113.2 shutdown` / `end` |
| Ask | `BGP neighbor on R2 is idle` |
| Expect | `BGP neighbor 203.0.113.2 is administratively shut down`; quote the `Idle (Admin)` row |
| Undo (R2) | `configure terminal` / `router bgp 65001` / `no neighbor 203.0.113.2 shutdown` / `end` |

### #9 IPsec pre-shared key mismatch — ✅ proven 27 Nov (third run)

| | |
|---|---|
| Plant (BR1) | `configure terminal` / `crypto isakmp key WRONG-KEY address 203.0.113.1` / `end` |
| Make it bite | The old tunnel stays up until it is torn down. On **both** R2 and BR1: `clear crypto session`. Then from the PC1 console: `ping 10.20.20.10` |
| Check it is active (BR1 console) | `show crypto isakmp sa` must **not** show `QM_IDLE` (expect `MM_NO_STATE` / `MM_KEY_EXCH`, or no row); `show logging \| include IKMP` may show `IKMP_BAD_MESSAGE` |
| Ask | `IPsec VPN tunnel between R2 and BR1 is down` |
| Expect | Playbook *IPsec VPN down*; phase 1 stuck in `MM_…` and/or `pre-shared key mismatch` |
| If the tunnel is still up | Rootvex now says `No fault found by the code checks: … QM_IDLE … traffic both ways` — the fault is not active yet; clear again and send traffic |
| Undo (BR1) | `configure terminal` / `crypto isakmp key <real key> address 203.0.113.1` / `end`, then `clear crypto session` on both. The real key is `LAB_VPN_PSK` in `.env` — read it there, do not paste it in chat. |

### #10 No default route ("can't reach website") — ⬜

| | |
|---|---|
| Plant (R1) | `configure terminal` / `no ip route 0.0.0.0 0.0.0.0 10.0.12.2` / `end` |
| Ask | `user behind R1 can't reach a website` |
| Expect | Playbook *User cannot reach a website*; `R1 has no default route`; quote `Gateway of last resort is not set` |
| Undo (R1) | `configure terminal` / `ip route 0.0.0.0 0.0.0.0 10.0.12.2` / `end` |

### #11 ACL blocking web traffic — ⬜

| | |
|---|---|
| Plant (R2) | `configure terminal` / `ip access-list extended BLOCK_WEB` / `deny tcp any any eq www` / `permit ip any any` / `exit` / `interface GigabitEthernet1/0` / `ip access-group BLOCK_WEB in` / `end` |
| Traffic | PC1 console: `curl http://192.0.2.80` (a few times; it should fail) |
| Ask | `user behind R2 cannot browse the web site` |
| Expect | `R2: an access-list deny entry has N hits (tcp any any eq www)` |
| Undo (R2) | `configure terminal` / `interface GigabitEthernet1/0` / `no ip access-group BLOCK_WEB in` / `exit` / `no ip access-list extended BLOCK_WEB` / `end` |

---

## 3. When the answer is wrong

1. Keep `logs/fault-NN.txt`.
2. Look at the **Playbook checks** section: did the router print the line the
   expectation needs?
   - **Yes, but code missed it** → the regex in `src/rootvex/playbooks/analyzers.py`
     or `config/playbooks/*.yaml` must match the real format.
   - **No** → the fault did not take effect yet (wait longer, `clear ip ospf process`,
     `clear crypto session`) or needs a different check command.
3. Copy the real output into
   `tests/fixtures/scenarios/<scenario>/cisco_ios/<DEVICE>/<command_slug>.txt`
   (slug: lower case, spaces → `_`, no filter: `show_logging.txt`).
4. Fix, then `uv run ruff check . && uv run pytest`, commit on `phase4`.

Send the saved output to Claude or Gemini; do not send `.env` or passwords.

---

## 4. Record results

After each fault, add a row to `docs/SETUP_LOG.md` section 16:

| # | Fault | Expected | Got | Result |
|---|---|---|---|---|

## 5. Close Phase 4

1. All 11 faults ✅ (or a written reason for any skipped).
2. Update `docs/MANUAL.md` Phase 4 "Live:" line and Part 0.
3. `uv run scripts/build_pdfs.py`
4. Commit and push `phase4`; review; merge into `main` (your decision).
5. Start Phase 5 (`docs/PHASE4-5_STEPS.md`).

## 6. Progress

| # | Fault | Status |
|---|---|---|
| 1 | OSPF passive | ✅ 27 Nov |
| 2 | OSPF timers | ✅ 27 Nov |
| 3 | OSPF area | ✅ 27 Nov |
| 4 | OSPF MTU | ✅ 27 Nov |
| 5 | Interface shutdown | ✅ 27 Nov |
| 6 | Interface flapping | ✅ 27 Nov |
| 7 | BGP wrong AS | ✅ 27 Nov |
| 8 | BGP shut down | ✅ 27 Nov |
| 9 | IPsec key | ✅ 27 Nov |
| 10 | No default route | ⬜ |
| 11 | ACL blocks web | ⬜ |
