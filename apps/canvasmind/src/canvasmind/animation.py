"""Animate an SVG from the library with MotionLang, in the user's style, and refine animations by chat.

Only vector artwork is animated: an SVG splits into parts that can move on their own, a photo doesn't.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field

from motionlang.agent.llm import LLM, Call, extract_json_text
from PIL import ImageDraw, ImageFont

from . import media, models, style
from .library import Item

CANVAS = {"width": 720, "height": 720, "fps": 30, "background": "#ffffff"}  # square: logos, and phones
GIF_WIDTH = 480
GIF_FPS = 20
PLAN_TOKENS = 6000  # cap per planner call (see _animate)

LABEL_PROMPT = """Image 1 is a vector artwork a motion designer wants to animate; image 2 is the same artwork with each part
numbered. Parts: {parts}.
1. Name every part by what it is ("cup body", "steam wisp", "left ray"), 1-3 words, keyed by part id.
2. Suggest one animation for it that a motion designer would make (15-30 words, 2-4 beats, ending on the finished
artwork), referring to parts by name, using only 2D motion: move in or out, rotate, scale, fade, bounce, wiggle, draw a
line on, wipe a shape in.
Reply with JSON only: {{"labels": {{"<part id>": "<name>"}}, "idea": "..."}}"""


class NotAnimatable(ValueError):
    pass


@dataclass
class Animated:
    item: Item
    gif: bytes
    ok: bool
    why: str = ""
    note: str = ""
    calls: list[Call] = field(default_factory=list)


def _numbered(imported: dict) -> tuple[bytes, bytes]:
    """The artwork, and the same with each part's number at its centre (for the vision model)."""
    doc = media.svg_doc_from_imported(imported, CANVAS)
    img = media._render(doc, 1)[0]
    clean = media.jpeg(img)
    scale = img.width / CANVAS["width"]
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    for n, a in enumerate(imported["assets"].values(), 1):
        x, y, w, h = a.get("bbox") or (0, 0, 0, 0)
        cx, cy = (x + w / 2) * scale, (y + h / 2) * scale
        draw.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill="#e11d48")
        draw.text((cx, cy), str(n), fill="white", anchor="mm", font=font)
    return clean, media.jpeg(img)


def label_parts(llm: LLM, imported: dict) -> tuple[dict, str, Call]:
    ids = list(imported["assets"])
    clean, marked = _numbered(imported)
    parts = ", ".join(f"{n} = {aid}" for n, aid in enumerate(ids, 1))
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": media.data_url(clean)}},
        {"type": "image_url", "image_url": {"url": media.data_url(marked)}},
        {"type": "text", "text": LABEL_PROMPT.format(parts=parts)}]}]
    text, call = llm.json("vision", "label parts", msgs, think=False, max_tokens=1500, temperature=0.2)
    d = json.loads(extract_json_text(text))
    by_num = {str(n): aid for n, aid in enumerate(ids, 1)}
    labels = {}
    for k, v in (d.get("labels") or {}).items():
        aid = k if k in imported["assets"] else by_num.get(str(k))
        if aid and isinstance(v, str) and v.strip():
            labels[aid] = v.strip()[:40]
    return labels, str(d.get("idea", "")).strip(), call


def _gif_and_stills(doc: dict) -> tuple[bytes, bytes, bytes]:
    from motionlang import check
    from motionlang.render import to_frames, to_gif

    tracks = check(doc).baked.to_json()
    gif = to_gif(tracks, fps=GIF_FPS, width=GIF_WIDTH)
    frames = to_frames(tracks, fps=4, width=360)
    picks = [frames[round(i * (len(frames) - 1) / 3)] for i in range(4)] if len(frames) >= 4 else frames
    strip = media._strip(picks)
    thumb = media.fit(frames[-1], media.THUMB_PX)
    return gif, media.jpeg(strip), media.jpeg(thumb, quality=80)


def _store(studio, doc: dict, *, title: str, brief: str, source: str, parent: str, version: int) -> Item:
    gif, strip, thumb = _gif_and_stills(doc)
    data = json.dumps(doc, ensure_ascii=False, indent=1).encode()
    facts = media.motion_facts(doc) | {"parent": parent, "version": version, "brief": brief[:300]}
    tags = ["animation", *facts["verbs"], *facts["easings"]]
    item = Item(id=studio.lib.new_id(), kind="motion", filename=f"{title.lower().replace(' ', '_')[:40] or 'animation'}.motion.json",
                source=source, created=time.time(), title=title[:80], caption=brief[:500], tags=tags, facts=facts,
                sha256=studio.lib.sha(data))
    return studio.lib.add(item, data, strip, thumb, extra={"animation.gif": gif})


def animate(studio, item_id: str, brief: str = "", *, in_my_style: bool = True) -> Animated:
    from motionlang.importers import import_svg

    if not item_id:  # from words alone: the planner draws basic shapes and text itself
        if not brief.strip():
            raise NotAnimatable("say what should happen, or give an SVG item to animate")
        return _animate(studio, brief.strip(), {}, None, title=brief.strip()[:40], parent=None, in_my_style=in_my_style)
    src = studio.lib.get(item_id)
    if src.kind == "motion":
        raise NotAnimatable(f"{item_id} is already an animation; use tweak to change it")
    if src.kind != "svg":
        raise NotAnimatable(f"{item_id} is a {src.kind}, and only SVG artwork can be animated (its parts move on their own). "
                            "Ask the user for the SVG file or a link to it.")
    svg = studio.lib.original(item_id).read_text()
    imported = import_svg(svg, fit=(CANVAS["width"], CANVAS["height"]), margin=0.12)
    llm = studio.make_llm()
    calls: list[Call] = []
    try:
        labels, idea, call = label_parts(llm, imported)
        calls.append(call)
        for aid, label in labels.items():
            imported["assets"][aid]["label"] = label
    except Exception:  # labels help the planner; ids alone still work
        idea = ""
    brief = brief.strip() or idea or "its parts arrive one by one and settle into the finished artwork"
    return _animate(studio, brief, imported, llm, title=f"{src.title} animation", parent=item_id, in_my_style=in_my_style,
                    calls=calls)


def _animate(studio, brief: str, imported: dict, llm, *, title: str, parent: str | None, in_my_style: bool,
             calls: list | None = None) -> Animated:
    from motionlang.agent.loop import generate

    llm = llm or studio.make_llm()
    calls = list(calls or [])
    t0 = time.time()
    prof = studio.style.profile()
    hint = style.summary(prof) if in_my_style else ""
    intent = brief + (f"\n\nThe user's motion style (follow it unless the request says otherwise): {hint}" if hint else "")
    # Structured output (Super's default) with a 6000-token cap: under the schema grammar Super sometimes writes a
    # number that never ends ("by": 360.0000000000000600830…); the cap stops it after ~30 s and motionlang finishes in
    # JSON mode. Valid Super documents use 2-5k tokens. Measured Oct 22: 4/4 valid in 29-48 s (JSON mode alone: 2/4).
    out = generate(intent, canvas=CANVAS, assets=imported.get("assets"), suggested_actors=imported.get("actors"), llm=llm,
                   max_repairs=2, max_tokens=PLAN_TOKENS)
    calls += [a.call for a in out.attempts]
    best = out.doc if out.ok else (out.best.doc if out.best else None)
    if best is not None and not imported.get("assets"):  # from words alone: check the planner drew everything that was asked for
        try:
            missing, call = models.missing_objects(llm, brief, best)
            calls.append(call)
        except Exception:
            missing = []
        if missing and time.time() - t0 < 45:  # one more plan only while it fits in Hermes's 120 s tool timeout
            again = generate(intent + "\n\nThe animation must show every object the request names, including: "
                             + "; ".join(missing) + ".", canvas=CANVAS, llm=llm, max_repairs=0, max_tokens=PLAN_TOKENS)
            calls += [a.call for a in again.attempts]
            if again.ok:
                out, best, missing = again, again.doc, []
        missed = missing
    else:
        missed = []
    if best is None or _no_bake(best):
        raise RuntimeError("the planner couldn't produce a playable animation; " + "; ".join(out.final.problems()[:2] if out.final else []))
    item = _store(studio, best, title=title, brief=brief, source="animate", parent=parent, version=1)
    note = "" if out.ok else "closest playable attempt; the validator still flags: " + "; ".join(out.final.problems()[:2])
    if missed:
        note = (note + "; " if note else "") + "this may be missing: " + ", ".join(missed) + " (offer to add it with tweak)"
    return Animated(item, studio.lib.gif(item.id), out.ok, why=style.explain(item.facts, prof) if hint else "",
                    note=note, calls=calls)


_TEMPO = re.compile(r"^\s*(?:(?:make|play)\s+(?:it|this)\s+)?(?P<amount>a (?:bit|little)|slightly|much|way|a lot)?\s*"
                    r"(?P<dir>slower|faster|slow (?:it )?down|speed (?:it )?up)(?: please)?[\s.!]*$", re.I)
_TIME_FIELDS = ("duration", "delay", "offset")  # block length, start delay, stagger spacing (SPEC §4)


def tempo_factor(request: str) -> float | None:
    """How much to stretch time for a pure speed request ("slower", "a bit faster"); None for anything else."""
    m = _TEMPO.match(request)
    if not m:
        return None
    base = 1.5 if m["dir"].lower().startswith(("slow",)) else 1 / 1.5
    amount = (m["amount"] or "").lower()
    power = 0.55 if amount in ("a bit", "a little", "slightly") else 1.7 if amount in ("much", "way", "a lot") else 1.0
    return round(base ** power, 3)


def retime(doc: dict, factor: float) -> dict:
    """The same animation with every block duration, delay and stagger offset scaled by `factor`."""
    out = copy.deepcopy(doc)

    def walk(node):
        if isinstance(node, dict):
            for k in _TIME_FIELDS:
                if isinstance(node.get(k), (int, float)) and not isinstance(node.get(k), bool):
                    node[k] = round(node[k] * factor, 3)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(out.get("timeline"))
    return out


def tweak(studio, item_id: str, request: str) -> Animated:
    from motionlang import check
    from motionlang.agent.loop import tweak as ml_tweak

    src = studio.lib.get(item_id)
    if src.kind != "motion":
        raise NotAnimatable(f"{item_id} is not an animation; animate an SVG first")
    doc = json.loads(studio.lib.original(item_id).read_text())
    new, calls = None, []
    if (factor := tempo_factor(request)) is not None:  # speed changes need no model: scale the timeline
        retimed = retime(doc, factor)
        if check(copy.deepcopy(retimed)).report.ok:
            new = retimed
    if new is None:
        out = ml_tweak(doc, request, llm=studio.make_llm(), max_repairs=1, max_tokens=PLAN_TOKENS)
        calls = [a.call for a in out.attempts]
        if not out.ok or out.doc is None:
            raise RuntimeError("couldn't apply that change: " + "; ".join(out.final.problems()[:2] if out.final else []))
        new = out.doc
    base_title = src.title.split(" (v")[0]
    version = int(src.facts.get("version", 1)) + 1
    brief = f"{src.facts.get('brief', src.caption)} → {request}"
    item = _store(studio, new, title=f"{base_title} (v{version})", brief=brief, source="tweak", parent=item_id, version=version)
    return Animated(item, studio.lib.gif(item.id), True, calls=calls)


def _no_bake(doc: dict) -> bool:
    from motionlang import check

    try:
        return check(copy.deepcopy(doc)).baked is None
    except Exception:
        return True

