"""Command-line interface: `rootvex --help`."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from rootvex import __version__
from rootvex.config import PROJECT_ROOT, Settings, load_settings
from rootvex.log import setup_logging

app = typer.Typer(help="Rootvex - lab-only AI troubleshooting agent.", no_args_is_help=True)
console = Console()


def _load_lab(s: Settings, fixtures: bool):
    """Inventory + allowlist + runner. --fixtures falls back to the example inventory."""
    from rootvex.devices.allowlist import Allowlist
    from rootvex.devices.connection import FixtureRunner, NetmikoRunner
    from rootvex.devices.inventory import load_inventory

    inv_path = s.resolve(s.lab.inventory_file)
    if fixtures and not inv_path.exists():
        inv_path = PROJECT_ROOT / "config/inventory.example.yaml"
    inventory = load_inventory(inv_path, s.lab.allowed_networks)
    allowlist = Allowlist.load(s.resolve(s.lab.allowlist_dir))
    use_fixtures = fixtures or s.lab.device_backend == "fixtures"
    runner = FixtureRunner(s.resolve(s.lab.fixtures_dir)) if use_fixtures else NetmikoRunner()
    return inventory, allowlist, runner


@app.command()
def version() -> None:
    """Print the Rootvex version."""
    console.print(f"rootvex {__version__}")


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
def devices(fixtures: bool = typer.Option(False, help="Use the example inventory.")) -> None:
    """List lab devices from the inventory (after the lab-network check)."""
    inventory, _, _ = _load_lab(load_settings(), fixtures)
    table = Table("name", "host", "platform", "username", "password env var")
    for d in inventory:
        table.add_row(d.name, str(d.host), d.platform, d.username, d.password_env)
    console.print(table)


@app.command()
def check(device: str, command: str, fixtures: bool = typer.Option(False)) -> None:
    """Dry-run: would this command be allowed on this device? Nothing is sent."""
    inventory, allowlist, _ = _load_lab(load_settings(), fixtures)
    dev = inventory.get(device)
    d = allowlist.check(dev.platform, command)
    if d.allowed:
        console.print(f"[green]ALLOWED[/green] {dev.name} ({dev.platform}): {d.command}")
    else:
        console.print(f"[red]DENIED[/red] {dev.name} ({dev.platform}): {d.command} - {d.reason}")
        raise typer.Exit(1)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Describe the problem in plain language."),
    fixtures: bool = typer.Option(False, help="Use recorded outputs instead of SSH."),
    fake: bool = typer.Option(False, help="Phase 2 fake tool only (no inventory needed)."),
    show_steps: bool = typer.Option(False, "--steps", help="Print every tool call."),
    scenario: str = typer.Option(
        "",
        help="Replay a recorded fault from tests/fixtures/scenarios/<name> (implies --fixtures).",
    ),
) -> None:
    """Troubleshoot a problem: plan, collect read-only evidence, report."""
    from langchain_core.messages import ToolMessage

    from rootvex.agent.graph import run_agent
    from rootvex.llm import build_llm

    s = load_settings()
    log_file = setup_logging(s.resolve(s.logging.dir), s.logging.level)
    if fake:
        from rootvex.tools.fake import make_fake_tools

        tools, names = make_fake_tools(), ["R1", "R2"]
    else:
        from rootvex.tools.device_tools import make_device_tools

        inventory, allowlist, runner = _load_lab(s, fixtures or bool(scenario))
        if scenario:
            from rootvex.devices.connection import FixtureRunner

            folder = PROJECT_ROOT / "tests/fixtures/scenarios" / scenario
            if not folder.is_dir():
                console.print(f"[red]No recorded scenario {scenario!r}[/red] (see lab/FAULTS.md)")
                raise typer.Exit(2)
            runner = FixtureRunner(folder)
        tools = make_device_tools(inventory, allowlist, runner, s.agent.max_output_chars)
        names = inventory.names()

    import openai

    try:
        with console.status(f"Troubleshooting with {s.llm.model}..."):
            events: list[dict] = []
            state = run_agent(
                question,
                build_llm(s.llm),
                tools,
                s.agent,
                names,
                on_event=events.append,
                playbooks=_playbooks(),
            )
    except openai.APITimeoutError:  # subclass of APIConnectionError, so it must come first
        console.print(
            f"[red]The model did not answer within {s.llm.request_timeout_s}s.[/red] "
            "Thinking models and first loads are slow; raise ROOTVEX_LLM__REQUEST_TIMEOUT_S "
            "(e.g. 600) or try a smaller model."
        )
        raise typer.Exit(2) from None
    except openai.APIConnectionError:
        console.print(
            f"[red]Cannot reach the model server at {s.llm.base_url}.[/red] "
            "Is Ollama running? Try: ollama serve  /  ollama pull " + s.llm.model
        )
        raise typer.Exit(2) from None
    except openai.NotFoundError:
        console.print(f"[red]Model {s.llm.model!r} not found.[/red] Try: ollama pull {s.llm.model}")
        raise typer.Exit(2) from None

    console.rule("Plan")
    console.print(state.get("plan", ""), markup=False)
    if show_steps:
        checks = [e for e in events if e.get("type") == "tool" and e.get("tool") == "playbook"]
        if checks:
            console.rule(f"Playbook checks: {state.get('playbook', '')}")
            for e in checks:
                console.print(_clip(str(e["result"])), markup=False)
        console.rule("Tool calls (model)")
        for m in state["messages"]:
            if isinstance(m, ToolMessage):
                console.print(_clip(str(m.content)), markup=False)
    console.rule("Report")
    console.print(state["report"].to_text(), markup=False)
    console.print(f"\n[dim]run_id={state['run_id']}  steps={state['steps']}  log={log_file}[/dim]")


@app.command()
def web(
    host: str = typer.Option(None, help="Address to listen on (default from settings: 127.0.0.1)."),
    port: int = typer.Option(None, help="Port (default from settings: 8000)."),
) -> None:
    """Start the local web prototype: chat page, live steps, report, Approve/Reject."""
    import uvicorn

    from rootvex.web.app import create_app

    s = load_settings()
    application = create_app(s)
    h, p = host or s.web.host, port or s.web.port
    if h not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[yellow]Listening on {h}: other machines can reach the page. "
            "The login uses plain HTTP; keep it on a trusted lab network.[/yellow]"
        )
    console.print(
        f"Rootvex web: http://{'127.0.0.1' if h == '0.0.0.0' else h}:{p}  (user {s.web.user})"
    )
    uvicorn.run(application, host=h, port=p, log_level="warning")


if __name__ == "__main__":
    app()


def _playbooks():
    from rootvex.playbooks import load_playbooks, playbook_dir

    return load_playbooks(playbook_dir())


def _clip(text: str, limit: int = 1500) -> str:
    """Display only; the model saw the full output."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... {len(text) - limit} more chars not shown]"
