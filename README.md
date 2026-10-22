# MotionLang DSL × [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/)

Development workspace for three hackathon projects built on **MotionLang**, a declarative motion language designed for LLM generation and verification.

| Path | What |
| --- | --- |
| [`packages/motionlang`](packages/motionlang) | The MotionLang DSL: [spec](packages/motionlang/SPEC.md), validator, baker, edits, schema, preview player |
| [`apps/sketchdynamics`](apps/sketchdynamics) | Sketch + talk → animation (Best Apps & Agents): `uv run sketchdynamics` |
| [`apps/motionharness`](apps/motionharness)  | Coding agent that writes and tests MotionLang verbs in Token Factory Sandboxes (Coding & Agentic Engineering) |
| [`apps/canvasmind`](apps/canvasmind/) | Private creative-memory assistant (Personal AI) |

```bash
uv sync
uv run pytest
uv run motionlang preview packages/motionlang/examples/draw_and_zoom.motion.json -o out/preview.html
```

Planning docs: [docs/implementation-plan.md](docs/implementation-plan.md). Agent guidance: [AGENTS.md](AGENTS.md).

License: Apache-2.0
