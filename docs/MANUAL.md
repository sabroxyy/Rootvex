# Rootvex Manual

**Version 3 — status as of 23 November 2026: Phase 3 complete.** Rootvex was called NetWarden until 22 November 2026; the rename is complete.

A step-by-step guide to building, running, and growing Rootvex: a
self-hosted AI agent that helps network and security engineers troubleshoot
lab devices, using local open-weight models.

- **Part 0** is the project status: what is finished, what is in progress, what is next.
- **Part 1** explains what the project is and how it works.
- **Part 2** covers the workstation and the GNS3 lab.
- **Part 3** walks through every build phase, with what was built and how to test it.
- **Part 4** covers daily use, settings, and troubleshooting.
- **Part 5** records the problems met so far and how they were solved.

Items marked *(planned)* describe phases that are not built yet; commands and
file names there may change.

---

## Contents

0. [Part 0 — Project status](#part-0--project-status)
1. [Part 1 — The project](#part-1--the-project)
2. [Part 2 — Workstation and lab](#part-2--workstation-and-lab)
3. [Part 3 — Build phases](#part-3--build-phases)
4. [Part 4 — Using and operating Rootvex](#part-4--using-and-operating-rootvex)
5. [Part 5 — Problems solved so far](#part-5--problems-solved-so-far)
6. [Glossary](#glossary)

---

## Part 0 — Project status

### Phases at a glance

| Phase | Name | Status |
|---|---|---|
| 0 | Plan | ✅ Done — `docs/DESIGN.md` (+ PDF) |
| 1 | Skeleton | ✅ Done — config, audit log, CLI, tests, CI |
| 2 | Agent loop | ✅ Done — LangGraph agent, limits, structured report |
| 3 | Read-only device tools | ✅ Done — tested on recorded outputs and on the live GNS3 lab |
| 4 | Troubleshooting playbooks | ⬜ **Next** |
| 5 | Knowledge base (RAG) | ⬜ Planned |
| 6 | Evaluation | ⬜ Planned |
| 7 | API and web UI | ⬜ Planned |
| 8 | Approved configuration changes | ⬜ Planned |
| 9 | Packaging | ⬜ Planned |

### What works today

- **The live lab runs**: 4 Cisco 7200 routers (IOS 12.4(24)T5) in GNS3 with
  OSPF, eBGP, NAT and an IPsec VTI tunnel, reachable over SSH on the
  management network.
- `rootvex ask "Is OSPF healthy between R1 and R2?"` SSHes into the real
  routers, runs 7 read-only commands and correctly reports OSPF healthy
  (R1–R2 FULL, R2–BR1 FULL over the tunnel).
- Config commands (`configure terminal`, `conf t`, `reload`) are **denied before
  they reach the router**, and every denial is in the audit log.
- Against recorded outputs, `granite4:3b` found an OSPF area mismatch (R1 area 0,
  R2 area 1) with high confidence.
- Read-only device tools with a deny-by-default Cisco IOS allowlist, a lab-only
  inventory check, secret redaction, and untrusted-output wrapping.
- 57 automated tests pass (`uv run pytest`), no devices or model needed.
- The whole GNS3 lab is described as code (`lab/`) and builds with one command.
- Health checks for the workstation: GPU (`lab/check_gpu.sh`) and GNS3
  (`lab/check_gns3_env.sh`).
- One codebase under the new name: repo `sabroxyy/Rootvex`, branch `main`,
  folder `~/projects/rootvex`. `scripts/check_old_name.sh` confirms no old
  names are left on the workstation.

### Known limitations (to fix in Phase 4)

- **Findings can be mis-copied.** The check confirms each cited command really
  ran, but not that the finding text matches that device's output. Seen live:
  R2's Tunnel0 credited to R1, and R2's Gi1/0 given a wrong address.
- **The small model reframes or skips steps.** Asked to run `configure terminal`,
  it ran show commands instead; without a playbook it may stop early.
- **SW1 is unmanaged** (GNS3's built-in switch); the port-security fault needs
  an IOSvL2 image (CML-Personal, optional).
- **IOS 12.4 needs old SSH algorithms** (SHA-1 key exchange, `ssh-rsa`); Rootvex
  pins paramiko 4.0.0, and your own `ssh` needs the lab entry in `~/.ssh/config`.

### Rename to Rootvex ✅

| Done | Detail |
|---|---|
| Code | Package `rootvex`, CLI `rootvex`, variables `ROOTVEX_*`, logs `logs/rootvex-*.jsonl` |
| Lab | Device login `rootvex`, GNS3 project `rootvex-lab`, templates `Rootvex …`, config names `RV-MGMT` / `RV-TS` / `RV-VTI` |
| Two copies merged | The newer work from `~/netwarden` (16 commits) was merged into the renamed repo and renamed too |
| Clean-up | Old GNS3 projects and templates removed (`--remove-old`), old folders `~/netwarden`, `~/projects/netwarden`, `~/Desktop/NetWarden` deleted, old branches deleted; only `main` remains |
| Check | `bash scripts/check_old_name.sh` → "No old names left that need action" |

Only history lines still say "NetWarden" (the original name decision and the
"renamed from" notes).

### Next steps (Phase 4)

1. Playbooks (YAML) for: interface down, OSPF neighbor down, BGP neighbor down,
   IPsec VPN down, "user can't reach website".
2. Each finding must quote an exact line from that device's output, checked in code.
3. Plant each fault in the lab and prove the playbook finds it.
4. Optional: IOSv + IOSvL2 (CML-Personal) for a managed SW1 —
   `LAB_ROUTER_PLATFORM=iosv`, `LAB_SWITCH_PLATFORM=iosvl2`; configs don't change.

### Where the code lives

| Item | Value |
|---|---|
| Repository | `github.com/sabroxyy/Rootvex` (private; the old NetWarden URL redirects) |
| Branch | `main` (feature work on short-lived branches, merged after review) |
| Local folder | `~/projects/rootvex` on the workstation |

---

## Part 1 — The project

### What Rootvex does

You describe a problem in plain language — *"OSPF neighbor between R1 and R2
is down"* — and Rootvex:

1. **Plans** which read-only checks to run, on which devices.
2. **Collects evidence** by running allowlisted commands on lab devices.
3. **Analyzes** the output.
4. **Reports** the likely cause, the evidence (each item tied to a command it
   really ran), a proposed fix (never applied), its confidence, and next steps.

It never changes a device. Approved changes come only in Phase 8.

### What it is not

- Not a replacement for the engineer: it gathers evidence and suggests; you decide.
- Not for production or employer networks: lab only, enforced in code.
- Not a model trained from scratch: it uses existing open-weight models.

### How it works

```
 Engineer --question--> CLI (rootvex ask)
                          |
                          v
                 LangGraph agent:  plan -> agent <-> tools -> report
                     |                       |
        prompts / answers               allowlisted commands
                     |                       |
              Ollama on the GPU       Device tools (Netmiko, SSH)
              (granite4:3b)                  |
                                     GNS3 lab devices (10.99.0.0/24)

   Every model call, tool call and command -> audit log (JSON lines, secrets redacted)
```

| Component | Role | Code |
|---|---|---|
| **Ollama** | Runs the model locally; OpenAI-compatible API on port 11434 | — |
| **Agent (LangGraph)** | Plan → collect evidence → report, with step and time limits | `src/rootvex/agent/` |
| **Device tools** | `list_devices`, `run_show_command`; SSH via Netmiko | `src/rootvex/tools/`, `src/rootvex/devices/` |
| **Allowlist** | Regex list of read-only commands per platform | `config/allowlist/cisco_ios.yaml` |
| **Inventory** | Lab devices by name; IPs must be inside `10.99.0.0/24` | `config/inventory.local.yaml` |
| **Audit log** | Every step, with run id, timings and token counts | `logs/rootvex-YYYYMMDD.jsonl` |
| **Playbooks** *(Phase 4)* | Ordered checks for common problems | *(planned)* |
| **Knowledge base** *(Phase 5)* | Docs and notes the agent can cite | *(planned)* |

### The safety model

These rules are enforced in code, not only in prompts:

| Rule | How it is enforced today |
|---|---|
| Lab only | Inventory hosts must be IP addresses inside `allowed_networks` (`10.99.0.0/24`); anything else refuses to load. The model can only name devices by inventory key |
| Read-only by default | Every command is checked against anchored regexes before SSH. Abbreviations (`sh run`), `;`, newlines, backticks and more than one `|` filter are refused |
| Changes need approval | No change tool exists yet. Phase 8 adds diff → approval → apply → verify → rollback |
| Untrusted input | Device output is stripped of control characters, truncated, and wrapped in `<untrusted_device_output>` tags the model is told never to obey; output cannot close the tag early |
| No leaked secrets | Credentials only in `.env`; inventory holds env-var *names*; passwords, secrets and keys are redacted from logs and from device output |
| Bounded runs | Max 12 tool calls and 300 s per run; a run that hits a limit still returns a partial report |
| No invented evidence | Evidence the model lists is checked against commands that really ran; anything else is marked **UNVERIFIED** |

---

## Part 2 — Workstation and lab

### Workstation status

| Item | Status | Detail |
|---|---|---|
| Ubuntu 26.04.1 LTS, Dell Precision 7810 | ✅ | 2× Xeon E5-2680 v3 (48 threads), 15 GiB RAM |
| Disk | ✅ | SSD 233 GB, ~104 GB free. 1 TB HDD is NTFS at `/mnt/win` (not used) |
| NVIDIA GTX 1060 6 GB, driver 580.178.04, CUDA 13.0 | ✅ | Load test: 100% GPU, 42 tok/s, peak 59 °C |
| Ollama 0.40.17 as a systemd service | ✅ | Fixed: the old install had no service |
| Docker without sudo | ✅ | |
| GNS3 2.2.61 (GUI = server), KVM, ubridge, dynamips | ✅ | GNS3 VM disabled (not needed on Linux) |
| Management interface `tap-nw` 10.99.0.1/24 | ✅ | Created with nmcli |
| Router images | ✅ | Cisco 7200 IOS 12.4(24)T5 (`c7200-adventerprisek9-mz.124-24.T5.image`, md5 verified); 15.2(4)S5 crashed |

Full history: [`SETUP_LOG.md`](SETUP_LOG.md).

### Health checks

Run from `~/projects/rootvex`. Both are read-only and need no sudo.

```bash
bash lab/check_gpu.sh            # driver, temps, Xid errors, Ollama sees the GPU
```

```bash
bash lab/check_gpu.sh --load     # + real model run: 100% GPU? tokens/s? peak temp?
```

```bash
bash lab/check_gns3_env.sh       # KVM, groups, GNS3, ubridge, images, tap-nw
```

Each line starts with OK, WARN or FAIL. Fix FAIL lines first.

### The GNS3 lab

Stage B of the design: HQ, ISP and Branch, with out-of-band management.

```
   Host tap-nw 10.99.0.1 -- MGMT-CLOUD -- MGMT-SW -- mgmt port of R1 .11, R2 .12, BR1 .13, ISP1 .14, SW1 .21

   HQ (AS 65001, OSPF 0)             ISP (AS 65100)          BRANCH (AS 65002)
 PC1 -- SW1 -- R1 ------ R2 ----------- ISP1 ----------------- BR1 -- PC2
                         NAT, eBGP        |  DNS www.lab.test   eBGP, NAT
                                        WEB1 192.0.2.80 (nginx)
          R2 Tunnel0 172.16.0.1 <==== IPsec VTI over ISP1 ====> BR1 Tunnel0 172.16.0.2
```

| File | Purpose |
|---|---|
| `lab/topology.yaml` | Nodes, templates, links: the one place to change the lab |
| `lab/configs/*.cfg` | Full startup configs (R1, R2, ISP1, BR1, SW1) with logical ports |
| `lab/hosts/*.interfaces` | IP settings for PC1, PC2, WEB1 (Docker) |
| `lab/build_gns3_lab.py` | Builds the project through the GNS3 API |
| `lab/README.md` | Build steps, checks, design notes, PNETLab notes |
| `docs/lab-topology-3d.html` | Interactive 3D view: Phase 3 (live), Phase 4 (fault points), Later (FW1, managed SW1) |

**Logical ports.** Router configs say `{{IF_MGMT}}`, `{{IF1}}`…; the platform
maps them to real names, so the same configs run on any router image:

| Logical | c7200 (in use) | IOSv (optional) |
|---|---|---|
| mgmt (VRF MGMT) | FastEthernet0/0 | GigabitEthernet0/0 |
| if1 | GigabitEthernet1/0 | GigabitEthernet0/1 |
| if2 | GigabitEthernet2/0 | GigabitEthernet0/2 |
| if3 | GigabitEthernet3/0 | GigabitEthernet0/3 |

**Design choices**
- Management in its own VRF, so breaking OSPF/BGP/VPN never cuts the agent's SSH.
- SSH only, and only from 10.99.0.1 (`access-class RV-MGMT`).
- R1 → R2 static default; R2 and BR1 learn the web/DNS network and each
  other's WAN through eBGP, so the tunnel depends on BGP (realistic multi-symptom faults).
- Secrets (`LAB_DEVICE_PASSWORD`, `LAB_VPN_PSK`) are filled in from `.env` at
  build time and never stored in the repo.
- Platform syntax is filled in too: the management VRF is `ip vrf MGMT` on the
  7200 (IOS 12.4 has no `vrf definition`) and `vrf definition MGMT` on IOSv.

### Build the lab

One-time host setup (already done on this workstation):

```bash
nmcli connection add type tun ifname tap-nw con-name tap-nw mode tap owner $(id -u) \
  ipv4.method manual ipv4.addresses 10.99.0.1/24 ipv6.method disabled
```

```bash
nmcli connection up tap-nw && sudo apt install -y mtools
```

`.env` must contain (lab-only values, never real passwords):

```
LAB_DEVICE_PASSWORD=...
LAB_VPN_PSK=...
LAB_ROUTER_PLATFORM=c7200
LAB_C7200_IMAGE=c7200-adventerprisek9-mz.124-24.T5.image
```

The image goes in `~/GNS3/images/IOS/`. Create the inventory once:
`cp config/inventory.example.yaml config/inventory.local.yaml`.

Open the GNS3 GUI (it starts the local server), then:

```bash
uv run python lab/build_gns3_lab.py --rebuild --start
```

The script reads the GUI's server login itself, checks images, creates
templates, the `rootvex-lab` project, nodes, links and configs, then starts
everything, sets an Idle-PC value (each router then uses ~7% CPU instead of a
full core), and creates the routers' SSH keys on their consoles (IOS 12.4 will
not do it from the startup config). It takes about 5 minutes. Then:

```bash
ping -c 3 10.99.0.11 && ssh rootvex@10.99.0.11
```

Expected on the routers: `show ip ospf neighbor` FULL between R1–R2 (and
R2–BR1 over Tunnel0), `show ip bgp summary` on ISP1 with both peers up,
`show crypto ipsec sa` on R2 with packets counting.

RAM: about 2.5 GB with everything running. Close Chrome first.

**Your own `ssh`** needs the old algorithms IOS 12.4 uses, for the lab network
only. Add to `~/.ssh/config`:

```
Host 10.99.0.*
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    KexAlgorithms +diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1
    HostKeyAlgorithms +ssh-rsa
    PubkeyAcceptedAlgorithms +ssh-rsa
    Ciphers +aes128-cbc,aes256-cbc
```

The first two lines stop "host key has changed" errors after each rebuild;
use them only for this throwaway lab network.

---

## Part 3 — Build phases

Each phase: plan → build in small commits → test → review → next phase.

---

### Phase 0 — Plan ✅

**Built:** [`DESIGN.md`](DESIGN.md) and its PDF: verified environment,
architecture, agent graph, tools, safety design, repo layout, pinned
dependency versions, GNS3 lab design (topology, addressing, faults).

**Decisions since then** (the design's open questions):

| Question | Decision |
|---|---|
| Router images | Cisco 7200, IOS 12.4(24)T5 (15.2(4)S5 crashed on Dynamips). IOSv + IOSvL2 optional later |
| Default model | `granite4:3b`: correct on the OSPF test; `qwen3.5:4b` too slow ("thinking") |
| Vector store | LanceDB (to confirm in Phase 5) |
| Git flow | Small commits on a branch, merged into `main` after review |

---

### Phase 1 — Skeleton ✅

**Built:** `uv` project pinned to Python 3.12 with pinned dependencies;
`config/settings.yaml` with `.env` / `ROOTVEX_*` overrides
(`src/rootvex/config.py`); JSON-lines audit log with run id and secret
redaction (`src/rootvex/log.py`); Typer CLI; pytest + ruff; GitHub Actions CI.

**Test it**

```bash
uv sync --python 3.12
```

```bash
uv run pytest && uv run ruff check .
```

```bash
uv run rootvex config
```

**Done:** ✅ fresh clone → tests pass · ✅ test proves secrets are redacted ·
✅ no secrets in the repo.

---

### Phase 2 — Agent loop ✅

**Built** (`src/rootvex/agent/`):

| Node | What it does |
|---|---|
| `plan` | Model writes a short numbered plan of read-only checks |
| `agent` | Model calls tools, or says it is done |
| `tools` | Runs tool calls; each is one step; errors are returned, never crash the run |
| `report` | Model writes JSON; evidence is checked against commands that really ran |

Limits: `agent.max_steps` (12) and `agent.timeout_s` (300); hitting either
still produces a report that says it is partial. LLM settings
`max_tokens` (2048 per call) and `reasoning_effort` stop "thinking" models
from rambling. The model is reached through the OpenAI-compatible API, so a
bigger model on a rented GPU is a config change only.

**Test it**

```bash
uv run rootvex ask "Why is OSPF down between R1 and R2?" --fake
```

**Done:** ✅ agent calls a tool and uses the result · ✅ step limit and timeout
tested · ✅ model change is config-only.

---

### Phase 3 — Read-only device tools ✅

**Built**

| Part | File |
|---|---|
| Inventory loader + lab-network check | `src/rootvex/devices/inventory.py` |
| Allowlist (deny by default, ~50 IOS commands) | `src/rootvex/devices/allowlist.py`, `config/allowlist/cisco_ios.yaml` |
| SSH runner (Netmiko, per-device timeouts) | `src/rootvex/devices/connection.py` |
| Fixture runner (recorded outputs, no devices) | same file; outputs in `tests/fixtures/cisco_ios/<DEVICE>/` |
| Tools `list_devices`, `run_show_command` | `src/rootvex/tools/device_tools.py` |
| CLI `devices`, `check`, `ask --fixtures` | `src/rootvex/cli.py` |
| Lab SSH set-up (RSA keys on the console) | `lab/ios_console.py`, `build_gns3_lab.py --ssh` |

**Results**
- 57 tests pass (allowlist, inventory, tools, console helper, end-to-end agent).
- Recorded outputs: `granite4:3b` found the OSPF area mismatch (11 steps, high
  confidence, evidence verified).
- **Live lab:** `rootvex ask "Is OSPF healthy between R1 and R2?"` ran 7 real
  commands over SSH and reported OSPF healthy (R1–R2 FULL, R2–BR1 FULL over
  Tunnel0); the model never changed anything.
- **Live deny test:** `configure terminal`, `conf t` and `reload` sent through the
  tool to R1 were all DENIED before SSH and logged as `command_denied`; an
  allowed `show ip interface brief | include 1/0` returned real output.

**Test it**

```bash
uv run rootvex check R1 "configure terminal" --fixtures     # must say DENIED
```

```bash
uv run rootvex ask "OSPF between R1 and R2 is down" --fixtures --steps
```

```bash
uv run rootvex ask "Is OSPF healthy between R1 and R2?" --steps     # live lab
```

**Done when** — all met on 23 Nov 2026:

| # | Check | Status |
|---|---|---|
| 1 | Tools, allowlist, inventory built | ✅ |
| 2 | Mocked tests pass | ✅ |
| 3 | Agent finds a real fault with a real model | ✅ |
| 4 | GNS3 environment check has no FAIL lines | ✅ |
| 5 | Lab built and routers booted | ✅ c7200 IOS 12.4(24)T5 |
| 6 | `ssh rootvex@10.99.0.11` works | ✅ SSH 2.0 |
| 7 | Agent runs real show commands on the lab | ✅ 7 commands, correct verdict |
| 8 | Allowlist blocks `configure terminal` on a real router | ✅ denied and logged |

---

### Phase 4 — Troubleshooting playbooks ⬜ *(next)*

**Goal:** give the small model an ordered checklist so it does not stop early.
The first OSPF run showed why: without a playbook the 3B model saw "no
neighbors" and stopped.

| Playbook | Core checks | Lab faults to inject |
|---|---|---|
| Interface down / flapping | Status, errors, duplex/speed, logs | `shutdown`, duplex mismatch, err-disable on SW1 |
| OSPF neighbor down | Interface up, subnet, area, timers, auth, MTU, passive | Area / hello / MTU / auth mismatch, passive |
| BGP neighbor down | Peer reachability, AS numbers, update-source, ACLs | Wrong remote-as or neighbor IP, ACL on TCP 179 |
| IPsec VPN down | IKE/IPsec SA, proposals, PSK, peer reachability | PSK or transform-set mismatch, ISP link down |
| User can't reach website | DNS, gateway, routing, NAT, ACL | Wrong PC gateway, NAT missing, DNS entry missing |

**Also in Phase 4** (from the Phase 3 live runs): each finding must quote an
exact line from that device's output, and code checks the quote — the live run
credited R2's Tunnel0 to R1.

**Done when:** each playbook finds its planted fault in the lab and cites the proof.

---

### Phase 5 — Knowledge base (RAG) ⬜ *(planned)*

Ingest your notes, runbooks and permitted vendor docs; embed with `bge-m3`
(Ollama); store in LanceDB; cite document and section in reports. Unknown
topics must produce "I don't know", not invention.

### Phase 6 — Evaluation ⬜ *(planned)*

20+ scenarios (`eval/scenarios/*.yaml`) replayed with recorded outputs, scored
on root-cause accuracy, invented facts, steps and time. The fixture runner
built in Phase 3 is the base for this.

### Phase 7 — API and web UI ⬜ *(planned)*

FastAPI (ask, history, health) on localhost/LAN only, with auth; optional
Open WebUI. Every request in the audit log.

### Phase 8 — Approved configuration changes ⬜ *(planned)*

Propose exact commands + running-config diff → you approve by typing the
device name → separate restricted tool applies → read-only verification →
automatic rollback on failure. Nothing changes without an approval record.

### Phase 9 — Packaging ⬜ *(planned)*

Docker Compose for the stack (Ollama on the host or via NVIDIA Container
Toolkit), full docs, demo script.

---

## Part 4 — Using and operating Rootvex

### Commands

| Command | What it does |
|---|---|
| `uv run rootvex config` | Show effective settings (secrets hidden) |
| `uv run rootvex devices [--fixtures]` | List inventory devices (after the lab check) |
| `uv run rootvex check R1 "show ip route"` | Allowlist dry-run; nothing is sent |
| `uv run rootvex ask "..." [--fixtures] [--steps]` | Troubleshoot; `--fixtures` uses recorded outputs, `--steps` prints every tool call |
| `uv run python lab/build_gns3_lab.py [--rebuild] [--start]` | Build the GNS3 lab |
| `bash lab/check_gpu.sh [--load]` / `bash lab/check_gns3_env.sh` | Health checks |
| `uv run python lab/build_gns3_lab.py --ssh` | Create missing SSH keys on the running lab's routers |
| `uv run python lab/build_gns3_lab.py --remove-old` | Delete the pre-rename GNS3 project and `NetWarden …` templates |
| `bash scripts/check_old_name.sh` | Find any leftover use of the old name on the workstation (read-only) |
| `uv run scripts/build_pdfs.py` | Rebuild the PDFs from the Markdown |

### Asking good questions

Include the device, the symptom, and what changed:

> ✅ "R1 and R2 lost their OSPF adjacency after I changed R2's interface config."
> ❌ "Network broken."

### Reading a report

| Field | Meaning |
|---|---|
| Likely cause | The agent's best explanation |
| Confidence | low / medium / high |
| Evidence | Device, exact command, what it showed; **UNVERIFIED** if the command never ran |
| Proposed fix | Suggested config, **not applied** |
| Next steps | What to check next |
| NOTE: stopped early | A step or time limit was hit; the report is partial |

Always check the evidence yourself before acting on a proposed fix.

### Settings

Edit `config/settings.yaml`, or override any key in `.env` / the environment
with `ROOTVEX_<SECTION>__<KEY>`:

| Setting | Default | Note |
|---|---|---|
| `llm.model` | `granite4:3b` | `ROOTVEX_LLM__MODEL=qwen3.5:4b` to compare |
| `llm.base_url` | `http://localhost:11434/v1` | Point at a rented GPU over a private tunnel, never the open internet |
| `llm.request_timeout_s` | 300 | Per model call |
| `llm.max_tokens` | 2048 | Per model call |
| `llm.reasoning_effort` | none set | `none` to try to turn off "thinking" |
| `agent.max_steps` / `agent.timeout_s` | 12 / 300 | Per run |
| `lab.allowed_networks` | `10.99.0.0/24` | Never add the home LAN |
| `lab.device_backend` | `netmiko` | `fixtures` = recorded outputs |

### Where things are stored

| Data | Location | In git? |
|---|---|---|
| Settings | `config/settings.yaml` | yes |
| Credentials, lab secrets | `.env` | **no** |
| Devices | `config/inventory.local.yaml` (copy of the example) | **no** |
| Allowed commands | `config/allowlist/cisco_ios.yaml` | yes |
| Recorded outputs | `tests/fixtures/cisco_ios/<DEVICE>/` | yes |
| Audit logs | `logs/rootvex-YYYYMMDD.jsonl` | **no** |
| Lab definition | `lab/` | yes |
| GNS3 projects / images | `~/GNS3/projects`, `~/GNS3/images` | **no** |
| Models | `/usr/share/ollama/.ollama` (SSD) | **no** |

### Troubleshooting

| Problem | Check / fix |
|---|---|
| "Cannot reach the model server" | `systemctl is-active ollama`; `curl http://127.0.0.1:11434/api/version` |
| "The model did not answer within …s" | Thinking model or first load; raise `ROOTVEX_LLM__REQUEST_TIMEOUT_S`, or use `granite4:3b` |
| Model is slow | `ollama ps` — must say 100% GPU; otherwise use a smaller model |
| Agent stops before finding the cause | Use `--steps` to see what it ran; Phase 4 playbooks fix this |
| Command refused | Not on the allowlist, or abbreviated; use full words (`show running-config`) |
| SSH to a lab device fails | `ip -br addr show tap-nw` (UP?), `ip neigh show dev tap-nw`, router console |
| Router console full of "TLB Exception" | Image does not run on this Dynamips; **stop it at once** (it fills the disk) |
| Disk suddenly full | `du -xsh ~/GNS3/projects/*`; delete `dynamips_i*_stdout.txt` / `c7200_i*_log.txt` with routers stopped |
| GPU missing after an update | `bash lab/check_gpu.sh`; Pascal stays on driver branch 580 |

### Rebuilding the PDFs

The Markdown in `docs/` is the source; the PDFs are generated from it:

```bash
uv run scripts/build_pdfs.py
```

### Updating safely

```bash
git pull origin main
```

```bash
uv sync && uv run pytest
```

---

## Part 5 — Problems solved so far

| # | Problem | Cause | Fix |
|---|---|---|---|
| 1 | Ollama "not running" | Old install had no systemd service | Reran the official installer → `ollama.service` enabled |
| 2 | Ollama warns about CUDA 13 | GTX 1060 (compute 6.1) not in Ollama's CUDA 13 build | Harmless: it uses its CUDA 12 build. Before upgrading Ollama check release notes; pin with `OLLAMA_VERSION=0.40.17` if needed |
| 3 | First agent run missed the cause | Few recorded outputs; small model stopped early | More recordings, interface-level answers from full outputs; Phase 4 playbooks next |
| 4 | `qwen3.5:4b` "cannot reach model" | Actually a timeout: 3,000+ thinking tokens in one step | Separate timeout message; `max_tokens`, `reasoning_effort`, 300 s timeout |
| 5 | GPU check reported throttling | Script bug ("Not Active" matched) | Fixed |
| 6 | c7200 routers crash at power-on | Image does not run on Dynamips 0.2.25 (TLB exception) | Ruled out RAM, memory model, midplane, I/O memory, ghost IOS, Idle-PC. Moving to IOSv |
| 7 | Disk 100% full | Crash loop wrote ~105 GB of Dynamips logs | Deleted logs; test scripts now stop a router after 3 crash messages |
| 8 | IOSv links would fail | GNS3 names IOSv ports `Gi0/1`, configs say `GigabitEthernet0/1` | Port matching treats long and short names the same (caught in a dry run) |
| 9 | `git pull` blocked | A stray Dynamips log was committed by mistake | Removed; Dynamips files added to `.gitignore` |
| 10 | Two copies of the code diverged | Rename done in `~/projects/rootvex` while new work went on in `~/netwarden` | One merge onto the renamed `main` (git carried the code into `src/rootvex/`), then the same rename on the new content; 52 tests pass |
| 11 | GNS3 could not delete the old c7200 project | Docker had created files owned by root inside it | `sudo rm -rf <project>/project-files/docker`; `--remove-old` now keeps going and prints that command |
| 12 | Name check reported itself | The checker's own search patterns contain the old name | The script skips its own file |
| 13 | IOS 12.4 rejected the management VRF | 12.4 has no `vrf definition` (IOS 15 syntax) | VRF lines are filled per platform: `ip vrf` on the 7200 |
| 14 | SSH refused on port 22 | IOS 12.4 does not run `crypto key generate` from the startup config | Build creates the RSA keys on each console (`--ssh`) |
| 15 | "No matching key exchange method" | IOS 12.4 only offers SHA-1 key exchange and `ssh-rsa`; OpenSSH and paramiko 5 refuse them | `~/.ssh/config` entry for 10.99.0.*; paramiko pinned to 4.0.0 |
| 16 | "LAB_DEVICE_PASSWORD is not set" | Only `ROOTVEX_*` keys were read from `.env` | `.env` values are exported to the environment at start-up |
| 17 | R1 reported "key created" when it had a key | A stale console prompt was read as the end of `show ip ssh` | Helper waits for each command's echo; key kept, reported correctly |
| 18 | "Host identification has changed" | Each rebuild gives routers new SSH keys | Lab-only `StrictHostKeyChecking no` / `UserKnownHostsFile /dev/null` |

Lessons:
- Never leave a crash-looping Dynamips router running.
- A small model needs structure (playbooks) more than a bigger context.
- Test the lab build against a real GNS3 server before using it — the dry run
  caught the IOSv port-name bug before it reached the workstation.
- Keep one working copy. Two folders for one project led to diverged history.
- Old IOS needs old crypto: plan client settings (OpenSSH, paramiko) up front.
- "Confirmed it ran" is not "confirmed it's true": findings need quoting.

---

## Glossary

| Term | Meaning |
|---|---|
| **Agent** | A model running in a loop that decides which tool to call next |
| **Tool** | A function the agent may call, such as running a show command |
| **LangGraph** | Python framework for building agent loops as graphs of steps |
| **Ollama** | Program that downloads and runs open-weight models locally |
| **Open-weight model** | A model whose weights you can download and run yourself |
| **VRAM** | GPU memory; decides which model sizes fit |
| **Tokens/s** | Generation speed; ~42 for `granite4:3b` on the GTX 1060 |
| **Thinking / reasoning model** | A model that writes long hidden reasoning before answering |
| **Fixture** | A recorded command output used instead of a real device |
| **Allowlist** | The list of commands the agent is permitted to run |
| **Prompt injection** | Text in data (logs, hostnames) trying to give the model instructions |
| **GNS3** | Network emulator that runs the lab routers, switches and hosts |
| **Dynamips** | GNS3's emulator for older Cisco routers such as the 7200 |
| **IOSv / IOSvL2** | Cisco virtual router / switch images, from Cisco Modeling Labs |
| **CML-Personal** | Cisco Modeling Labs personal licence; source of IOSv images |
| **Idle-PC** | Dynamips setting that stops each router using a full CPU core |
| **VRF** | A separate routing table; here it keeps management traffic apart |
| **RAG** | Searching documents and giving the relevant parts to the model |
| **MCP** | Model Context Protocol: a standard way to expose tools to AI apps |
| **Evaluation (eval)** | A repeatable test set that scores the agent's answers |
