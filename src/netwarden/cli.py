"""Command-line interface: `netwarden --help`."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from netwarden import __version__
from netwarden.config import load_settings

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


if __name__ == "__main__":
    app()
