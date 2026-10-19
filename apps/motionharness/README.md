# MotionHarness

License: Apache-2.0 · Nebius x NVIDIA Global AI Hackathon, **Coding & Agentic Engineering** track

A coding agent for technical artists and graphics developers who extend **MotionLang** ([`motionlang`](../../packages/motionlang)), a declarative 2D motion language built for LLMs. Describe a new motion verb in one sentence, for example "add a `spring_follow` verb: the actor chases a target with spring physics". NVIDIA Nemotron models on Nebius Token Factory write several candidate implementations, each with its own tests and a demo scene. Every candidate runs in a sandbox branched from one prepared checkpoint, against MotionLang's fixed plugin contract suite and its own tests. Nemotron VL then looks at the rendered motion and critiques it. The agent repairs what fails and ships the winner as a ready-to-install plugin package, with a diff, a test report and a preview.

```text
"add a spring_follow verb"           Tavily (docs, only if the task needs an external spec)
        │
        ▼
  ┌── c1 (Super, closed form) ──┐    each candidate: verb.py + test_verb.py + demo.motion.json
  ├── c2 (Super, physical) ─────┤    written in parallel
  └── c3 (Ultra, minimal) ──────┘
        │  one sandbox run each, branched from the environment checkpoint:
        │  pip install the plugin → contract suite (SPEC §11) → own tests → bake the demo
        ▼
  passing? ── no ──► repair from the test output, branching from that candidate's checkpoint
        │ yes
        ▼
  render a contact sheet ──► Nemotron VL: "no visible overshoot; this is critically damped" ──► polish
        │ accepted
        ▼
  plugin/ (pyproject entry point `motionlang.verbs`) · plugin.diff · report.md · preview.html · frames.png
```

## Run it

```bash
uv sync                                         # from the repo root
cp apps/motionharness/.env.example .env         # then add NEBIUS_API_KEY (and NEBIUS_PROJECT_ID for Sandboxes)
uv run motionharness serve                      # dashboard on http://127.0.0.1:8766/
uv run motionharness new-verb "add a \`swing\` verb: the actor swings like a pendulum about a pivot above it and comes to rest"
```

`new-verb` options: `--candidates 3`, `--models super,super,ultra` (per candidate; `super`, `ultra`, `nano` or a full model id), `--rounds 3` (repair rounds), `--namespace acme` (verb `acme.swing`), `--sandbox auto|local|contree`, `--no-critique`, `--no-docs`. Runs are written to `out/motionharness/<run id>/`. `motionharness runs` lists them, and `motionharness sandbox-check --run` verifies Token Factory Sandboxes access and caches the environment image.

**From a coding agent (MCP)**: `motionharness mcp` serves the tools `new_verb`, `list_runs`, `get_run` and `read_artifact` over stdio. For Claude Code:

```bash
claude mcp add motionharness -- uv run --directory /path/to/this/repo motionharness mcp
```

**The acceptance gate** is MotionLang's own contract suite, `motionlang.testing.contract` (SPEC §11), run with `MOTIONLANG_CONTRACT_VERB=<verb>` so only the new verb is checked. Every shipped plugin carries a one-line `tests/test_contract.py` that runs it, next to the tests the model wrote.

Name verbs as actions, the way a prompt would use them ("the lamp *swings*"): `swing`, not `pendulum`. A noun-named verb reads as an object, and a planner such as SketchDynamics' may try to draw it. A fast check before writing (Nemotron Super, reasoning off: does "the actor <name>s" read as English?) flags noun-like names and suggests one (`✎` in the log); rerun with `--name` to take it.

Install what it made:

```bash
uv pip install out/motionharness/<run id>/plugin   # into the workspace venv, where motionlang is
uv run motionlang prompt | grep mh.swing                 # the planner sees the new verb right away
```

motionlang isn't on PyPI yet, so a plain `pip install <plugin>` in another environment fails to resolve `motionlang>=0.1`. Install the library first there: `pip install packages/motionlang out/motionharness/<run id>/plugin`.

## The dashboard

- **Branch tree**: the environment checkpoint, then each candidate as a node, with bars for the contract suite and its own tests, and the critic's score. Repairs and polishes hang off the candidate they branched from.
- **Tests**: every contract and task test with its failure output, plus the checks around them: the package installs, it registers the verb, the verb shows up in the JSON Schema and the planner prompt, and the demo validates. What was sent back to the model for repair is shown verbatim.
- **Motion**: the contact sheet the critic saw (onion skin, keyframes, position/scale/rotation curves), a live player, and the critique.
- **Code / Diff**: the three files the model wrote, and the diff against the candidate it repaired.
- **Ship**: the package, the install command, report.md, plugin.diff, preview.html.
- **Model calls**: every Token Factory request on a timeline and in a table: model, reasoning on/off, latency, prompt and completion tokens.

## How Nemotron / Token Factory is used

| Step | Model (Token Factory) | What it does |
| --- | --- | --- |
| Name check | `nvidia/nemotron-3-super-120b-a12b`, reasoning off, JSON mode | Does "the actor *swing*s" read as English? Flags noun-like verb names (`pendulum`) and suggests an action name (~1 s). Nano was unreliable at this |
| Need external docs? | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off, JSON mode | Decides whether the task needs a spec or third-party API, and writes up to two search queries for Tavily (~1 s) |
| Write | `nvidia/nemotron-3-super-120b-a12b` by default; `nvidia/Nemotron-3-Ultra-550b-a55b` per candidate on request | Writes the plugin module, focused tests and a demo document, given MotionLang's plugin contract, the base class source and four core verbs as reference implementations. Candidates differ in approach (closed form / physical / minimal) and temperature |
| Repair | the candidate's model | Gets the failing tests' verbatim output (assertion text, tracebacks, validator errors with hints) and returns fixed files; unchanged files may be marked `(unchanged)` |
| Critique | `nvidia/Nemotron-3.5-Super-VL`, reasoning off, JSON mode | Looks at a rendered contact sheet of the demo and scores the motion against the task (1–10, issues, one suggestion) |
| Polish | the candidate's model | Revises a passing candidate the critic didn't accept, from the critique |
| Sandboxes | Token Factory Sandboxes (Beta) | Each candidate is one operation started from an immutable image: the environment checkpoint for new candidates, the parent candidate's saved image for repairs. Candidates run concurrently |

All chat calls use the OpenAI-compatible Token Factory endpoint through `motionlang.agent`'s client. Each one is logged with model, latency and tokens.

### Sandboxes, and the local fallback

Generated code never runs in the harness process. The harness sends files into a sandbox and reads back one JSON bundle (JUnit reports, logs, baked tracks). Rendering and critique work on that data, so the host never imports model-written code.

- **`contree` (Token Factory Sandboxes)**, used when `contree-sdk` is installed and `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID` are set. The first run builds an environment image (python 3.12 + motionlang + pytest/hypothesis) and caches its UUID in `~/.cache/motionharness/contree.json`. Every candidate is a `run(..., disposable=False)` from an image, so its result is a new image: a repair branches from the candidate it fixes, and the tree in the dashboard is the tree of images.
- **`local`**, the fallback: the same checkpoint and branch semantics with directories and subprocesses. It strips secrets from the environment, but it does **not** isolate untrusted code. Use it on your own machine only.

## Tests

```bash
uv run pytest apps/motionharness -q     # scripted model, local sandbox: no key, no network
```

They cover a repair from failing tests, a polish driven by the critique, a polish that breaks the tests and gets repaired, parallel candidates that never pass, the secret-stripping sandbox, the contract-suite gate on a good and a broken plugin, the Sandboxes backend against a fake client (checkpoint branching, platform errors), the contact sheet, the dashboard API including path traversal, and the MCP tools.

## Layout

```
src/motionharness/
  harness.py    the loop: write → test → critique → repair/polish → ship; the run record
  sandbox.py    Sandbox interface; ContreeSandbox (Token Factory Sandboxes) and LocalSandbox
  runner.py     one candidate → one sandbox job; JUnit + probe bundle → Report
  plugin.py     Task, model reply parsing, plugin package files, diffs, reports
  prompts.py    plugin contract, reference verbs, write/repair/critique prompts
  render.py     baked tracks → contact sheet (onion skin, keyframes, curves) with Pillow
  server.py     dashboard API; static/ holds the single-page UI
  mcp_server.py MCP tools: new_verb, list_runs, get_run, read_artifact
  sandbox_files/  what runs inside the sandbox besides the plugin: the probe and the local install shim
```
