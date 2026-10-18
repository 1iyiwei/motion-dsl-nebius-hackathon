# SketchDynamics

License: Apache-2.0 · Nebius x NVIDIA Global AI Hackathon, **Best Apps & Agents** track

Draw rough strokes, type what should happen, and get a playable animation in seconds. Then refine it by typing short changes: "slower", "more bounce", "make it blue".

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

## How Nemotron / Token Factory is used

| Step | Model (Token Factory) | What it does |
| --- | --- | --- |
| Plan | `nvidia/Nemotron-3-Ultra-550b-a55b` | Writes the whole MotionLang document from the prompt and the stroke geometry (JSON mode) |
| Repair | Ultra | Gets the validator's errors (code, block, message, hint) and fixes the document. Stops early if the same problem survives a repair |
| Edit | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | Turns "slower" into a few id-addressed edit ops (~1 s) instead of regenerating |
| Edit retry | Nano, reasoning on | If the ops don't apply or don't validate |
| Regenerate | Ultra | Last resort for a tweak the editor can't express |

All model calls go through `motionlang.agent` (OpenAI-compatible client pointed at Token Factory). The panel shows each call's model, latency, tokens, and the validator verdict, including the exact problems sent back for repair.

## API

| Endpoint | Body | Returns |
| --- | --- | --- |
| `POST /generate` | `{intent, strokes: {s1: {points: [[x,y],…]}}, canvas: {width, height}}` | outcome (below) |
| `POST /tweak` | `{doc, instruction}` | outcome |
| `POST /bake` | `{doc}` | `{doc, tracks, report}` with no model call |

Outcome: `{ok, path, note, seconds, tokens, attempts: [{kind, role, model, think, seconds, prompt_tokens, completion_tokens, ok, problems, issues}], doc, tracks, report}`. `tracks` is the baked player format from `motionlang` (see SPEC.md), or null if nothing could be baked. Add `?stream=1` to get NDJSON events instead (`call_start`, `call_end`, then `result` or `error`); the UI uses that to fill the panel live.

## Develop

```bash
uv run pytest apps/sketchdynamics -q     # backend tests with a scripted fake model (no network)
```

Layout: `src/sketchdynamics/server.py` (FastAPI app + CLI), `src/sketchdynamics/static/` (vanilla JS: `app.js` sketching/panel, `player.js` baked-tracks player, no build step).

## Roadmap

- **Redraw a path**: drag or redraw one stroke and re-bake with `POST /bake` (no model call), since paths reference strokes by id.
- **Voice**: speak the prompt and tweaks (Web Speech API in the browser).
- **Sketch understanding**: send a rendering of the canvas to `Nemotron-3.5-Super-VL` so it can tell a "ball" circle from a "path" line, and label strokes for the planner.
- **Motion references (Tavily)**: let the planner look up techniques ("squash and stretch timing", "logo reveal styles") and cite them in the panel.
- **Intent check (MoVer)**: translate the prompt into a MoVer program and verify the baked tracks, feeding failures into the same repair loop.
- **Export**: a Nebius Serverless job renders MP4/GIF/Lottie.
- **Hosted demo** so judges can try it without a key.

## Feedback on Token Factory

See the repo's feedback log; highlights will be copied here for the submission.
