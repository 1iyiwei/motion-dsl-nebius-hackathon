"""Prompts for generating, repairing and editing MotionLang documents."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from ..edits import __doc__ as EDIT_OPS_DOC
from ..paths import path_bbox
from ..schema import prompt_reference

RULES = """\
Rules:
- Reply with ONE JSON object: the complete MotionLang document. No prose, no markdown.
- "motion": "0.1". Use the given canvas size. Give every actor explicit init x/y inside the canvas.
- Every block needs a short unique id (b1, b2, ...); block ids must differ from actor ids.
- Never run two absolute verbs on the same actor property at the same time. Sequence them (seq),
  or make one relative (`by`, `relative: true`, bounce, wiggle).
- move_along: put the actor at the path's first point first, or use "relative": true.
- Prefer durations between 0.3 and 2 s. Keep the whole timeline short unless asked otherwise.
- When the user drew strokes, use them: as paths ({"stroke": id}) or as visible actors (shape kind "stroke").
- When the user gave assets (e.g. the parts of their logo), animate those parts: one actor per part with
  shape {"kind": "asset", "ref": <asset id>}, using the asset ids exactly as given. Don't include
  document.assets in your reply (it is added for you) and never write path data. Each suggested actor's
  init x/y is its resting position: where the part was drawn, measured from its parent group's position
  (top-level actors use canvas coordinates). Keep those numbers. To bring a part in from elsewhere, change its
  init to the starting point and `move` it back `to` the resting x/y. Group actors move all their parts at once.
  Positions (init x/y, move "to") are in the parent's coordinates, the same frame as the resting x/y shown;
  for a group, "move by" a canvas-sized offset is usually simplest.
  reveal and draw_on already keep a part hidden until they start: don't also set init.opacity 0 on it.
  Anything that starts hidden (opacity 0, scale 0) must be brought into view by a verb.
- Keep everything on screen: include {"type": "in_bounds", "margin": 8} in constraints. For a deliberate
  entrance or exit (something slides in from off-screen or leaves the screen), list that block's id in the
  constraint's "ignore" list, e.g. {"type": "in_bounds", "margin": 8, "ignore": ["enter", "exit"]}.
  in_bounds is checked on the stage, before the camera: camera zooms/pans never violate it, so don't
  ignore blocks just because the camera crops them.
- Constraints record the user's requirements. Besides in_bounds, add one only when the user asked for it
  (e.g. "under 3 seconds" → total_duration).
- Put the user's request in meta.intent and a short title in meta.title.
"""


@cache
def _example() -> str:
    text = resources.files("motionlang").joinpath("examples_data/ball_logo.motion.json").read_text()
    return json.dumps(json.loads(text), separators=(",", ":"))


def generation_system() -> str:
    return (
        "You are a motion designer who writes animations in the MotionLang DSL, a JSON language.\n\n"
        f"{prompt_reference()}\n\n{RULES}\nExample document:\n{_example()}\n"
    )


def _asset_desc(aid: str, a: dict[str, Any]) -> str:
    bbox = a.get("bbox")
    if bbox is None:
        try:
            bbox = path_bbox(a["d"])
        except (KeyError, ValueError):
            bbox = None
    desc = f'asset "{aid}"'
    if a.get("label") and a["label"] != aid:
        desc += f' ({a["label"]})'
    paint = [f"fill {a['fill']}" if a.get("fill") else "", f"stroke {a['stroke']}" if a.get("stroke") else ""]
    if any(paint):
        desc += ", " + ", ".join(x for x in paint if x)
    if bbox is not None:
        desc += f"; drawn at canvas bbox x {bbox[0]:.0f}, y {bbox[1]:.0f}, w {bbox[2]:.0f}, h {bbox[3]:.0f}"
    return desc


def assets_summary(assets: dict[str, Any], suggested_actors: list[dict] | None = None) -> str:
    """Describe assets by id, label, bbox and colour only: path data never goes to the model.

    With suggested actors, parts are shown once, as a tree of actors (asset details inline), since a
    separate asset list repeating the same ids reads as duplicates to a model."""
    if not suggested_actors:
        return (
            "The user supplied these assets (vector parts). Reference them by id with shape "
            '{"kind": "asset", "ref": id}; they are added to document.assets for you, unchanged:\n'
            + "\n".join(f"- {_asset_desc(k, a)}" for k, a in assets.items())
        )
    kids: dict[str | None, list[dict]] = {}
    for a in suggested_actors:
        kids.setdefault(a.get("parent"), []).append(a)
    lines: list[str] = []

    def show(a: dict, depth: int) -> None:
        init = a.get("init", {})
        where = f'at ({init.get("x", 0):g}, {init.get("y", 0):g}) ' + (f'relative to {a["parent"]}' if a.get("parent") else "in canvas coordinates")
        shape = a["shape"]
        what = "group" if shape["kind"] == "group" else _asset_desc(shape["ref"], assets.get(shape["ref"], {}))
        lines.append(f'{"  " * depth}- actor "{a["id"]}": {what}; rests {where}')
        for c in kids.get(a["id"], []):
            show(c, depth + 1)

    for a in kids.get(None, []):
        show(a, 0)
    return (
        "The user's artwork, as actors (each part is one actor showing one asset; groups move their parts together):\n"
        + "\n".join(lines)
        + "\n\nStart your document's \"actors\" from exactly this list (same ids; change init only for a part's "
        "starting pose; you may add actors):\n" + json.dumps(suggested_actors, separators=(",", ":"))
        + "\nThe assets themselves are added to document.assets for you; don't write them."
    )


def without_asset_data(doc: dict[str, Any]) -> dict[str, Any]:
    """The document as the model sees it: `assets` left out (it would echo path data otherwise)."""
    return {k: v for k, v in doc.items() if k != "assets"}


def generation_user(
    intent: str,
    canvas: dict[str, Any],
    strokes: dict[str, Any] | None = None,
    assets: dict[str, Any] | None = None,
    suggested_actors: list[dict] | None = None,
) -> str:
    parts = [f"Request: {intent}", f"Canvas: {json.dumps(canvas)}"]
    if assets:
        parts.append(assets_summary(assets, suggested_actors))
    if strokes:
        lines = []
        for sid, s in strokes.items():
            pts = s["points"]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            lines.append(
                f'- "{sid}": {len(pts)} points from {pts[0]} to {pts[-1]}, '
                f"bbox x {min(xs):.0f}–{max(xs):.0f}, y {min(ys):.0f}–{max(ys):.0f}"
            )
        parts.append("The user drew these strokes (put them in document.strokes unchanged):\n" + "\n".join(lines))
        parts.append("strokes JSON: " + json.dumps(strokes, separators=(",", ":")))
    return "\n\n".join(parts)


def repair_user(errors: list[str]) -> str:
    return (
        "The validator rejected your document:\n"
        + "\n".join(f"- {e}" for e in errors)
        + "\n\nFix every problem and reply with the complete corrected document as JSON only."
    )


EDIT_GUIDE = (
    f"## Edit operations\n{EDIT_OPS_DOC}\n"
    "Address blocks and actors by their id; `field` may be dotted to reach nested values (\"to.zoom\", \"init.x\", "
    "\"path.points\"). Use `pointer` only for canvas, constraints and meta. "
    'Remove an init value with {"op": "unset", "id": "ray1", "field": "init.opacity"}; add a block with '
    '{"op": "insert", "parent": "<container block id>", "block": {...}, "after": "<sibling id>"}; add a missing actor with '
    '{"op": "set", "pointer": "/actors/-", "value": {...actor...}}.\n'
)


def repair_ops_user(errors: list[str], doc: dict[str, Any] | None = None) -> str:
    """Ask for a fix as edit ops: a near-valid document gets patched instead of rewritten (rewrites drift)."""
    head = f"Your edits produced this document:\n{_doc_text(doc)}\n\n" if doc is not None else ""
    return (
        head + "The validator rejected the document:\n" + "\n".join(f"- {e}" for e in errors)
        + "\n\nFix every problem with edit operations on that document instead of rewriting it. "
        "Each hint names the exact change to make.\n\n" + EDIT_GUIDE
        + '\nReply with JSON only: {"ops": [ ... ]}.'
    )


def edit_failed_user(error: str) -> str:
    return f"Those ops were not applied: {error}\nNo edits were applied. Reply with corrected {{\"ops\": [...]}} for the same document."


def edit_system() -> str:
    return (
        "You edit MotionLang DSL documents with small edit operations instead of rewriting them.\n\n"
        f"{prompt_reference()}\n\n{EDIT_GUIDE}"
        'Example: for blocks {"id":"roll","verb":"move_along",...,"duration":1.6} and {"id":"cam","verb":"camera","to":{"zoom":1.5}}, '
        'the request "slower roll, zoom in more" becomes {"ops": [{"op":"set","id":"roll","field":"duration","value":2.4}, '
        '{"op":"set","id":"cam","field":"to.zoom","value":2.2}]}.\n'
        "Cover every part of the request.\n"
        'Reply with JSON only: {"ops": [ ... ]}. Use the fewest ops that satisfy the request. '
        "To make something slower/faster, change durations; bigger/smaller, change distances or sizes. "
        "Keep ids stable; new blocks need new unique ids. "
        "Only set fields that the reference above lists for that verb, actor or constraint type; never invent fields. "
        "Leave constraints alone unless the request conflicts with one. "
        "If the request directly conflicts with a total_duration constraint (e.g. 'slower'), the request wins: "
        "raise that constraint's `max` in the same ops, using the pointer of the constraint whose type is total_duration. "
        "in_bounds has no max, and when there is no total_duration constraint there is nothing to update."
    )


def _doc_text(doc: dict[str, Any]) -> str:
    text = json.dumps(without_asset_data(doc), separators=(",", ":"))
    if doc.get("assets"):
        text += "\n\n" + assets_summary(doc["assets"])
    return text


def edit_user(doc: dict[str, Any], instruction: str) -> str:
    return f"Document:\n{_doc_text(doc)}\n\nChange request: {instruction}"


def regenerate_user(doc: dict[str, Any], instruction: str) -> str:
    return (
        f"Current document:\n{_doc_text(doc)}\n\n"
        f"Change request: {instruction}\n\nReply with the complete updated document as JSON only. Keep unaffected parts as they are. "
        "If the request conflicts with a constraint (e.g. 'slower' vs total_duration), the request wins: update the constraint."
    )
