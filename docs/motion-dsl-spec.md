# Motion: DSL Spec v0.1 (DRAFT, decisions of Oct 17 applied)

A declarative, intent-level motion language that LLMs write, machines verify, and runtimes play.
It is shared by SketchDynamics (P1, produces it), MotionHarness (P2, extends it with new verbs and exporters) and CanvasMind (P3, indexes it).

> Decisions from the Oct 17 review are in §13. Related work (MoVer, OmniLottie) is in §14.

---

## 1. Design goals

1. **Easy for an LLM to generate**: plain JSON, a small vocabulary, a default for every parameter, no free-form code. One JSON Schema doubles as the Token Factory structured-output schema.
2. **Checkable**: the semantics are deterministic. Every actor's state at time `t` is computable, so constraints are checked on sampled trajectories, not by eyeballing.
3. **Editable in small pieces**: every block has a stable `id`. Fast models edit by id (§8), not by rewriting the whole document.
4. **Sketch-aware**: paths can point at user-drawn strokes directly.
5. **Compiles down**: the output is baked tracks that drive a web player, Lottie, or Blender.

### Why not an existing format?

| Format | Why not as the authoring layer |
| --- | --- |
| Lottie | Keyframe-level, not intent-level; verbose; LLMs get it wrong easily. Good **export target**. |
| GSAP / CSS animations | Imperative JS: you must run it to check it, and it's tied to the browser. |
| Manim / bpy scripts | Arbitrary code: unbounded, hard to validate or edit in small pieces. |
| USD animation | 3D-pipeline-heavy; overkill for a 12-day 2D scope. |

Motion sits above these and compiles down to them.

---

## 2. Scope and conventions

- **2D only in v0.1**. 3D can come later through the same verb model (vec2 → vec3).
- **Time** in seconds (float). `fps` is used only for sampling and export.
- **Space**: canvas units, origin top-left, +x right, +y down (matches SVG and screen coordinates). Angles in degrees, clockwise positive.
- **Determinism**: anything random takes a `seed`; same document + same seed → same frames.

## 3. Document shape

```json
{
  "motion": "0.1",
  "canvas": { "width": 1280, "height": 720, "background": "#ffffff", "fps": 60 },
  "strokes": { "s1": { "points": [[100,500],[300,320],[520,480]] } },
  "actors": [ ... ],
  "timeline": { ... },
  "constraints": [ ... ],
  "meta": { "title": "...", "intent": "original user request, kept for traceability" }
}
```

`strokes` holds raw sketch input (vector points from the canvas). Paths reference them by id, so redrawing a stroke changes the motion without regenerating the timeline.

## 4. Actors

```json
{ "id": "ball", "shape": { "kind": "ellipse", "rx": 30, "ry": 30 },
  "style": { "fill": "#e4572e" },
  "init": { "x": 100, "y": 500, "rotation": 0, "sx": 1, "sy": 1, "opacity": 1 },
  "pivot": [0.5, 0.5], "parent": null, "z": 0 }
```

- `shape.kind`: `rect | ellipse | polygon | path (svg d) | text | image (url) | stroke (ref) | group`.
- **Animatable properties**: `x, y, rotation, sx, sy, opacity, fill, stroke`, plus `trim` (0–1, visible fraction of a path/stroke shape). These are the only things verbs touch.
- **Built-in `camera` actor**: always present, with props `x, y, zoom, rotation` (initially centered on the canvas, zoom 1). Constraints like `in_bounds` are checked in camera (screen) space.
- `parent`: hierarchical transforms (a child moves with its group). `pivot`: rotate/scale origin, as a fraction of the bounding box.

## 5. Timeline: blocks

The timeline is a **tree**. Containers arrange time; verbs (leaves) change properties.

| Container | Meaning |
| --- | --- |
| `seq` | Children one after another |
| `par` | Children start together; duration = the longest child |
| `stagger` | Like `par`, but child *i* starts at `i × offset` |
| `repeat` | Plays its single child `count` times (`count: "inf"` loops in the player; for validation, sampled for 1 cycle) |

Every block has `id` (required, unique), plus optional `delay` (s, before it starts) and `label`.

```json
{ "id": "root", "seq": [
    { "id": "b1", "verb": "move_along", "actor": "ball", "path": { "stroke": "s1" }, "duration": 1.2, "ease": "inOutCubic" },
    { "id": "b2", "par": [
        { "id": "b3", "verb": "bounce", "actor": "ball", "height": 80, "count": 3 },
        { "id": "b4", "verb": "fade", "actor": "logo", "to": 1, "duration": 0.5, "delay": 0.3 } ] } ] }
```

**Sync**: v0.1 uses tree position only. An `after: "<blockId>"` field for cross-branch sync would be more expressive but makes timing a constraint-solving problem. **Deferred to v0.2**, to be revisited once hands-on authoring shows whether it's needed.

## 6. Verbs (v0.1 core set: 14)

Common fields: `actor`, `duration` (default per verb), `ease` (default `inOutQuad`), `delay`.
Most property verbs take **either** `to` (absolute target) **or** `by` (relative delta), never both.

| Verb | Params | Composes as (§ rule below) | Notes |
| --- | --- | --- | --- |
| `move` | `to:[x,y]` \| `by:[dx,dy]` | abs (`to`) / rel (`by`) | |
| `move_along` | `path`, `orient:bool=false`, `reverse:bool=false`, `relative:bool=false` | abs (rel if `relative`) | Constant speed along the path's length, shaped by `ease`; `orient` turns the actor along the path direction. If the actor isn't at the path start → `E_DISCONTINUITY` (fix: add a `move` first, or set `relative: true` to translate the path to the actor) |
| `rotate` | `to` \| `by` (deg) | abs / rel | |
| `scale` | `to` \| `by` (number or `[sx,sy]`) | abs / rel | |
| `fade` | `to` (0–1) | abs | |
| `recolor` | `prop: fill\|stroke`, `to: color` | abs | Interpolated in OKLab |
| `orbit` | `center:[x,y]` \| `around:actorId`, `radius`, `turns=1`, `start_angle=0` | abs | |
| `bounce` | `height`, `count=3`, `decay=0.6`, `squash=0.15` | rel | Physically shaped arcs in place; squash-and-stretch on landing |
| `wiggle` | `amplitude`, `frequency=4`, `props=[x,y]`, `seed` | rel | Smooth noise, returns to rest at both ends |
| `spring_to` | `prop`, `to`, `stiffness=170`, `damping=26` | abs | Duration is derived (settles below a threshold) unless given |
| `follow` | `target:actorId`, `lag=0.2`, `offset:[dx,dy]` | abs | Tracks another actor's position with delay |
| `draw_on` | `from=0`, `to=1` | abs | Reveals a `path`/`stroke` actor progressively (animates `trim`), the classic "line draws itself" effect |
| `camera` | `to:{x?,y?,zoom?,rotation?}` \| `by:{...}` | abs / rel | Pans/zooms/rotates the view; targets the built-in `camera` actor, so `actor` is omitted |
| `hold` | `duration` | — | Explicit wait |

**Property aliases**: `position` = `x,y`; `scale` = `sx,sy`. They can be used anywhere a `prop` is named.

**Easing set**: `linear`; Penner `in/out/inOut × Quad, Cubic, Quart, Sine, Expo, Back, Elastic, Bounce`; `cubicBezier(x1,y1,x2,y2)` given as `{"bezier":[...]}`.

**Path forms**: `{ "stroke": "s1" }` (smoothed with Catmull-Rom), `{ "points": [[x,y],...], "smooth": true }`, `{ "svg": "M ... " }`, `{ "arc": { "center":[x,y], "radius": r, "from": deg, "to": deg } }`.

### Composition rule

When two verbs touch the same property over overlapping time:
- **absolute + relative → the absolute sets the base, the relative adds on top** (e.g. `move_along` + `wiggle` = a wobbly path).
- **relative + relative → add.**
- **absolute + absolute → error `E_CONFLICT`.** The validator reports both block ids; the LLM fixes it.

A clear, checkable rule beats a clever, implicit one, and it gives the repair loop a precise error.

## 7. Constraints

These are declarative checks run against tracks sampled at `canvas.fps`. They exist so the agent can be told *exactly* what's wrong.

| Constraint | Params | Fails when |
| --- | --- | --- |
| `in_bounds` | `actors="*"`, `margin=0` | Any part of the actor's bounding box leaves the canvas |
| `max_speed` | `actor`, `px_per_s` | The speed exceeds the limit at any sample |
| `smooth` | `actor`, `max_jerk` | The third derivative of position spikes (catches visual "pops" at block seams) |
| `no_overlap` | `a`, `b`, `during:[t0,t1]?` | The bounding boxes intersect |
| `ends_at` | `actor`, `pos:[x,y]`, `t?`, `tol=2` | The final (or time-`t`) position is off target |
| `total_duration` | `max` \| `exact`, `tol` | The timeline length is outside the limit |

Built-in checks (always on): schema validity, unique ids, existing references, `E_CONFLICT`, and continuity (no jump at a block boundary unless one was asked for).

### Two tiers of verification

| Tier | Question it answers | How |
| --- | --- | --- |
| **1. Well-formed** (this section) | "Is this animation physically sane?" bounds, speed, jerk, conflicts | Our validator, deterministic, milliseconds |
| **2. Intent** ([MoVer](https://github.com/jama1017/MoVer), SIGGRAPH 2025) | "Does it do what the user *said*?" e.g. "the ball moves right, *then* the logo appears" | A Nemotron model translates the prompt into a MoVer first-order-logic program; the MoVer verifier checks it against our baked tracks (§10 `to_mover` exporter) |

Tier 1 needs no prompt; tier 2 catches "valid but wrong." Both produce structured errors that feed the same repair loop.

## 8. Edit operations (fast-path for Nano/Super)

Edits are a list of id-addressed operations, applied atomically and then revalidated:

```json
[ { "op": "set",    "id": "b1", "field": "duration", "value": 0.8 },
  { "op": "set",    "id": "b3", "field": "height",   "value": 120 },
  { "op": "insert", "parent": "root", "after": "b2", "block": { "id": "b5", "verb": "hold", "duration": 0.5 } },
  { "op": "remove", "id": "b4" },
  { "op": "wrap",   "ids": ["b1"], "with": "par", "id": "b6" } ]
```

"Slower" → one `set` op from a small model in about 200 ms, instead of regenerating the whole document with the big one. This is the latency and credit-saving story judges want to see.

## 9. Validator output

```json
{ "ok": false,
  "errors": [
    { "code": "E_BOUNDS", "severity": "error", "block": "b3", "actor": "ball", "t": 2.15,
      "pointer": "/timeline/seq/1/par/0",
      "message": "ball leaves canvas bottom by 34px at t=2.15s",
      "hint": "reduce bounce.height or move the path up" } ],
  "warnings": [ ... ],
  "stats": { "duration": 3.4, "actors": 2, "blocks": 5 } }
```

Error codes: `E_SCHEMA, E_DUP_ID, E_REF, E_CONFLICT, E_DISCONTINUITY, E_BOUNDS, E_SPEED, E_JERK, E_OVERLAP, E_ENDS_AT, E_DURATION`.
Each error carries `hint`, a sentence the repair prompt includes word for word.

## 10. Execution model

```
document ──validate(schema/refs)──► resolve timing ──► sample(t) per actor ──► baked tracks
                                                                    │
                                          constraints ◄─────────────┤
                                                                    ├──► web player (plays tracks)
                                                                    ├──► MoVer exporter (intent verification, §7)
                                                                    ├──► Lottie exporter
                                                                    └──► bpy exporter (stretch)
```

**Python is the single source of truth**: it does the timing resolution, sampling and constraints. The browser plays the **baked tracks**, i.e. per-actor property samples at fps, with redundant samples dropped. Why:
- One implementation, not two that drift apart (important for a solo build).
- Exporters and the validator see exactly what the player shows.
- Re-baking a 5 s scene at 60 fps is a few milliseconds, so live preview stays interactive.

The trade-off is that the client can't play un-baked documents offline. That's acceptable for the hackathon.

## 11. Verb plugin contract (what MotionHarness writes)

A verb is a Python module that registers:

```python
class Bounce(Verb):
    name = "bounce"
    kind = "relative"                 # relative | absolute (may depend on params, e.g. to vs by)
    props = ("y", "sx", "sy")         # properties it writes
    Params = BounceParams             # pydantic model → JSON Schema fragment
    def default_duration(self, p): ...
    def sample(self, p, u: float, base: State) -> Delta: ...   # u ∈ [0,1], after easing
```

Every verb must pass the generic **contract test suite** (run in Token Factory Sandboxes by MotionHarness):

1. **Deterministic**: same inputs, same output.
2. **Identity at start**: a relative verb gives a zero delta at u=0.
3. **Continuous**: no jumps inside [0,1], and the end state matches the next block's start.
4. **Bounded**: finite output for any parameters within schema range (property-based, with Hypothesis).
5. **Schema round-trip**: the params validate, and defaults fill in.
6. **Golden frames**: rendered sample frames match a reference. This applies once a verb is accepted; for new verbs, a vision-model critique is used instead.

Exporters follow the same pattern: `Exporter.export(tracks) -> bytes`, plus round-trip tests.

### Designed for adding verbs

Adding a verb must not require touching the core. That's what makes MotionHarness possible.

- **Registry is the single source of truth.** At startup, the core collects every registered `Verb` and generates from it:
  1. the JSON Schema (a discriminated union on `verb`), which is also the structured-output schema sent to Token Factory;
  2. the **verb reference section of the LLM system prompt** (name, params with defaults, composes-as, one example each, taken from each verb's docstring);
  3. the docs page.
  A new verb is therefore usable by SketchDynamics' planner on the next run, with no prompt editing.
- **Entry-point plugins**: third-party verbs ship as Python packages registered under the `motionlang.verbs` entry point. Namespaced names (`acme.spring_follow`) avoid collisions; core verbs are unprefixed.
- **Versioned**: each verb declares `since: "0.1"`. Documents declare `"motion": "0.1"` plus `"requires": ["acme.spring_follow>=1"]` for non-core verbs, so a missing plugin fails as `E_REF` at validation, not mid-render.
- **The contract test suite is the acceptance gate.** A verb that passes it is valid, whoever (or whatever agent) wrote it.

## 12. Worked example

User sketches a swoosh and says: *"the ball rolls along my line, bounces three times, then the logo fades in and pops."*

```json
{
  "motion": "0.1",
  "canvas": { "width": 1280, "height": 720, "fps": 60 },
  "strokes": { "s1": { "points": [[80,560],[400,380],[760,600],[1000,560]] } },
  "actors": [
    { "id": "ball", "shape": { "kind": "ellipse", "rx": 28, "ry": 28 }, "style": { "fill": "#e4572e" },
      "init": { "x": 80, "y": 560 } },
    { "id": "logo", "shape": { "kind": "text", "text": "ACME", "size": 96 }, "style": { "fill": "#222" },
      "init": { "x": 640, "y": 260, "opacity": 0, "sx": 0.6, "sy": 0.6 } }
  ],
  "timeline": { "id": "root", "seq": [
    { "id": "roll",   "verb": "move_along", "actor": "ball", "path": { "stroke": "s1" }, "orient": true, "duration": 1.6, "ease": "inOutSine" },
    { "id": "hop",    "verb": "bounce", "actor": "ball", "height": 90, "count": 3 },
    { "id": "reveal", "par": [
      { "id": "in",  "verb": "fade", "actor": "logo", "to": 1, "duration": 0.4 },
      { "id": "pop", "verb": "spring_to", "actor": "logo", "prop": "scale", "to": 1 } ] } ] },
  "constraints": [
    { "type": "in_bounds", "actors": "*", "margin": 8 },
    { "type": "total_duration", "max": 5 }
  ],
  "meta": { "intent": "the ball rolls along my line, bounces three times, then the logo fades in and pops" }
}
```

## 13. Decisions (Oct 17 review)

| # | Question | Decision | Rationale |
| --- | --- | --- | --- |
| 1 | Dimensions | **2D only** for v0.1 | 2D already supports rich animation; 3D later via vec2 → vec3 |
| 2 | Composition | **abs + abs on the same prop = `E_CONFLICT`**; relative layers on top | Raise an error instead of applying silent heuristics |
| 3 | Runtime | **Python-baked tracks**; browser plays tracks | (Neutral; chosen for a single implementation) |
| 4 | Cross-branch `after:` sync | **v0.2** | Revisit after hands-on authoring shows whether it's needed |
| 5 | Verb set | **+ `draw_on`, `camera`**; extensibility is a first-class goal (§11) | MotionHarness depends on adding verbs easily |
| 6 | Name | Language: **Motion**. Document key: `"motion": "0.1"`. Files: `*.motion.json`. Package: **`motionlang`** | `motion` is taken on PyPI and on npm (Motion, formerly Framer Motion, a popular animation library); `motionlang` is free on both |

## 14. Related work

### MoVer (Ma & Agrawala, SIGGRAPH 2025), Apache-2.0, `pip install mover`

A first-order-logic DSL for **verifying** spatio-temporal properties of motion graphics ("square translates right by 100 px, *before* the circle rotates"). Its pipeline: an LLM writes GSAP JavaScript → it renders and samples per-frame transforms → an LLM-synthesized MoVer program checks it → correction feedback → retry. It includes a 5,600-prompt dataset with ground-truth MoVer programs.

**How Motion relates**: complementary, not competing.
- MoVer verifies; Motion is what gets generated. MoVer's own generator writes *imperative GSAP code*, which is the thing Motion replaces with a declarative, constraint-checkable layer.
- MoVer's verifier input is a per-object sampled JSON (`tweens` with type/start/end frames, per-frame `CTM`, `AABB`, `transformedPts`). Our baked tracks already contain all of this, so a `to_mover` exporter is small. And because we know each verb's semantics, our `tweens` list is exact rather than inferred.
- **Benchmark opportunity (P2 MotionHarness)**: run a subset of the MoVer dataset through (a) LLM → GSAP and (b) LLM → Motion, both with Nemotron on Token Factory, and compare first-try and after-repair pass rates. Batch runs fit Nebius Serverless Jobs. This turns "our DSL is easier for LLMs" from a claim into a measured result.
- Checked in MoVer's source: the verifier reads `info.objects[] = {id, size, shape, fill, stroke}` and matches color by **substring on a color name** (`"royal blue"` matches `"blue"`). So the exporter must emit **named colors** (map hex → nearest CSS color name) and **shape words** (`ellipse` with rx = ry → `"circle"`, `rect` with w = h → `"square"`).

### OmniLottie (CVPR 2026), weights, MMLottie-2M dataset, MMLottieBench

A 4B VLM fine-tuned to generate Lottie from text/image/video, using a **parameterized Lottie token** representation because raw Lottie JSON is too verbose and fragile for language models.

**How Motion relates**:
- It independently confirms our premise that raw Lottie is a poor generation target. They solve it by **training a specialized model** on a compact token format; we solve it with an **intent-level DSL + a general LLM (Nemotron) + verification**. Ours needs no training, is human-readable and editable by id, and can be checked. Theirs captures complex shape/style detail that a 14-verb DSL can't. That's a fair contrast for the write-up.
- Not usable as our core model: it isn't an NVIDIA model, and it needs a ~15 GB GPU. Possible stretch baseline on Nebius AI Cloud.
- **MMLottie-2M / MMLottieBench**: a source of eval prompts and reference animations (check the dataset license), and possibly seed content for CanvasMind. Our Lottie exporter makes Motion outputs comparable on their benchmark.
