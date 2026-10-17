# motionlang: the Motion DSL

License: Apache-2.0

**Motion** is a declarative 2D motion language designed to be **written by LLMs, checked by machines, and played anywhere**.
Instead of asking a model for keyframes (Lottie) or imperative animation code (GSAP), you ask it for *intent*: actors, a timeline of verbs (`move_along`, `bounce`, `spring_to`, `draw_on`, `camera`, …) and constraints. `motionlang` then:

- **validates** it, returning errors written for an LLM repair loop: every error has a JSON pointer, the block to blame, and a concrete hint;
- **bakes** it deterministically into per-frame tracks for players and exporters;
- **edits** it with tiny id-addressed ops, so a small fast model can handle "slower" or "more bounce" without regenerating the document;
- **generates** its own JSON Schema (for structured output) and LLM prompt reference from the verb registry, so a newly added verb is usable immediately.

The full language spec is in [SPEC.md](SPEC.md).

## Install

```bash
pip install motionlang          # once published
# in this workspace: uv sync
```

## Quick start

```bash
motionlang validate examples/ball_logo.motion.json
motionlang preview  examples/draw_and_zoom.motion.json -o preview.html   # standalone HTML/SVG player
motionlang bake     examples/ball_logo.motion.json -o tracks.json
motionlang schema   > motion.schema.json                                 # JSON Schema (draft 2020-12)
motionlang prompt   > motion-reference.md                                # verb reference for system prompts
motionlang edit     doc.motion.json ops.json -o doc2.motion.json
```

```python
import motionlang

result = motionlang.check(doc)          # doc: dict parsed from JSON
if not result.ok:
    for e in result.report.errors:      # E_BOUNDS [block=hop actor=ball] at t=0.42s: ball goes 34px past the top edge ...
        print(e)
tracks = result.baked.to_json()         # player format

doc2, issues = motionlang.apply_edits(doc, [{"op": "set", "id": "hop", "field": "height", "value": 60}])
```

## A document

```json
{
  "motion": "0.1",
  "canvas": { "width": 1280, "height": 720, "fps": 60 },
  "strokes": { "s1": { "points": [[80,560],[400,380],[760,600],[1000,560]] } },
  "actors": [
    { "id": "ball", "shape": { "kind": "ellipse", "rx": 28, "ry": 28 }, "style": { "fill": "#e4572e" }, "init": { "x": 80, "y": 560 } }
  ],
  "timeline": { "id": "root", "seq": [
    { "id": "roll", "verb": "move_along", "actor": "ball", "path": { "stroke": "s1" }, "duration": 1.6 },
    { "id": "hop",  "verb": "bounce", "actor": "ball", "height": 90, "count": 3 }
  ] },
  "constraints": [ { "type": "in_bounds", "margin": 8 } ]
}
```

## Core ideas

- **Composition rule**: absolute verbs set a value and relative verbs add on top. Two absolute verbs fighting over the same property at the same time is an error (`E_CONFLICT`), never a silent heuristic.
- **No surprise jumps**: absolute verbs start from the current value. If one would make an actor teleport, you get `E_DISCONTINUITY` with a fix.
- **One source of truth**: Python resolves timing, samples, and checks constraints. Players just play the baked tracks.
- **Extensible**: verbs are plugins (`motionlang.verbs` entry point). Any verb that passes the generic contract suite in `tests/test_contract.py` (deterministic, identity at start, continuous, bounded, schema round-trip) is a valid Motion verb.

## Verbs (v0.1)

`move`, `move_along`, `rotate`, `scale`, `fade`, `recolor`, `draw_on`, `camera`, `orbit`, `bounce`, `wiggle`, `spring_to`, `follow`, `hold`. Run `motionlang prompt` for parameters and examples.

## Related work

Motion complements [MoVer](https://github.com/jama1017/MoVer) (SIGGRAPH 2025), a first-order-logic language for *verifying* motion graphics against a prompt. Motion is the generation target, and MoVer checks intent. See SPEC.md §14.
