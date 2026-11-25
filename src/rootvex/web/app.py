"""Rootvex web prototype: a local chat page in front of the same read-only agent.

    uv run rootvex web            # http://127.0.0.1:8000, login from .env

- Login required (ROOTVEX_WEB__USER / ROOTVEX_WEB__PASSWORD); refuses to start without one.
- One run at a time (one local GPU); later requests wait their turn.
- Approve / Reject only RECORDS the decision and who made it. Nothing is ever sent
  to a device: the agent's tools stay read-only, exactly as on the command line.
- Requests, steps, reports and decisions are kept in SQLite (data/rootvex.db).
"""

import secrets
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from rootvex.config import Settings, load_settings
from rootvex.log import get_logger, setup_logging
from rootvex.web.store import RunStore

STATIC = Path(__file__).resolve().parent / "static"
log = get_logger("rootvex.web")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    mode: str = Field("recorded", pattern="^(live|recorded)$")


class DecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str = Field("", max_length=500)


def _default_tools(settings: Settings, mode: str):
    from rootvex.cli import _load_lab
    from rootvex.tools.device_tools import make_device_tools

    inventory, allowlist, runner = _load_lab(settings, fixtures=(mode == "recorded"))
    tools = make_device_tools(inventory, allowlist, runner, settings.agent.max_output_chars)
    return tools, inventory.names()


def create_app(
    settings: Settings | None = None,
    llm=None,
    tools_factory: Callable | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    if not settings.web.password or not settings.web.password.get_secret_value():
        raise SystemExit(
            "Set ROOTVEX_WEB__PASSWORD in .env: the web page never runs without a login."
        )
    setup_logging(settings.resolve(settings.logging.dir), settings.logging.level)
    store = RunStore(settings.resolve(settings.web.db_file))
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rootvex-run")
    tools_factory = tools_factory or (lambda mode: _default_tools(settings, mode))
    basic = HTTPBasic(realm="Rootvex")

    def user(creds: Annotated[HTTPBasicCredentials, Depends(basic)]) -> str:
        ok_user = secrets.compare_digest(creds.username.encode(), settings.web.user.encode())
        ok_pass = secrets.compare_digest(
            creds.password.encode(), settings.web.password.get_secret_value().encode()
        )
        if not (ok_user and ok_pass):
            log.warning("web_login_failed", user=creds.username)
            raise HTTPException(401, "Wrong user or password", {"WWW-Authenticate": "Basic"})
        return creds.username

    User = Annotated[str, Depends(user)]

    def run_job(run_id: str, question: str, mode: str) -> None:
        import openai

        from rootvex.agent.graph import run_agent
        from rootvex.llm import build_llm
        from rootvex.playbooks import load_playbooks, playbook_dir

        try:
            tools, names = tools_factory(mode)
            model = llm or build_llm(settings.llm)
            state = run_agent(
                question,
                model,
                tools,
                settings.agent,
                names,
                on_event=lambda e: store.add_event(run_id, e),
                playbooks=load_playbooks(playbook_dir()),
            )
            store.finish(run_id, state["report"].model_dump())
        except openai.APITimeoutError:
            store.fail(
                run_id, f"The model did not answer within {settings.llm.request_timeout_s}s."
            )
        except openai.APIConnectionError:
            store.fail(
                run_id,
                f"Cannot reach the model server at {settings.llm.base_url}. Is Ollama running?",
            )
        except Exception as exc:  # noqa: BLE001 - show the engineer what went wrong
            log.error("web_run_failed", run=run_id, error=str(exc))
            store.fail(run_id, f"{type(exc).__name__}: {exc}")

    app = FastAPI(title="Rootvex", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def index(_: User):
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health(who: User):
        return {
            "ok": True,
            "user": who,
            "model": settings.llm.model,
            "live_backend": settings.lab.device_backend,
        }

    @app.post("/api/runs")
    def start_run(req: AskRequest, who: User):
        run_id = uuid.uuid4().hex[:12]
        store.create(run_id, who, req.question.strip(), req.mode)
        log.info("web_run_start", run=run_id, user=who, mode=req.mode, question=req.question)
        worker.submit(run_job, run_id, req.question.strip(), req.mode)
        return {"id": run_id}

    @app.get("/api/runs")
    def list_runs(_: User):
        return store.recent()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, _: User):
        run = store.get(run_id)
        if not run:
            raise HTTPException(404, "No such run")
        return run

    @app.post("/api/runs/{run_id}/decision")
    def decide(run_id: str, req: DecisionRequest, who: User):
        if not store.decide(run_id, req.decision, who, req.note):
            raise HTTPException(409, "Only a finished run without a decision can be decided")
        log.info("web_decision", run=run_id, user=who, decision=req.decision, note=req.note)
        return store.get(run_id)

    app.state.store = store
    app.state.worker = worker
    return app
