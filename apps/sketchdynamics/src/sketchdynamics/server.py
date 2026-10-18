"""SketchDynamics web server: sketch + intent → MotionLang document → baked tracks for the browser player.

POST /generate {intent, strokes, canvas}   Nemotron Ultra plans the document, the validator drives repairs
POST /tweak    {doc, instruction}          Nemotron Nano edits it with id-addressed ops (escalates if needed)
POST /bake     {doc}                       validate + bake only, no model (e.g. after redrawing a stroke)

/generate and /tweak take `?stream=1` and then answer with NDJSON events, one per model call as it
starts and ends, followed by the final result. That's what lets the side panel fill in live.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import webbrowser
from collections.abc import Callable, Iterator
from importlib import resources
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from motionlang import check
from motionlang.agent import LLM, Attempt, Call, Outcome, generate, load_env, model_for, tweak

PLANNER_KINDS = {"generate", "repair", "regenerate"}


class Stroke(BaseModel):
    points: list[tuple[float, float]] = Field(min_length=2)


class Canvas(BaseModel):
    width: int = 1280
    height: int = 720
    background: str = "#ffffff"
    fps: int = 60


class GenerateRequest(BaseModel):
    intent: str = Field(min_length=1)
    strokes: dict[str, Stroke] = {}
    canvas: Canvas = Canvas()


class TweakRequest(BaseModel):
    doc: dict[str, Any]
    instruction: str = Field(min_length=1)


class BakeRequest(BaseModel):
    doc: dict[str, Any]


def call_json(call: Call) -> dict[str, Any]:
    return {
        "purpose": call.purpose,
        "model": call.model,
        "think": call.think,
        "seconds": round(call.seconds, 3),
        "prompt_tokens": call.prompt_tokens,
        "completion_tokens": call.completion_tokens,
    }


def attempt_json(a: Attempt) -> dict[str, Any]:
    return {
        "kind": a.kind,
        "role": "planner" if a.kind in PLANNER_KINDS else "editor",
        **call_json(a.call),
        "ok": a.ok,
        "problems": a.problems(),
        "issues": [e.to_dict() for e in a.report.errors] if a.report else [],
    }


def bake_json(doc: dict[str, Any] | None) -> dict[str, Any]:
    if doc is None:
        return {"doc": None, "tracks": None, "report": None}
    res = check(doc)
    return {"doc": doc, "tracks": res.baked.to_json() if res.baked else None, "report": res.report.to_dict()}


def outcome_json(out: Outcome) -> dict[str, Any]:
    tokens = sum(a.call.prompt_tokens + a.call.completion_tokens for a in out.attempts)
    return {
        "ok": out.ok,
        "path": out.path,
        "note": out.note,
        "seconds": round(out.seconds, 3),
        "tokens": tokens,
        "attempts": [attempt_json(a) for a in out.attempts],
        **bake_json(out.doc),
    }


class ReportingLLM(LLM):
    """LLM that announces each call before and after it runs (for the live panel)."""

    def __init__(self, inner: LLM, emit: Callable[[dict[str, Any]], None]):
        self.backend, self.calls, self._emit = inner.backend, inner.calls, emit

    def json(self, role: str, purpose: str, messages: list[dict[str, Any]], **kw: Any) -> tuple[str, Call]:
        self._emit({"type": "call_start", "role": role, "purpose": purpose, "model": model_for(role), "think": kw.get("think", True)})
        text, call = super().json(role, purpose, messages, **kw)
        self._emit({"type": "call_end", "role": role, **call_json(call)})
        return text, call


def create_app(llm_factory: Callable[[], LLM] | None = None) -> FastAPI:
    """`llm_factory` builds a fresh LLM per request; tests pass one with a scripted backend."""
    make_llm = llm_factory or LLM
    app = FastAPI(title="SketchDynamics")
    static = resources.files("sketchdynamics") / "static"
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.middleware("http")
    async def no_cache(request, call_next):  # a local tool: always serve the current UI after an upgrade
        resp = await call_next(request)
        if request.method == "GET":
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    def run(job: Callable[[LLM], Outcome]) -> dict[str, Any]:
        try:
            return outcome_json(job(make_llm()))
        except Exception as e:  # missing key, network, rate limits: show it in the UI, don't 500 blindly
            raise HTTPException(502, detail=f"{type(e).__name__}: {e}") from e

    def streamed(job: Callable[[LLM], Outcome]) -> StreamingResponse:
        q: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def worker() -> None:
            try:
                q.put({"type": "result", **outcome_json(job(ReportingLLM(make_llm(), q.put)))})
            except Exception as e:
                q.put({"type": "error", "detail": f"{type(e).__name__}: {e}"})
            q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def events() -> Iterator[str]:
            while (ev := q.get()) is not None:
                yield json.dumps(ev, separators=(",", ":")) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static / "index.html").read_text()

    @app.post("/generate")
    def post_generate(req: GenerateRequest, stream: bool = False):
        strokes = {k: {"points": [list(p) for p in s.points]} for k, s in req.strokes.items()}
        job = lambda llm: generate(req.intent, canvas=req.canvas.model_dump(), strokes=strokes or None, llm=llm)  # noqa: E731
        return streamed(job) if stream else JSONResponse(run(job))

    @app.post("/tweak")
    def post_tweak(req: TweakRequest, stream: bool = False):
        job = lambda llm: tweak(req.doc, req.instruction, llm=llm)  # noqa: E731
        return streamed(job) if stream else JSONResponse(run(job))

    @app.post("/bake")
    def post_bake(req: BakeRequest):
        return JSONResponse(bake_json(req.doc))

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="sketchdynamics", description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the page")
    args = ap.parse_args(argv)
    load_env()
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    print(f"SketchDynamics on {url}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
