"""Command-line interface: `netwarden --help`."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from netwarden import __version__
from netwarden.config import load_settings
from netwarden.log import setup_logging

app = typer.Typer(help="NetWarden - lab-only AI troubleshooting agent.", no_args_is_help=True)
console = Console()


@app.command()
def version() -> None:
    """Print the NetWarden version."""
    console.print(f"netwarden {__version__}")


@app.command("config")
def show_config() -> None:
    """Show the effective settings (secrets hidden)."""
    s = load_settings()
    table = Table("setting", "value")
    for section in ("llm", "agent", "lab", "logging"):
        for key, val in getattr(s, section).model_dump().items():
            table.add_row(f"{section}.{key}", str(val))
    console.print(table)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Describe the problem in plain language."),
) -> None:
    """Troubleshoot a problem (Phase 2: fake interface tool only)."""
    from netwarden.agent.graph import run_agent
    from netwarden.llm import build_llm
    from netwarden.tools.fake import make_fake_tools

    s = load_settings()
    log_file = setup_logging(s.resolve(s.logging.dir), s.logging.level)
    with console.status(f"Thinking with {s.llm.model}..."):
        state = run_agent(question, build_llm(s.llm), make_fake_tools(), s.agent, ["R1", "R2"])
    console.rule("Plan")
    console.print(state.get("plan", ""))
    console.rule("Report")
    console.print(state["report"].to_text())
    console.print(f"\n[dim]run_id={state['run_id']}  steps={state['steps']}  log={log_file}[/dim]")


if __name__ == "__main__":
    app()
