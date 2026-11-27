# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py==4.3.0", "mdit-py-plugins==0.7.0"]
# ///
"""Build the Rootvex PDFs from the Markdown in docs/ (the single source of truth).

    uv run scripts/build_pdfs.py            # all documents
    uv run scripts/build_pdfs.py manual     # just one: manual | design | setup

Markdown -> HTML with markdown-it-py (tables, task lists, heading anchors), then
printed by headless Chrome/Chromium. Page footers come from CSS @page margin boxes.
Set CHROME=/path/to/chrome if it is not found automatically.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

REPO = Path(__file__).resolve().parents[1]
DOCS = {
    "manual": ("MANUAL.md", "Rootvex-Manual.pdf", "Rootvex Manual"),
    "design": ("DESIGN.md", "Rootvex-Design.pdf", "Rootvex Design (Phase 0 + status)"),
    "setup": ("SETUP_LOG.md", "Rootvex-SetupLog.pdf", "Rootvex Workstation Setup Log"),
    "runbook": ("PHASE4_RUNBOOK.md", "Rootvex-Phase4-Runbook.pdf", "Rootvex Phase 4 Runbook"),
}

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm;
  @bottom-left { content: "%(label)s"; font: 7.5pt "DejaVu Sans", sans-serif; color: #6b7b83; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages);
                  font: 7.5pt "DejaVu Sans", sans-serif; color: #6b7b83; } }
html { font-family: "DejaVu Sans", "Liberation Sans", "Noto Color Emoji", sans-serif;
       font-size: 10pt; color: #1d2329; line-height: 1.45; }
body { margin: 0; }
h1 { font-size: 22pt; color: #12364a; border-bottom: 3px solid #2f6f8f;
     padding-bottom: 6px; margin: 0 0 10px; }
h2 { font-size: 15pt; color: #12364a; margin: 22px 0 8px; border-bottom: 1px solid #c9d6dd;
     padding-bottom: 3px; break-after: avoid; }
h3 { font-size: 12pt; color: #2f6f8f; margin: 16px 0 6px; break-after: avoid; }
a { color: #1f5f80; text-decoration: none; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.6pt; background: #eef3f5;
       padding: 0 3px; border-radius: 3px; }
pre { background: #f3f6f8; border: 1px solid #d5e0e5; border-radius: 4px; padding: 8px 10px;
      white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.2pt; line-height: 1.35; }
table { border-collapse: collapse; width: 100%%; margin: 8px 0 12px; font-size: 8.8pt; }
tr { break-inside: avoid; }
th { background: #2f6f8f; color: #fff; text-align: left; padding: 4px 6px; }
td { border-bottom: 1px solid #d5e0e5; padding: 4px 6px; vertical-align: top; }
tr:nth-child(even) td { background: #f6f9fa; }
blockquote { margin: 8px 0; padding: 6px 12px; border-left: 4px solid #2f6f8f;
             background: #eef5f8; }
hr { border: none; border-top: 1px solid #c9d6dd; margin: 16px 0; }
ul.contains-task-list { list-style: none; padding-left: 1.2em; }
"""


def find_chrome() -> str:
    candidates = [os.environ.get("CHROME", ""), "google-chrome", "chromium", "chromium-browser"]
    candidates += [str(p) for p in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")]
    for c in candidates:
        if c and (shutil.which(c) or Path(c).is_file()):
            return shutil.which(c) or c
    raise SystemExit("Chrome/Chromium not found; set CHROME=/path/to/chrome")


def build(key: str, chrome: str) -> None:
    src, out, title = DOCS[key]
    md = (
        MarkdownIt("commonmark")
        .enable(["table", "strikethrough"])
        .use(anchors_plugin, max_level=3)
        .use(tasklists_plugin)
    )
    body = md.render((REPO / "docs" / src).read_text(encoding="utf-8"))
    label = f"{title} · built {date.today():%d %b %Y}"
    html = (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{CSS % {'label': label}}</style></head><body>{body}</body></html>"
    )
    pdf = REPO / "docs" / out
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "doc.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run(
            [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--no-pdf-header-footer", f"--print-to-pdf={pdf}", page.as_uri()],
            check=True, capture_output=True, timeout=120,
        )  # fmt: skip
    print(f"{pdf.relative_to(REPO)}  ({pdf.stat().st_size // 1024} KB)")


def main() -> None:
    keys = sys.argv[1:] or list(DOCS)
    unknown = [k for k in keys if k not in DOCS]
    if unknown:
        raise SystemExit(f"Unknown document {unknown}; choose from {list(DOCS)}")
    chrome = find_chrome()
    for k in keys:
        build(k, chrome)


if __name__ == "__main__":
    main()
