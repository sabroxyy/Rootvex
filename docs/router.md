# Optional cloud models (rootvex.router)

Rootvex is local-first. `router.mode: local` (the default) keeps every step on the `llm:` model,
and nothing leaves the network. The router adds two opt-in modes that use Gemini and Claude
through their OpenAI-compatible APIs, the same client `rootvex.llm` uses for Ollama.

| Mode | analyze (reads device output) | reason (root cause + commands) | review | Leaves the network |
|---|---|---|---|---|
| `local` | local | local | local | nothing |
| `hybrid` | local | Claude | Gemini | the redacted summary only |
| `cloud` | Gemini | Claude | Gemini | redacted device output |

## Turn it on
1. Put the keys in `.env` (never committed): `GEMINI_API_KEY=...`, `ANTHROPIC_API_KEY=...`
2. Set `router.mode: hybrid` in `config/settings.yaml` (or `ROOTVEX_ROUTER__MODE=hybrid`).
3. Check the model names in `config/settings.yaml` against each provider's docs.

If a key is missing, Rootvex refuses to start the session and names the variable.

```python
from rootvex.config import load_settings
from rootvex.router import Session

result = Session(load_settings(), user="sabry").troubleshoot(question, wrapped_device_output)
result["status"]  # complete | ready_for_approval | needs_engineer_review
```

## Safety
- **Redaction** (`rootvex/redact.py`) runs on everything sent to a cloud model:
  - **Removed, never restored:** passwords, PSKs, SNMP communities, keys, certificates, and any
    secret registered with `log.register_secret` (lab passwords, API keys).
  - **Swapped for placeholders that keep subnet structure:** IPs, MACs, hostnames, domains,
    emails and `extra_terms`. The answer is mapped back, so the engineer sees real values.
- **Two-model check:** if the reviewer disagrees or an answer isn't valid JSON, the status is
  `needs_engineer_review`.
- **Deny-list:** reboot, erase, factory reset, reload and disabling an interface always force
  `needs_engineer_review`.
- **Audit:** `logs/llm_audit.jsonl` records every call. For cloud calls it keeps the exact
  redacted prompt that left the network.
- **Advisory only:** the router never touches a device. Changes still go through the approval
  queue.

## Limits
Usernames are not redacted. Private prefixes shorter than /16 and public prefixes shorter than
/24 keep their length but lose alignment. Before using hybrid mode on real configs, read a few
`sent_prompt` entries in the audit log.
