# Motion DSL × [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/)

Development workspace for three hackathon projects built on **Motion**, a declarative motion language designed for LLM generation and verification.

| Path | What |
| --- | --- |
| [`packages/motionlang`](packages/motionlang) | The Motion DSL: [spec](packages/motionlang/SPEC.md), validator, baker, edits, schema, preview player |
| `apps/sketchdynamics` (planned) | Sketch + talk → animation (Best Apps & Agents) |
| `apps/motionharness` (planned) | Coding agent that writes and tests Motion verbs in Token Factory Sandboxes (Coding & Agentic Engineering) |
| `apps/canvasmind` (planned) | Private creative-memory assistant (Personal AI) |

```bash
uv sync
uv run pytest
uv run motionlang preview packages/motionlang/examples/draw_and_zoom.motion.json -o out/preview.html
```

Planning docs: [docs/implementation-plan.md](docs/implementation-plan.md). Agent guidance: [AGENTS.md](AGENTS.md).

License: Apache-2.0
