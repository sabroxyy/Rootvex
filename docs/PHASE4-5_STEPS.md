# Phase 4 and Phase 5 — status and step-by-step work plan

Status as of 27 November 2026. Branch: `phase4` (not merged into `main`).
Rules for everyone working on this (Claude Code, Gemini CLI, you): see `GEMINI.md`
— lab only, read-only, no secrets in chat, no invented output, run
`uv run ruff check .` and `uv run pytest` after every change, ask before push/merge.

---

## Phase 4 — Troubleshooting playbooks

### Status

| Part | Status |
|---|---|
| Quote-checked evidence (every finding quotes a real output line) | ✅ Done |
| Playbook engine + 5 playbooks (OSPF, interface, BGP, IPsec, website) | ✅ Done |
| Code analyzers (compare both ends of a link, counters, logs) | ✅ Done |
| Playbook runs inside the agent graph, before the model | ✅ Done |
| Code finding with proof overrules a disagreeing model ("Model's view") | ✅ Done |
| Model re-asking a playbook command reuses the output | ✅ Done |
| 11 recorded faults + 1 healthy case pass (`--scenario`) | ✅ Done — 89 tests pass |
| Recorded run on the workstation (`--scenario ospf_passive`) | ✅ Correct answer, confidence high |
| **Live-lab proof of the 11 faults** | 🟡 **6/11** — #1, #2, #4, #5, #6, #7 ✅; #3 to confirm; next #8–#11 |
| Merge `phase4` into `main` | ⬜ After live proof |

### Steps to finish Phase 4

**Step 1 — Get the code**
```bash
cd ~/projects/rootvex
git fetch origin && git switch phase4 && git pull
uv sync
uv run pytest -q
```
Expect: all tests pass.

**Step 2 — Bring the lab up**
1. Start GNS3, open project `rootvex-lab`, press ▶ (start all). Wait ~2 minutes.
2. Management link up:
   ```bash
   nmcli connection up tap-nw
   ip -br addr show tap-nw          # must show 10.99.0.1/24 and UP
   ping -c 2 10.99.0.11             # R1
   ping -c 2 10.99.0.12             # R2
   ```
3. If ping works but Rootvex still times out: `ssh rootvex@10.99.0.11`
   (needs the `~/.ssh/config` entry for 10.99.0.* from `lab/README.md`).
   If SSH is refused: `uv run python lab/build_gns3_lab.py --ssh`.

**Step 3 — Health check**
```bash
uv run rootvex ask "OSPF neighbor R1 R2 is down"
```
Expect: no cause from the code checks; R1–R2 neighbors FULL.

**Step 4 — Run each fault from `lab/FAULTS.md`** (repeat for #1 … #11)

| Where | What |
|---|---|
| GNS3 → right-click router → **Console** (prompt `R1#`) | Plant the fault (column "Plant") |
| Wait | ~40 s (OSPF dead timer); BGP up to 3 min; IPsec: `clear crypto session` |
| Ubuntu terminal | `uv run rootvex ask "<Ask column>" --steps` |
| Check | Top line of "Likely cause" matches "Expected top finding"; evidence `quote` lines are real |
| Router console | Undo the fault (column "Undo") |

Router commands are typed **only** in the router console. In the Ubuntu
terminal they fail with "command not found".

**Step 5 — Record the result of each fault** in `docs/SETUP_LOG.md`
(table: fault #, expected, got, pass/fail).

**Step 6 — If live output differs from the recording**
1. Save the real output (from `--steps`) to
   `tests/fixtures/scenarios/<scenario>/cisco_ios/<DEVICE>/<command_slug>.txt`
   (slug = command in lower case, spaces → `_`, e.g. `show_ip_ospf_interface.txt`).
2. Fix the regex in `src/rootvex/playbooks/analyzers.py` or the YAML in
   `config/playbooks/`.
3. `uv run ruff check . && uv run pytest` → commit on `phase4`.

**Step 7 — Close Phase 4**
1. Update `docs/MANUAL.md` Phase 4 "Done when" line (Live: ✅ n/11) and Part 0.
2. `uv run scripts/build_pdfs.py`
3. Commit, push `phase4`, review on GitHub, merge into `main` (you decide).

**Done when:** each playbook finds its planted fault in the live lab and cites
the proof line.

---

## Phase 5 — Knowledge base (RAG)

### Goal

Rootvex answers with your own notes, runbooks and allowed vendor docs, and cites
**document + section**. If the knowledge base has nothing relevant, it says
"no source found" instead of inventing.

### Design (agreed in `docs/DESIGN.md` §3.5)

| Item | Choice |
|---|---|
| Sources | `knowledge/` folder: Markdown/text notes, runbooks; PDFs allowed locally, raw vendor PDFs git-ignored |
| Chunking | Heading-aware, ~800 tokens, keep `source` + `section` |
| Embeddings | `bge-m3` via Ollama (local, multilingual incl. Arabic) |
| Store | LanceDB (embedded, file-based, no server) in `data/kb/` |
| Safety | Retrieved text is untrusted (wrapped like device output); never sent to cloud models |

### Status

| Part | Status |
|---|---|
| Design | ✅ Done (DESIGN §3.5) |
| Everything else | ⬜ Not started |

### Steps to build Phase 5

**Step 1 — Prepare the workstation**
```bash
ollama pull bge-m3
ollama list                      # bge-m3 present (~1.2 GB)
```

**Step 2 — Dependencies** (new branch from `main` after Phase 4 is merged)
```bash
git switch main && git pull && git switch -c phase5
uv add lancedb pypdf
```

**Step 3 — Settings** — add to `config/settings.yaml` and `config.py`:
```yaml
kb:
  dir: knowledge              # source documents
  db_dir: data/kb             # LanceDB files (git-ignored)
  embed_model: bge-m3
  chunk_tokens: 800
  top_k: 4
  min_score: 0.45             # below this = "no source found"
```

**Step 4 — Ingest** — `src/rootvex/kb/ingest.py`
1. Walk `knowledge/` (`.md`, `.txt`, `.pdf`).
2. Split by headings (`#`, `##`; PDF: per page), then into ~800-token chunks.
3. Each chunk: `text`, `source` (relative path), `section` (heading or page),
   `sha256` of the file (to skip unchanged files on re-ingest).
4. Embed with Ollama `bge-m3`, write to LanceDB table `chunks`.
5. CLI: `uv run rootvex kb ingest` → prints files, chunks, time.

**Step 5 — Search** — `src/rootvex/kb/search.py`
1. `search(question, top_k) -> list[Hit(source, section, text, score)]`.
2. Drop hits below `min_score`.
3. CLI: `uv run rootvex kb search "ospf passive interface"`.

**Step 6 — Agent tool** — `search_knowledge(query)` in `src/rootvex/tools/`
1. Returns hits wrapped in `<untrusted_knowledge source=".." section="..">`.
2. Counts as a step, logged in the audit log.
3. No hits → returns `NO SOURCE FOUND` (the model must say so).

**Step 7 — Report citations**
1. `Report.sources` becomes `[{source, section, quote}]`.
2. Code checks each cited source/section was really returned by
   `search_knowledge` in this run (same idea as the quote check);
   otherwise `[UNVERIFIED]`.
3. Web page and `to_text()` show sources.

**Step 8 — Starter knowledge**
1. `knowledge/README.md` (what goes here, what must not: no employer configs, no secrets).
2. One runbook per playbook, e.g. `knowledge/runbooks/ospf-neighbor-down.md`,
   written from `lab/FAULTS.md` and the playbook YAML.

**Step 9 — Tests** (no Ollama needed: fake embedder)
1. Chunking keeps headings and `source`/`section`.
2. Re-ingest skips unchanged files.
3. Search returns the right runbook for "ospf passive".
4. Question with no match → `NO SOURCE FOUND`, report says no source.
5. Cited source that was not retrieved → UNVERIFIED.
6. Prompt-injection text inside a document is wrapped, not obeyed.

**Step 10 — Prove it on the workstation**
```bash
uv run rootvex kb ingest
uv run rootvex kb search "OSPF neighbor stuck in EXSTART"
uv run rootvex ask "OSPF neighbor R1 R2 is down" --scenario ospf_mtu
```
Expect: report cites `knowledge/runbooks/ospf-neighbor-down.md` + section.
Then ask something not in the knowledge base → "no source found".

**Step 11 — Docs and close**
Update MANUAL (Phase 5 section, commands, Part 0), DESIGN §11, SETUP_LOG,
README; rebuild PDFs; commit; push `phase5`; merge after review.

**Done when:** answers cite document + section that really were retrieved, and
an unknown topic gives "no source found", not invention.

---

## Order of work

1. Phase 4 Steps 1–3 (lab reachable) — ✅ done 27 Nov
2. Phase 4 Step 4 for faults #1–#11
3. Phase 4 Steps 5–7, merge
4. Phase 5 Steps 1–11
