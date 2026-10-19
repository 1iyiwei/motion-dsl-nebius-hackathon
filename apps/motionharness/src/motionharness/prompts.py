"""What the coding models read: the plugin contract, real verb source as examples, and the task."""

from __future__ import annotations

import inspect
import re

from .plugin import Source, Task

CONTRACT = """\
# MotionLang verb plugin contract (SPEC §11)

A verb is a class registered with `@register`. It turns parameters + progress into property values; the core
derives timing, composition, continuity checks, the JSON Schema and the LLM prompt reference from what it declares.

- `name`: third-party verbs MUST be namespaced ("acme.spring_follow"); registration rejects bare names.
- `Params`: a pydantic model subclassing `VerbParams` (extra fields forbidden). Give numeric fields bounds with
  `Field(default, ge=/gt=/le=/lt=, description=...)`: the contract suite fuzzes every numeric param within its bounds.
  Do NOT declare the common block fields (id, verb, actor, duration, delay, ease); the core adds them.
- `examples`: a NON-EMPTY list (give two) of param dicts (no id/actor). Each one must validate on a default actor AND
  visibly change something (a verb whose effect is zero is reported as E_NOOP).
- `mode(p)`: "abs" (returns absolute values; must equal `ctx.start[prop]` at u=0) or "rel" (returns deltas added on
  top; must be exactly 0 at u=0).
- `props(p)`: the actor props it writes. Actor props: x, y, rotation (deg, clockwise), sx, sy, opacity, trim,
  clip_l/clip_t/clip_r/clip_b, fill, stroke. Coordinates are canvas px, +y is down.
- `default_duration(p)`: seconds when the block gives no `duration`.
- `uses_ease`: False for physical/time-based motion (u is then linear progress).
- `sample(p, u, t, ctx) -> dict`: u ∈ [0, 1] progress, t = seconds since the block started. ctx.start (composed
  values just before the block), ctx.t0, ctx.duration, ctx.value(actor, prop, abs_time) to read another actor,
  ctx.path(spec) for strokes.
- optional METHODS (each takes `p`; don't assign them as attributes): `actor_refs(p)` (other actors it reads;
  REQUIRED if it calls ctx.value on them), `check_target(p, actor)`, `jumps_at_start(p)`, `holds_before(p)`, `offstage(p)`.
- The class docstring's first paragraph is the LLM-facing summary (required).

## The generic contract suite (the acceptance gate; it runs automatically, don't copy it)
1. Valid + deterministic: each example validates on a default document and baking twice gives identical tracks.
   The default document is an 800×600 canvas with actors "a" (40×40 rect at 300,300), "b" (ellipse r=20 at
   550,300, never moving) and "line" (a stroke). Examples that need another actor should reference "b".
2. Identity at start: rel → all deltas 0 at u=0; abs → equals ctx.start at u=0.
3. Continuous: the largest step between samples must halve under 8× finer sampling (no jumps anywhere in
   [0, 1], including at u=1: make sure the value at u=1 is the limit of the values just before it).
4. Bounded: finite output for any numeric params within their declared bounds (Hypothesis). Guard divisions,
   exp/overflow and sqrt of negatives for extreme values; durations up to 20 s.
5. Schema round-trip: example params validate, dump and re-validate; docstring present.
Closed-form evaluation is preferred over integrating step by step (sample() may be called at any u, in any order);
if you must integrate, integrate deterministically from 0 to t with a fixed step inside sample() and keep it cheap:
sample() is called for every frame and the suite bakes hundreds of documents, so a run must finish in seconds.
"""

FORMAT = """\
Reply with a two-to-four sentence design note, then exactly these three files, each as a `###` header line
followed by one fenced block:

### verb.py
```python
<the plugin module: imports, Params model, the @register'd Verb subclass>
```

### test_verb.py
```python
<pytest tests for THIS verb's own semantics (3–6 focused tests): what the task asks for, e.g. "ends at the
target", "overshoots by at most X", "lags behind a moving target". Build documents inline with a small helper (or
`from motionlang.testing import make_doc`: make_doc(*blocks, actors=[...]) gives the default document above) and use `motionlang.check(doc)` (result.ok, result.report.summary(), result.baked.props[actor][prop] → per-frame list,
result.baked.times). Every block needs a unique "id"; every document that uses the verb declares
`"requires": ["<verb name>"]`. Pick parameters that make the effect clearly visible (a verb that changes nothing
fails as E_NOOP). Derive expected numbers from the parameters and your formula (with a tolerance), never from a
guess: e.g. compute the expected peak/period, then assert the baked value is within 5% of it. Always write `r = check(doc); assert r.ok, r.report.summary()` so a
failure shows the validator's message and hint. Import the verb only through motionlang (load_plugins()), not from
the module path. Keep each test fast (bake a few seconds at most).>
```

### demo.motion.json
```json
<a complete MotionLang document (1280×720, fps 30, 2–6 s, with "requires": ["<verb name>"]) that shows the verb off clearly: simple ellipse/rect
actors with distinct fill colours, the verb on a visible actor, and whatever else makes it readable (e.g. a moving
target). It must validate, and stay on the canvas.>
```
Import what you use: `from motionlang.verbs import Verb, VerbParams, SampleContext, register`, `from motionlang.verbs.base
import Mode`, `from pydantic import Field`, `from typing import Any`. There is no Vec2 type to import: write
`tuple[float, float]`. Make sure every name you use is imported or defined. Only the standard library, numpy, pydantic and motionlang are installed.
"""


def _core_source(names: list[str]) -> str:
    from motionlang.verbs import core

    src = inspect.getsource(core)
    out = []
    for name in names:
        m = re.search(rf"\n(class {name}Params\(.*?)(?=\n\n\n|\Z)", src, re.S)
        v = re.search(rf"\n(@register\nclass {name}\(Verb\):.*?)(?=\n\n\nclass |\n\n\n@register|\n\n\n# ----|\Z)", src, re.S)
        helpers = ""
        if name == "SpringTo":
            h = re.search(r"\n(def _spring_response.*?)(?=\n\n\n@register)", src, re.S)
            helpers = h.group(1) if h else ""
        if v:
            out.append("\n\n".join(x for x in (m.group(1) if m else "", helpers, v.group(1)) if x))
    return "\n\n\n".join(out)


EXEMPLARS = ["Follow", "SpringTo", "Bounce", "Wiggle"]


EXAMPLE_DOC = """{"motion": "0.1",
 "canvas": {"width": 1280, "height": 720, "fps": 30, "background": "#ffffff"},
 "actors": [
   {"id": "ball", "shape": {"kind": "ellipse", "rx": 28, "ry": 28}, "style": {"fill": "#e4572e"}, "init": {"x": 200, "y": 360}},
   {"id": "box", "shape": {"kind": "rect", "w": 60, "h": 60}, "style": {"fill": "#2973b2"}, "init": {"x": 900, "y": 360}}
 ],
 "timeline": {"id": "root", "par": [
   {"id": "slide", "verb": "move", "actor": "box", "to": [1000, 200], "duration": 2},
   {"id": "chase", "verb": "follow", "actor": "ball", "target": "box", "lag": 0.3, "duration": 3}
 ]},
 "constraints": [{"type": "in_bounds", "margin": 8}]}"""


def dsl_reference() -> str:
    """The document format (everything in the generated prompt reference before the verb list) plus the verb names."""
    from motionlang import load_plugins
    from motionlang.schema import prompt_reference

    ref = prompt_reference()
    head = ref[: ref.find("## Verbs")] if "## Verbs" in ref else ref[:4000]
    names = ", ".join(sorted(load_plugins()))
    return f"{head.strip()}\n\nExisting verbs (don't duplicate them): {names}\n\nA complete document:\n```json\n{EXAMPLE_DOC}\n```"


def system_prompt() -> str:
    from motionlang.verbs import base

    return (
        "You are MotionHarness, a careful graphics engineer who extends MotionLang, a declarative 2D motion DSL, by "
        "writing verb plugins in Python. Your code is run against a fixed contract test suite and your own tests in "
        "an isolated sandbox, then a vision model looks at rendered frames. Write correct, closed-form, numerically "
        "safe code. Be concise.\n\n"
        + CONTRACT
        + "\n# MotionLang documents (what tests and demo.motion.json build)\n" + dsl_reference() + "\n"
        + "\n# motionlang.verbs.base (the interface)\n```python\n" + inspect.getsource(base) + "\n```\n"
        + "\n# Core verbs as reference implementations (from motionlang.verbs.core; `_lerp(a, b, u)` is a local helper)\n```python\n"
        + _core_source(EXEMPLARS) + "\n```\n\n" + FORMAT
    )


def write_messages(task: Task, approach: str = "", docs: str = "") -> list[dict]:
    user = [f"Task: {task.description}", "", f"Verb name: `{task.verb}` (use exactly this `name`).",
            f"It is installed as the package `{task.module}` (module `{task.module}.verb`)."]
    if approach:
        user += ["", f"Approach for this candidate: {approach}"]
    if docs:
        user += ["", "Reference material found on the web (may help; verify before relying on it):", docs]
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": "\n".join(user)}]


def _files_block(src: Source) -> str:
    return "\n\n".join(f"### {name}\n```{'json' if name.endswith('json') else 'python'}\n{body.rstrip()}\n```" for name, body in src.as_dict().items())


def repair_messages(task: Task, src: Source, problems: list[str], critique: dict | None = None) -> list[dict]:
    msgs = write_messages(task)
    msgs.append({"role": "assistant", "content": (src.notes + "\n\n" if src.notes else "") + _files_block(src)})
    parts = []
    if problems:
        parts += ["The sandbox ran your files. These checks failed (verbatim test output):", ""] + [f"- {p}" for p in problems]
    if critique:
        lead = ("You were addressing this review of the rendered demo when the tests above broke; fix them and keep addressing it:"
                if problems else "All tests pass, but a reviewer looked at rendered frames of demo.motion.json and wants changes:")
        parts += ["", lead,
                  f"score {critique.get('score')}/10. Issues: " + "; ".join(critique.get("issues") or []),
                  f"Suggestion: {critique.get('suggestion', '')}",
                  *(["Measured from the demo's baked frames:"] + [f"  {m}" for m in critique.get("metrics", [])] if critique.get("metrics") else []),
                  "Fix the motion itself (verb.py) if the verb behaves wrongly (including defaults that hide the effect); fix the demo "
                  "if only the demo is unclear (e.g. make the target stop abruptly so a spring visibly overshoots)."]
    parts += ["", "Fix the root cause. For each failing test of your own, first decide from the task whether the test or the "
              "verb is wrong, and say which in the note: a test that expected a guessed number is fixed by deriving the number "
              "from the formula; a verb that misses what the task asks is fixed in verb.py. The contract suite is fixed. Reply in the same format. For a file that needs no change you may "
              "write its `###` header followed by `(unchanged)` instead of the block."]
    msgs.append({"role": "user", "content": "\n".join(parts)})
    return msgs


CRITIC = """\
You review motion for a library of animation verbs. The image is a contact sheet of a demo animation: top left an
onion skin (10 poses over time, faint = early, coloured lines = pivot paths of the actors the new verb moves), right
the curves of position/scale/rotation over time (green band: when the verb is active), bottom six keyframes.

The verb `{verb}` is supposed to: {description}
Its own summary: {summary}

Measured from the baked frames. These numbers are exact ground truth: where they disagree with what you think you
see in the image, the numbers win (e.g. "overshoot 0.0%" means there is NO overshoot, whatever the curves look like):
{metrics}

Judge only the motion: does it do what the task says, and does it look physically and visually right (smooth,
plausible overshoot/settling, no sudden jumps, readable)? First check each property the task names (overshoot,
settling, lag, oscillation, …) against the measurements; a named property that the measurements show is missing
means matches_intent is false and the score is at most 5. Reply in JSON:
{{"score": 1-10, "matches_intent": true|false, "observations": "<what you see, 1-2 sentences>",
  "issues": ["<concrete problem>", ...], "suggestion": "<one concrete change to the verb or demo>"}}
Use an empty issues list when it looks right. Score 8+ means ready to ship.
"""


def critic_messages(task: Task, summary: str, image_url: str, metrics: list[str] | None = None) -> list[dict]:
    text = CRITIC.format(verb=task.verb, description=task.description, summary=summary or "(none)",
                         metrics="\n".join(f"- {m}" for m in metrics or []) or "- (nothing moves)")
    return [{"role": "user", "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image_url}}]}]


DOCS = """\
A developer asked a coding agent to extend a 2D animation library: "{description}".
Name the external file format, product or third-party API this task depends on, if any (e.g. "Lottie", "Blender bpy",
"After Effects", "SVG SMIL"). Math and physics (springs, pendulums, easing, noise) are NOT external.
Reply in JSON: {{"external": "<name, or none>", "queries": ["<web search query about it>"]}} (queries empty when none).
"""

NAMING = """\
Animation verbs are named so that a sentence "the actor <word>s" reads naturally: "the ball bounces", "it orbits", "it hops".
Name to check: `{name}` (what it does: {description}). Take its main word ("{word}").
Does "the actor {word}s" read as natural English, with "{word}" used as a verb? Words that are nouns too are fine when
they also work as verbs ("springs", "pulses", "hops", "spirals", "rockets", "wiggles"); words that only work as nouns
are not ("pendulums", "heartbeats", "paths").
Reply in JSON: {{"sentence": "the actor {word}s", "natural": true|false, "action_name": "<if not natural: a snake_case name whose last word is a verb, e.g. swing; else empty>"}}
"""

PREPOSITIONS = {"to", "by", "in", "out", "on", "off", "up", "down", "at", "along", "around", "into", "onto", "from", "away", "back", "over"}


def naming_word(name: str) -> str:
    """The word a sentence would conjugate: the last one that isn't a preposition (spring_to → spring)."""
    words = [w for w in name.split("_") if w]
    while len(words) > 1 and words[-1] in PREPOSITIONS:
        words.pop()
    return words[-1] if words else name
