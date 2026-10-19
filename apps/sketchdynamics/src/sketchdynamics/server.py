"""SketchDynamics web server: sketch + intent → MotionLang document → baked tracks for the browser player.

POST /generate    {intent, strokes, canvas, assets?, actors?}   Nemotron Ultra plans the document, the validator drives repairs
POST /tweak       {doc, instruction}          Nemotron Nano edits it with id-addressed ops (escalates if needed)
POST /bake        {doc}                       validate + bake only, no model (e.g. after redrawing a stroke)
POST /import-svg  {svg}                       a user's logo → MotionLang assets + suggested actors + a still preview
POST /label-parts {image, assets, groups?}    Nemotron VL names each logo part and groups parts that move as one
POST /regroup     {assets, groups, actors?}   a grouping (VL's or the user's) → actor tree + still preview
POST /shapes/*    objects without a shape → library / web (Tavily) / Nemotron-drawn SVGs; see shapes.py

/generate and /tweak take `?stream=1` and then answer with NDJSON events: `call_start` when a model call
begins, `attempt` once its reply has been validated, then the final `result`. The side panel fills in live.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import queue
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from motionlang import check
from motionlang.agent import LLM, Attempt, Call, Outcome, generate, load_env, tweak
from motionlang.agent.llm import extract_json_text
from motionlang.importers import import_svg
from motionlang.preview import player_js_path, preview_html

from .guard import Guard
from .grouping import build_actors, groups_from_actors, normalize_groups, part_opacity, split_part
from .shapes import Candidate, Library, WebSearch, clean_svg, draw_messages, judge_messages, needed_messages, parse_objects, parse_scores, placed_parts

EDITOR_KINDS = {"edit", "edit-repair"}  # every other attempt kind (generate, repair, repair-edit, regenerate) is the planner's

# The user picks the planner per request. Super-120B (with structured output, which generate() turns on for it)
# is cheaper and was the most reliable on logos; Ultra-550B is the stronger fallback when a plan fails.
PLANNERS = {"super": "nvidia/nemotron-3-super-120b-a12b", "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b"}
DEFAULT_PLANNER = os.environ.get("SKETCHDYNAMICS_PLANNER", "super")
Planner = Literal["super", "ultra"]


def planner_models(choice: str | None) -> dict[str, str]:
    return {"planner": PLANNERS.get(choice or DEFAULT_PLANNER, PLANNERS["super"])}


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
    assets: dict[str, dict[str, Any]] = {}  # from /import-svg, labels possibly filled in by /label-parts
    actors: list[dict[str, Any]] = []  # the importer's suggested actors
    planner: Planner | None = None


class TweakRequest(BaseModel):
    doc: dict[str, Any]
    instruction: str = Field(min_length=1)
    planner: Planner | None = None  # used if the fast edit escalates to a regenerate


class SplitRequest(BaseModel):
    assets: dict[str, dict[str, Any]]
    groups: list[dict[str, Any]] = []
    actors: list[dict[str, Any]] = []
    part: str
    canvas: Canvas = Canvas()


class BakeRequest(BaseModel):
    doc: dict[str, Any]


class ExportRequest(BaseModel):
    doc: dict[str, Any]
    title: str = Field("SketchDynamics animation", max_length=200)


class ImportRequest(BaseModel):
    svg: str = Field(min_length=1, max_length=2_000_000)
    canvas: Canvas = Canvas()


class LabelRequest(BaseModel):
    image: str = Field(pattern=r"^data:image/(png|jpeg);base64,")  # the logo as the user sees it
    marked: str | None = Field(None, pattern=r"^data:image/(png|jpeg);base64,")  # same, part n tagged "n" (set-of-marks)
    name: str = Field("", max_length=200)  # the file name: often says what the artwork is ("wind-energy")
    assets: dict[str, dict[str, Any]]
    groups: list[dict[str, Any]] = []  # the current grouping, shown to the model as a starting point


class RegroupRequest(BaseModel):
    assets: dict[str, dict[str, Any]] = Field(min_length=1)
    groups: list[dict[str, Any]] = []
    actors: list[dict[str, Any]] = []  # the current actors, to keep imported part opacities
    canvas: Canvas = Canvas()


class NeededRequest(BaseModel):
    intent: str = Field(min_length=1, max_length=2000)
    have: list[str] = Field([], max_length=200)  # names of what's already on the canvas


ShapeName = Annotated[str, Field(min_length=1, max_length=40)]


class FindRequest(BaseModel):
    name: ShapeName


class JudgeRequest(BaseModel):
    name: ShapeName
    image: str = Field(pattern=r"^data:image/(png|jpeg);base64,")  # the candidates in a numbered grid
    count: int = Field(ge=1, le=12)


class DrawRequest(BaseModel):
    name: ShapeName
    feedback: str = Field("", max_length=500)  # the VL reviewer's note on the previous drawing
    previous: str = Field("", max_length=50_000)


class PlaceRequest(BaseModel):
    name: ShapeName
    svg: str = Field(min_length=1, max_length=500_000)
    assets: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    actors: list[dict[str, Any]] = []
    canvas: Canvas = Canvas()


class SaveRequest(BaseModel):
    name: ShapeName
    svg: str = Field(min_length=1, max_length=500_000)
    source: str = Field("nemotron", max_length=20)
    page_url: str = Field("", max_length=500)
    license: str = Field("", max_length=80)
    credit: str = Field("", max_length=80)


MAX_LABELLED_PARTS = 60


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
        "role": "editor" if a.kind in EDITOR_KINDS else "planner",
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


def closest_playable(out: Outcome) -> dict[str, Any]:
    """The valid document, else the attempt with the fewest problems that still bakes.

    Outcome.doc falls back to the *last* parseable document, which after a bad repair can be one that
    doesn't even parse, while an earlier attempt was one tweak away and would have played fine."""
    if out.ok:
        return bake_json(out.doc)
    tried = [a for a in out.attempts if a.doc is not None]
    for a in sorted(tried, key=lambda a: (len(a.problems()), -out.attempts.index(a))):
        baked = bake_json(a.doc)
        if baked["tracks"] is not None:
            return baked
    return bake_json(out.doc)


def outcome_json(out: Outcome) -> dict[str, Any]:
    tokens = sum(a.call.prompt_tokens + a.call.completion_tokens for a in out.attempts)
    return {
        "ok": out.ok,
        "path": out.path,
        "note": out.note,
        "seconds": round(out.seconds, 3),
        "tokens": tokens,
        "attempts": [attempt_json(a) for a in out.attempts],
        **closest_playable(out),
    }


class AnnouncingLLM(LLM):
    """LLM that announces each call as it starts, so the panel can show a running card.
    Verdicts come from the loop's on_attempt hook once the reply has been validated."""

    def __init__(self, inner: LLM, emit: Callable[[dict[str, Any]], None]):
        self.__dict__.update(inner.__dict__)  # same backend, model overrides and call log as the wrapped LLM
        self._emit = emit

    def json(self, role: str, purpose: str, messages: list[dict[str, Any]], **kw: Any) -> tuple[str, Call]:
        self._emit({"type": "call_start", "role": role, "purpose": purpose, "model": self.model_for(role), "think": kw.get("think", True)})
        return super().json(role, purpose, messages, **kw)


def still_doc(imported: dict[str, Any], canvas: dict[str, Any]) -> dict[str, Any]:
    """The imported logo as a valid one-frame document, to show it before anything is generated."""
    return {
        "motion": "0.1", "canvas": {**canvas, **imported["canvas"]}, "assets": imported["assets"], "actors": imported["actors"],
        "timeline": {"id": "root", "seq": [{"id": "still", "verb": "hold", "duration": 0.1}]},
    }


def grouped_logo(assets: dict[str, Any], groups: list[dict[str, Any]], canvas: dict[str, Any], opacity: dict[str, float]) -> dict[str, Any]:
    groups = normalize_groups(groups, assets)
    actors = build_actors(assets, groups, opacity)
    still = {"canvas": {k: canvas[k] for k in ("width", "height")}, "assets": assets, "actors": actors}
    return {"groups": groups, "actors": actors, "preview": bake_json(still_doc(still, canvas))}


def label_messages(
    image: str, assets: dict[str, dict[str, Any]], groups: list[dict[str, Any]] | None = None,
    marked: str | None = None, name: str = "",
) -> list[dict[str, Any]]:
    parts = []
    for n, (aid, a) in enumerate(list(assets.items())[:MAX_LABELLED_PARTS], 1):
        x, y, w, h = (round(v) for v in a.get("bbox", (0, 0, 0, 0)))
        colour = a.get("fill") or a.get("stroke") or "none"
        named = a.get("label") if a.get("label") not in (None, aid, (a.get("meta") or {}).get("source")) else None
        parts.append(f'- {"#" + str(n) + " " if marked else ""}"{aid}": bbox x={x} y={y} w={w} h={h}, colour {colour}'
                     + (f', named "{named}" in the file' if named else ""))
    text = (
        ("The first image is a logo on a canvas (coordinates in pixels of the image, origin top-left). The second image "
         "is the same logo with each part tagged by its number #n, placed on that part's outline.\n" if marked else
         "This image is a logo on a canvas (coordinates in pixels of the image, origin top-left).\n")
        + (f'The file is called "{name}".\n' if name else "")
        + "It is made of these vector parts:\n" + "\n".join(parts) + "\n\n"
        "1. Say what each part is, as a motion designer would name layers: 1-4 words, e.g. \"front wheel\", "
        "\"letter A\", \"leaf outline\".\n"
        "2. Group parts into the LARGEST pieces that move rigidly together, the way an animator would rig them: "
        "a car body with its windows; all letters of one word; all blades of a propeller together WITH its hub "
        "(one piece, not one group per blade); a leaf with its stem. A part's fill, outline and highlight always go "
        "together. Each part goes in at most one group, a group has at least 2 parts, and parts that move on their "
        "own stay ungrouped. Parts that move differently (a spinning propeller vs the plane it is on) go in different groups.\n"
        "3. If a group would naturally rotate (a propeller, wheel, gear, clock hand), give \"pivot\": the id of the part at "
        "its rotation centre (e.g. the hub or axle).\n"
        "4. Suggest 3 animations a motion designer would make for this artwork, as requests a user could type "
        "(15-30 words each): one subtle and professional, one playful, one dramatic. Each is a short sequence of 2-4 "
        "beats that builds the artwork up or shows what it does, ending on the finished logo, e.g. \"the stem draws itself "
        "upward, the leaf unfolds from it with a small bounce, then the wordmark slides in from the right\". Refer to "
        "parts and groups by what they are (\"the leaf\", \"the wordmark\"), never by id. Use only what 2D motion can do: move "
        "in or out, rotate, scale, fade, bounce, wiggle, draw a line on, wipe a shape in, recolor.\n"
        + (f"The file's own layers group them as {json.dumps([g['parts'] for g in groups])}; layers often follow colour, "
           "not objects, so regroup freely.\n" if groups else "")
        + "Reply with JSON only: "
        '{"description": "one sentence about the whole logo", "labels": {"<part id>": "<label>", ...}, '
        '"groups": [{"label": "<what the group is>", "parts": ["<part id>", ...], "pivot": "<part id, optional>"}, ...], '
        '"suggestions": ["<request>", "<request>", "<request>"]}'
    )
    images = [image, marked] if marked else [image]
    return [{"role": "user", "content": [*({"type": "image_url", "image_url": {"url": u}} for u in images), {"type": "text", "text": text}]}]


def spent(llm: LLM) -> int:
    return sum(c.prompt_tokens + c.completion_tokens for c in llm.calls)


_FROM_ENV: Any = object()


def create_app(
    llm_factory: Callable[[], LLM] | None = None, guard: Guard | None = None,
    web: WebSearch | None = _FROM_ENV, library: Library | None = None,
) -> FastAPI:
    """`llm_factory` builds a fresh LLM per request; tests pass one with a scripted backend.
    `guard` limits model calls for a hosted copy (default: from SKETCHDYNAMICS_* env vars, all off).
    `web` finds SVGs online (default: Tavily when TAVILY_API_KEY is set); `library` is where shapes are kept
    (default: static/samples, writable unless SKETCHDYNAMICS_SAVE_SHAPES=0)."""
    make_llm = llm_factory or LLM
    guard = guard or Guard.from_env()
    web = WebSearch.from_env() if web is _FROM_ENV else web
    static = resources.files("sketchdynamics") / "static"
    library = library or Library(Path(str(static / "samples")),
                                 writable=os.environ.get("SKETCHDYNAMICS_SAVE_SHAPES", "1") not in ("0", "false", "no"))
    app = FastAPI(title="SketchDynamics")
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.middleware("http")
    async def headers(request, call_next):
        resp = await call_next(request)
        if request.method == "GET":  # a local tool: always serve the current UI after an upgrade
            resp.headers["Cache-Control"] = "no-cache"
        # A hosted copy is meant to be "unlisted": reachable by link, never indexed. Covers what the page's
        # <meta name="robots"> can't (scripts, samples, exports).
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        return resp

    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> FileResponse:  # crawlers only look at the site root
        return FileResponse(str(static / "robots.txt"), media_type="text/plain")

    Job = Callable[[LLM, Callable[[Attempt], None] | None], Outcome]

    def llm_for(models: dict[str, str]) -> LLM:
        llm = make_llm()
        for role, model in models.items():
            llm.models.setdefault(role, model)  # an LLM the caller already configured wins
        return llm

    def run(job: Job, models: dict[str, str]) -> dict[str, Any]:
        llm = llm_for(models)
        try:
            return outcome_json(job(llm, None))
        except Exception as e:  # missing key, network, rate limits: show it in the UI, don't 500 blindly
            raise HTTPException(502, detail=f"{type(e).__name__}: {e}") from e
        finally:
            guard.spend(spent(llm))

    def streamed(job: Job, models: dict[str, str]) -> StreamingResponse:
        q: queue.Queue[dict[str, Any] | None] = queue.Queue()

        llm = AnnouncingLLM(llm_for(models), q.put)

        def worker() -> None:
            try:
                on_attempt = lambda a: q.put({"type": "attempt", **attempt_json(a)})  # noqa: E731
                q.put({"type": "result", **outcome_json(job(llm, on_attempt))})
            except Exception as e:
                q.put({"type": "error", "detail": f"{type(e).__name__}: {e}"})
            finally:
                guard.spend(spent(llm))
            q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def events() -> Iterator[str]:
            while (ev := q.get()) is not None:
                yield json.dumps(ev, separators=(",", ":")) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static / "index.html").read_text()

    @app.get("/config")
    def get_config() -> dict[str, Any]:
        """What the UI needs to know about this deployment's limits."""
        return {**guard.status(), "web_search": web is not None, "save_shapes": library.writable}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/player.js")
    def player_js() -> FileResponse:  # motionlang's own player, so the app and `motionlang preview` never drift
        return FileResponse(player_js_path(), media_type="text/javascript")

    @app.post("/generate")
    def post_generate(req: GenerateRequest, request: Request, stream: bool = False):
        guard.check(request)
        strokes = {k: {"points": [list(p) for p in s.points]} for k, s in req.strokes.items()}
        job = lambda llm, on_attempt: generate(  # noqa: E731
            req.intent, canvas=req.canvas.model_dump(), strokes=strokes or None, assets=req.assets or None,
            suggested_actors=req.actors or None, llm=llm, on_attempt=on_attempt,
        )
        models = planner_models(req.planner)
        return streamed(job, models) if stream else JSONResponse(run(job, models))

    @app.post("/tweak")
    def post_tweak(req: TweakRequest, request: Request, stream: bool = False):
        guard.check(request)
        job = lambda llm, on_attempt: tweak(req.doc, req.instruction, llm=llm, on_attempt=on_attempt)  # noqa: E731
        models = planner_models(req.planner)
        return streamed(job, models) if stream else JSONResponse(run(job, models))

    @app.post("/bake")
    def post_bake(req: BakeRequest):
        return JSONResponse(bake_json(req.doc))

    @app.post("/export.html")
    def post_export_html(req: ExportRequest):
        """A self-contained HTML player (motionlang's own) for the animation: open it anywhere, no server."""
        res = check(req.doc)
        if res.baked is None:
            raise HTTPException(422, detail="this document can't be played: " + res.report.summary()[:300])
        slug = re.sub(r"[^a-z0-9]+", "-", req.title.lower()).strip("-")[:60] or "animation"
        return Response(preview_html(res.baked.to_json(), title=req.title), media_type="text/html",
                        headers={"Content-Disposition": f'attachment; filename="{slug}.html"'})

    @app.post("/import-svg")
    def post_import_svg(req: ImportRequest):
        canvas = req.canvas.model_dump()
        try:
            imported = import_svg(req.svg, fit=(canvas["width"], canvas["height"]))
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e
        if not imported["assets"]:
            raise HTTPException(422, detail="no drawable shapes found in that SVG (text and images aren't imported yet)")
        # Start from the file's own <g> groups, flattened to one level the user (or VL) can edit.
        groups = groups_from_actors(imported["actors"], imported["assets"])
        return JSONResponse({**imported, **grouped_logo(imported["assets"], groups, canvas, part_opacity(imported["actors"]))})

    @app.post("/regroup")
    def post_regroup(req: RegroupRequest):
        return JSONResponse(grouped_logo(req.assets, req.groups, req.canvas.model_dump(), part_opacity(req.actors)))

    @app.post("/split-part")
    def post_split_part(req: SplitRequest):
        if req.part not in req.assets:
            raise HTTPException(422, detail=f"unknown part {req.part!r}")
        try:
            assets, groups, pieces = split_part(req.assets, req.groups, req.part)
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e
        opacity = part_opacity(req.actors)
        if req.part in opacity:
            opacity.update({p: opacity[req.part] for p in pieces})
        return JSONResponse({"assets": assets, "pieces": pieces, **grouped_logo(assets, groups, req.canvas.model_dump(), opacity)})

    @app.post("/label-parts")
    def post_label_parts(req: LabelRequest, request: Request):
        guard.check(request)
        llm = make_llm()
        try:
            text, call = llm.json("vision", "label", label_messages(req.image, req.assets, req.groups, req.marked, req.name), think=False, max_tokens=2000, temperature=0.2)
            guard.spend(spent(llm))
            reply = json.loads(extract_json_text(text))
        except Exception as e:  # labels are a nice-to-have: generation works without them
            return JSONResponse({"labels": {}, "groups": None, "suggestions": [], "description": "", "error": f"{type(e).__name__}: {e}",
                                 "call": call_json(llm.calls[-1]) if llm.calls else None})
        reply = reply if isinstance(reply, dict) else {}
        ids = list(req.assets)[:MAX_LABELLED_PARTS]

        def part_id(ref: Any) -> Any:  # with the marked image, models often answer "#3" or 3 instead of the id
            m = re.fullmatch(r"#?\s*(\d+)", str(ref).strip())
            if str(ref) not in req.assets and m and 1 <= int(m.group(1)) <= len(ids):
                return ids[int(m.group(1)) - 1]
            return ref

        raw = reply.get("labels") if isinstance(reply.get("labels"), dict) else {}
        labels = {part_id(k): str(v)[:60] for k, v in raw.items() if part_id(k) in req.assets and isinstance(v, str) and v.strip()}
        # None = the model offered no grouping (keep the current one); [] = it says nothing moves together
        groups = None
        if isinstance(reply.get("groups"), list):
            fixed = [{**g, "parts": [part_id(p) for p in g.get("parts") or []], "pivot": part_id(g["pivot"]) if g.get("pivot") is not None else None}
                     for g in reply["groups"] if isinstance(g, dict)]
            groups = normalize_groups(fixed, req.assets)
        raw_ideas = reply.get("suggestions") if isinstance(reply.get("suggestions"), list) else []
        suggestions = [" ".join(x.split())[:240] for x in raw_ideas if isinstance(x, str) and x.strip()][:3]
        return JSONResponse({"labels": labels, "groups": groups, "suggestions": suggestions,
                             "description": str(reply.get("description", ""))[:300], "call": call_json(call)})

    # ---------- shapes for objects the planner can't draw ----------

    def model_call(role: str, purpose: str, messages: list[dict[str, Any]], **kw: Any) -> tuple[str, Call]:
        llm = llm_for(planner_models(None)) if role == "planner" else make_llm()
        try:
            return llm.json(role, purpose, messages, **kw)
        except Exception as e:
            raise HTTPException(502, detail=f"{type(e).__name__}: {e}") from e
        finally:
            guard.spend(spent(llm))

    @app.post("/shapes/needed")
    def post_shapes_needed(req: NeededRequest, request: Request):
        """Nano lists the objects a request shows that have no shape yet ("a crystal falls" → ["crystal"])."""
        guard.check(request)
        text, call = model_call("editor", "objects", needed_messages(req.intent, req.have), think=False, max_tokens=300, temperature=0.1)
        try:
            objects = parse_objects(extract_json_text(text), req.intent)
        except ValueError:
            objects = []
        have = {h.lower() for h in req.have}
        return {"objects": [o for o in objects if o not in have], "call": call_json(call)}

    @app.post("/shapes/find")
    def post_shapes_find(req: FindRequest, request: Request):
        """The library first; the web (Tavily) only when the library has nothing for this name."""
        found = library.find(req.name)
        if found:
            return {"candidates": [c.to_json() for c in found], "searched": "library", "web": None}
        if web is None:
            return {"candidates": [], "searched": "library", "web": None,
                    "note": "web search is off (no TAVILY_API_KEY): Nemotron can draw one"}
        guard.check(request)
        t0 = time.monotonic()
        try:
            cands, stats = web.search(req.name)
        except Exception as e:  # the web is optional: drawing still works
            return {"candidates": [], "searched": "web", "web": None, "note": f"web search failed ({type(e).__name__}): Nemotron can draw one"}
        return {"candidates": [c.to_json() for c in cands], "searched": "web",
                "web": {**stats, "seconds": round(time.monotonic() - t0, 2), "domains": ["svgrepo.com", "commons.wikimedia.org"]}}

    @app.post("/shapes/judge")
    def post_shapes_judge(req: JudgeRequest, request: Request):
        """Nemotron VL scores the candidates (shown as one numbered grid) for how well they read as the object."""
        guard.check(request)
        text, call = model_call("vision", "judge", judge_messages(req.name, req.image, req.count), think=False, max_tokens=800, temperature=0.1)
        try:
            scores = parse_scores(extract_json_text(text), req.count)
        except ValueError:
            scores = parse_scores("{}", req.count)
        return {"scores": scores, "call": call_json(call)}

    @app.post("/shapes/draw")
    def post_shapes_draw(req: DrawRequest, request: Request):
        """The planner model writes a flat SVG icon; if the importer can't use it, it gets one retry with the reason."""
        guard.check(request)
        calls, error, svg = [], "", ""
        for _ in range(2):
            text, call = model_call("planner", "draw", draw_messages(req.name, req.feedback, req.previous, error), think=False, max_tokens=6000, temperature=0.4)
            calls.append(call_json(call))
            try:
                raw = json.loads(extract_json_text(text))
                raw = raw.get("svg", "") if isinstance(raw, dict) else ""
                svg, parts = clean_svg(raw if isinstance(raw, str) else "")
                break
            except ValueError as e:
                error, svg = str(e)[:300], ""
        if not svg:
            return {"candidate": None, "calls": calls, "error": f"the drawing couldn't be used: {error}"}
        cand = Candidate(f"draw:{len(calls)}", req.name, req.name, "nemotron", svg, parts,
                         license="Apache-2.0", license_verified=True, credit="Nemotron")
        return {"candidate": cand.to_json(), "calls": calls}

    @app.post("/shapes/place")
    def post_shapes_place(req: PlaceRequest):
        """Add the chosen shape to the scene as parts (one rigid group if it has several), beside what's there."""
        canvas = req.canvas.model_dump()
        try:
            parts, opacity, group = placed_parts(req.svg, req.name, req.assets, (canvas["width"], canvas["height"]))
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e
        assets = {**req.assets, **parts}
        groups = [*req.groups, *([group] if group else [])]
        logo = grouped_logo(assets, groups, canvas, {**part_opacity(req.actors), **opacity})
        return JSONResponse({"assets": assets, "added": list(parts), **logo})

    @app.post("/shapes/save")
    def post_shapes_save(req: SaveRequest):
        try:
            file = library.save(req.name, req.svg, license=req.license, source=req.source, page_url=req.page_url, credit=req.credit)
        except PermissionError as e:
            raise HTTPException(403, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e
        return {"file": file}

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
