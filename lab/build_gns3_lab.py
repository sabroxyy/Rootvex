"""Build the Rootvex GNS3 lab (Stage B) through the GNS3 server REST API.

Reads lab/topology.yaml, creates the templates it needs, the project, nodes and
links, and loads every device's startup config:

  * Cisco 7200 (Dynamips): config is written as the node's startup-config,
    which GNS3 loads into NVRAM when the router starts. With --start, an Idle-PC
    value is computed so the routers don't each burn a full CPU core.
  * IOSv (QEMU): config goes on a config disk as ios_config.txt; needs `mtools`.
  * Docker PCs / web server: /etc/network/interfaces is written into the node.

Router configs use logical ports ({{IF_MGMT}}, {{IF1}}, ...) that the router
platform maps to real interfaces. Secrets ({{LAB_DEVICE_PASSWORD}}, {{LAB_VPN_PSK}})
come from .env and are only written into the GNS3 project on this machine.

Usage (from the repo root, GNS3 GUI open so its server runs):
    uv run python lab/build_gns3_lab.py                 # build, leave nodes stopped
    uv run python lab/build_gns3_lab.py --start         # build, start, set Idle-PC
    uv run python lab/build_gns3_lab.py --rebuild       # delete the old project first
    uv run python lab/build_gns3_lab.py --idlepc        # set Idle-PC on the running lab
    uv run python lab/build_gns3_lab.py --ssh           # create RSA keys so SSH works
    uv run python lab/build_gns3_lab.py --remove-old    # delete pre-rename GNS3 objects
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import yaml

LAB_DIR = Path(__file__).resolve().parent
REPO = LAB_DIR.parent
SECRETS = ("LAB_DEVICE_PASSWORD", "LAB_VPN_PSK")


class GNS3:
    def __init__(self, url: str, user: str | None, password: str | None):
        self.url = url.rstrip("/") + "/v2"
        self.auth = None
        if user:
            token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
            self.auth = f"Basic {token}"

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, timeout=120):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/octet-stream" if raw else "application/json")
        if self.auth:
            req.add_header("Authorization", self.auth)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode() or "null"
                return json.loads(text) if text.strip()[:1] in "{[n" else text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except (ValueError, AttributeError):
                pass
            raise SystemExit(f"GNS3 API {method} {path} failed ({exc.code}): {detail}") from None
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"Cannot reach the GNS3 server at {self.url}: {exc.reason}. "
                "Start GNS3 (or `gns3server`) and check --server."
            ) from None


def gui_server_login() -> tuple[str | None, str | None]:
    """The GNS3 GUI starts its local server with a random password; read it from its config."""
    import configparser

    for path in sorted(Path.home().glob(".config/GNS3/*/gns3_server.conf"), reverse=True):
        cfg = configparser.ConfigParser()
        cfg.read(path)
        if cfg.getboolean("Server", "auth", fallback=False):
            return cfg.get("Server", "user", fallback=None), cfg.get(
                "Server", "password", fallback=None
            )
    return None, None


def load_env() -> dict[str, str]:
    """Values from .env, overridden by real environment variables."""
    env: dict[str, str] = {}
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                env[m.group(1)] = m.group(2).strip().strip("'\"")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("LAB_", "GNS3_"))})
    return env


def render(
    text: str,
    env: dict[str, str],
    ports: dict[str, str] | None = None,
    platform_vars: dict[str, str] | None = None,
) -> str:
    """Fill {{NAME}}: interface names and syntax from the router platform, secrets from .env."""
    port_vars = {
        f"IF_{k.upper()}" if k == "mgmt" else k.upper(): v for k, v in (ports or {}).items()
    }
    port_vars.update(platform_vars or {})

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in port_vars:
            return port_vars[key]
        if not env.get(key) or env[key] in ("change-me", "change-me-too"):
            raise SystemExit(f"Set {key} in .env (see .env.example) before building the lab.")
        return env[key]

    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def ensure_templates(api: GNS3, templates: dict, env: dict) -> dict[str, dict]:
    existing = {t["name"]: t for t in api.call("GET", "/templates")}
    out = {}
    for key, t in templates.items():
        if t["name"] in existing:
            out[key] = existing[t["name"]]
            print(f"  template {t['name']!r} exists")
            continue
        if t["type"] == "dynamips":
            body = {
                "name": t["name"],
                "template_type": "dynamips",
                "compute_id": "local",
                "category": "router",
                "symbol": ":/symbols/router.svg",
                "platform": "c7200",
                "image": env.get(t["image_env"], t["image_default"]),
                "ram": t["ram"],
                "nvram": 512,
                "idlepc": env.get("LAB_C7200_IDLEPC", ""),
                "console_type": "telnet",
                **t["slots"],
            }
        elif t["type"] == "qemu":
            image = env.get(t["image_env"], t["image_default"])
            body = {
                "name": t["name"],
                "template_type": "qemu",
                "compute_id": "local",
                "category": "switch" if t["adapters"] > 4 else "router",
                "symbol": ":/symbols/multilayer_switch.svg"
                if t["adapters"] > 4
                else ":/symbols/router.svg",
                "platform": "x86_64",
                "ram": t["ram"],
                "cpus": 1,
                "adapters": t["adapters"],
                "adapter_type": "e1000",
                "port_name_format": t["port_name_format"],
                "port_segment_size": t.get("port_segment_size", 0),
                "hda_disk_image": image,
                "hda_disk_interface": "virtio",
                "create_config_disk": True,  # startup config disk (drive d / hdd)
                "console_type": "telnet",
                "on_close": "power_off",
                "options": "-nographic",
            }
        else:
            body = {
                "name": t["name"],
                "template_type": "docker",
                "compute_id": "local",
                "category": "guest",
                "image": t["image"],
                "adapters": 1,
                "console_type": "none" if "nginx" in t["image"] else "telnet",
            }
        out[key] = api.call("POST", "/templates", body)
        extra = f" (image {body.get('hda_disk_image') or body.get('image')})"
        print(f"  template {t['name']!r} created{extra}")
    return out


def short_ifname(name: str) -> str:
    """'GigabitEthernet0/1', 'Gi0/1' and 'g0/1' all become 'gi0/1'."""
    n = name.strip().lower()
    for long, short in (("gigabitethernet", "gi"), ("fastethernet", "fa"), ("ethernet", "e")):
        if n.startswith(long):
            return short + n[len(long) :]
    return (
        {"g": "gi", "f": "fa"}.get(n[:1], n[:1]) + n[1:]
        if n[:1] in "gf" and n[1:2].isdigit()
        else n
    )


def find_port(node: dict, spec: str) -> tuple[int, int]:
    """Map 'Gi0/1', 'eth0', 'tap-nw' or a bare port number to (adapter, port)."""
    want = short_ifname(spec)
    for p in node["ports"]:
        if want in (short_ifname(p["name"]), short_ifname(p.get("short_name", ""))):
            return p["adapter_number"], p["port_number"]
    if spec.isdigit():
        for p in node["ports"]:
            if p["port_number"] == int(spec):
                return p["adapter_number"], p["port_number"]
    names = ", ".join(p["name"] for p in node["ports"])
    raise SystemExit(f"{node['name']}: no port {spec!r} (has: {names})")


def ios_config_zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ios_config.txt", text)
    return buf.getvalue()


def pick_proposal(proposals: list[str]) -> str | None:
    """Dynamips proposals look like '0x6060aed4 [52]'. GNS3 prefers counts of 50-60."""
    scored = []
    for p in proposals:
        m = re.match(r"(0x[0-9a-fA-F]+)\s*\[(\d+)\]", p.strip())
        if m:
            scored.append((abs(int(m.group(2)) - 55), m.group(1)))
    return min(scored)[1] if scored else None


def set_idlepc(api: GNS3, pid: str, nodes: dict, templates: dict, boot_wait: int = 0) -> None:
    """Dynamips routers without an Idle-PC value use 100% of a CPU core each.

    The value can only be measured once IOS has finished booting, so wait first,
    retry, and fall back to the proposal list if auto-detection finds nothing.
    """
    routers = [n for n in nodes.values() if n["node_type"] == "dynamips"]
    if not routers or routers[0]["properties"].get("idlepc"):
        return
    if boot_wait:
        print(f"Waiting {boot_wait}s for IOS to boot before measuring Idle-PC...")
        time.sleep(boot_wait)
    base = f"/projects/{pid}/nodes/{routers[0]['node_id']}/dynamips"
    idle = None
    for attempt in range(1, 4):
        print(
            f"Computing Idle-PC on {routers[0]['name']} (attempt {attempt}/3, up to 2 minutes)..."
        )
        try:
            idle = api.call("GET", f"{base}/auto_idlepc", timeout=300)["idlepc"]
            break
        except SystemExit as exc:
            print(f"  not yet: {str(exc).split(': ', 1)[-1]}")
            time.sleep(30)
    if not idle:
        try:
            proposals = api.call("GET", f"{base}/idlepc_proposals", timeout=300)
            idle = pick_proposal(proposals)
            print(f"  proposals: {proposals}")
        except SystemExit as exc:
            print(f"  no proposals: {exc}")
    if not idle:
        print("  Could not find an Idle-PC value. Routers still work, each using one CPU core.")
        print("  Try later:  uv run python lab/build_gns3_lab.py --idlepc")
        print("  or in the GNS3 GUI: right-click R1 > Idle-PC, pick a value marked with *.")
        return
    for r in routers:
        api.call("PUT", f"/projects/{pid}/nodes/{r['node_id']}", {"properties": {"idlepc": idle}})
    for t in templates.values():
        if t.get("template_type") == "dynamips":
            api.call("PUT", f"/templates/{t['template_id']}", {"idlepc": idle})
    print(f"  Idle-PC {idle} set on {len(routers)} routers and saved in the template.")
    print(f"  Tip: add LAB_C7200_IDLEPC={idle} to .env so future builds skip this step.")


def enable_ssh(nodes: dict, env: dict) -> None:
    """IOS 12.4 does not create its RSA key from the startup config; do it on the console."""
    from ios_console import ConsoleError, ensure_ssh

    password = env.get("LAB_DEVICE_PASSWORD", "")
    routers = [n for n in nodes.values() if n["node_type"] in ("dynamips", "qemu")]
    print(f"Enabling SSH on {len(routers)} device(s) via their consoles (waits for boot)...")
    for n in routers:
        try:
            result = ensure_ssh(n["console"], n["name"], password)
            print(f"  {n['name']:<5} {result}")
        except (ConsoleError, OSError) as exc:
            print(f"  {n['name']:<5} FAILED: {exc}")
            print(
                "        On its console: conf t / crypto key generate rsa modulus 2048 / end / wr"
            )


def remove_old(api: GNS3, old: dict) -> None:
    """Delete GNS3 objects left from before the rename (exact names from topology.yaml).

    Keeps going after an error, so one stuck project doesn't block the rest.
    """
    projects = set(old.get("projects", []))
    prefix = old.get("template_prefix")
    removed, failed = 0, []
    for p in api.call("GET", "/projects"):
        if p["name"] not in projects:
            continue
        try:
            if p["status"] != "opened":
                api.call("POST", f"/projects/{p['project_id']}/open", {})
            api.call("POST", f"/projects/{p['project_id']}/nodes/stop", {}, timeout=300)
            api.call("DELETE", f"/projects/{p['project_id']}")
            print(f"  deleted project {p['name']!r} (and its files)")
            removed += 1
        except SystemExit as exc:
            failed.append(p)
            print(f"  could not delete project {p['name']!r}: {str(exc).split(': ', 1)[-1][:200]}")
    if prefix:
        for t in api.call("GET", "/templates"):
            if t["name"].startswith(prefix) and not t.get("builtin"):
                try:
                    api.call("DELETE", f"/templates/{t['template_id']}")
                    print(f"  deleted template {t['name']!r}")
                    removed += 1
                except SystemExit as exc:
                    print(f"  could not delete template {t['name']!r}: {exc}")
    print(f"Removed {removed} old GNS3 object(s)." if removed else "Nothing old removed.")
    for p in failed:
        folder = p.get("path") or f"~/GNS3/projects/{p['project_id']}"
        print(
            f"\nProject {p['name']!r} has files GNS3 cannot delete (usually Docker files owned "
            f"by root). Fix, then run --remove-old again:\n"
            f"  sudo rm -rf {folder}/project-files/docker"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("GNS3_SERVER", "http://127.0.0.1:3080"))
    ap.add_argument("--user", default=os.environ.get("GNS3_USER"))
    ap.add_argument("--password", default=os.environ.get("GNS3_PASSWORD"))
    ap.add_argument("--topology", type=Path, default=LAB_DIR / "topology.yaml")
    ap.add_argument("--rebuild", action="store_true", help="delete an existing project first")
    ap.add_argument("--start", action="store_true", help="start all nodes after building")
    ap.add_argument(
        "--idlepc", action="store_true", help="only set Idle-PC on the existing, running lab"
    )
    ap.add_argument(
        "--ssh", action="store_true", help="only enable SSH (RSA keys) on the running lab"
    )
    ap.add_argument(
        "--remove-old",
        action="store_true",
        help="only delete the pre-rename GNS3 project(s) and templates listed in topology.yaml",
    )
    args = ap.parse_args()

    topo = yaml.safe_load(args.topology.read_text())
    env = load_env()
    user, password = args.user, args.password
    if not user:
        user, password = gui_server_login()
    api = GNS3(args.server, user, password)

    version = api.call("GET", "/version")["version"]
    print(f"GNS3 server {version} at {args.server}")

    if args.remove_old:
        remove_old(api, topo.get("old_names", {}))
        return

    if args.ssh:
        proj = next((p for p in api.call("GET", "/projects") if p["name"] == topo["project"]), None)
        if not proj:
            raise SystemExit(f"Project {topo['project']!r} not found. Build it first.")
        if proj["status"] != "opened":
            api.call("POST", f"/projects/{proj['project_id']}/open", {})
        nodes = {n["name"]: n for n in api.call("GET", f"/projects/{proj['project_id']}/nodes")}
        enable_ssh(nodes, env)
        return

    if args.idlepc:
        proj = next((p for p in api.call("GET", "/projects") if p["name"] == topo["project"]), None)
        if not proj:
            raise SystemExit(f"Project {topo['project']!r} not found. Build it first.")
        if proj["status"] != "opened":
            api.call("POST", f"/projects/{proj['project_id']}/open", {})
        nodes = {n["name"]: n for n in api.call("GET", f"/projects/{proj['project_id']}/nodes")}
        for n in nodes.values():
            if n["node_type"] == "dynamips":
                n["properties"]["idlepc"] = ""  # measure again even if one is set
        templates = {t["name"]: t for t in api.call("GET", "/templates")}
        set_idlepc(api, proj["project_id"], nodes, templates)
        return

    platform = env.get("LAB_ROUTER_PLATFORM", topo["router_platform"])
    if platform not in topo["templates"]:
        raise SystemExit(f"Unknown router platform {platform!r} (see topology.yaml templates)")
    for n in topo["nodes"].values():
        if n.get("template") == "router":
            n["template"] = platform
    # SW1: a managed IOSvL2 switch when that image exists, else GNS3's built-in switch.
    switch = env.get("LAB_SWITCH_PLATFORM", topo.get("switch_platform", "builtin"))
    for n in topo["nodes"].values():
        if n.get("template") == "switch":
            if switch == "builtin":
                del n["template"]
                n.pop("config", None)
                n["builtin"] = "ethernet_switch"
                n["ports"] = topo["builtin_switch_ports"]
            else:
                n["template"] = switch
    print(f"Switch platform: {switch}")
    used = {n["template"] for n in topo["nodes"].values() if "template" in n}
    topo["templates"] = {k: v for k, v in topo["templates"].items() if k in used}
    print(f"Router platform: {platform}")

    # Render every config first, so a missing secret stops us before anything is created.
    rendered: dict[str, str] = {}
    for name, n in topo["nodes"].items():
        src = n.get("config") or n.get("network")
        if src:
            tpl = topo["templates"].get(n.get("template"), {})
            text = (LAB_DIR / src).read_text()
            rendered[name] = render(text, env, tpl.get("ports"), tpl.get("vars"))

    # Cisco images are licensed, so they are never downloaded for you.
    for t in topo["templates"].values():
        if t["type"] not in ("qemu", "dynamips"):
            continue
        image = env.get(t["image_env"], t["image_default"])
        have = {i["filename"] for i in api.call("GET", f"/computes/local/{t['type']}/images")}
        if image not in have:
            folder = "IOS" if t["type"] == "dynamips" else "QEMU"
            found = sorted(f for f in have if not f.startswith(("empty", "OVMF", "config")))
            raise SystemExit(
                f"{t['type']} image {image!r} is not on the GNS3 server.\n"
                f"Copy it to ~/GNS3/images/{folder}/ or set {t['image_env']}= in .env "
                f"to the file name you have. Images found: {found or 'none'}"
            )

    print("Templates:")
    templates = ensure_templates(api, topo["templates"], env)

    project_name = topo["project"]
    for p in api.call("GET", "/projects"):
        if p["name"] == project_name:
            if not args.rebuild:
                raise SystemExit(f"Project {project_name!r} already exists. Use --rebuild.")
            if p["status"] != "opened":
                api.call("POST", f"/projects/{p['project_id']}/open", {})
            api.call("DELETE", f"/projects/{p['project_id']}")
            print(f"Deleted old project {project_name!r}")
    project = api.call("POST", "/projects", {"name": project_name, "auto_close": False})
    pid = project["project_id"]
    print(f"Project {project_name!r} created ({pid})")

    nodes: dict[str, dict] = {}
    for name, n in topo["nodes"].items():
        if "builtin" in n:
            body = {
                "name": name,
                "node_type": n["builtin"],
                "compute_id": "local",
                "x": n["x"],
                "y": n["y"],
            }
            if n["builtin"] == "cloud":
                body["properties"] = {
                    "ports_mapping": [
                        {"interface": n["tap"], "name": n["tap"], "port_number": 0, "type": "tap"}
                    ]
                }
            node = api.call("POST", f"/projects/{pid}/nodes", body)
        else:
            tid = templates[n["template"]]["template_id"]
            node = api.call(
                "POST",
                f"/projects/{pid}/templates/{tid}",
                {"x": n["x"], "y": n["y"], "compute_id": "local"},
            )
            node = api.call("PUT", f"/projects/{pid}/nodes/{node['node_id']}", {"name": name})
        nodes[name] = node

        if name in rendered and node["node_type"] == "dynamips":
            # Loaded into the router's NVRAM by GNS3 every time the router starts.
            cfg = f"configs/i{node['properties']['dynamips_id']}_startup-config.cfg"
            api.call(
                "POST",
                f"/projects/{pid}/nodes/{node['node_id']}/files/{cfg}",
                raw=rendered[name].encode(),
            )
        elif name in rendered:
            if node["node_type"] == "qemu":
                path, payload = "config.zip", ios_config_zip(rendered[name])
            else:
                path, payload = "etc/network/interfaces", rendered[name].encode()
            api.call("POST", f"/projects/{pid}/nodes/{node['node_id']}/files/{path}", raw=payload)
        has_config = "yes" if name in rendered else "-"
        print(f"  node {name:<10} {node['node_type']:<16} config: {has_config}")

    def port_map(name: str) -> dict:
        n = topo["nodes"][name]
        return n.get("ports") or topo["templates"].get(n.get("template"), {}).get("ports", {})

    def has_port(name: str, port: str) -> bool:
        ports = port_map(name)
        logical = bool(re.fullmatch(r"mgmt|if\d|p\d", port))
        return not (ports and logical and port not in ports)

    print("Links:")
    for a, b in topo["links"]:
        # Skip links to a logical port the chosen platform doesn't have
        # (the built-in SW1 has no "mgmt" port).
        missing = [e for e in (a, b) if not has_port(*e.split(maxsplit=1))]
        if missing:
            print(f"  {a:<16} <-> {b}   (skipped: {missing[0]} not on this platform)")
            continue
        ends = []
        for end in (a, b):
            name, port = end.split(maxsplit=1)
            port = port_map(name).get(port, port)  # logical -> real interface name
            adapter, port_no = find_port(nodes[name], port)
            ends.append(
                {
                    "node_id": nodes[name]["node_id"],
                    "adapter_number": adapter,
                    "port_number": port_no,
                }
            )
        api.call("POST", f"/projects/{pid}/links", {"nodes": ends})
        print(f"  {a:<16} <-> {b}")

    if args.start:
        print("Starting all nodes...")
        api.call("POST", f"/projects/{pid}/nodes/start", {}, timeout=300)
        set_idlepc(api, pid, nodes, templates, boot_wait=90)
        enable_ssh(nodes, env)

    print(f"\nDone. Open project {project_name!r} in the GNS3 GUI.")
    print("Then from the host:  ssh rootvex@10.99.0.11   (give the routers 1-3 minutes)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
