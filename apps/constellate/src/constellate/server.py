"""Constellate web app: sketch or say a show, brief the site, make it flyable, watch it fly.

    constellate serve                  # http://127.0.0.1:8780/
"""

from __future__ import annotations

import json
import queue
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from motionlang.agent.llm import LLM
from motionlang.preview import player_js_path

from . import agent
from .flight import tracks_m
from .pipeline import Checked, load_show, run
from .show import Limits, Show

STATIC = Path(__file__).parent / "static"
EXAMPLES = Path(__file__).parent / "examples"
SAMPLES = Path(__file__).parent / "samples"
PORT = 8780


class ShowReq(BaseModel):
    show: dict


class PlanReq(BaseModel):
    intent: str
    drones: int = 100
    sources: dict = {}
    limits: dict = {}


class TweakReq(BaseModel):
    show: dict
    request: str


class BriefReq(BaseModel):
    venue: str
    date: str


def _show(raw: dict) -> Show:
    try:
        return Show.model_validate(raw)
    except ValidationError as e:
        raise HTTPException(422, detail=str(e)) from e


def checked_json(c: Checked) -> dict:
    """Everything the page draws for a show: the script, tracks for the player, the flight report, step spans."""
    return {
        "show": c.show.model_dump(mode="json"),
        "tracks": c.baked.to_json(precision=2) if c.baked is not None else None,
        "flight": c.flight.to_dict(),
        "motion_ok": c.motion.ok,
        "steps": [{"id": s.step, "kind": s.kind, "index": s.index, "t0": round(s.t0, 2), "t1": round(s.t1, 2),
                   "moving_until": round(s.moving_until, 2), "formation": s.formation} for s in c.compiled.spans],
        "ok": c.ok,
    }


def sweep_json(sw) -> dict:
    return {"certificate": sw.certificate(), "by_wind": {str(k): v for k, v in sw.by_wind().items()},
            "runs": [{"wind": r.wind, "seed": r.seed, "ok": r.ok, "max_err": round(r.max_err, 2), "near_pairs": r.near_pairs,
                      "min_sep": round(r.min_sep, 2), "direction": round(r.direction)} for r in sw.runs]}


def streamed(job: Callable[[Callable[[dict], None]], dict]) -> StreamingResponse:
    """Run `job(emit)` in a thread; stream its events and then its result as NDJSON."""
    q: queue.Queue[dict | None] = queue.Queue()

    def worker() -> None:
        try:
            q.put({"type": "result", **job(q.put)})
        except Exception as e:  # shown in the page's log
            q.put({"type": "error", "msg": f"{type(e).__name__}: {e}"})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def events() -> Iterator[str]:
        while (ev := q.get()) is not None:
            yield json.dumps(ev, separators=(",", ":"), default=float) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


def create_app(media_dir: Path | None = None, llm_factory: Callable[[], LLM] = LLM) -> FastAPI:
    app = FastAPI(title="Constellate")
    media = Path(media_dir or tempfile.mkdtemp(prefix="constellate-media-"))
    media.mkdir(parents=True, exist_ok=True)
    videos: dict[str, dict] = {}
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/config")
    def config() -> dict:
        import os

        return {"llm": bool(os.environ.get("NEBIUS_API_KEY")), "tavily": bool(os.environ.get("TAVILY_API_KEY")),
                "limits": Limits().model_dump()}

    @app.get("/player.js")
    def player_js() -> FileResponse:  # motionlang's own player, so the app and `motionlang preview` never drift
        return FileResponse(player_js_path(), media_type="text/javascript")

    @app.get("/api/examples")
    def examples() -> list[dict]:
        out = []
        for p in sorted(EXAMPLES.glob("*.show.json")):
            out.append({"name": p.name.removesuffix(".show.json"), "title": json.loads(p.read_text()).get("title", p.name)})
        return out

    @app.get("/api/examples/{name}")
    def example(name: str) -> dict:
        p = EXAMPLES / f"{name}.show.json"
        if not p.is_file() or p.parent != EXAMPLES:
            raise HTTPException(404)
        return load_show(p).model_dump(mode="json")

    @app.get("/api/samples/{name}")
    def sample_svg(name: str) -> FileResponse:
        p = SAMPLES / name
        if p.suffix != ".svg" or not p.is_file() or p.parent != SAMPLES:
            raise HTTPException(404)
        return FileResponse(p, media_type="image/svg+xml")

    @app.post("/api/check")
    def check(req: ShowReq) -> dict:
        return checked_json(run(_show(req.show)))

    @app.post("/api/plan")
    def plan(req: PlanReq):
        base = _show({"formations": {"x": {"kind": "preset", "shape": "ring"}}, "steps": [{"kind": "form", "id": "x", "formation": "x"}],
                      "drones": req.drones, "sources": req.sources, "limits": {**Limits().model_dump(), **req.limits}})

        def job(emit):
            show = agent.plan(req.intent, base, llm_factory(), emit)
            return checked_json(run(show))
        return streamed(job)

    @app.post("/api/tweak")
    def tweak(req: TweakReq):
        show = _show(req.show)

        def job(emit):
            new, ops, note = agent.tweak(show, req.request, llm_factory(), emit)
            emit({"type": "repair", "msg": note or f"{len(ops)} edits", "ops": ops})
            return {**checked_json(run(new)), "ops": ops, "note": note}
        return streamed(job)

    @app.post("/api/fly")
    def fly(req: ShowReq):
        show = _show(req.show)

        def job(emit):
            out = agent.make_flyable(show, llm_factory(), emit)
            res = {**checked_json(out.checked), "flyable": out.ok, "rounds": len(out.rounds)}
            if out.sweep:
                res["sweep"] = sweep_json(out.sweep)
            return res
        return streamed(job)

    @app.post("/api/sweep")
    def sweep_only(req: ShowReq) -> dict:
        from .sweep import sim_issues, sweep

        show = _show(req.show)
        c = run(show)
        if not c.flight.ok:
            raise HTTPException(409, detail="fix the flight check first")
        sw = sweep(show, c.compiled, c.baked)
        return {**sweep_json(sw), "issues": [i.to_dict() for i in sim_issues(show, c.compiled, c.baked, sw)]}

    @app.post("/api/brief")
    def brief(req: BriefReq):
        from .briefing import brief as do_brief

        def job(emit):
            b = do_brief(req.venue, req.date, llm_factory(), emit)
            return {"briefing": b.to_dict()}
        return streamed(job)

    @app.post("/api/video")
    def video(req: ShowReq) -> dict:
        show = _show(req.show)
        vid = uuid.uuid4().hex[:12]
        videos[vid] = {"status": "rendering"}

        def work() -> None:
            from .sim import Wind, simulate
            from .video import baked_colors, write_mp4

            try:
                c = run(show)
                times, plan = tracks_m(c.baked, show)
                wind = Wind(speed=show.limits.design_wind, direction_deg=60)
                r = simulate(times, plan, show.limits.max_flight_time, wind, seed=0, alert=show.limits.near_miss)
                out = write_mp4(media / f"{vid}.mp4", r.pos, baked_colors(c.baked, show.drones), show.limits)
                videos[vid] = {"status": "done", "url": f"/media/{out.name}", "wind": wind.speed}
            except Exception as e:
                videos[vid] = {"status": "error", "msg": f"{type(e).__name__}: {e}"}
        threading.Thread(target=work, daemon=True).start()
        return {"id": vid}

    @app.get("/api/video/{vid}")
    def video_status(vid: str) -> dict:
        if vid not in videos:
            raise HTTPException(404)
        return videos[vid]

    @app.get("/media/{name}")
    def media_file(name: str) -> FileResponse:
        p = media / name
        if not p.is_file() or p.parent != media:
            raise HTTPException(404)
        return FileResponse(p)

    return app


def serve(host: str = "127.0.0.1", port: int = PORT, open_browser: bool = True) -> None:
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    print(f"Constellate on {url}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
