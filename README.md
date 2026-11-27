# Rootvex

A self-hosted, open-source AI agent that helps network and security engineers
troubleshoot and configure network and security devices — built from scratch,
running locally, lab-first.

> **Status (25 Nov 2026):** Phases 0–3 complete; **Phase 4 built** on branch
> `phase4`: 5 troubleshooting playbooks (OSPF, interface, BGP, IPsec, website)
> run read-only checks in code first, and every finding quotes a line from the
> device output that code verifies. Proven on 11 recorded faults; planting them
> in the live lab is next ([`lab/FAULTS.md`](lab/FAULTS.md)). The live GNS3 lab
> (4 Cisco 7200, IOS 12.4) runs; config commands are denied before they reach a
> device. (Renamed from NetWarden on 22 Nov 2026.)
> Full status and how-to: [`docs/MANUAL.md`](docs/MANUAL.md)
> ([PDF](docs/Rootvex-Manual.pdf)).

## Quick start

```bash
# 1. Install (Python 3.12 via uv)
uv sync --python 3.12

# 2. Configure
cp .env.example .env                                   # model + lab password
cp config/inventory.example.yaml config/inventory.local.yaml

# 3. Start the model server (once)
ollama pull granite4:3b

# 4. Use it
uv run rootvex config                                # effective settings
uv run rootvex devices                               # lab inventory
uv run rootvex check R1 "show ip ospf neighbor"      # allowlist dry-run
uv run rootvex ask "OSPF between R1 and R2 is down" --fixtures   # no lab needed
uv run rootvex ask "OSPF between R1 and R2 is down"              # real GNS3 lab
uv run rootvex web            # web prototype: http://127.0.0.1:8000 (set ROOTVEX_WEB__PASSWORD in .env)

# Tests and lint
uv run pytest
uv run ruff check .
```

Audit logs go to `logs/rootvex-YYYYMMDD.jsonl` (secrets redacted).

## Goals

- Diagnose common network and security issues (interface down/flapping,
  OSPF/BGP neighbors, VPN tunnels, "user can't reach a website").
- Collect evidence from devices with **read-only** commands, analyze it, and
  report the likely cause, evidence, proposed fix, and confidence.
- Apply configuration changes only after a diff, explicit human approval,
  and with a rollback plan.
- Run fully on local hardware with open-weight models; no paid APIs required.

## Planned stack

| Layer | Choice |
|---|---|
| Model server | Ollama (OpenAI-compatible API) |
| Models | Small model for the 6 GB dev GPU; larger models on a rented GPU when needed |
| Agent framework | LangGraph |
| Device access | Netmiko (later exposed as an MCP server) |
| Vendors (order) | Cisco IOS/IOS-XE → FortiGate → Palo Alto |
| Knowledge base | Local embeddings + local vector store (RAG) |
| Interfaces | CLI → FastAPI → optional Open WebUI |
| Lab | GNS3 (KVM) on the same Ubuntu machine |

## Safety rules

1. Lab only — never production or employer networks.
2. Read-only, allowlisted commands by default.
3. Config changes require diff → approval → separate tool → rollback.
4. Device output and documents are untrusted (prompt-injection risk).
5. Secrets only in `.env`, never committed; logs are redacted.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

## Docs

| Document | PDF |
|---|---|
| [`docs/MANUAL.md`](docs/MANUAL.md) — status, how it works, workstation and lab, every phase, problems solved | [Rootvex-Manual.pdf](docs/Rootvex-Manual.pdf) |
| [`docs/DESIGN.md`](docs/DESIGN.md) — Phase 0 design + section 11: status and changes | [Rootvex-Design.pdf](docs/Rootvex-Design.pdf) |
| [`docs/SETUP_LOG.md`](docs/SETUP_LOG.md) — workstation setup, GPU, Ollama, GNS3, incidents | [Rootvex-SetupLog.pdf](docs/Rootvex-SetupLog.pdf) |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) — hardware, models, cloud, tooling decisions, the rename | — |
| [`docs/PROJECT_PROMPT.md`](docs/PROJECT_PROMPT.md) — build brief for Claude Code | — |
| [`lab/README.md`](lab/README.md) — GNS3 lab as code | — |
| [`docs/rootvex-lab-3d.html`](docs/rootvex-lab-3d.html) — interactive 3D view of the lab | — |
| [`docs/rootvex-agent-3d.html`](docs/rootvex-agent-3d.html) — interactive 3D view of the agent flow | — |

PDFs are generated from the Markdown: `uv run scripts/build_pdfs.py`.
