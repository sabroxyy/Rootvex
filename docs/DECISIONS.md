# Decisions and research notes

Prices and model rankings below were checked in November 2026 and change
often — re-check before relying on them.

## Scope

- The agent stays **outside any employer network**. It runs on a personal
  machine against personal lab devices (GNS3) with lab-only credentials.
- No employer configs, topology, logs, runbooks, or scripts are used.

## Hardware

| Item | Decision |
|---|---|
| Workstation | Dell Precision 7810 (dual-socket class, Z840 equivalent) |
| OS | Ubuntu 26.04 LTS, installed on the 256 GB SSD (`sda`) |
| Storage | 1 TB WD HDD (`sdb`) kept for models, datasets, backups |
| GPU now | GTX 1060 6 GB — fine for building and small models; cannot fine-tune with modern tools (Pascal) |
| GPU upgrade | RTX 3060 **12 GB** preferred (~EGP 19,000–21,000 used, Nov 2026); RTX 3060/3070 8 GB acceptable; RTX 3090 24 GB for 27B-class models |

Notes:
- VRAM matters more than speed for local models.
- GTX 10-series cards: driver 580 is the last supporting branch; no Unsloth.
- Check PSU wattage and PCIe power connectors before any GPU upgrade.
- Used workstation options compared: HP Z840 (2× E5-2698 v4, 32 GB, 1125 W, EGP 29,350),
  HP Z640 (E5-2680 v4, 16 GB, EGP 23,000), HP Z640 (E5-1650 v4, 16 GB, EGP 20,000).

## Models (by VRAM)

| VRAM | Model to try | Notes |
|---|---|---|
| 6 GB (now) | `granite4:3b` | **In use.** 100% GPU, 42 tok/s, solved the OSPF test (Nov 22) |
| 6 GB (now) | `qwen3.5:4b` | Fits, but "thinks" 3,000+ tokens per step (~29 tok/s); too slow for the agent loop |
| 6 GB (now) | `qwen3.5:9b` | ~6.6 GB, partly on CPU, slower |
| 12 GB | `qwen3.5:9b` | Fits comfortably |
| 16 GB | `gpt-oss:20b` | Native function calling |
| 24 GB | `qwen3.6:27b`, `glm-4.7-flash` | Best local agent options |

- Embeddings for RAG: BGE-M3 (multilingual incl. Arabic); Nomic Embed v2 lighter.
- Fine-tune small dense models (QLoRA, Unsloth); avoid MoE models at first.
- Use RAG for knowledge; fine-tune only for method and output format.

## Cloud GPU (when local isn't enough)

| Option | Price (Nov 2026) | Notes |
|---|---|---|
| Google Cloud L4 (24 GB), `g2-standard-4` | ~$0.71/hr on-demand, ~$0.42/hr Spot | Free trial can't use GPUs; upgrade to paid |
| RunPod RTX 4090 Community | ~$0.34/hr | Lab/fake data only |
| RunPod RTX 4090 Secure | ~$0.89/hr | More trustworthy hosts |

Plan: build daily on the local PC; rent ~20 GPU hours/month for big models
and fine-tuning (~EGP 465/month on L4 Spot at $1 = EGP 55).

## Lab router images (updated Nov 23)

- `c7200-advipservicesk9-mz.152-4.S5` does **not** boot on Dynamips 0.2.25 (TLB exception).
- **In use: `c7200-adventerprisek9-mz.124-24.T5`** (IOS 12.4(24)T5): boots cleanly,
  ~7% CPU per router with Idle-PC. Needs `ip vrf` syntax, RSA keys created on the
  console, and SHA-1 SSH algorithms (paramiko pinned to 4.0.0).
- Optional later: **IOSv + IOSvL2 from CML-Personal** (`vios-adventerprisek9-m.spa.159-3.m9`,
  `vios_l2-adventerprisek9-m.ssa.high_iron_20200929`) for a managed SW1.
- IOL L3 15.7 needs an IOU licence; FortiGate 7.0.5 is for Stage C (use the KVM build).

## Software stack

- Ollama, LangGraph, Netmiko (+ netmiko-mcp: deny-by-default allowlist),
  FortiManager MCP (beta, lab only), Docker, Open WebUI, GNS3 with KVM.
- Kali runs as a VM on Ubuntu, not as the main OS.
- GitHub Desktop has no official Linux build; use git + gh CLI + VS Code.

## Name

Chosen: **NetWarden**. Alternatives considered: RootCause, ShowRun, Triage,
NOCmate, Fixpoint, PacketSage, Netfix Agent.

Renamed to **Rootvex** on 2026-11-22: Python package `rootvex`, CLI `rootvex`,
environment variables `ROOTVEX_*`, log files `logs/rootvex-*.jsonl`. A second
pass the same day renamed what had been kept for the old lab, because that lab
never booted: lab device login `rootvex` (was `netwarden`), GNS3 project
`rootvex-lab` (was `netwarden-lab`), templates `Rootvex IOSv/IOSvL2/c7200/ipterm/nginx`
(were `NetWarden …`), and router config names `RV-MGMT`, `RV-TS`, `RV-VTI` (were `NW-…`).
Old GNS3 templates and the old project can be removed with
`uv run python lab/build_gns3_lab.py --remove-old`.

## Sources

- https://localairun.pages.dev/local-llm-tools
- https://www.promptquorum.com/power-local-llm/best-local-models-tool-calling-2026
- https://vdf.ai/blog/best-local-llm-for-agents-tool-calling/
- https://www.morphllm.com/best-ollama-models
- https://www.openlayer.com/blog/embedding-models-rag-retrieval
- https://pypi.org/project/netmiko-mcp/
- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://computeprices.com/providers/google/gpus/l4
- https://computeprices.com/providers/runpod/gpus/rtx4090
- https://www.dubizzle.com.eg (used GPU and workstation listings)
