# MotionLang DSL

License: Apache-2.0

**MotionLang** is a declarative 2D motion language designed to be **written by LLMs, checked by machines, and played anywhere**.
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
motionlang import-svg logo.svg --fit 1280x720 -o logo.json             # SVG → assets + suggested actors
motionlang render   doc.motion.json --gif out.gif --width 480             # animated GIF (pip install motionlang[render])
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

## LLM loop (Nemotron on Nebius Token Factory)

`pip install motionlang[agent]` and set `NEBIUS_API_KEY` (a `.env` file is picked up automatically):

```bash
motionlang generate "a red ball drops, bounces three times, then rolls off to the right" -o ball.motion.json --preview ball.html
motionlang tweak ball.motion.json "higher bounces, slower roll" -o ball2.motion.json --preview ball2.html
```

- **generate**: Nemotron 3 Ultra writes a document; validator errors (with hints) go back to it until it's clean.
- **tweak**: Nemotron 3 Nano emits a few edit ops with reasoning turned off (~1–2 s). If they don't validate, it retries once with reasoning, then Ultra regenerates the document.
- Every call is logged with model, latency and tokens (`-v` also shows raw replies). Override models with `MOTION_PLANNER_MODEL` / `MOTION_EDITOR_MODEL`.
- `generate(..., on_attempt=fn)` and `tweak(..., on_attempt=fn)` call `fn(attempt)` right after each attempt is validated, for live progress in a UI.
- Near-valid documents are repaired with edit ops (`repair-edit` attempts) instead of being rewritten; badly broken ones are rewritten.
- `LLM(models={"planner": "nvidia/nemotron-3-super-120b-a12b"})` overrides models per instance (per request in a server); `llm.model_for(role)` reports the effective model.
- `generate(..., structured=...)` (CLI `--structured/--no-structured`) constrains the planner to the JSON Schema with Token Factory structured output. By default it is on exactly when the planner is a Super model (`prefers_structured`). `max_tokens=` (CLI `--max-tokens`) caps each planner call; about 6000 suits Super, whose valid documents use 2–3k tokens. If a structured reply comes back unparseable (Super sometimes writes a number that never ends), later rounds switch to plain JSON mode. Pair it with `MOTION_PLANNER_MODEL=nvidia/nemotron-3-super-120b-a12b`: on the sample logo this succeeded 6/6 (median ~45 s), while Ultra in plain JSON mode often drifts off the format, and Ultra under constrained decoding degenerates.

Web apps can play baked tracks with the packaged player: serve `motionlang.preview.player_js_path()` and `import { Player } from "./player.js"` (the same player `motionlang preview` embeds).

```python
from motionlang.agent import generate, tweak
out = generate("three circles orbit a sun, then the camera zooms in")
print(out.ok, out.seconds, [str(a) for a in out.attempts])
```

## Animating your own logo

Import an SVG once, then let the model animate it part by part. The model never sees or writes path data: it gets each part's id, label, bounding box and colour, references parts by id (`shape: {"kind": "asset", "ref": "swoosh"}`), and the agent merges your assets back into every document.

Try it on a test logo from the repo root (`out/` is gitignored; `generate` and `tweak` need `NEBIUS_API_KEY`):

```bash
uv run motionlang import-svg packages/motionlang/tests/fixtures/svg/nested.svg --fit 1280x720 -o out/logo.json
uv run motionlang generate "reveal the logo part by part, then the mark spins once and the whole logo pops" \
    --assets out/logo.json -o out/logo_ai.motion.json --preview out/logo_ai.html
uv run motionlang tweak out/logo_ai.motion.json "slower, and reveal right to left" -o out/logo_ai2.motion.json --preview out/logo_ai2.html
open out/logo_ai.html
```

`import-svg` prints what it couldn't keep (gradients become solid colours; text and filters are skipped). The other fixtures (`arcs.svg`, `styled.svg`) and your own SVGs work the same way. `import-svg ... --doc -o x.motion.json` writes a complete static document you can animate by hand.

```python
from motionlang.importers import import_svg
from motionlang.agent import generate

logo = import_svg(svg_text, fit=(1280, 720))   # {"canvas", "assets", "actors", "dropped"}
out = generate("the swoosh wipes in, then the wordmark fades up", canvas=logo["canvas"],
               assets=logo["assets"], suggested_actors=logo["actors"])
```

The importer flattens transforms, converts circles, rects, polygons and arcs to path data, keeps `<g>` groups as suggested group actors, and names parts from `id`, `inkscape:label` or `<title>`. Filled parts are wiped in with `reveal`; stroked parts can also `draw_on`.

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
- **Extensible**: verbs are plugins (`motionlang.verbs` entry point). Any verb that passes the generic contract suite in `motionlang.testing.contract` (`pip install motionlang[testing]`, then `from motionlang.testing.contract import *` in a test module; `MOTIONLANG_CONTRACT_VERB=name` checks one verb) (deterministic, identity at start, continuous, bounded, schema round-trip) is a valid MotionLang verb.

## Verbs (v0.1)

Verbs are MotionLang's *kinemes*, the smallest meaningful units of movement (a term from kinesics). A timeline composes them into larger motion.

`move`, `move_along`, `rotate`, `scale`, `fade`, `recolor`, `draw_on`, `reveal`, `enter`, `exit`, `camera`, `orbit`, `bounce`, `wiggle`, `spring_to`, `follow`, `hold`. Run `motionlang prompt` for parameters and examples.

## Related work

MotionLang complements [MoVer](https://github.com/jama1017/MoVer) (SIGGRAPH 2025), a first-order-logic language for *verifying* motion graphics against a prompt. MotionLang is the generation target, and MoVer checks intent. Unlike [OmniLottie](https://github.com/OpenVGLab/OmniLottie) (CVPR 2026), a fine-tuned model that generates Lottie directly, MotionLang uses a general LLM plus a validator. Its composition rule (absolute verbs as the primary motion, relative verbs layered on top) parallels the primary/secondary motion split in [fog](https://fog-motion-emotion.vercel.app/) (Liu & Chilton, 2026). See SPEC.md §14.
