"""Reversible redaction for network configs, logs and prompts before they go to a cloud LLM.

What it does (one Redactor per troubleshooting session, so mappings stay consistent):

* Secrets (passwords, PSKs, SNMP communities, keys, private keys/certs, tokens) are replaced
  with [REDACTED_SECRET]. They are never restored: the model never needs them.
* IPv4 addresses are mapped to fake addresses that keep the network structure, so the model
  can still reason about subnets, routes and policies:
    - private (RFC 1918, CGNAT) -> each original /16 maps to 10.N.0.0/16, host part kept
    - public                    -> each original /24 maps to a /24 inside 198.18.0.0/15
  Netmasks, wildcards, loopback, multicast, link-local and 0.0.0.0 are left alone.
* IPv6 addresses: each original /48 maps to 2001:db8:N::/48, rest kept.
* MAC addresses, e-mail addresses, FQDNs, device hostnames and your own extra terms
  (company or site names) are replaced with consistent placeholders.
* Exact secret values you pass in known_secrets (e.g. lab passwords registered with
  rootvex.log.register_secret) are scrubbed wherever they appear.
* restore() turns the model's answer back into real values, including new addresses the
  model proposes inside a mapped subnet (e.g. a new host in the same /16).

Known limits: usernames are not redacted; prefixes shorter than the mapping block
(e.g. a private /12 or a public /16) keep their length but lose alignment. Always review
what is sent (see the audit log in llm.py) before using hybrid/cloud mode in production.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

SECRET = "[REDACTED_SECRET]"

# --- Secrets ---------------------------------------------------------------------------------

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.S)

_V = r"(?P<v>\"[^\"]*\"|'[^']*'|\S+)"  # a quoted or bare value


def _one_line(p: str) -> str:
    """Stop patterns from jumping to the next line (e.g. 'set authentication md5' + newline)."""
    return p.replace(r"\s+", r"[ \t]+").replace(r"\s*", r"[ \t]*")


_SECRET_PATTERNS = [
    re.compile(_one_line(p), re.I | re.M)
    for p in (
        # FortiGate: set psksecret ENC ..., set password ENC ..., set passwd ..., set key ...
        r"^\s*set\s+(?:psksecret|password|passwd|secret|key|passphrase|auth-pwd|priv-pwd|"
        r"ldap-password|sso-password|server-key|private-key-password|keyword)\s+(?:ENC\s+)?" + _V,
        r"\bENC\s+(?P<v>\S{16,})",
        # Cisco IOS / NX-OS / ASA
        r"^\s*enable\s+(?:secret|password)(?:\s+level\s+\d+)?(?:\s+\d)?\s+(?P<v>\S+)",
        r"^\s*username\s+\S+\b.*?\b(?:secret|password)(?:\s+\d)?\s+(?P<v>\S+)",
        r"\b(?:password|secret)\s+[0-9]\s+(?P<v>\S+)",
        r"^\s*key-string(?:\s+\d)?\s+(?P<v>\S+)",
        r"^\s*key\s+[0-7]\s+(?P<v>\S+)\s*$",
        r"^\s*snmp-server\s+community\s+(?P<v>\S+)",
        r"^\s*crypto\s+isakmp\s+key(?:\s+\d)?\s+(?P<v>\S+)",
        r"^\s*(?:tacacs-server|radius-server)\s+(?:host\s+\S+\s+)?key(?:\s+\d)?\s+(?P<v>\S+)",
        r"^\s*neighbor\s+\S+\s+password(?:\s+\d)?\s+(?P<v>\S+)",
        r"\bmd5\s+(?:\d\s+)?(?P<v>\S+)",
        # Cisco IKEv2 / Palo Alto set-format / Huawei
        r"\bpre-shared-key\s+(?:(?:local|remote|key|cipher|simple)\s+)?(?:\d\s+)?" + _V,
        # Huawei: cipher / irreversible-cipher / simple
        r"\b(?:irreversible-)?cipher\s+(?P<v>\S+)",
        r"\b(?:password|key)\s+simple\s+(?P<v>\S+)",
        # Palo Alto set-format and XML
        r"\bphash\s+(?P<v>\S+)",
        r"<(?P<tag>phash|password|secret|key|private-key|passphrase|auth-password|priv-password)>"
        r"(?P<v>[^<]*)</(?P=tag)>",
        # Generic logs / env / JSON / YAML
        r"[\"']?\b(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|pwd)\b[\"']?\s*[=:]\s*"
        r"(?P<v>\"[^\"]*\"|'[^']*'|[^\s,}]+)",
        r"\bBearer\s+(?P<v>[A-Za-z0-9._~+/=-]{8,})",
    )
]

# FortiGate SNMP community names live in "config system snmp community ... set name X".
_FGT_SNMP_BLOCK = re.compile(r"(config system snmp community.*?^end\b)", re.S | re.M | re.I)
_FGT_SET_NAME = re.compile(r"^([ \t]*set[ \t]+name[ \t]+)(\"[^\"]*\"|\S+)", re.M | re.I)

# --- Names -----------------------------------------------------------------------------------

_HOSTNAME_DIRECTIVES = [
    re.compile(_one_line(p), re.I | re.M)
    for p in (
        r"^\s*set\s+hostname\s+\"?(?P<n>[^\"\s]+)\"?",  # FortiGate
        r"^\s*hostname\s+(?P<n>\S+)",  # Cisco
        r"^\s*sysname\s+(?P<n>\S+)",  # Huawei
        r"<hostname>(?P<n>[^<]+)</hostname>",  # Palo Alto XML
        r"\bdeviceconfig\s+system\s+hostname\s+(?P<n>\S+)",  # Palo Alto set-format
        r"\bdevname=\"?(?P<n>[^\"\s]+)\"?",  # FortiGate logs
    )
]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24}\b")
_FQDN = re.compile(
    r"(?<![\w.@-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,24}(?![\w-])"
)

PUBLIC_DOMAINS = {
    "fortinet.com",
    "fortiguard.com",
    "cisco.com",
    "paloaltonetworks.com",
    "huawei.com",
    "f5.com",
    "microsoft.com",
    "google.com",
    "github.com",
    "ietf.org",
    "rfc-editor.org",
    "example.com",
    "example.net",
    "example.org",
    "example",
    "localhost",
}
_FILE_EXTS = {
    "txt",
    "log",
    "conf",
    "cfg",
    "py",
    "json",
    "yaml",
    "yml",
    "xml",
    "csv",
    "pdf",
    "html",
    "htm",
    "js",
    "ts",
    "md",
    "sh",
    "exe",
    "zip",
    "gz",
    "tgz",
    "tar",
    "bin",
    "out",
    "deb",
    "rpm",
    "img",
    "pcap",
    "pcapng",
    "png",
    "jpg",
    "jpeg",
    "ini",
    "toml",
    "env",
    "pem",
    "crt",
    "key",
}

# --- Addresses -------------------------------------------------------------------------------

_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")
_IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
_MAC = re.compile(
    r"(?<![\w:.-])(?:(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}|"
    r"(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4})(?![\w:.-])"
)

_PRIVATE_V4 = [
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
]
_PUBLIC_POOL = ipaddress.ip_network("198.18.0.0/15")  # 512 x /24 (RFC 2544 benchmark range)


def _is_mask_or_wildcard(value: int) -> bool:
    bits = f"{value:032b}"
    return bits == "1" * bits.count("1") + "0" * bits.count("0") or bits == "0" * bits.count(
        "0"
    ) + "1" * bits.count("1")


@dataclass
class Redactor:
    extra_terms: list[str] = field(default_factory=list)
    known_secrets: set[str] | frozenset[str] = field(default_factory=frozenset)
    redact_macs: bool = True
    redact_fqdns: bool = True

    def __post_init__(self) -> None:
        self._priv16: dict[str, str] = {}  # "172.16" -> "10.0"
        self._pub24: dict[str, str] = {}  # "203.0.113" -> "198.18.0"
        self._v6_48: dict[str, str] = {}  # "2001:0db8:abcd" (exploded) -> "2001:0db8:0000"
        self._names: dict[str, str] = {}  # original -> token (hosts, terms)
        self._fqdns: dict[str, str] = {}
        self._emails: dict[str, str] = {}
        self._macs: dict[str, str] = {}
        self.stats: dict[str, int] = {}

    # ---------------------------------------------------------------- public API
    def redact(self, text: str) -> str:
        text = self._redact_secrets(text)
        text = self._redact_names(text)
        text = _EMAIL.sub(self._email_sub, text)
        if self.redact_fqdns:
            text = _FQDN.sub(self._fqdn_sub, text)
        text = _IPV6.sub(self._v6_sub, text)
        text = _IPV4.sub(self._v4_sub, text)
        if self.redact_macs:
            text = _MAC.sub(self._mac_sub, text)
        return text

    def restore(self, text: str) -> str:
        """Map a model answer (written in placeholder space) back to real values."""
        if self.redact_macs and self._macs:
            rev = {v.lower(): k for k, v in self._macs.items()}
            text = _MAC.sub(lambda m: rev.get(m.group(0).lower(), m.group(0)), text)
        text = _IPV4.sub(self._v4_restore, text)
        text = _IPV6.sub(self._v6_restore, text)
        for mapping in (self._fqdns, self._emails, self._names):
            text = self._reverse(mapping, text)
        return text

    # ---------------------------------------------------------------- secrets
    def _redact_secrets(self, text: str) -> str:
        for value in sorted(self.known_secrets, key=len, reverse=True):
            if value and value in text:
                self._count("secret", text.count(value))
                text = text.replace(value, SECRET)
        text, n = _PEM.subn(SECRET, text)
        self._count("secret", n)

        def snmp_block(m: re.Match) -> str:
            block, k = _FGT_SET_NAME.subn(lambda s: s.group(1) + SECRET, m.group(1))
            self._count("secret", k)
            return block

        text = _FGT_SNMP_BLOCK.sub(snmp_block, text)

        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(self._secret_sub, text)
        return text

    def _secret_sub(self, m: re.Match) -> str:
        if m.group("v") == SECRET or m.group("v").strip("\"'") == SECRET:
            return m.group(0)
        start, end = m.span("v")
        s0 = m.start(0)
        self._count("secret")
        return m.group(0)[: start - s0] + SECRET + m.group(0)[end - s0 :]

    # ---------------------------------------------------------------- names
    def _redact_names(self, text: str) -> str:
        for pattern in _HOSTNAME_DIRECTIVES:
            for m in pattern.finditer(text):
                name = m.group("n").strip("\"'")
                if name and name not in self._names and name != SECRET:
                    self._names[name] = f"HOST-{self._next('host')}"
        for term in self.extra_terms:
            if term and term not in self._names:
                self._names[term] = f"TERM-{self._next('term')}"
        if not self._names:
            return text
        alternation = "|".join(re.escape(k) for k in sorted(self._names, key=len, reverse=True))
        # Case-sensitive so restore() gives back exactly what was there.
        # Add case variants to extra_terms if your logs use them (e.g. "grifols", "GRIFOLS").
        pattern = re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])")

        def sub(m: re.Match) -> str:
            self._count("name")
            return self._names[m.group(0)]

        return pattern.sub(sub, text)

    def _email_sub(self, m: re.Match) -> str:
        value = m.group(0)
        if value not in self._emails:
            self._emails[value] = f"user-{self._next('email')}@example.com"
        self._count("email")
        return self._emails[value]

    def _fqdn_sub(self, m: re.Match) -> str:
        value = m.group(0)
        lower = value.lower()
        tld = lower.rsplit(".", 1)[-1]
        if (
            _MAC.fullmatch(value)
            or tld in _FILE_EXTS
            or lower in PUBLIC_DOMAINS
            or any(lower.endswith("." + d) for d in PUBLIC_DOMAINS)
            or value in self._fqdns.values()
        ):
            return value
        if value not in self._fqdns:
            self._fqdns[value] = f"host-{self._next('fqdn')}.example"
        self._count("fqdn")
        return self._fqdns[value]

    # ---------------------------------------------------------------- IPv4
    def _v4_sub(self, m: re.Match) -> str:
        raw = m.group(0)
        try:
            ip = ipaddress.IPv4Address(raw)
        except ValueError:
            return raw
        value = int(ip)
        if (
            ip.is_loopback
            or ip.is_multicast
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_reserved
            or _is_mask_or_wildcard(value)
        ):
            return raw
        o = raw.split(".")
        if any(ip in net for net in _PRIVATE_V4):
            key = ".".join(o[:2])
            if key not in self._priv16:
                n = len(self._priv16)
                if n > 255:
                    return self._overflow(raw)
                self._priv16[key] = f"10.{n}"
            self._count("ipv4")
            return f"{self._priv16[key]}.{o[2]}.{o[3]}"
        key = ".".join(o[:3])
        if key not in self._pub24:
            n = len(self._pub24)
            if n >= 512:
                return self._overflow(raw)
            base = _PUBLIC_POOL.network_address + n * 256
            self._pub24[key] = ".".join(str(base).split(".")[:3])
        self._count("ipv4")
        return f"{self._pub24[key]}.{o[3]}"

    def _overflow(self, raw: str) -> str:
        token = f"IPV4-{self._next('ipv4_overflow')}"
        self._names[raw] = token
        return token

    def _v4_restore(self, m: re.Match) -> str:
        o = m.group(0).split(".")
        rev16 = {v: k for k, v in self._priv16.items()}
        rev24 = {v: k for k, v in self._pub24.items()}
        if ".".join(o[:2]) in rev16:
            return f"{rev16['.'.join(o[:2])]}.{o[2]}.{o[3]}"
        if ".".join(o[:3]) in rev24:
            return f"{rev24['.'.join(o[:3])]}.{o[3]}"
        return m.group(0)

    # ---------------------------------------------------------------- IPv6
    def _v6_sub(self, m: re.Match) -> str:
        raw = m.group(0)
        try:
            ip = ipaddress.IPv6Address(raw)
        except ValueError:
            return raw
        if ip.is_loopback or ip.is_multicast or ip.is_link_local or ip.is_unspecified:
            return raw
        groups = ip.exploded.split(":")
        key = ":".join(groups[:3])
        if key not in self._v6_48:
            self._v6_48[key] = f"2001:0db8:{len(self._v6_48):04x}"
        self._count("ipv6")
        return str(ipaddress.IPv6Address(":".join([self._v6_48[key]] + groups[3:])))

    def _v6_restore(self, m: re.Match) -> str:
        raw = m.group(0)
        try:
            ip = ipaddress.IPv6Address(raw)
        except ValueError:
            return raw
        groups = ip.exploded.split(":")
        rev = {v: k for k, v in self._v6_48.items()}
        key = ":".join(groups[:3])
        if key not in rev:
            return raw
        return str(ipaddress.IPv6Address(":".join([rev[key]] + groups[3:])))

    # ---------------------------------------------------------------- MAC
    def _mac_sub(self, m: re.Match) -> str:
        raw = m.group(0)
        if raw.lower() not in self._macs:
            n = self._next("mac")
            octets = [
                "02",
                "00",
                "00",
                f"{(n >> 16) & 255:02x}",
                f"{(n >> 8) & 255:02x}",
                f"{n & 255:02x}",
            ]
            if "." in raw:
                h = "".join(octets)
                fake = f"{h[0:4]}.{h[4:8]}.{h[8:12]}"
            else:
                fake = ("-" if "-" in raw else ":").join(octets)
            self._macs[raw.lower()] = fake
        self._count("mac")
        return self._macs[raw.lower()]

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _reverse(mapping: dict[str, str], text: str) -> str:
        if not mapping:
            return text
        rev = {v.lower(): k for k, v in mapping.items()}
        alternation = "|".join(re.escape(v) for v in sorted(rev, key=len, reverse=True))
        return re.sub(
            rf"(?<![\w-])(?:{alternation})(?![\w-])",
            lambda m: rev[m.group(0).lower()],
            text,
            flags=re.I,
        )

    def _next(self, kind: str) -> int:
        key = f"_n_{kind}"
        n = getattr(self, key, 0) + 1
        setattr(self, key, n)
        return n

    def _count(self, kind: str, n: int = 1) -> None:
        if n:
            self.stats[kind] = self.stats.get(kind, 0) + n
