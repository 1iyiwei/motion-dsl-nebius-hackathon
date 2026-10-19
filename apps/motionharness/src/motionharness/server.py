"""MotionHarness dashboard: start a run, watch the branch tree, tests, frames and every model call.

GET  /api/info                      sandbox backend, models, whether Tavily is configured
GET  /api/runs                      past and running runs (summaries)
POST /api/runs                      {task, name?, namespace?, candidates?, models?, rounds?, critique?} → {id}
GET  /api/runs/{id}                 the run record (run.json), live while it runs
GET  /api/runs/{id}/file/{path}     a run artifact: candidates/c1/frames.png, preview.html, plugin.diff, report.md, …
"""

from __future__ import annotations

import json
import os
import threading
from importlib import resources
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .harness import MODELS, Config, Harness, Run, coder_model, tavily_search
from .plugin import Task
from .sandbox import make_sandbox

ARTIFACT_TYPES = {".png": "image/png", ".html": "text/html", ".diff": "text/plain", ".md": "text/plain", ".json": "application/json",
                  ".py": "text/plain", ".toml": "text/plain", ".txt": "text/plain"}


class StartRun(BaseModel):
    task: str = Field(min_length=8, max_length=1000)
    name: str = ""
    namespace: str = "mh"
    candidates: int = Field(3, ge=1, le=5)
    models: list[Literal["super", "ultra", "nano"]] = []
    rounds: int = Field(3, ge=0, le=5)
    critique: bool = True
    docs: bool = True


def create_app(root: Path = Path("out/motionharness"), sandbox_kind: str = "auto", harness: Harness | None = None) -> FastAPI:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="MotionHarness")
    static = resources.files("motionharness") / "static"
    live: dict[str, Run] = {}
    state: dict = {"harness": harness}

    def get_harness() -> Harness:
        if state["harness"] is None:
            state["harness"] = Harness(make_sandbox(sandbox_kind, root / ".sandbox"), root=root, tavily=tavily_search())
        return state["harness"]

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
        h = get_harness()
        return {"sandbox": h.sandbox.describe(), "coder": coder_model(), "vision": os.environ.get("MOTION_VISION_MODEL", MODELS["vl"]),
                "tavily": h.tavily is not None, "models": MODELS}

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
    def start(body: StartRun) -> dict:
        try:
            task = Task(body.task, name=body.name, namespace=body.namespace or "mh")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        h = get_harness()
        cfg = Config(candidates=body.candidates, models=list(body.models), max_rounds=body.rounds, critique=body.critique, docs=body.docs)
        run = Run(task, cfg, root, h.sandbox)
        live[run.id] = run
        run.save()
        threading.Thread(target=h.run, args=(task, cfg), kwargs={"run": run}, daemon=True).start()
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

    @app.get("/api/runs/{run_id}/file/{path:path}")
    def artifact(run_id: str, path: str):
        d = run_dir(run_id)
        p = (d / path).resolve()
        if not p.is_relative_to(d) or not p.is_file() or p.suffix not in ARTIFACT_TYPES:
            raise HTTPException(404)
        headers = {"Content-Security-Policy": "sandbox allow-scripts"} if p.suffix == ".html" else {}
        return FileResponse(p, media_type=ARTIFACT_TYPES[p.suffix], headers=headers)

    return app
