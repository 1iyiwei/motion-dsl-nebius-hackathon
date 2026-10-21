# MotionLang: DSL Spec v0.1 (DRAFT, decisions of Oct 17 applied)

A declarative, intent-level motion language that LLMs write, machines verify, and runtimes play.
It is shared by SketchDynamics (P1, produces it), MotionHarness (P2, extends it with new verbs and exporters) and CanvasMind (P3, indexes it).

> Decisions from the Oct 17 review are in §13. Related work (MoVer, OmniLottie, LogoMotion, fog) is in §14.
> Reference implementation: the `motionlang` Python package next to this file. Where this spec and the code disagree, that's a bug in one of them; fix both together.

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

MotionLang sits above these and compiles down to them.

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
  "assets": { "swoosh": { "kind": "path", "d": "M...", "fill": "#e4572e", "bbox": [x, y, w, h], "label": "Swoosh" } },
  "actors": [ ... ],
  "timeline": { ... },
  "constraints": [ ... ],
  "meta": { "title": "...", "intent": "original user request, kept for traceability" }
}
```

`strokes` holds raw sketch input (vector points from the canvas). Paths reference them by id, so redrawing a stroke changes the motion without regenerating the timeline.

`assets` holds imported vector parts, typically the pieces of a user's logo, in canvas coordinates. Each is `{kind: "path", d, fill?, stroke?, stroke_width?, fill_rule? ("nonzero" | "evenodd"), bbox? [x, y, w, h], label?, meta?}`; `d` accepts any SVG path command, `bbox` is informational (geometry always comes from `d`), and `label` names the part for people and models. Actors show an asset by reference (`shape: {kind: "asset", ref}`), so a document can be animated part by part without an LLM ever reading or writing path data: the agent shows the model only each asset's id, label, bbox and colours, tells it to reference assets by id, and merges the user's assets back into every document it returns (§3.1).

### 3.1 Importing SVG (`motionlang.importers.svg`)

`import_svg(text, fit=None, margin=0.1, split="auto") -> {canvas, assets, actors, dropped, notes}` (CLI: `motionlang import-svg logo.svg --fit 1280x720 -o logo.json`, or `--doc` for a complete static document):

- One asset per drawn element (`path`, `rect` incl. rounded corners, `circle`, `ellipse`, `line`, `polyline`, `polygon`, and `<use>` of those). Every transform on the way down is flattened into the path data, and all shapes and arcs become absolute `M/L/C/Q/Z` data.
- **Compound paths are split** (`split="auto"`, the default). Icon files often pack a whole colour layer into one `<path>` with many subpaths. Each subpath becomes its own asset (`<id>_1`, `<id>_2`, ...), and holes stay with their shape: a filled subpath whose bbox lies inside another's and winds the other way (or any inside subpath under `evenodd`) is kept in that shape's path data. The original element becomes a suggested group of its pieces, and `notes` reports each split. `split="none"` keeps one asset per element. Every asset records `meta.source`, the element it came from.
- Ids come from the element's `id`, else `inkscape:label`, else a meaningful class (auto classes like `cls-1`/`st0` are skipped), else `<tag><n>`; they are made unique and never `camera`. `label` comes from `inkscape:label`, a `<title>` child, or the id.
- Paint: presentation attributes, simple `<style>` rules (tag, `.class`, `#id`) and inline `style`, with inheritance. Gradients and patterns fall back to a solid colour (a gradient's middle colour); `opacity` × `fill-opacity` becomes the actor's `init.opacity`. Filters, clip paths, masks, text and images are not imported; each one is listed in `dropped`, and so is each gradient fallback.
- `actors` is a suggested hierarchy: one `group` actor per named `<g>` (anonymous single-child wrappers collapse), a root group (`logo`) when there is more than one top-level part, and one asset actor per part. Group actors sit at the centre of their parts, so rotating or scaling a group pivots naturally; asset actors carry their resting `init.x/y` explicitly (the drawn spot in the parent's frame, the same value that omitting them gives), so a model can plan moves with real numbers.
- `fit=(W, H)` scales the artwork uniformly and centres it in a W×H canvas, leaving `margin` (a fraction of the canvas) on each side. Without it, coordinates stay in viewBox units (the viewBox origin moves to 0, 0).
- Standard-library XML only; documents that declare XML entities are rejected.

## 4. Actors

```json
{ "id": "ball", "shape": { "kind": "ellipse", "rx": 30, "ry": 30 },
  "style": { "fill": "#e4572e" },
  "init": { "x": 100, "y": 500, "rotation": 0, "sx": 1, "sy": 1, "opacity": 1 },
  "pivot": [0.5, 0.5], "parent": null, "z": 0 }
```

- `shape.kind` and its fields: `rect {w, h, r?}`, `ellipse {rx, ry}`, `polygon {points}`, `path {d, closed?}` (any SVG path command, arcs included), `text {text, size?, font?, weight?}`, `image {url, w, h}`, `stroke {ref}` (a sketch stroke), `asset {ref}` (an entry of `document.assets`), `group {}` (no geometry; a parent for others).
- `label` (optional): what the actor depicts, e.g. a logo part's name.
- **Placement**: each shape has a local bounding box `[0, w] × [0, h]`. `pivot` is a point in that box as fractions (default center). The actor's `x, y` is **where its pivot sits** in its parent's space: world = parent · T(x, y) · R(rotation) · S(sx, sy) · T(−pivot).
- **`init` defaults**: rotation 0, sx = sy = 1, opacity 1, trim 1, clips 0. If `x`/`y` are omitted, drawn shapes (`stroke`, `polygon`, `path`, `asset`) stay exactly where they were drawn; other shapes start at the canvas center. For a drawn shape with a `parent`, "where drawn" is expressed in the parent's frame at rest (its initial position and rotation, unit scale), so a part stays in place inside its group even if the group starts at scale 0 and pops in.
- **`style` defaults**: `fill` `#333333` for closed shapes (rect, ellipse, polygon, text, closed path), none for open strokes; `stroke` `#333333` for open strokes/paths, none otherwise; `stroke_width` 4. Asset actors take `fill`, `stroke`, `stroke_width` and `fill_rule` from their asset; `style.fill`/`style.stroke` override them.
- Children (`parent`) are positioned in the parent's local box and inherit its transform and opacity.
- **Animatable properties**: `x, y, rotation, sx, sy, opacity, fill, stroke`, plus `trim` (0–1, visible fraction of a path/stroke outline) and `clip_l, clip_t, clip_r, clip_b` (0–1, the hidden fraction of the shape's bounding box from the left, top, right and bottom; the clip is applied in the shape's own frame, so it rotates and scales with it). These are the only things verbs touch.
- **Built-in `camera` actor**: always present, with props `x, y, zoom, rotation` (initially centered on the canvas, zoom 1). Constraints are checked on the stage (before the camera), so the camera can frame freely.
- `parent`: hierarchical transforms (a child moves with its group). `pivot`: rotate/scale origin, as a fraction of the bounding box. A `group` has no geometry, so it rotates and scales about its own `x, y`: to spin a group around a point (a hub), put the group's `x, y` there and give its children offsets from it.

## 5. Timeline: blocks

The timeline is a **tree**. Containers arrange time; verbs (leaves) change properties.

| Container | Meaning |
| --- | --- |
| `seq` | Children one after another |
| `par` | Children start together; duration = the longest child |
| `stagger` | Like `par`, but child *i* starts at `i × offset` |
| `repeat` | `{"id", "repeat": <one block>, "count": 2}` plays the block `count` times (`"inf"` loops in the player; one cycle is validated) |

`stagger` takes `offset` (default 0.1 s). Each iteration of a repeat starts from wherever the previous one ended, so a repeated `rotate by 90` accumulates while a repeated `move to` only moves once.

Every block has `id` (required, unique), plus optional `delay` (s, before it starts) and `label`.

```json
{ "id": "root", "seq": [
    { "id": "b1", "verb": "move_along", "actor": "ball", "path": { "stroke": "s1" }, "duration": 1.2, "ease": "inOutCubic" },
    { "id": "b2", "par": [
        { "id": "b3", "verb": "bounce", "actor": "ball", "height": 80, "count": 3 },
        { "id": "b4", "verb": "fade", "actor": "logo", "to": 1, "duration": 0.5, "delay": 0.3 } ] } ] }
```

**Sync**: v0.1 uses tree position only. An `after: "<blockId>"` field for cross-branch sync would be more expressive but makes timing a constraint-solving problem. **Deferred to v0.2**, to be revisited once hands-on authoring shows whether it's needed.

## 6. Verbs (v0.1 core set: 17)

Verbs are MotionLang's **kinemes**. The word comes from Ray Birdwhistell's kinesics, where a kineme is the smallest unit of movement that carries meaning, as a phoneme is for sound. Each verb is one such unit (a bounce, a reveal, a spring into place) and has a fixed, checkable meaning. A timeline composes kinemes into larger motion, by sequencing and by the composition rule below.

Common fields: `actor`, `duration` (default per verb, usually 1 s), `ease` (default `inOutQuad`; `orbit` defaults to `linear`; physical verbs ignore it), `delay`.
Any verb may target the built-in `camera` actor when its props are camera props (`x, y, zoom, rotation`), e.g. `spring_to` on `zoom`.
Most property verbs take **either** `to` (absolute target) **or** `by` (relative delta), never both.

| Verb | Params | Composes as (§ rule below) | Notes |
| --- | --- | --- | --- |
| `move` | `to:[x,y]` \| `by:[dx,dy]` | abs (`to`) / rel (`by`) | |
| `move_along` | `path`, `orient:bool=false`, `reverse:bool=false`, `relative:bool=false` | abs (rel if `relative`) | Constant speed along the path's length, shaped by `ease`. `orient` turns the actor as the path turns, keeping its starting heading (so pre-rotate an arrow to face the path's initial direction). If the actor isn't at the path start → `E_DISCONTINUITY` (fix: add a `move` first, or set `relative: true` to translate the path to the actor) |
| `rotate` | `to` \| `by` (deg) | abs / rel | |
| `scale` | `to` \| `by` (number or `[sx,sy]`) | abs / rel | |
| `fade` | `to` (0–1) | abs | |
| `recolor` | `prop: fill\|stroke`, `to: color` | abs | Interpolated in OKLab |
| `orbit` | `center:[x,y]` \| `around:actorId`, `radius?`, `turns=1`, `start_angle?` | abs | `radius`/`start_angle` default to the actor's current distance and angle, so it starts without a jump. Giving them explicitly moves the start point → `E_DISCONTINUITY` unless the actor is already there |
| `bounce` | `height`, `count=3`, `decay=0.6`, `squash=0.15` | rel | Physically shaped arcs in place; squash-and-stretch on landing |
| `wiggle` | `amplitude`, `frequency=4`, `props=[x,y]`, `seed` | rel | Smooth noise, returns to rest at both ends. Amplitude is px for x/y, degrees for rotation, hundredths for sx/sy/opacity |
| `spring_to` | `prop`, `to`, `stiffness=170`, `damping=26` | abs | Duration is the time to settle within 0.1%; a given `duration` time-scales it. Damping ratio must be ≥ 0.05 |
| `follow` | `target:actorId`, `lag=0.2`, `offset:[dx,dy]` | abs | Tracks another actor's position with delay |
| `draw_on` | `from=0`, `to=1` | abs (deliberate jump to `from`) | Reveals a `stroke`/`path`/`polygon`/`asset` outline progressively (animates `trim`), the classic "line draws itself" effect |
| `reveal` | `direction=left_to_right` (`right_to_left`, `top_to_bottom`, `bottom_to_top`, `from_center`), `from=0`, `to=1` (visible fraction) | abs (deliberate jump to `from`) | Wipes a shape into view behind a moving straight edge (a directional mask over its bounding box), so it works on filled shapes, where `draw_on` only traces outlines. `from_center` opens from the centre on both axes. `from: 1, to: 0` wipes it out. Not for groups: reveal each child |
| `enter` | `from=left` (`right`, `top`, `bottom`, `scale`, `fade`), `distance?` (px) | abs (deliberate jump to the start pose) | Brings an actor (or a whole group) in from just off-canvas, or from scale 0 / transparent, to where it rests: its pose just before the block, normally its init. Hidden until the block starts. The default distance is 1.1× the canvas width/height, scaled by the parents' scale, so a part resting on the canvas starts fully off it. Default ease `outCubic` |
| `exit` | `to=right` (same choices), `distance?` | abs | The mirror: from where it is to just off-canvas (or scale 0 / transparent). Default ease `inCubic` |
| `camera` | `to:{x?,y?,zoom?,rotation?}` \| `by:{...}` | abs / rel | Pans/zooms/rotates the view; targets the built-in `camera` actor, so `actor` is omitted |
| `hold` | `duration` | — | Explicit wait |

**Hidden until their turn**: when `draw_on`, `reveal` or `enter` is the first absolute verb on its property, its starting value also applies before it starts. A logo part revealed at t = 1.2 s is therefore hidden from t = 0, not visible and then cut. (Verb hook: `holds_before`.)

**Property aliases**: `position` = `x,y`; `scale` = `sx,sy`. They can be used anywhere a `prop` is named.

**Easing set**: `linear`; Penner `in/out/inOut × Quad, Cubic, Quart, Sine, Expo, Back, Elastic, Bounce`; `cubicBezier(x1,y1,x2,y2)` given as `{"bezier":[...]}`.

**Path forms**: `{ "stroke": "s1" }` (smoothed with Catmull-Rom), `{ "points": [[x,y],...], "smooth": true }`, `{ "svg": "M ... " }`, `{ "arc": { "center":[x,y], "radius": r, "from": deg, "to": deg } }`.

**Path space**: any path form may set `"space": "canvas"` or `"space": "parent"`. Canvas space is where the user drew, and is the default for `stroke`. Parent space is the actor's own `x, y` space, and is the default for `points`, `svg` and `arc`. For an actor with a parent, a canvas-space path is mapped through the parent's world transform at the block's start, so a grouped logo part follows a drawn line exactly where it was drawn. The actor must still start at the path's first point (in its own space), unless `relative: true`. The `E_DISCONTINUITY` hint gives the exact `move` to get there.

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
| `in_bounds` | `actors="*"`, `margin=0`, `ignore=[block ids]` | Any part of a visible actor's bounding box leaves the canvas. Blocks in `ignore` are deliberate off-screen motion: their actor (and its parts, for a group) isn't checked while they run. `enter`/`exit` from or to an edge are exempt automatically: the actor isn't checked until its entrance ends, or from when its exit starts. While an actor follows a drawn stroke (`move_along` on a canvas-space stroke), and afterwards while it stays where the line left it, only its pivot (the point on the line) must stay on the canvas: the user's drawing decides where it goes |
| `max_speed` | `actor`, `px_per_s` | The speed exceeds the limit at any sample |
| `smooth` | `actor`, `max_jerk` | The third derivative of position spikes (catches visual "pops" at block seams) |
| `no_overlap` | `a`, `b`, `during:[t0,t1]?` | The bounding boxes intersect |
| `ends_at` | `actor`, `pos:[x,y]`, `t?`, `tol=2` | The final (or time-`t`) position is off target |
| `total_duration` | `max` \| `exact`, `tol` | The timeline length is outside the limit |

Built-in checks (always on): schema validity, unique ids, existing references (actors, parents, strokes, assets: an unknown asset `ref` is `E_REF` with the defined asset ids and a "did you mean"), readable asset path data, visibility (`E_INVISIBLE`: an actor with a shape that is never visible at any frame, e.g. `init.opacity: 0` with nothing fading it in), no-ops (`E_NOOP`: a verb whose effect is zero over its whole run, e.g. `move` to where the actor already is, which almost always means a missing starting pose), `E_CONFLICT`, and continuity (no jump at a block boundary unless one was asked for). Timelines over 300 s are refused (`E_DURATION`) before baking.

All constraints use **world space** (canvas coordinates, before the camera), so `in_bounds` means "stays on the stage". Camera moves are deliberate framing: zooming in on one actor may push others out of frame without breaking `in_bounds`. `in_bounds` also ignores frames where the actor is invisible (opacity ≤ 0.01), so things may wait off-stage while hidden. Positions mean the actor's pivot.

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

Also: `set` on an actor id (`"field": "init.x"`), `set` with a JSON `pointer` for anything else (`"/canvas/fps"`; a final `-` appends to a list, e.g. `"/actors/-"` adds an actor), `unset` by id + field or by `pointer`, and `replace` (`{"op": "replace", "id", "block"}`). On any failing op nothing is applied and a single `E_EDIT` names the op index.

**Suggested actors are kept.** When an app passes suggested actors (an imported logo's tree), the agent fills in what the model's document leaves out, by id: missing or `null` `init` keys, `shape`, `parent`. A suggested actor the model dropped entirely is added back on `generate` (not on a tweak's regenerate, where removing it may be the point). Constrained decoding tends to emit `null` for optional fields, and losing a group's offsets breaks the drawn layout. The model only has to state what it changes, such as a part's starting pose.

**Containers written as verbs are rewritten.** A block like `{"id": "b2", "verb": "par", "children": [...]}` (the list may also be under the container's own key, or `blocks`, `items`, `steps`, `body`) becomes `{"id": "b2", "par": [...]}` before validation. When there's no list to move, the `E_REF` hint says the word is a container, with the exact shape to write.

**Repairs use the same ops.** When the planner's document has the right top-level shape and at most 12 problems, the agent asks for edit ops that fix them instead of a rewritten document, because whole-document rewrites drift away from what was already right. A rewrite is requested when the top level is wrong (missing `timeline`, unknown top-level keys), when there are many problems, or once when patching stalls. A stall is the identical problem set (code, block, actor and pointer) surviving a round.

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

Error codes: `E_SCHEMA, E_DUP_ID, E_REF, E_CONFLICT, E_DISCONTINUITY, E_INVISIBLE, E_NOOP, E_BOUNDS, E_SPEED, E_JERK, E_OVERLAP, E_ENDS_AT, E_DURATION`, plus `E_EDIT` from edit ops. Warnings use the same codes with `"severity": "warning"`.
Each error carries `hint`, a sentence the repair prompt includes word for word. Hints are literal and structural: they name the exact change, with real ids, paths and numbers (`remove "opacity" from actors[3].init`, `add the block {"id": "ray1_fade", "verb": "fade", "actor": "ray1", "to": 1, ...}`, `change b1's "to" to about [212, -40] (sun moves in logo's coordinates)`), and they never use prose that reads like a field or verb name. When an error has no block or actor, its text includes the JSON pointer. A part pushed off-screen by its parent group's motion is blamed on that motion, and an `in_bounds.ignore` entry for a group's block also exempts the group's parts.

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

**Raster output**: `motionlang.render` (`pip install motionlang[render]`, Pillow) draws the same tracks to frames: `to_frames(tracks, fps=None, width=None)` returns PIL images, and `to_gif(tracks, fps=20, width=480, loop=True)` returns GIF bytes sized for chat. The CLI is `motionlang render FILE.motion.json --gif out.gif [--width 480] [--fps 20] [--once]`. It matches the reference player for fills (holes by fill rule), strokes, opacity, `trim`, `clip` and transforms; `image` actors aren't drawn, and text uses a system sans-serif font.

**Reference player**: `motionlang/static/player.js`, an ES module exporting `Player(layer, {onFrame, onState})` with `load(tracks)`, `play()`, `pause()`, `toggle()`, `seek(i)` and `loop`. It implements every track, including `clip`. Python exposes it as `motionlang.preview.PLAYER_JS` (source) and `player_js_path()` (for apps to serve), and `preview_html` embeds the same file, so apps and previews can't drift apart.

**Baked tracks format** (`motionlang bake`): `{motion, fps, duration, frames, loop, canvas, actors[], tweens[]}`. Each actor is `{id, geom, style, tracks: {m, opacity, fill, stroke, trim, clip}}`, sorted by `z`. `geom` is the drawing primitive in bbox-local coordinates plus its bbox size `bw, bh` (asset paths keep their curves; `fill_rule` is included for paths). `clip` is `[l, t, r, b]`. `m` is the bbox-local → screen matrix `[a, b, c, d, e, f]` (SVG order), and `opacity` already includes parents. A track that never changes is stored once; otherwise it's one value per frame. `tweens` lists every scheduled verb with `{block, verb, actor, start, end, props}`, which is what the MoVer exporter needs.

## 11. Verb plugin contract (what MotionHarness writes)

A verb is a class registered with `@register`:

```python
@register
class Bounce(Verb):
    """Hop in place with gravity-shaped arcs ... (first paragraph = LLM-facing summary)"""
    name = "bounce"                       # third-party verbs must be namespaced: "acme.spring_follow"
    Params = BounceParams                 # pydantic model (extra fields forbidden) → JSON Schema
    uses_ease = False                     # physical timing; ignore `ease`
    examples = [{"height": 80, "count": 3}]   # used by docs, the prompt reference and the contract tests
    def mode(self, p): return "rel"       # "abs" | "rel" (may depend on params, e.g. to vs by) | None
    def props(self, p): return ("y", "sx", "sy")
    def default_duration(self, p): ...
    def sample(self, p, u, t, ctx) -> dict: ...
    # optional: jumps_at_start, holds_before, offstage, actor_refs, path_specs, check_target
```

`sample(p, u, t, ctx)` gets progress `u ∈ [0, 1]` (already eased if `uses_ease`), local time `t` in seconds, and a context: `ctx.start` (the composed values of its props just before it starts), `ctx.t0`, `ctx.duration`, `ctx.value(actor, prop, t)` (read other actors), and `ctx.path(spec)` (resolved, arc-length-parameterized paths). abs verbs return values; rel verbs return deltas.

Every verb must pass the generic **contract test suite**. It ships in the package as `motionlang.testing.contract` (`pip install motionlang[testing]`): a plugin's test module runs it with `from motionlang.testing.contract import *`, and `MOTIONLANG_CONTRACT_VERB=<name>` limits it to one verb. MotionHarness runs it in Token Factory Sandboxes. It checks:

1. **Valid + deterministic**: each example validates on a default actor (so it must visibly change something, or it fails as `E_NOOP`), and baking twice gives identical tracks.
2. **Identity at start**: a rel verb returns 0 at u=0; an abs verb returns `ctx.start` at u=0 (unless `jumps_at_start`).
3. **Continuous**: the largest step between samples must shrink under 8× finer sampling. A real jump doesn't shrink.
4. **Bounded**: finite output for any numeric params within their declared bounds (property-based, with Hypothesis).
5. **Schema round-trip**: example params validate, dump and re-validate; the verb has a docstring summary.
6. **Golden frames** (planned): rendered frames match a reference once a verb is accepted; for new verbs, a vision-model critique is used instead.

Exporters follow the same pattern: `Exporter.export(tracks) -> bytes`, plus round-trip tests.

### Designed for adding verbs

Adding a verb must not require touching the core. That's what makes MotionHarness possible.

- **Registry is the single source of truth.** At startup, the core collects every registered `Verb` and generates from it:
  1. the JSON Schema (a discriminated union on `verb`), which is also the structured-output schema sent to Token Factory;
  2. the **verb reference section of the LLM system prompt** (name, params with defaults, composes-as, one example each, taken from each verb's docstring);
  3. the docs page.
  A new verb is therefore usable by SketchDynamics' planner on the next run, with no prompt editing.
- **Entry-point plugins**: third-party verbs ship as Python packages registered under the `motionlang.verbs` entry point. Namespaced names (`acme.spring_follow`) avoid collisions and are enforced at registration; core verbs are unprefixed.
- **Versioned**: each verb declares `since: "0.1"`. Documents declare `"motion": "0.1"` plus `"requires": ["acme.spring_follow>=1"]` for non-core verbs, so a missing plugin fails as `E_REF` (unknown verb) at validation, not mid-render. A plugin verb that's used but not listed in `requires` gets a warning.
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
| 6 | Name | Language: **MotionLang** (called "Motion" until Oct 18; renamed because the word is generic and is also the name of a popular animation library). Document key: `"motion": "0.1"`. Files: `*.motion.json`. Package: **`motionlang`** | `motion` is taken on PyPI and on npm (Motion, formerly Framer Motion, a popular animation library); `motionlang` is free on both |
| 7 | Bring-your-own artwork (Oct 18) | **`assets` by reference**, imported from SVG; the model sees id/label/bbox/colour only | Path data is long and fragile: the LLM must never echo it. Parts stay editable by id, like strokes |

## 14. Related work

### MoVer (Ma & Agrawala, SIGGRAPH 2025), Apache-2.0, `pip install mover`

A first-order-logic DSL for **verifying** spatio-temporal properties of motion graphics ("square translates right by 100 px, *before* the circle rotates"). Its pipeline: an LLM writes GSAP JavaScript → it renders and samples per-frame transforms → an LLM-synthesized MoVer program checks it → correction feedback → retry. It includes a 5,600-prompt dataset with ground-truth MoVer programs.

**How MotionLang relates**: complementary, not competing.
- MoVer verifies; MotionLang is what gets generated. MoVer's own generator writes *imperative GSAP code*, which is the thing MotionLang replaces with a declarative, constraint-checkable layer.
- MoVer's verifier input is a per-object sampled JSON (`tweens` with type/start/end frames, per-frame `CTM`, `AABB`, `transformedPts`). Our baked tracks already contain all of this, so a `to_mover` exporter is small. And because we know each verb's semantics, our `tweens` list is exact rather than inferred.
- **Benchmark opportunity (P2 MotionHarness)**: run a subset of the MoVer dataset through (a) LLM → GSAP and (b) LLM → MotionLang, both with Nemotron on Token Factory, and compare first-try and after-repair pass rates. Batch runs fit Nebius Serverless Jobs. This turns "our DSL is easier for LLMs" from a claim into a measured result.
- Checked in MoVer's source: the verifier reads `info.objects[] = {id, size, shape, fill, stroke}` and matches color by **substring on a color name** (`"royal blue"` matches `"blue"`). So the exporter must emit **named colors** (map hex → nearest CSS color name) and **shape words** (`ellipse` with rx = ry → `"circle"`, `rect` with w = h → `"square"`).

### OmniLottie (CVPR 2026), Apache-2.0, 4B weights, MMLottie-2M dataset, MMLottieBench

Code: <https://github.com/OpenVGLab/OmniLottie>. Project page: <https://omnilottie.com/>.

A 4B VLM fine-tuned to generate Lottie from text/image/video, using a **parameterized Lottie token** representation because raw Lottie JSON is too verbose and fragile for language models.

**How MotionLang relates**:
- It independently confirms our premise that raw Lottie is a poor generation target. They solve it by **training a specialized model** on a compact token format; we solve it with an **intent-level DSL + a general LLM (Nemotron) + verification**. Ours needs no training, is human-readable and editable by id, and can be checked. Theirs captures complex shape/style detail that a 17-verb DSL can't. That's a fair contrast for the write-up.
- Not usable as our core model: it isn't an NVIDIA model, and it needs a ~15 GB GPU (its README reports about 8 s per 256 Lottie tokens). Possible stretch baseline on Nebius AI Cloud.
- **Logos**: its image-to-Lottie mode redraws the artwork as part of the generated Lottie, so the output only approximates the user's logo. MotionLang keeps the user's exact paths by reference (`assets`, §3) and generates only the motion.
- **MMLottie-2M / MMLottieBench**: a source of eval prompts and reference animations (check the dataset license), and possibly seed content for CanvasMind. Our Lottie exporter makes MotionLang outputs comparable on their benchmark.

### LogoMotion (Liu, Kazi, Wei, Fisher, Langlois, Walker, Chilton; arXiv 2024, rev. 2025)

Paper: [arXiv 2405.07065](https://arxiv.org/abs/2405.07065).

LogoMotion generates animation code for a logo's parts with **visually-grounded code synthesis and program repair**. It analyses the logo visually, writes down a design concept, generates the animation code, then checks the result visually and repairs it. Code-connected AI editing widgets let novices adjust the motion, grouping and timing afterwards. In a comparison on 276 animations it produced more content-aware animation than an industry-leading tool. In a user study (n=16), the widgets supported control, iteration and creative expression better than a prompt-only baseline.

**How MotionLang relates**: MotionLang's bring-your-own-logo path (§3, §3.1) follows LogoMotion's pipeline. The artifact is a checkable document instead of code:
- **Parts, grounded.** LogoMotion grounds generation in the logo's actual parts. MotionLang does the same with `assets`: the importer splits the SVG into parts and keeps the `<g>` hierarchy as suggested group actors. Labels can come from the file or from a vision model (Nemotron-3.5-Super-VL in SketchDynamics). The model plans over ids, labels, bounding boxes and colours, and never reads or writes path data, so it can't damage the artwork.
- **Synthesis and repair.** LogoMotion repairs code after checking it visually. MotionLang repairs a declarative document using structured validator errors with hints (§9): deterministic checks such as bounds, conflicts, discontinuities and never-visible parts (`E_INVISIBLE`). A rendered-frame critique by a vision model would fit as a third tier above §7's two.
- **Editing.** LogoMotion's code-connected widgets for motion, grouping and timing map onto id-addressed edit ops (§8): `set` a block's `duration` or `delay`, `set` an actor's `parent` to regroup, `wrap` blocks in a `stagger`. A widget can therefore emit ops directly, without regenerating anything.
- **Logo vocabulary.** Logo reveals mostly wipe filled shapes, so MotionLang added `reveal` (a directional mask) next to `draw_on`. Parts revealed later stay hidden until their turn (§6).
- **Evaluation.** Its 276-animation comparison protocol is a template for evaluating SketchDynamics' logo mode against a prompt-only or code-generating baseline.

### fog (Liu & Chilton, 2026, under submission): motion and emotion through function composition

Paper: [arXiv 2607.07952](https://arxiv.org/abs/2607.07952). Project page: <https://fog-motion-emotion.vercel.app/>.

An LLM writes motion *functions* for an open-ended vocabulary of Verbs (chase, avoid, swoop), Adverbs (hesitantly, forcefully), Gestures (nod, wave) and Emotions (anger as tremor → lunge → recoil), applied to Heider-Simmel-style shapes. Functions compose like f(g(x)): an Adverb wraps a Verb (`hesitantly(follow)`) and reshapes its dynamics through shared *channels* (speed, wait, easing, acceleration, offset). Each entity has one **PrimaryMotion** and several **SecondaryMotion** slots (Adverbs, Gestures, Emotions decorate the primary). The engine integrates velocity over time, with choreography guarantees such as arriving from A to B in a set time. In a perceptual study, people recognised the intended meaning 68% of the time (2.68× chance); composed motions scored lower (58–70%), because motions contributed by different functions can overlap and cancel out.

**How MotionLang relates, especially on composition**:
- **Same split, different layer.** fog's primary/secondary split is close to our abs/rel rule (§6): an absolute verb is the primary motion of a property, and relative verbs (wiggle, bounce, `by`) layer on top, like fog's secondary motions. The difference is where composition happens. fog composes *functions* through a channel contract, so an adverb changes how a verb runs. MotionLang composes *property values* over time with a fixed rule, which can be checked and edited by id.
- **Conflicts are explicit.** fog reports that composed functions can cancel each other out. MotionLang makes the dangerous case an error: two absolute verbs on the same property at the same time is `E_CONFLICT`, with both block ids in the message. Relative layers still add and can cancel, so the tier-2 intent check (MoVer, §7) is what catches "valid but means something else".
- **Open vocabulary, gated.** fog generates new functions on the fly, and found that this randomness can hurt the user experience. MotionHarness also has an LLM write new verbs, but only as registered plugins that must pass the contract suite (§11) before the planner sees them. MotionLang documents stay declarative data, and code lives only in verbs.
- **Closed form vs. integration.** fog integrates velocity (x = ∫v dt), which suits reactive, agent-like motion (chase, flee). MotionLang evaluates each property at any t in closed form, which makes baking deterministic and lets the validator sample constraints exactly. Relational verbs here (`follow`, `orbit`) read other actors' values at t instead.
- **Ideas to borrow (v0.2 candidates).** (1) An *adverb* layer: a container or field that reshapes the timing of the blocks it wraps (speed, easing, hesitation pauses, overshoot) without changing what they do, e.g. `{"adverb": "hesitantly", "of": block}`. This maps closely to fog's channel contract and is easy for an LLM to say. (2) *Gestures* as named relative verbs (nod, shrug, perk up) that layer on any primary motion. (3) *Emotions* as macros that expand to phased verb sequences (tremor → lunge → recoil), so they stay checkable.

