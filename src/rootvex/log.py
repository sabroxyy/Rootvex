"""Audit logging: JSON lines, one file per day, every run tagged with a run_id.

Secrets are redacted before anything is written (hard rule 5 and 6).
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog

# Config lines that carry secrets in Cisco/Fortinet/Palo Alto output.
_SECRET_PATTERNS = [
    re.compile(
        r"(?P<key>\b(?:password|secret|enable secret|enable password|key-string|"
        r"pre-shared-key|psk|set psksecret|set password|community|snmp-server community|"
        r"key \d|authentication-key|md5)\b\s+(?:\d\s+)?)(?P<val>\S+)",
        re.IGNORECASE,
    ),
]
_REDACTED = "***REDACTED***"

# Exact secret values to scrub (e.g. lab passwords loaded from env vars).
_known_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret value so it is scrubbed from every log line."""
    if value and len(value) >= 4:
        _known_secrets.add(value)


def redact_text(text: str) -> str:
    for secret in _known_secrets:
        text = text.replace(secret, _REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group("key") + _REDACTED, text)
    return text


def _redact(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def _redact_processor(_logger, _method, event_dict):
    return _redact(event_dict)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def setup_logging(log_dir: Path, level: str = "INFO", console: bool = False) -> Path:
    """Send structlog JSON lines to logs/rootvex-YYYYMMDD.jsonl. Returns the file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"rootvex-{datetime.now(UTC):%Y%m%d}.jsonl"

    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if console or os.environ.get("ROOTVEX_LOG_CONSOLE"):
        handlers.append(logging.StreamHandler(sys.stderr))

    root = logging.getLogger("rootvex")
    root.handlers.clear()
    for h in handlers:
        h.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(h)
    root.setLevel(level.upper())
    root.propagate = False

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # adds run_id
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return log_file


def get_logger(name: str = "rootvex"):
    return structlog.get_logger(name)
