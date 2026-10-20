"""MotionHarness dashboard: start a run, watch the branch tree, tests, frames and every model call.

GET  /api/info                      sandbox backend, models, whether Tavily is configured
GET  /api/runs                      past and running runs (summaries)
POST /api/runs                      {task, name?, namespace?, candidates?, models?, rounds?, critique?} → {id}
GET  /api/runs/{id}                 the run record (run.json), live while it runs
GET  /api/runs/{id}/file/{path}     a run artifact: candidates/c1/frames.png, preview.html, plugin.diff, report.md, …
GET  /api/runs/{id}/plugin.zip      the shipped plugin package (+ report, diff, preview) as a zip
GET  /api/runs/{id}/scenes          "try in a scene" trials: one prompt planned with and without the run's verb
POST /api/runs/{id}/scenes          {prompt} → {n}; planned in the background, validated in sandboxes
GET  /api/installed                 verb plugins installed in this environment
POST /api/runs/{id}/install|uninstall   install or remove a run's plugin here (local use; apps need a restart)
"""

from __future__ import annotations

import json
import os
import threading
from importlib import resources
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import installer
from .guard import Guard
from .harness import MODELS, Config, Harness, Run, coder_model, tavily_search
from .plugin import Task
from .scenes import SceneTrial, SceneTrier, list_trials
from .sandbox import make_sandbox

ARTIFACT_TYPES = {".png": "image/png", ".html": "text/html", ".diff": "text/plain", ".md": "text/plain", ".json": "application/json",
                  ".py": "text/plain", ".toml": "text/plain", ".txt": "text/plain"}


class TryScene(BaseModel):
    prompt: str = Field(min_length=4, max_length=500)


class StartRun(BaseModel):
    task: str = Field(min_length=8, max_length=1000)
    name: str = ""
    namespace: str = "mh"
    candidates: int = Field(3, ge=1, le=5)
    models: list[Literal["super", "ultra", "nano"]] = []
    rounds: int = Field(3, ge=0, le=5)
    critique: bool = True
    docs: bool = True


HOSTED_CAPS = {"candidates": 3, "rounds": 3}


def hosted_from_env() -> bool:
    return os.environ.get("MOTIONHARNESS_HOSTED", "") not in ("", "0", "false")


def create_app(root: Path = Path("out/motionharness"), sandbox_kind: str = "auto", harness: Harness | None = None,
               hosted: bool | None = None, guard: Guard | None = None) -> FastAPI:
    """hosted (default: MOTIONHARNESS_HOSTED): a public copy. Generated code runs only in Token Factory Sandboxes
    (runs are refused while they're unavailable), installing into the server is off, and runs are capped.
    guard (default: MOTIONHARNESS_* env vars, all off): access code, per-visitor rate limit, daily token budget."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    hosted = hosted_from_env() if hosted is None else hosted
    guard = guard or Guard.from_env()
    max_active = int(os.environ.get("MOTIONHARNESS_MAX_ACTIVE", "1" if hosted else "0") or 0)  # 0 = unlimited
    app = FastAPI(title="MotionHarness")
    static = resources.files("motionharness") / "static"
    live: dict[str, Run] = {}
    state: dict = {"harness": harness, "unavailable": None}

    def get_harness() -> Harness:
        if state["harness"] is None:
            # A hosted copy never runs model-written code outside Token Factory Sandboxes.
            state["harness"] = Harness(make_sandbox("contree" if hosted else sandbox_kind, root / ".sandbox"), root=root, tavily=tavily_search())
        return state["harness"]

    def usable_harness() -> Harness:
        try:
            return get_harness()
        except RuntimeError as e:  # hosted and Sandboxes not active (yet): say why, keep serving past runs
            raise HTTPException(503, detail=f"Runs are paused: {e}. Past runs stay viewable.") from e

    def active_runs() -> int:
        return sum(1 for r in live.values() if r.status == "running")

    def admit(request: Request) -> None:
        if max_active and active_runs() >= max_active:
            raise HTTPException(429, detail="Another run is in progress on this demo; watch it, or try again in a few minutes.")
        guard.check(request)

    def spend_after(fn, run_tokens):  # count a run's tokens against the daily budget once it ends
        def go() -> None:
            try:
                fn()
            finally:
                guard.spend(run_tokens())
        return go

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static / "index.html").read_text()

    @app.get("/static/{name}")
    def static_file(name: str):
        if name not in {"app.js", "app.css"}:
            raise HTTPException(404)
        return HTMLResponse((static / name).read_text(), media_type="text/javascript" if name.endswith(".js") else "text/css")

    @app.get("/api/info")
    def info() -> dict:
        base = {"hosted": hosted, "coder": coder_model(), "vision": os.environ.get("MOTION_VISION_MODEL", MODELS["vl"]),
                "models": MODELS, "caps": HOSTED_CAPS if hosted else None, **guard.status()}
        try:
            h = get_harness()
        except RuntimeError as e:
            return {**base, "sandbox": {"backend": "unavailable", "reason": str(e)}, "tavily": False}
        return {**base, "sandbox": h.sandbox.describe(), "tavily": h.tavily is not None}

    @app.get("/api/runs")
    def runs() -> list[dict]:
        out = []
        for p in sorted(root.glob("*/run.json"), reverse=True)[:50]:
            try:
                r = json.loads(p.read_text())
            except ValueError:
                continue
            out.append({"id": r["id"], "status": r["status"], "verb": r["task"]["verb"], "description": r["task"]["description"],
                        "winner": r.get("winner"), "candidates": len(r["candidates"]), "seconds": r.get("seconds"), "tokens": r.get("tokens", 0),
                        "sandbox": (r.get("sandbox") or {}).get("backend")})
        return out

    @app.post("/api/runs")
    def start(body: StartRun, request: Request) -> dict:
        try:
            task = Task(body.task, name=body.name, namespace=body.namespace or "mh")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        h = usable_harness()
        admit(request)
        candidates, rounds = body.candidates, body.rounds
        if hosted:
            candidates, rounds = min(candidates, HOSTED_CAPS["candidates"]), min(rounds, HOSTED_CAPS["rounds"])
        cfg = Config(candidates=candidates, models=list(body.models), max_rounds=rounds, critique=body.critique, docs=body.docs)
        run = Run(task, cfg, root, h.sandbox)
        live[run.id] = run
        run.save()
        work = spend_after(lambda: h.run(task, cfg, run=run), lambda: sum(c["prompt_tokens"] + c["completion_tokens"] for c in run.calls))
        threading.Thread(target=work, daemon=True).start()
        return {"id": run.id}

    def run_dir(run_id: str) -> Path:
        d = (root / run_id).resolve()
        if d.parent != root.resolve() or not (d / "run.json").exists():
            raise HTTPException(404, "no such run")
        return d

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        if run_id in live:
            return JSONResponse(live[run_id].to_dict())
        return JSONResponse(json.loads((run_dir(run_id) / "run.json").read_text()))

    @app.get("/api/runs/{run_id}/plugin.zip")
    def plugin_zip(run_id: str):
        d = run_dir(run_id)
        if not (d / "plugin" / "pyproject.toml").exists():
            raise HTTPException(404, "this run shipped no plugin")
        dist = installer.run_info(d)["dist"]
        return Response(installer.plugin_zip(d), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{dist}.zip"'})

    @app.get("/api/runs/{run_id}/scenes")
    def scenes(run_id: str) -> list[dict]:
        return list_trials(run_dir(run_id))

    @app.post("/api/runs/{run_id}/scenes")
    def try_scene(run_id: str, body: TryScene, request: Request) -> dict:
        d = run_dir(run_id)
        run = json.loads((d / "run.json").read_text())
        h = usable_harness()
        guard.check(request)
        trial = SceneTrial(d, body.prompt)
        SceneTrier(h).start(run, d, trial, on_done=lambda t: guard.spend(t.tokens()))
        return {"n": trial.n}

    @app.get("/api/installed")
    def list_installed() -> list[dict]:
        return [] if hosted else installer.installed()

    def local_only() -> None:
        if hosted:
            raise HTTPException(403, detail="Installing into the demo server is off; download the plugin instead.")

    @app.post("/api/runs/{run_id}/install")
    def install_plugin(run_id: str) -> dict:
        local_only()
        ok, log = installer.install(run_dir(run_id))
        if not ok:
            raise HTTPException(500, log)
        return {"installed": installer.installed()}

    @app.post("/api/runs/{run_id}/uninstall")
    def uninstall_plugin(run_id: str) -> dict:
        local_only()
        ok, log = installer.uninstall(installer.run_info(run_dir(run_id))["dist"])
        if not ok:
            raise HTTPException(500, log)
        return {"installed": installer.installed()}

    @app.get("/api/runs/{run_id}/file/{path:path}")
    def artifact(run_id: str, path: str):
        d = run_dir(run_id)
        p = (d / path).resolve()
        if not p.is_relative_to(d) or not p.is_file() or p.suffix not in ARTIFACT_TYPES:
            raise HTTPException(404)
        headers = {"Content-Security-Policy": "sandbox allow-scripts"} if p.suffix == ".html" else {}
        return FileResponse(p, media_type=ARTIFACT_TYPES[p.suffix], headers=headers)

    return app
