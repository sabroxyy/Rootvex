# Notes for Gemini CLI (handoff from Claude Code)

Read this first. Then `docs/PHASE4_RUNBOOK.md` (exact procedure per fault), `docs/PHASE4-5_STEPS.md` (the step-by-step plan), `docs/MANUAL.md` (Part 0 and Phase 4) and `lab/FAULTS.md`.

## Project in one paragraph

Rootvex is a lab-only, read-only AI troubleshooting agent for network devices
(Python 3.12, uv, LangGraph, local model `granite4:3b` on Ollama, Netmiko SSH).
The lab is GNS3 with 4 Cisco 7200 routers (IOS 12.4(24)T5): R1, R2, ISP1, BR1,
management network 10.99.0.0/24 in VRF MGMT. Interfaces: if1 = Gi1/0,
if2 = Gi2/0, if3 = Gi3/0, mgmt = Fa0/0.

## Where we are (27 Nov 2026)

- Branch `phase4` (not merged into `main`). Phases 0-3 done.
- Phase 4 built: 5 playbooks in `config/playbooks/`, code analyzers in
  `src/rootvex/playbooks/`, quote-checked evidence, code findings with proof
  overrule a model answer that disagrees ("Model's view").
- `uv run ruff check .` passes, `uv run pytest` = 89 passed.
- Recorded faults replay correctly:
  `uv run rootvex ask "OSPF neighbor R1 R2 is down" --scenario ospf_passive`
  gives "R1 GigabitEthernet1/0 is a passive OSPF interface", confidence high.

## Next task: prove Phase 4 on the live lab

For each fault in `lab/FAULTS.md` (start with #1, passive interface on R1):

1. Healthy check: `uv run rootvex ask "OSPF neighbor R1 R2 is down"`.
2. The user plants the fault **in the router console** (GNS3: right-click the
   router, Console). Router commands are NOT typed in the Ubuntu shell.
3. Wait about 40 s, then `uv run rootvex ask "<question from the table>" --steps`.
4. Compare the top finding with the "Expected top finding" column.
5. The user undoes the fault in the console.

If the live output differs from the recording, save the real output under
`tests/fixtures/scenarios/<name>/cisco_ios/<DEVICE>/<command_slug>.txt`, then
fix the regex in `src/rootvex/playbooks/analyzers.py` or the playbook YAML.

## Rules (from the project owner, do not relax)

- Lab only. Never touch production or employer networks or configs.
- Rootvex is read-only. It never sends config commands; only the user types
  them, by hand, in the lab console.
- Never read, print or send `.env`, `config/inventory.local.yaml`, `data/`,
  `logs/` or any key or password (`.geminiignore` blocks them). Never ask the
  user to paste a password into chat.
- Ask Rootvex the exact question from the runbook card, not a pasted log line.
- When you report a result, copy Rootvex's own `Likely cause`, `Code checks found`
  and `Proposed fix` lines. Do not add your own diagnosis or fix to them; if you
  think Rootvex is wrong, say so separately.
- Do not invent device output. Only real router output or the recorded files
  count as evidence. (A past review invented OSPF output formats.)
- Show the exact command and the real result before saying something works.
  Run `uv run ruff check .` and `uv run pytest` after any code change.
- Small commits, one per logical step, on `phase4`. Ask the user before any
  push, merge into `main`, delete or rebase.
- Keep answers short and concrete.
