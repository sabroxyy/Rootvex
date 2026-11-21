"""Find Dynamips settings that let the c7200 image boot on this machine.

Creates a throwaway GNS3 project, boots ONE router per variant, reads its console
for ~45 seconds, and reports whether IOS started or crashed (TLB exceptions).
Nothing in the netwarden-lab project is touched. Stop the lab's routers first
so they don't compete for CPU.

    uv run python lab/test_c7200.py            # stops at the first variant that boots
    uv run python lab/test_c7200.py --all      # try every variant
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_gns3_lab import GNS3, gui_server_login, load_env  # noqa: E402

PROJECT = "netwarden-c7200-test"
BASE = {"platform": "c7200", "npe": "npe-400", "midplane": "vxr", "ram": 512}
VARIANTS = [
    ("current (512 MB, sparsemem, mmap)", {"sparsemem": True, "mmap": True}),
    ("sparse memory OFF", {"sparsemem": False, "mmap": True}),
    ("RAM 256 MB", {"ram": 256, "sparsemem": False}),
    ("RAM 384 MB", {"ram": 384, "sparsemem": False}),
    ("midplane std", {"midplane": "std", "sparsemem": False}),
    ("I/O memory 10%", {"iomem": 10, "sparsemem": False}),
]
GOOD = re.compile(r"Cisco IOS Software|Restricted Rights Legend|Press RETURN|cisco 7206VXR", re.I)
BAD = re.compile(r"TLB \(\w+\) Exception|Unexpected exception|\*\*\* System received", re.I)


def read_console(port: int, seconds: int) -> str:
    """Read raw console output (telnet negotiation bytes are dropped)."""
    data = b""
    end = time.time() + seconds
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.settimeout(1)
            while time.time() < end:
                try:
                    chunk = s.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                data += chunk
                text = data.decode(errors="ignore")
                if b"Press RETURN" in data or len(BAD.findall(text)) >= 3:
                    break  # answer known; a crash loop would otherwise flood the disk
    except OSError as exc:
        return f"<console error: {exc}>"
    return re.sub(rb"\xff[\xfb-\xfe].|\xff.", b"", data).decode(errors="ignore")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:3080")
    ap.add_argument(
        "--all", action="store_true", help="try every variant, not just until one boots"
    )
    ap.add_argument("--seconds", type=int, default=45, help="console watch time per variant")
    args = ap.parse_args()

    env = load_env()
    image = env.get("LAB_C7200_IMAGE", "c7200-advipservicesk9-mz.152-4.S5.image")
    api = GNS3(args.server, *gui_server_login())
    print(f"GNS3 {api.call('GET', '/version')['version']}, image {image}")

    for p in api.call("GET", "/projects"):
        if p["name"] == PROJECT:
            if p["status"] != "opened":
                api.call("POST", f"/projects/{p['project_id']}/open", {})
            api.call("DELETE", f"/projects/{p['project_id']}")
    pid = api.call("POST", "/projects", {"name": PROJECT, "auto_close": False})["project_id"]

    results = []
    try:
        for i, (label, props) in enumerate(VARIANTS, 1):
            body = {
                "name": f"T{i}",
                "node_type": "dynamips",
                "compute_id": "local",
                "properties": {**BASE, "image": image, **props},
            }
            node = api.call("POST", f"/projects/{pid}/nodes", body)
            nid = node["node_id"]
            print(f"\n[{i}/{len(VARIANTS)}] {label}: booting...", flush=True)
            try:
                api.call("POST", f"/projects/{pid}/nodes/{nid}/start", {}, timeout=120)
            except SystemExit as exc:
                print(f"  could not start: {exc}")
                results.append((label, "start failed"))
                api.call("DELETE", f"/projects/{pid}/nodes/{nid}")
                continue
            out = read_console(node["console"], args.seconds)
            bad = len(BAD.findall(out))
            booted = bool(GOOD.search(out)) and bad == 0
            verdict = "BOOTS" if booted else ("CRASHES" if bad else "no output yet")
            first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")[:100]
            print(f"  {verdict}  (crash messages: {bad})  first line: {first!r}")
            results.append((label, verdict))
            api.call("POST", f"/projects/{pid}/nodes/{nid}/stop", {}, timeout=120)
            api.call("DELETE", f"/projects/{pid}/nodes/{nid}")
            if booted and not args.all:
                break
    finally:
        api.call("DELETE", f"/projects/{pid}")

    print("\nSummary:")
    for label, verdict in results:
        print(f"  {verdict:<14} {label}")


if __name__ == "__main__":
    main()
