# SketchDynamics

License: Apache-2.0 · Nebius x NVIDIA Global AI Hackathon, **Best Apps & Agents** track

Draw rough strokes or drop in your own SVG logo, type what should happen, and get a playable animation. Then refine it by typing short changes: "slower", "more bounce", "make it blue".

Under the hood the app writes a program in **MotionLang** ([`motionlang`](../../packages/motionlang)), a declarative motion language built for LLMs. NVIDIA Nemotron models on Nebius Token Factory write and edit it, and a deterministic validator checks it. The validator's errors carry hints, and those go back to the model until the animation is valid. The side panel shows every model call as it happens.

## Run it

```bash
uv sync                      # from the repo root
cp apps/sketchdynamics/.env.example .env    # then add your NEBIUS_API_KEY
uv run sketchdynamics        # serves http://127.0.0.1:8765/ and opens it
```

Options: `--port 8765`, `--host 127.0.0.1`, `--no-browser`. Outside this workspace, `pip install sketchdynamics` pulls `motionlang[agent]` from PyPI.

1. Draw on the canvas. Each stroke gets an id (`s1`, `s2`, …) you can mention in the prompt.
2. Type what should happen, e.g. "a ball rolls along my line and bounces", and press **Generate**.
3. Watch it play. Scrub, pause (space), toggle the sketch overlay.
4. Type a change ("slower") and press **Tweak**. Every result is a version chip you can go back to.

**Your own logo**: press **Import SVG logo** (or drop an `.svg` on the canvas, or try **Sample logo**). The importer splits it into parts, keeping the file's groups. Nemotron VL looks at the logo, names each part ("sun disc", "left ray") and groups parts that form one object, such as the sun disc and its rays. The planner animates the parts and groups by name: pieces pop in one by one, a filled shape wipes in (`reveal`), a wave draws itself. The path data stays on the server. The models only see part ids, labels, bounding boxes and colours, so a detailed logo costs no more tokens than a simple one.

**Groups** move as one rigid piece. "The sun rises" then needs one `move` on `sun` rather than six moves that must stay in formation. The import starts from the file's own `<g>` groups, VL suggests better ones, and you can change them: click parts to select them, press **Group**, or press × to dissolve a group. Hovering a part or group outlines it on the canvas. A group can have a **pivot**, the part it turns about: VL picks one for things that rotate (a turbine's rotor turns about its hub), or select a part in a group and press **Rotate … about this part**.

The model's grouping is a starting point, and grouping is partly a matter of taste, so all of it stays editable. With parts selected you can group them, click a group's name to add them to that group, take them out of their group, or split a part into its pieces (for example, a ring the importer kept with its hole). Double-click a group's name to rename it.

Icon SVGs often pack a whole object into one `<path>` per colour (all three blade fills in one path). The importer splits such compound paths into their pieces, keeping holes with their shapes. To tell 20+ pieces apart, VL gets two images: the logo, and the same logo with each part tagged by a number. It also gets the file name, which often says what the artwork is. Try **Wind turbine**: VL rigs a rotor (blades and hub, pivoting on the hub), a tower and the motion lines. **Cloud bolt** is the minimal case: two parts that should animate separately. Gradients become solid colours, and text, filters and masks are skipped for now; the app lists what it simplified.

## How Nemotron / Token Factory is used

| Step | Model (Token Factory) | What it does |
| --- | --- | --- |
| Name and group logo parts | `nvidia/Nemotron-3.5-Super-VL`, reasoning off | Looks at the rendered logo plus the part list, names each part, and groups parts that should move together (~2–3 s) |
| Plan | `nvidia/nemotron-3-super-120b-a12b` by default, with structured output (the MotionLang JSON Schema); `nvidia/Nemotron-3-Ultra-550b-a55b` on request | Writes the whole MotionLang document from the prompt, the strokes and the logo's parts and groups. Pick the planner next to **Generate**. When a run fails, **Retry with Ultra** re-runs it on the bigger model |
| Repair | the planner | Gets the validator's errors (code, block, message, literal fix) and patches the document with edit ops, or rewrites it when it's far off. Stops early if the exact same problems survive a repair |
| Edit | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | Turns "slower" into a few id-addressed edit ops (~1 s) instead of regenerating |
| Edit retry | Nano, reasoning on | If the ops don't apply or don't validate |
| Regenerate | the chosen planner | Last resort for a tweak the editor can't express |

All model calls go through `motionlang.agent` (OpenAI-compatible client pointed at Token Factory). The panel shows each call's model, latency, tokens, and the validator verdict, including the exact problems sent back for repair.

## API

| Endpoint | Body | Returns |
| --- | --- | --- |
| `POST /generate` | `{intent, strokes: {s1: {points: [[x,y],…]}}, canvas: {width, height}, assets?, actors?, planner?: "super" \| "ultra"}` | outcome (below) |
| `POST /tweak` | `{doc, instruction, planner?}` | outcome |
| `POST /split-part` | `{assets, groups, actors, part}` | `{assets, pieces, groups, actors, preview}`: one part split into its subpaths, in place and in its group |
| `POST /bake` | `{doc}` | `{doc, tracks, report}` with no model call |
| `POST /import-svg` | `{svg}` | `motionlang.importers.import_svg` output (`assets`, suggested `actors`, `dropped`) plus a still `preview` |
| `POST /label-parts` | `{image: "data:image/jpeg;base64,…", assets, groups?}` | `{labels: {id: label}, groups: [{id, label, parts}] or null, description, call}`; failures are soft (empty labels plus `error`) |
| `POST /regroup` | `{assets, groups: [{label, parts}], actors?}` | `{groups, actors, preview}`: the actor tree `logo → groups → parts`, with every part where it was drawn |

Outcome: `{ok, path, note, seconds, tokens, attempts: [{kind, role, model, think, seconds, prompt_tokens, completion_tokens, ok, problems, issues}], doc, tracks, report}`. `tracks` is the baked player format from `motionlang` (see SPEC.md). When no attempt validates, `doc`/`tracks` are the closest attempt that still plays, or null. Add `?stream=1` to get NDJSON events instead: `call_start` when a model call begins, `attempt` once its reply is validated (same fields as above), then `result` or `error`. The UI uses that to fill the panel live.

## Develop

```bash
uv run pytest apps/sketchdynamics -q     # backend tests with a scripted fake model (no network)
```

Layout: `src/sketchdynamics/server.py` (FastAPI app + CLI), `grouping.py` (semantic groups → actor tree), `src/sketchdynamics/static/` (vanilla JS, no build step: `app.js` for sketching, logo import and the panel; `samples/` for the sample logos: `sunrise.svg` is ours; `wind-energy-…` and `cloud-bolt-…` are from SVG Repo, so check their licenses before redistributing). The player is motionlang's own `player.js`, served at `/player.js`, so the app and `motionlang preview` play tracks identically.

## Roadmap

- **Redraw a path**: drag or redraw one stroke and re-bake with `POST /bake` (no model call), since paths reference strokes by id.
- **Voice**: speak the prompt and tweaks (Web Speech API in the browser).
- **Sketch understanding**: the logo labelling already sends a rendering to `Nemotron-3.5-Super-VL`; do the same for sketches so it can tell a "ball" circle from a "path" line.
- **Raster logos**: vectorize or segment a PNG so its parts can move separately.
- **Masks**: "rises from behind the waves" currently means drawn under the waves. A horizon mask would hide the sun below the line as well.
- **Select on the canvas**: click or lasso parts on the stage itself, not only the chips.
- **Motion references (Tavily)**: let the planner look up techniques ("squash and stretch timing", "logo reveal styles") and cite them in the panel.
- **Intent check (MoVer)**: translate the prompt into a MoVer program and verify the baked tracks, feeding failures into the same repair loop.
- **Export**: a Nebius Serverless job renders MP4/GIF/Lottie.
- **Hosted demo** so judges can try it without a key.

## Feedback on Token Factory

See the repo's feedback log; highlights will be copied here for the submission.
