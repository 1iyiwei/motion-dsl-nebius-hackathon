"""Prompts for generating, repairing and editing Motion documents."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from ..edits import __doc__ as EDIT_OPS_DOC
from ..schema import prompt_reference

RULES = """\
Rules:
- Reply with ONE JSON object: the complete Motion document. No prose, no markdown.
- "motion": "0.1". Use the given canvas size. Give every actor explicit init x/y inside the canvas.
- Every block needs a short unique id (b1, b2, ...); block ids must differ from actor ids.
- Never run two absolute verbs on the same actor property at the same time. Sequence them (seq),
  or make one relative (`by`, `relative: true`, bounce, wiggle).
- move_along: put the actor at the path's first point first, or use "relative": true.
- Prefer durations between 0.3 and 2 s. Keep the whole timeline short unless asked otherwise.
- When the user drew strokes, use them: as paths ({"stroke": id}) or as visible actors (shape kind "stroke").
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
        "You are a motion designer who writes animations in the Motion DSL, a JSON language.\n\n"
        f"{prompt_reference()}\n\n{RULES}\nExample document:\n{_example()}\n"
    )


def generation_user(intent: str, canvas: dict[str, Any], strokes: dict[str, Any] | None = None) -> str:
    parts = [f"Request: {intent}", f"Canvas: {json.dumps(canvas)}"]
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


def edit_system() -> str:
    return (
        "You edit Motion DSL documents with small edit operations instead of rewriting them.\n\n"
        f"{prompt_reference()}\n\n## Edit operations\n{EDIT_OPS_DOC}\n"
        "Address blocks and actors by their id; `field` may be dotted to reach nested values (\"to.zoom\", \"init.x\", "
        "\"path.points\"). Use `pointer` only for canvas, constraints and meta.\n"
        'Example: for blocks {"id":"roll","verb":"move_along",...,"duration":1.6} and {"id":"cam","verb":"camera","to":{"zoom":1.5}}, '
        'the request "slower roll, zoom in more" becomes {"ops": [{"op":"set","id":"roll","field":"duration","value":2.4}, '
        '{"op":"set","id":"cam","field":"to.zoom","value":2.2}]}.\n'
        "Cover every part of the request.\n"
        'Reply with JSON only: {"ops": [ ... ]}. Use the fewest ops that satisfy the request. '
        "To make something slower/faster, change durations; bigger/smaller, change distances or sizes. "
        "Keep ids stable; new blocks need new unique ids. "
        "If the request directly conflicts with a constraint (e.g. 'slower' vs total_duration), the request wins: "
        "update that constraint in the same ops (set with a pointer like /constraints/1/max)."
    )


def edit_user(doc: dict[str, Any], instruction: str) -> str:
    return f"Document:\n{json.dumps(doc, separators=(',', ':'))}\n\nChange request: {instruction}"


def regenerate_user(doc: dict[str, Any], instruction: str) -> str:
    return (
        f"Current document:\n{json.dumps(doc, separators=(',', ':'))}\n\n"
        f"Change request: {instruction}\n\nReply with the complete updated document as JSON only. Keep unaffected parts as they are. "
        "If the request conflicts with a constraint (e.g. 'slower' vs total_duration), the request wins: update the constraint."
    )
