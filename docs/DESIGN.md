# Rootvex design (Phase 0)

**Version 4 — 25 November 2026** (renamed from NetWarden on 22 November). Phases 0–3 are complete;
Phase 4 (playbooks) is built and proven on recorded faults (section 11.7); live-lab proof is next.
Sections 1–10 are the original Phase 0 design, kept as the record of what was
agreed. **Section 11 lists what changed during the build and answers the open
questions.** Current status and how-to: [`MANUAL.md`](MANUAL.md).

## 1. Verified environment (2026-11-19)

| Item | Found | Impact |
|---|---|---|
| OS / kernel | Ubuntu 26.04.1 LTS, Linux 7.0.0-43 | OK |
| CPU | 2x Xeon E5-2680 v3, 48 threads | Plenty for CPU offload and GNS3 |
| RAM | **15 GiB** total, ~6 GiB free at check time, 4 GiB swap | Tightest resource. Model + GNS3 routers + Docker must share it |
| Disk | SSD `/` 233 GB, 147 GB free. HDD `sdb2` 931 GB **NTFS** (fuse) at `/mnt/win` | Keep models and vector DB on the SSD. NTFS-over-fuse is slow and has no Unix permissions |
| GPU | GTX 1060 6 GB, driver 580.178.04, CUDA 13.0 | Ollama works; no fine-tuning |
| Python | System 3.14.4; `uv` 0.13.9 can install others | Project pins **3.12** via `uv` (best wheel coverage for netmiko/pyarrow deps) |
| Docker | 29.10.0, Compose v5.8.0 | **No NVIDIA runtime** (Container Toolkit not installed). Run Ollama natively until Phase 9 |
| Ollama | Binary 0.40.16 (= latest release). **No service running, no models pulled** | Must start it (`ollama serve` or a systemd unit) and pull models before Phase 2 |
| GNS3 | 2.2.61, `/dev/kvm` present, user in `kvm`, `ubridge`, `libvirt` | OK for Phase 3 lab |
| Git | git 2.53, gh 2.104 logged in as `sabroxyy`, remote `git@github.com:sabroxyy/Rootvex.git` | OK |

## 2. Goals and non-goals

Goals:
- Answer "why is X broken?" for lab devices by collecting evidence with
  read-only commands and reporting: likely cause, evidence, proposed fix,
  confidence, next steps.
- Run fully local with open-weight models; switch to a larger model on a
  rented GPU by changing config only.
- Code that is easy to read and explain on camera.

Non-goals (for now):
- Production or employer networks. Ever.
- Autonomous config changes. Phase 8 adds human-approved changes only.
- Multi-user auth, HA, or scale.

## 3. Architecture

```
            +---------------- CLI (typer) / FastAPI (Phase 7) ----------------+
            |                                                                 |
question -> |  LangGraph agent                                                |
            |   understand -> plan -> collect_evidence <-> tools -> analyze -> report
            |        |            (step limit + wall-clock timeout)            |
            +--------|--------------------|-----------------------------------+
                     |                    |
              LLM client           Tool layer (read-only)
         (OpenAI-compatible)     +-------------------------+
                     |           | guardrails:             |
              Ollama :11434      |  inventory (lab only)   |
          (or vLLM on cloud GPU) |  allowlist (deny default)|
                                 |  output sanitizer        |
                                 +-----------+-------------+
                                             | Netmiko (SSH)
                                       GNS3 lab devices

  Side services: RAG retriever (local embeddings + vector store),
                 playbooks (YAML), audit log (JSON lines, redacted)
```

### 3.1 Model access
- Use the **OpenAI-compatible** endpoint (`langchain-openai` `ChatOpenAI`
  with `base_url`). Ollama serves it at `http://localhost:11434/v1`; vLLM or
  llama.cpp on a cloud GPU serve the same API. Moving to a bigger model means
  editing `ROOTVEX_LLM_BASE_URL` and `ROOTVEX_LLM_MODEL` only.
- Default dev model: **`granite4:3b`** (tag confirmed on ollama.com). Fallback
  candidate: `qwen3.5:4b`. Phase 2 starts with a short tool-calling smoke test
  on both and we keep the one that calls tools reliably.
- Embeddings: **`bge-m3`** (tag `bge-m3:567m` confirmed on ollama.com).

### 3.2 Agent graph (LangGraph)
State is one typed dict: question, plan, evidence list, step count, final
report. Nodes:
1. `understand` - restate the problem, pick target devices from inventory,
   pick a playbook if one matches.
2. `plan` - ordered list of checks (from the playbook when available).
3. `collect_evidence` - the model calls tools; loops back until the plan is
   done, the step limit is hit, or the timeout fires.
4. `analyze` - compare evidence against "what to look for" in the playbook
   and retrieved docs.
5. `report` - structured output (pydantic model): `likely_cause`,
   `evidence[]` (each tied to a real command output), `proposed_fix`,
   `confidence` (low/medium/high), `next_steps[]`, `sources[]`.

Limits: `max_steps` (default 12 tool calls) and `timeout_s` (default 300),
both in config. Hitting a limit still produces a partial report that says so.

### 3.3 Tools
- Phase 2: one fake tool (`get_fake_interface_status`) to prove the loop.
- Phase 3: `run_show_command(device, command)`, `list_devices()`.
  Netmiko `ConnectHandler` per call (simple, no pooling at first),
  per-device timeouts from inventory.
- Later: the same tool functions exposed through an MCP server (`mcp` SDK)
  so other clients can reuse them.

### 3.4 Playbooks (Phase 4)
YAML files, one per symptom. Each has ordered checks:
```yaml
id: ospf_neighbor_down
vendors: [cisco_ios]
checks:
  - command: show ip ospf neighbor
    look_for: "Neighbor missing, or stuck in INIT/EXSTART/2WAY"
  - command: show ip ospf interface brief
    look_for: "Interface not in OSPF, wrong area, passive"
```
The playbook guides the plan; it does not bypass the allowlist.

### 3.5 RAG (Phase 5)
- Sources: `knowledge/` folder (my lab notes, runbooks, vendor docs I am
  allowed to keep locally). Raw vendor PDFs stay git-ignored.
- Pipeline: load -> chunk (heading-aware, ~800 tokens) -> embed with
  `bge-m3` via Ollama -> store in **LanceDB** (embedded, file-based, no
  server, lighter than Chroma). Each chunk keeps `source` + `section` so the
  report can cite it.
- Retrieved text is untrusted, same as device output.

### 3.6 Evaluation (Phase 6)
`eval/scenarios/*.yaml`: question, mocked device outputs, expected root
cause, facts that must not be invented. `eval/run.py` replays them against
the agent with mocked tools and scores: root-cause match, cited evidence
exists in tool output (hallucination check), steps used.

## 4. Safety design (maps to the hard rules)

| Rule | Mechanism |
|---|---|
| 1. Lab only | Inventory is a local YAML file (`inventory*.local.yaml`, git-ignored). Every device host must fall inside `allowed_networks` (CIDR list in config, e.g. GNS3 ranges). Out-of-range host = refuse to load. No discovery, no free-form hostnames from the model: the model can only name a device by inventory key |
| 2. Read-only, deny by default | Allowlist YAML per platform with anchored regexes (e.g. `^show ip ospf neighbor( detail)?$`). Command is normalized (trim, collapse spaces, lowercase keyword match). Rejected outright: newlines, `;`, `|` except `| include/section/begin`, abbreviations not in the list, anything not matched. A rejected command is logged and returned to the model as an error |
| 3. Changes last | Phase 8 only. Separate module and separate tool **not bound to the troubleshooting agent**. Flow: model proposes a change plan + rollback -> system fetches running config and renders a diff -> human approves by typing the device name in CLI/UI -> apply -> verify commands -> auto-rollback on failed verify |
| 4. Untrusted data | Tool output wrapped in `<untrusted_device_output device=... command=...>` tags, truncated to a max size, control characters stripped. System prompt states that text inside those tags is data and must never be followed as instructions. The tool set is fixed per graph, so injected text cannot unlock new capabilities |
| 5. Secrets | `.env` + env vars only, loaded with `pydantic-settings` using `SecretStr`. Inventory references credentials by env var name (`password_env: LAB_R1_PASSWORD`), never values. A log processor redacts known secret values and patterns (`password`, `secret`, `enable`, SNMP community, keys) |
| 6. Log everything | `structlog` JSON lines to `logs/rootvex-YYYYMMDD.jsonl`: every model call (model, tokens, latency), tool call (args, result size, allowed/denied), and device command (device, command, duration). Each run gets a `run_id` |
| 7. Local + open source | Ollama, LangGraph, Netmiko, LanceDB, FastAPI. No paid API anywhere |
| 8. Verify versions | Versions below checked on PyPI 2026-11-19; exact pins live in `uv.lock` |

## 5. Repository layout

```
rootvex/
  pyproject.toml            # uv project, pinned deps, ruff + pytest config
  uv.lock
  .python-version           # 3.12
  .env.example
  .gitignore
  README.md
  config/
    settings.yaml           # model, limits, allowed_networks, log dir
    inventory.example.yaml  # copy to inventory.local.yaml (git-ignored)
    allowlist/
      cisco_ios.yaml        # Phase 3
      fortinet.yaml         # later
      paloalto_panos.yaml   # later
  src/rootvex/
    __init__.py
    config.py               # load settings.yaml + .env
    logging.py              # structlog setup, redaction, run_id
    llm.py                  # build the chat model from config
    cli.py                  # `rootvex ask "..."`, `rootvex devices`
    agent/
      state.py              # AgentState
      prompts.py            # system prompt incl. untrusted-data rules
      graph.py              # LangGraph wiring
      report.py             # Report pydantic model
    devices/
      inventory.py          # load + validate inventory (lab CIDR check)
      allowlist.py          # normalize + match commands
      connection.py         # Netmiko wrapper with timeouts
    tools/
      fake.py               # Phase 2
      device_tools.py       # Phase 3
      sanitize.py           # wrap/truncate untrusted output
    playbooks/              # Phase 4: loader + *.yaml
    rag/                    # Phase 5: ingest.py, store.py, retrieve.py
    api/                    # Phase 7: FastAPI app
    changes/                # Phase 8: plan, diff, approve, apply, rollback
    mcp_server.py           # later
  knowledge/                # notes/runbooks for RAG (vendor PDFs git-ignored)
  eval/
    scenarios/
    run.py
  tests/
    fixtures/cisco_ios/*.txt  # captured show outputs
    test_config.py, test_allowlist.py, test_inventory.py, ...
  .github/workflows/ci.yml  # ruff + pytest on 3.12
  docs/
```

## 6. Dependencies

Latest versions on PyPI as of 2026-11-19. Phase 1 pins them in
`pyproject.toml`/`uv.lock`. Each is added in the phase that needs it.

| Package | Version | Phase | Why |
|---|---|---|---|
| pydantic | 2.14.4 | 1 | Typed config and report models |
| pydantic-settings | 2.16.3 | 1 | `.env` loading, `SecretStr` |
| pyyaml | 6.0.3 | 1 | Settings, inventory, allowlist, playbooks |
| structlog | 26.1.0 | 1 | JSON logs + redaction processor |
| typer | 0.29.2 | 1 | CLI |
| rich | 15.0.0 | 1 | Readable CLI output |
| pytest | 9.2.0 | 1 (dev) | Tests |
| ruff | 0.17.6 | 1 (dev) | Lint + format |
| langgraph | 1.2.23 | 2 | Agent graph |
| langchain-core | 1.8.3 | 2 | Messages, tool decorator |
| langchain-openai | 1.8.3 | 2 | OpenAI-compatible client for Ollama/vLLM |
| netmiko | 4.8.0 | 3 | SSH to devices |
| ntc-templates | 9.3.0 | 3 | TextFSM parsing of show output (optional) |
| lancedb | 0.41.0 | 5 | Local vector store |
| fastapi | 0.165.0 | 7 | API |
| uvicorn | 0.54.0 | 7 | API server |
| mcp | 2.3.0 | later | MCP server for device tools |

Not used: `langchain-ollama` (1.1.0). It would tie us to Ollama; the
OpenAI-compatible client keeps the cloud-GPU path config-only.

## 7. Machine setup needed before Phase 2

These are your machine, so I will not change them without asking:
1. Run Ollama as a service (the official installer creates a systemd unit),
   then `ollama pull granite4:3b`, `ollama pull qwen3.5:4b`, `ollama pull bge-m3`.
2. Check `ollama ps` shows the model on GPU.
3. Model storage: keep on the SSD (`~/.ollama` or `/usr/share/ollama`). The
   NTFS HDD is a poor fit; if you want models there later, reformat it ext4.
4. NVIDIA Container Toolkit: not needed until Phase 9.

## 8. Risks and limits

- **RAM (15 GiB)** is the main constraint. A 4B model in Ollama plus 2 to 4
  IOSv routers (about 0.5 to 1 GB each) plus Docker fits, but not much more.
  Prefer IOSv/IOL images over CSR1000v/Cat8000v (3 to 4 GB each).
- Small models (3B to 4B) are weak at multi-step tool use. Mitigations:
  playbooks drive the plan, strict structured output, step limit. The eval
  suite will show where a bigger model is needed.
- Allowlist regexes can be too strict (false denials) early on. Denials are
  logged so we can tune them.

## 9. GNS3 lab

Cisco IOSv/IOSvL2 first, so the lab fits 15 GiB RAM and runs on the local
GNS3 2.2.61 server with KVM. Built in stages; each stage unlocks the failures
the matching phase needs.

### 9.1 Images and nodes

| Item | Role | Approx RAM each |
|---|---|---|
| Cisco IOSv (`vios-adventerprisek9-m...qcow2`) | Routers R1, R2, ISP1, BR1 | 512 MB |
| Cisco IOSvL2 (`vios_l2-adventerprisek9-m...qcow2`) | Access switch SW1 | 768 MB |
| GNS3 docker `gns3/ipterm` | User PCs PC1, PC2 (`curl`, `ping`, `dig`) | ~30 MB |
| Docker `nginx:alpine` | Web server WEB1 | ~20 MB |
| GNS3 built-in Ethernet switch + Cloud node | Out-of-band management | 0 |
| Later: FortiGate-VM (KVM, permanent eval) | FW1 | ~2 GB |
| Later: Palo Alto PA-VM (KVM) | PA1, run with other nodes stopped | ~6.5 GB or more |

- Cisco images come from Cisco Modeling Labs (CML-Personal licence).
  FortiGate eval needs a FortiCare account; PA-VM needs a Palo Alto support
  account and licence.
- RAM figures are typical values; check each GNS3 template.
- Stages A and B together need about 3.5 GB RAM.

### 9.2 Topology

```
                Ubuntu host: tap-nw 10.99.0.1/24  (management only, no internet)
                                 |
                          [Cloud: tap-nw]
                                 |
       +------------- [MGMT-SW] GNS3 Ethernet switch --------------+
       |         |          |           |          |               |
     R1 .11    R2 .12    ISP1 .14     BR1 .13    SW1 .21     (FW1 .31 later)
     Gi0/0     Gi0/0     Gi0/0        Gi0/0      Gi0/0

 ================================ DATA PLANE ================================

  HQ (AS 65001, OSPF area 0)          ISP (AS 65100)        BRANCH (AS 65002)

 [PC1]--[SW1]--[R1]-----------[R2]-----------[ISP1]-----------[BR1]--[PC2]
   VLAN 10      Gi0/2 Gi0/1  Gi0/1 Gi0/2    Gi0/1 Gi0/2      Gi0/1 Gi0/2
                                 NAT + eBGP     |  Gi0/3        eBGP
                                                |
                                             [WEB1] 192.0.2.80
                                             www.lab.test (nginx)

   R2 Tunnel0 172.16.0.1/30 <==== IPsec VTI across ISP1 ====> BR1 Tunnel0 172.16.0.2/30
   (OSPF area 0 also runs over the tunnel: HQ LAN <-> Branch LAN)
```

Cabling: PC1 to SW1 Gi1/0 (access VLAN 10); SW1 Gi0/1 to R1 Gi0/2;
R1 Gi0/1 to R2 Gi0/1; R2 Gi0/2 to ISP1 Gi0/1; ISP1 Gi0/2 to BR1 Gi0/1;
ISP1 Gi0/3 to WEB1; BR1 Gi0/2 to PC2. Every Gi0/0 goes to MGMT-SW.

### 9.3 Addressing

| Link | Subnet | Addresses |
|---|---|---|
| Management (VRF `MGMT` on Gi0/0) | 10.99.0.0/24 | host .1, R1 .11, R2 .12, BR1 .13, ISP1 .14, SW1 .21, FW1 .31, PA1 .32 |
| HQ LAN (SW1 VLAN 10) | 10.10.10.0/24 | R1 .1, PC1 .10 |
| R1 - R2 | 10.0.12.0/30 | R1 .1, R2 .2 |
| R2 - ISP1 | 203.0.113.0/30 | R2 .1, ISP1 .2 |
| ISP1 - BR1 | 198.51.100.0/30 | ISP1 .1, BR1 .2 |
| ISP1 - WEB1 | 192.0.2.0/24 | ISP1 .1, WEB1 .80 |
| Branch LAN | 10.20.20.0/24 | BR1 .1, PC2 .10 |
| Tunnel R2 - BR1 | 172.16.0.0/30 | R2 .1, BR1 .2 |

DNS: ISP1 runs `ip dns server` with `ip host www.lab.test 192.0.2.80`;
PCs use ISP1 as resolver. Agent `allowed_networks` = `10.99.0.0/24` only,
never the home LAN `192.168.1.0/24`.

### 9.4 Build stages

| Stage | Nodes | Serves |
|---|---|---|
| A | MGMT-SW, Cloud, R1, R2 (OSPF) | Phase 3 device tools |
| B | + SW1, PC1, ISP1, WEB1, BR1, PC2; BGP, NAT, IPsec | Phase 4 playbooks, Phase 6 eval |
| C | + FW1 FortiGate between R2 and ISP1 | Fortinet support |
| D | PA1 in place of FW1, rest stopped | Palo Alto support |

### 9.5 Failures the lab can produce

| Playbook | Faults to inject |
|---|---|
| Interface down / flapping | `shutdown`, speed/duplex mismatch, port-security err-disable on SW1, suspended link in GNS3 |
| OSPF neighbor down (R1-R2, tunnel) | Area mismatch, hello/dead mismatch, MTU mismatch, passive-interface, auth mismatch |
| BGP neighbor down (R2/BR1 - ISP1) | Wrong `remote-as`, wrong neighbor IP, ACL blocking TCP 179, missing `network` |
| VPN down (R2 - BR1) | PSK mismatch, transform-set mismatch, no route to peer, ISP link down |
| User can't reach website (PC1 to `www.lab.test`) | Wrong PC gateway, no default route, NAT missing on R2, ACL block, DNS host missing, WEB1 stopped |

### 9.6 Base device config (IOSv, R1 example)

```
hostname R1
ip domain name lab.test
vrf definition MGMT
 address-family ipv4
interface GigabitEthernet0/0
 vrf forwarding MGMT
 ip address 10.99.0.11 255.255.255.0
 no shutdown
username rootvex privilege 15 secret <LAB-ONLY-PASSWORD>
crypto key generate rsa modulus 2048
ip ssh version 2
line vty 0 4
 login local
 transport input ssh
```

Management lives in its own VRF so breaking OSPF/BGP never cuts the agent's
SSH. Lab-only password; it goes in `.env` as `LAB_DEVICE_PASSWORD`. SW1
(IOSvL2) uses SVI VLAN 99 for management, Gi0/0 access VLAN 99.

### 9.7 Host management interface (one time)

```bash
nmcli connection add type tun ifname tap-nw con-name tap-nw mode tap owner $(id -u) \
  ipv4.method manual ipv4.addresses 10.99.0.1/24 ipv6.method disabled
nmcli connection up tap-nw
```

In GNS3, bind the Cloud node to `tap-nw`. Devices get no route to the
internet or the home LAN.

## 10. Open questions (answered in section 11)

1. GNS3 lab: which images do you have (IOSv, IOSvL2, IOL, versions)? Is the
   addressing plan in section 9 OK?
2. Default model: start with `granite4:3b` and compare `qwen3.5:4b` in
   Phase 2. OK?
3. Vector store: LanceDB (proposed) or Chroma?
4. Git flow: commit straight to `main` per logical step, or one branch + PR
   per phase?

## 11. Status and changes since Phase 0 (22 Nov 2026)

### 11.1 Open questions, answered

| # | Question | Answer |
|---|---|---|
| 1 | GNS3 images | Only `c7200-advipservicesk9-mz.152-4.S5` was available; it crashes on Dynamips 0.2.25 (TLB exception at power-on, every setting tried). **In use: `c7200-adventerprisek9-mz.124-24.T5`** (IOS 12.4(24)T5), which boots cleanly. IOSv + IOSvL2 (CML-Personal) stay optional for a managed SW1. IOL needs an IOU licence; the FortiGate VM is kept for Stage C (needs the KVM build) |
| 2 | Default model | **`granite4:3b`**. On the GTX 1060: 100% GPU, 42 tokens/s; found the OSPF area-mismatch test fault. `qwen3.5:4b` spends 3,000+ tokens "thinking" per step (~29 tok/s) and timed out |
| 3 | Vector store | LanceDB, to confirm when Phase 5 starts |
| 4 | Git flow | Small commits on a branch, merged into `main` after review. The project was renamed **NetWarden → Rootvex** on 22 Nov 2026 (package, CLI, `ROOTVEX_*` variables, lab device login `rootvex`, GNS3 project `rootvex-lab` and templates) |

### 11.2 Differences from the plan

| Area | Plan (sections 1–10) | Built |
|---|---|---|
| Logging module | `logging.py` | `log.py` (avoids confusion with Python's `logging`) |
| Agent graph | understand → plan → collect → analyze → report | `plan → agent ⇄ tools → report` (understand/plan and analyze/report merged; fewer model calls for a small model) |
| LLM limits | step limit, timeout | Also `max_tokens` per call (2048), `reasoning_effort`, request timeout 300 s |
| Hallucination check | In the evaluation phase | Already in every report: evidence not backed by a command that ran is marked UNVERIFIED |
| Fixtures | Tests only | `--fixtures` mode for demos without devices; interface-level commands answered from full recordings |
| Lab build | Manual GNS3 build | `lab/` as code: `topology.yaml`, configs with logical ports, `build_gns3_lab.py` via the GNS3 API |
| Router platform | IOSv | Configurable; **c7200 IOS 12.4 in use**, `iosv` optional; SW1 is IOSvL2 or GNS3's built-in switch |
| Platform syntax | — | Configs also use `{{MGMT_VRF}}` / `{{MGMT_VRF_FWD}}`: `ip vrf` on c7200, `vrf definition` on IOSv |
| SSH on IOS 12.4 | Key in startup config | IOS 12.4 ignores it; the build creates RSA keys on each console (`lab/ios_console.py`, `--ssh`) |
| SSH library | netmiko default | paramiko pinned to 4.0.0: paramiko 5 removed SHA-1 key exchange and `ssh-rsa`, all classic IOS offers |
| `.env` | `ROOTVEX_*` via pydantic-settings | All `.env` values exported at start-up so device passwords (`LAB_*`) are found |
| Inventory | 5 devices | 4 routers; SW1 added when IOSvL2 is used |
| Ollama | Native | Native as a systemd service (installer rerun); stays native until Phase 9 |
| Health checks | — | `lab/check_gpu.sh`, `lab/check_gns3_env.sh`, `lab/test_c7200.py`, `lab/test_dynamips_direct.sh` |

### 11.3 Dependency versions in use

Checked on PyPI on 21 Nov 2026 and pinned in `pyproject.toml` / `uv.lock`:
pydantic 2.14.4, pydantic-settings 2.16.3, pyyaml 6.0.4, structlog 26.1.0,
typer 0.29.2, rich 15.0.0, langgraph 1.2.24, langchain-core 1.8.4,
langchain-openai 1.8.5, netmiko 4.8.0; dev: pytest 9.2.0, ruff 0.17.6.
Phase 5+ packages are added when those phases start.

### 11.4 Rename to Rootvex

Everything was renamed, including the lab device login (`rootvex`), the GNS3
project (`rootvex-lab`), the templates (`Rootvex …`) and the router config names
(`RV-MGMT`, `RV-TS`, `RV-VTI`). The two diverged copies were merged into one
repository (`sabroxyy/Rootvex`, branch `main`). `scripts/check_old_name.sh`
checks the workstation for leftovers.

### 11.5 Phase 3 result (23 Nov 2026)

On the live lab, `rootvex ask` ran real read-only commands over SSH and
reported OSPF healthy (R1–R2 and R2–BR1 FULL); config commands sent through
the tool were denied before SSH and logged. Known limitation carried into
Phase 4: findings are checked for "command really ran" but not yet for "text
matches that device's output" (one run credited R2's Tunnel0 to R1).

### 11.6 Risks seen in practice

- **Small model gives up early** without structure → Phase 4 playbooks (done, 11.7).
- **Crash-looping emulated routers fill the disk** (~105 GB in hours) → test
  scripts stop a router after 3 crash messages; never leave one running.
- **Ollama on Pascal** depends on Ollama still shipping its CUDA 12 build →
  check release notes before upgrading.

### 11.7 Phase 4 as built (25 Nov 2026)

Changes from section 3.4:

- **Graph:** `plan → playbook → agent ⇄ tools → report`. The playbook node
  runs the checks itself (through the same `run_show_command` tool, so the
  allowlist, lab check, redaction and audit log all apply). It does not just
  "guide the plan": a 3B model skipped steps when only guided.
- **Playbook format:** `id`, `title`, `keywords` (to choose the playbook),
  ordered `checks`, regex `signatures` (cause/fix text with `{1}` and
  `{device}`), and named `analyzers` (Python, `src/rootvex/playbooks/analyzers.py`)
  for comparisons a regex cannot do — both ends of a link, counters.
- **Findings** carry `cause`, `fix`, `proof` (device, command, exact line),
  `weight` (how specific) and `symptom` (what is broken vs. why). Causes rank
  before symptoms; the same cause seen twice is kept once.
- **Quote check (from 11.5):** `Evidence.quote` must be in that device's
  output for that command (case and spacing ignored); code sets `verified` and
  `check`. The model's evidence and the code findings get the same check.
- **Report:** new fields `playbook` and `code_findings`. Code findings' proof
  lines are added as evidence. If the model's answer is weak (low confidence
  or empty), the best code cause becomes the likely cause at medium
  confidence; a confident model answer is kept.
- **Budget:** playbook checks are capped at `max_steps` commands and do not
  use the model's steps. Devices = those named in the question, else all.
- **Proof:** 11 recorded faults in IOS 12.4 format + 1 healthy case
  (`tests/fixtures/scenarios/`), each found with every proof line verified;
  `lab/FAULTS.md` gives the same faults to plant in GNS3. 86 tests pass.
- **Not done in Phase 4:** live-lab proof (needs the workstation), DNS and
  PC-gateway checks for "can't reach website" (hosts are not managed devices),
  err-disable/duplex faults (SW1 is unmanaged).

