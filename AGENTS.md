# AGENTS.md

Guidance for AI coding agents (Claude Code and others) working in this repo.

## What this is

The single repo for all Nebius x NVIDIA Global AI Hackathon submissions (deadline **Fri Oct 30 2026, 10:00 am PT**), all built on one shared library. Each project is submitted as soon as it is ready, with Devpost linking to its `apps/<name>/` folder:

| Path | What | Track |
| --- | --- | --- |
| `packages/motionlang/` | **MotionLang**, the DSL: spec, schema, validator, baker, exporters | (shared library) |
| `apps/sketchdynamics/` | Sketch + talk → animation app (`uv run sketchdynamics`) | Best Apps & Agents |
| `apps/motionharness/` | Coding agent that writes, tests (Token Factory Sandboxes) and critiques (Nemotron VL) MotionLang verb plugins; CLI, dashboard (`uv run motionharness serve`), MCP | Coding & Agentic Engineering |
| `apps/canvasmind/` (planned) | Private creative-memory assistant (Hermes Agent + OpenShell/NemoClaw) | Personal AI |
| `apps/embodiedsketcher/` (maybe) | Sketches → robot/edge-agent motion, only if time allows | Physical AI |

Plans and rationale are in `docs/` (`implementation-plan.md`, `ideation.md`). `docs/` stays in this repo: if this repo goes public, so does `docs/`. Per-app public repos made with `scripts/export_submission.py` never include it.

## Hard rules

- **The spec is the contract.** `packages/motionlang/SPEC.md` describes the language. Any behavior change in `motionlang` updates the spec in the same change.
- **Apps depend on `motionlang` by name** (`motionlang>=0.1` in their `pyproject.toml`), never by path. Only the root `pyproject.toml` maps it to the workspace. Everything lives in this one repo for now, but we may split apps into their own repos for judging, so keep each app splittable: depend on `motionlang` by name only, never import from another app, and give each app its own README (setup, how Nemotron/Token Factory is used, feedback) and `.env.example`.
- **Submitted projects must keep working through judging (Dec 1–15, 2026).** Once an app is submitted, tag the repo (`<app>-submitted`) and don't make breaking `motionlang` changes without re-checking that app.
- **Secrets** live in `.env` (gitignored). Never commit keys, never print them, never put them in examples.
- **Models**: all LLM calls go through NVIDIA open models (Nemotron family) on Nebius Token Factory (OpenAI-compatible API). Don't add other LLM providers to app runtime paths.
- **License**: Apache-2.0 everywhere.
- Commit or push only when the human asks.

## Commands

```bash
uv sync                                   # install workspace (creates .venv)
uv run pytest                             # all tests
uv run pytest packages/motionlang -q      # library tests only
uv run motionlang validate FILE.motion.json
uv run motionlang bake FILE.motion.json -o tracks.json
uv run motionlang preview FILE.motion.json -o preview.html   # self-contained SVG player
uv run motionlang schema                  # JSON Schema (also the LLM structured-output schema)
uv run motionlang prompt                  # verb reference for LLM system prompts
uv run motionlang generate "INTENT" -o out/x.motion.json --preview out/x.html   # Nemotron (needs NEBIUS_API_KEY in .env)
uv run motionlang tweak out/x.motion.json "slower" -o out/y.motion.json        # fast edit path
RUN_LIVE=1 uv run pytest -m live          # live Token Factory test (costs tokens)
python3 scripts/export_submission.py sketchdynamics   # public, self-contained copy of one app → ../sketchdynamics-public
uv run motionharness new-verb "add a \`pendulum\` verb: …"   # write/test/critique a verb plugin → out/motionharness/<run>/plugin
uv run motionharness serve                # MotionHarness dashboard (port 8766)
uv run motionharness sandbox-check --run  # Token Factory Sandboxes access (needs NEBIUS_PROJECT_ID)
```

## motionlang architecture

```
parse.py     JSON → Document (pydantic) + block tree; schema errors with JSON pointers
timing.py    block tree → scheduled leaves (absolute start/end per verb)
evaluate.py  lazy per-(actor, prop, t) evaluation applying the composition rule
bake.py      evaluator → per-frame tracks (props, world/screen matrices, AABBs)
validate.py  tier-1 checks: refs, conflicts, discontinuities, constraints → Report
edits.py     id-addressed edit ops (set/insert/remove/wrap)
verbs/       Verb base class + registry (entry point group `motionlang.verbs`); core verbs in verbs/core.py
schema.py    JSON Schema + LLM prompt reference generated from the registry
preview.py   baked tracks → standalone HTML/SVG player
importers/   svg.py: SVG → assets (transforms flattened, arcs → cubics) + suggested group actors
agent/       LLM loop (optional extra `agent`): llm.py client + telemetry, prompts.py, loop.py generate/tweak
```

Adding a verb: subclass `Verb` in a module, decorate with `@register`, expose the module via the `motionlang.verbs` entry point, and give it `examples`. The contract suite (`motionlang.testing.contract`, run by `packages/motionlang/tests/test_contract.py`) runs automatically over every registered verb; `MOTIONLANG_CONTRACT_VERB=<name>` limits it to one.

## Conventions

- Python ≥ 3.11, type hints, pydantic v2, numpy. No other runtime deps in `motionlang` without a reason.
- Error messages are written for an LLM repair loop: every `Issue` has a concrete `message` and an actionable `hint`.
- Keep comments sparse; explain *why*, not *what*.
- Token Factory models (Oct 17): planner `nvidia/Nemotron-3-Ultra-550b-a55b`, editor `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`
  (call with `chat_template_kwargs.enable_thinking=false` for speed), vision `nvidia/Nemotron-3.5-Super-VL`.
  Also available: `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3_5-Lightning`.
- Log platform friction in `docs/token-factory-feedback.md` as you hit it (it feeds the submission's feedback section).
