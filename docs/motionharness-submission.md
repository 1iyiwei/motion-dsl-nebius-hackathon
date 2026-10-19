# MotionHarness: submission draft (Coding & Agentic Engineering)

Draft for the Devpost form (fields as in `docs/submission-form.md`). Numbers marked TODO get filled from the final recorded run.

## General info

**Project name** (≤60): MotionHarness

**Elevator pitch** (≤200): A coding agent that extends an animation language: Nemotron writes new motion verbs and their tests, runs them in Token Factory Sandboxes, watches the motion with a vision model, and ships a plugin.

## Project details

Describe a new motion behaviour in one sentence, for example "add a `spring_follow` verb: the actor chases a target with spring physics, overshooting a little". MotionHarness has NVIDIA Nemotron write several candidate implementations as MotionLang plugins, each with its own tests and a demo scene. It runs every candidate in a sandbox branched from one prepared checkpoint, against a fixed contract test suite and the candidate's own tests. Nemotron VL then looks at the rendered motion. The agent repairs what fails, polishes what the critic rejects, and ships the winner as an installable package with a diff, a test report and a preview. A dashboard shows the branch tree, every test, the frames the critic saw and every model call.

### What it does

MotionHarness is for technical artists and graphics developers who extend a motion system. Its target is **MotionLang**, the declarative 2D motion language behind our SketchDynamics app. In MotionLang, verbs (`bounce`, `spring_to`, `orbit`, …) are entry-point plugins, and a verb is valid exactly when it passes a generic contract suite: deterministic, identity at start, continuous, bounded for any valid parameters, schema round-trip.

For one task, MotionHarness:

1. **Writes** three candidates in parallel with Nemotron 3 Super (or Ultra), each from a different angle: a closed-form solution, an explicit physical model, or the minimal implementation. A candidate is the plugin module, 3–6 tests of what *this* verb should do, and a demo document.
2. **Tests** each candidate in a sandbox: install the package, run the contract suite and its own tests, check that the verb appears in the JSON Schema and in the planner's prompt, and bake the demo. Only a JSON bundle comes back; the harness never imports model-written code.
3. **Repairs** failing candidates from the verbatim test output. Each repair branches from its parent's sandbox checkpoint, so the dashboard's tree is the tree of sandbox images.
4. **Critiques** passing candidates: the demo is rendered to a contact sheet (onion skin, keyframes, position/scale/rotation curves) and sent to Nemotron 3.5 Super VL with exact measurements (travel, overshoot %, settle time). If the motion doesn't match the task, for example a "spring" that never overshoots, the candidate is polished and tested again.
5. **Ships** the winner: a `pip install`-able package registering the verb under the `motionlang.verbs` entry point, a diff, `report.md`, a playable `preview.html` and the contact sheet. The new verb is immediately part of the schema and prompt that SketchDynamics' planner uses.

It runs as a CLI (`motionharness new-verb "…"`), a web dashboard (`motionharness serve`) and an MCP server (`motionharness mcp`), so Claude Code or Cursor can ask it for a tested verb.

### Inspiration

Building SketchDynamics, we added verbs by hand: write the math, write tests, look at it, notice the overshoot is wrong, fix it. MotionLang was designed so that adding a verb never touches the core and a verb is valid when it passes a contract suite. That makes "write a verb" a well-posed task for a coding agent with a sandbox. fog (Liu & Chilton) has an LLM write motion functions on the fly; we wanted the same open vocabulary, but gated by tests and a visual check before any planner sees the new verb. LogoMotion's visually grounded repair loop inspired the critic step.

### How we built it

- **Models (all on Nebius Token Factory)**: Nemotron 3 Super 120B writes and repairs code (reasoning on); Nemotron 3 Ultra 550B is available per candidate; Nemotron 3.5 Super VL critiques rendered motion (reasoning off, JSON mode, ~2 s); Nemotron 3 Nano decides whether a task needs external docs (reasoning off, ~1 s), and Tavily fetches them (for exporters and third-party specs).
- **Sandboxes**: a `Sandbox` interface with a Token Factory Sandboxes backend (contree-sdk): an environment image with Python, motionlang and the test tools is built once and cached by content hash. Every candidate is `run(disposable=False)` from an image and returns a new image, which is the next repair's branch point. Candidates run concurrently. A local subprocess backend with the same semantics (directories as images, secrets stripped) runs when Sandboxes isn't configured.
- **The prompt**: the plugin contract, the base class source and four core verbs as reference implementations, plus the document format, all generated from the installed library, so the agent always sees the current API.
- **Rendering**: Pillow draws the contact sheet from baked tracks (plain data from the sandbox), including SVG path flattening, so no browser is needed.
- **Dashboard**: FastAPI + vanilla JS, no build step. It polls the run record (`run.json`), which is also the CLI's and MCP's source of truth.
- **Tests**: 14 tests with a scripted fake model cover a repair, a critique-driven polish, a polish that breaks the tests and gets repaired, candidates that never pass, the contract gate on a good and a broken plugin, a fake Sandboxes client (checkpoint branching, platform errors), the dashboard API (including path traversal) and the MCP tools.

### Challenges we ran into

- **Sandboxes access**: Sandboxes is in Beta behind project activation. We built behind an interface with a local fallback so the agent worked from day one. TODO: update when the real backend has run.
- **Hidden failures**: our first live run reported "verb not registered" because a relative path made the plugin invisible to the test process, which hid the real error (`from motionlang.model import Vec2`, a type that doesn't exist). Surfacing the import traceback to the model fixed most first-round failures.
- **Tests pass, motion is wrong**: the first spring candidate passed every test but was critically damped, so it never overshot. The vision critic caught it. Then it went the other way: VL accepted a follower that ended 20 px off the target as "overshoot". Passing exact measurements with the image, and telling the critic they win, fixed that.
- **Finding a library bug**: pendulum candidates kept failing their own tests with "changes nothing" (`E_NOOP`) although they swing 16°. The agent's failures led us to a real validator bug: the no-op check sampled five points, which landed on the swing's zero crossings.
- **Format slips**: models put `(unchanged)` inside a code fence, or ignored few-shot examples in JSON mode. Each became a parser rule or a schema-shaped question.

### Accomplishments that we're proud of

- An agent whose acceptance gate is a real, fixed test suite from the library it extends, not tests it wrote for itself alone.
- Visual verification of motion with measurements, so "looks right" is checked too, not just "runs".
- The branch tree is literal: each node is a sandbox checkpoint, and repairs branch from their parent's state.
- The output is useful right away: one `pip install` and the new verb is in the planner's vocabulary.

### What we learned

- Contract tests turn "write me a feature" into a well-posed agent task; the more the library declares about itself (schema, prompt, examples), the less the agent has to guess.
- Vision models judge motion well from curves and onion skins, but need numbers to be reliable on small effects.
- Showing the repair loop (what failed, verbatim, and what changed) is what makes an agent's result trustworthy.

### What's next

- Exporters as a task kind (Lottie, After Effects, Blender), with Tavily-fetched specs and round-trip tests.
- The MoVer benchmark: Nemotron → GSAP vs. Nemotron → MotionLang on a subset of MoVer's prompts, as a Nebius Serverless Job.
- Merging two half-good candidates, and golden-frame regression tests once a verb is accepted.

## Built with

Python, FastAPI, Pydantic, NumPy, Pillow, pytest, Hypothesis, NVIDIA Nemotron 3 (Super 120B, Ultra 550B, Nano 30B), NVIDIA Nemotron 3.5 Super VL, Nebius Token Factory, Token Factory Sandboxes, Tavily, MCP, MotionLang

## Try it out

- Code: TODO public repo (export with `python3 scripts/export_submission.py motionharness`)

## Feedback (for organizers)

See `docs/token-factory-feedback.md`, Oct 19 entries: Sandboxes access, the SDK's missing `networking` option, Nano and few-shot JSON, Super as a coding model, VL as a motion critic.
