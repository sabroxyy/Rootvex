"""Build the Rootvex GNS3 lab (Stage B) through the GNS3 server REST API.

Reads lab/topology.yaml, creates the templates if missing, creates the project,
nodes and links, and loads every device's startup config:

  * IOSv / IOSvL2: config goes on a config disk as ios_config.txt (GNS3
    "create_config_disk"); IOSv reads it on first boot. Needs `mtools` on the host.
  * Docker PCs / web server: /etc/network/interfaces is written into the node.

Secrets ({{LAB_DEVICE_PASSWORD}}, {{LAB_VPN_PSK}}) come from .env / the environment
and are only written into the GNS3 project on this machine, never into the repo.

Usage (from the repo root, GNS3 server running):
    uv run python lab/build_gns3_lab.py                 # build, leave nodes stopped
    uv run python lab/build_gns3_lab.py --start         # build and start everything
    uv run python lab/build_gns3_lab.py --rebuild       # delete the old project first
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
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

    def call(self, method: str, path: str, body=None, raw: bytes | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/octet-stream" if raw else "application/json")
        if self.auth:
            req.add_header("Authorization", self.auth)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
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


def render(text: str, env: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
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
        if t["type"] == "qemu":
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
        extra = f" (image {body.get('hda_disk_image') or body['image']})"
        print(f"  template {t['name']!r} created{extra}")
    return out


def find_port(node: dict, spec: str) -> tuple[int, int]:
    """Map 'Gi0/1', 'eth0', 'tap-nw' or a bare port number to (adapter, port)."""
    for p in node["ports"]:
        if spec.lower() in (p["name"].lower(), p.get("short_name", "").lower()):
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("GNS3_SERVER", "http://127.0.0.1:3080"))
    ap.add_argument("--user", default=os.environ.get("GNS3_USER"))
    ap.add_argument("--password", default=os.environ.get("GNS3_PASSWORD"))
    ap.add_argument("--topology", type=Path, default=LAB_DIR / "topology.yaml")
    ap.add_argument("--rebuild", action="store_true", help="delete an existing project first")
    ap.add_argument("--start", action="store_true", help="start all nodes after building")
    args = ap.parse_args()

    topo = yaml.safe_load(args.topology.read_text())
    env = load_env()
    user, password = args.user, args.password
    if not user:
        user, password = gui_server_login()
    api = GNS3(args.server, user, password)

    version = api.call("GET", "/version")["version"]
    print(f"GNS3 server {version} at {args.server}")

    # Render every config first, so a missing secret stops us before anything is created.
    rendered: dict[str, str] = {}
    for name, n in topo["nodes"].items():
        src = n.get("config") or n.get("network")
        if src:
            rendered[name] = render((LAB_DIR / src).read_text(), env)

    # Cisco images are licensed (CML-Personal), so they are never downloaded for you.
    have = {i["filename"] for i in api.call("GET", "/computes/local/qemu/images")}
    found = sorted(f for f in have if f.endswith(".qcow2") and not f.startswith("empty"))
    for t in topo["templates"].values():
        if t["type"] == "qemu":
            image = env.get(t["image_env"], t["image_default"])
            if image not in have:
                raise SystemExit(
                    f"QEMU image {image!r} is not on the GNS3 server.\n"
                    f"Copy it to ~/GNS3/images/QEMU/ or set {t['image_env']}= in .env "
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

        if name in rendered:
            if node["node_type"] == "qemu":
                path, payload = "config.zip", ios_config_zip(rendered[name])
            else:
                path, payload = "etc/network/interfaces", rendered[name].encode()
            api.call("POST", f"/projects/{pid}/nodes/{node['node_id']}/files/{path}", raw=payload)
        has_config = "yes" if name in rendered else "-"
        print(f"  node {name:<10} {node['node_type']:<16} config: {has_config}")

    print("Links:")
    for a, b in topo["links"]:
        ends = []
        for end in (a, b):
            name, port = end.split(maxsplit=1)
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
        print("Starting all nodes (IOSv needs 2-4 minutes to boot)...")
        api.call("POST", f"/projects/{pid}/nodes/start", {})

    print(f"\nDone. Open project {project_name!r} in the GNS3 GUI.")
    print("Then from the host:  ssh netwarden@10.99.0.11   (after the routers have booted)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
