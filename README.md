# Rootvex

A self-hosted, open-source AI agent that helps network and security engineers
troubleshoot and configure network and security devices — built from scratch,
running locally, lab-first.

> Status: Phases 1–3 built (skeleton, agent loop, read-only device tools).
> See [`docs/DESIGN.md`](docs/DESIGN.md) for the design and
> [`docs/PROJECT_PROMPT.md`](docs/PROJECT_PROMPT.md) for the build brief.

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

- [`docs/MANUAL.md`](docs/MANUAL.md) — project manual: how it works, setup, every build phase from Phase 0 ([PDF](docs/NetWarden-Manual.pdf))
- [`docs/PROJECT_PROMPT.md`](docs/PROJECT_PROMPT.md) — build brief for Claude Code
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — hardware, models, cloud, and tooling decisions
- [`lab/`](lab/README.md) — GNS3 lab as code: device configs and a one-command build script
- [`docs/lab-topology-3d.html`](docs/lab-topology-3d.html) — interactive 3D view of the GNS3 lab (open in a browser)
- [`docs/SETUP_LOG.md`](docs/SETUP_LOG.md) — Ubuntu workstation setup and fixes
