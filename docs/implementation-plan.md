# Implementation Plan

Deadline: **Fri Oct 30, 10:00 am PT**. Three separate submissions, one per track, sharing one open-source core library.

## Ground rules (from the Official Rules)

- Each submission must be "unique and substantially different" → separate repos, separate users, separate UIs. Sharing a library is fine; sharing a product is not.
- Each project can win one Overall award, **or** one Track award + one Bonus award. Every project gets a real runtime Tavily call (Best Use of Tavily, $3k).
- Judges may score from the video + description alone → budget real time for each 3-min video.
- Each repo: OSI license visible at top (Apache-2.0), README with setup, a section on how Nemotron/Token Factory/Nebius services are used, and feedback on the tools.

## Platform facts (verified Oct 17)

- **Token Factory inference**: OpenAI-compatible API; text, vision (image2text), and embedding models; function calling and structured JSON output. Exact Nemotron model IDs require login → list them with `GET /v1/models` on day 1. (The "Nemotron-3-70B" name in ideation.md does not exist under that name.)
- **Token Factory Sandboxes (Beta, request access today)**: VM-isolated code execution, saved filesystem images, git-like branching ("try two fixes, keep the passing branch"), SSE output streaming, Python SDK / CLI / REST / MCP. Docs: https://docs.tokenfactory.nebius.com/sandboxes/overview
- **Nebius Serverless Endpoints / Jobs**: hosting and background work (exports, batch ingestion).

## Model routing (shared)

| Role | Model tier | Used for |
| --- | --- | --- |
| Planner / coder | Nemotron 3 Ultra | Turning intent into a DSL program, writing verb implementations, repair after test failures |
| Fast editor | Nemotron 3 Super / Nano | One-line tweaks, parameter edits, classification, tool routing |
| Eyes | NVIDIA vision model on Token Factory | Reading sketches, critiquing rendered frames, tagging images |
| Memory | Embedding model on Token Factory | CanvasMind retrieval |

Log tokens and latency per call. That gives concrete numbers for the "credits stretch further" story and the feedback prize.

---

## Shared core: Motion DSL, package `motionlang` (Oct 18–19)

The motion DSL. You own the spec: see [motion-dsl-spec.md](motion-dsl-spec.md). Goal: easy for an LLM to generate and mechanically checkable.

- **Schema**: Pydantic models → JSON Schema (also used as the Token Factory structured-output schema).
  - `Scene { canvas, actors[], timeline }`
  - `Actor { id, shape | svg | stroke_ref }`
  - Timeline = `seq` / `par` / `stagger` around about 10 verbs: `move_along(path)`, `orbit`, `rotate`, `scale`, `fade`, `bounce`, `wiggle`, `follow(target)`, `spring_to`, `morph`.
  - Each verb has `easing`, `duration`, `delay`.
- **Constraints**: `stay_in_bounds`, `max_speed`, `no_overlap(a,b)`, `ends_at(t)`, `smoothness(max_jerk)`.
- **Runtime**: one reference interpreter (TypeScript, Canvas/SVG) for the browser, plus a headless Python sampler that evaluates positions at time t for tests and frame rendering.
- **Validator**: schema check → constraint check on sampled trajectories → structured error list (feeds the repair loop).
- **Exporters (stretch)**: Lottie JSON, then Blender `bpy` script.

Open question for you: build on GSAP, Lottie or your prior motion-blocks work rather than starting from zero?

---

## P1: SketchDynamics Studio · Best Apps & Agents (Oct 20–23)

**User**: motion designers or marketers who want an animated explainer or logo motion without keyframing.

**Loop**: draw rough strokes on a canvas + type/speak "make the ball bounce along this line, then the logo spins in" → agent emits a Motion program → live preview → refine by talking ("slower", "more bounce") or by redrawing one path.

- **Frontend**: React + tldraw (or your existing canvas). Strokes go to the backend as vector data, not just pixels.
- **Backend**: FastAPI + LangGraph.
  - Nodes: `interpret_sketch` (vision + stroke geometry) → `plan` (Ultra, structured output) → `validate` → `repair` (bounded retries) → `preview`.
  - Edits route to Super/Nano with a JSON-patch output, so the app stays responsive.
- **Tavily**: "motion reference" tool. The agent looks up real techniques ("squash and stretch timing", "logo reveal styles") and cites them in its plan.
- **Intent check**: after the structural validator, Nemotron translates the user's request into a MoVer program and verifies the baked tracks (tier 2 in the spec §7). Failures feed the same repair loop.
- **Async export**: Nebius Serverless Job renders MP4/GIF/Lottie.
- **Deploy**: hosted demo (Serverless Endpoint or any host) so judges can try it.
- **Video beat**: sketch → animation in about 10 s, then three conversational edits.

## P2: MotionHarness · Coding & Agentic Engineering (Oct 24–26)

**Key reframe**: the agent writes **code**, not just JSON. Technical artists extend motion systems: new verbs, exporters to Blender/AE/Lottie, procedural motion. MotionHarness is a coding agent that writes and tests that code.

**User**: a technical artist or graphics developer, working from the CLI / MCP inside their existing coding tools.

**Loop** for "add a `spring_follow` verb" or "add a Blender exporter":

1. Read the DSL spec + the existing verbs (repo context).
2. Ultra writes the implementation **and** property-based tests (continuity, bounds, end conditions).
3. Run tests in **Token Factory Sandboxes**. Branch N candidate patches from one checkpoint in parallel, keep the passing branch, and roll back failures.
4. Render sample frames in the sandbox → vision-model critique ("overshoot looks wrong") → repair.
5. Emit a PR-ready diff + test report + rendered GIF.

- **Interfaces**: CLI and an MCP server (so Claude Code, Cursor and others can call it), plus a small web dashboard showing the branch tree, test results and frames. The dashboard is what earns the Design points.
- **Tavily**: runtime doc lookup (Blender `bpy` API, Lottie spec) when writing exporters.
- **Benchmark**: a Serverless Job runs a subset of the MoVer 5,600-prompt dataset as Nemotron → GSAP (MoVer's baseline) vs. Nemotron → Motion, and reports pass rates. That's a measured result for the write-up and video.
- **Why it's distinct from P1**: a different user (a developer, not a designer), a different artifact (code + tests, not animations), and a different interface.

## P3: CanvasMind · Personal AI (Oct 27–28)

**User**: an individual creative. A private, always-on creative memory that acts for them.

### The three named tools are one stack

| Layer | What it is | Role here |
| --- | --- | --- |
| **Hermes Agent** (Nous Research, MIT) | Self-hosted personal agent: persistent memory, self-created skills, messaging (Telegram/Discord/Slack/WhatsApp) | The assistant the user talks to |
| **OpenShell** (NVIDIA Agent Toolkit) | Sandbox runtime: filesystem/network/process policy + inference routing, so the agent never sees raw API keys | Keeps the user's files and credentials contained (the "data under your control" story) |
| **NemoClaw** (NVIDIA, alpha) | Host-side installer/orchestrator; `nemohermes` CLI sets up Hermes inside OpenShell | One-command setup |

Inference: NemoClaw onboarding supports "Other OpenAI-compatible endpoint" (`NEMOCLAW_PROVIDER=custom` + endpoint + model + `COMPATIBLE_API_KEY`) → point it at Token Factory Nemotron.

Install: `curl -fsSL https://www.nvidia.com/nemoclaw.sh | NEMOCLAW_AGENT=hermes bash -s -- --defer-onboarding`, then `nemohermes onboard`.

**Risk**: NemoClaw is alpha. Spike it for half a day early (Oct 18, while waiting on Sandboxes access). Fallback: plain Hermes Agent pointed at Token Factory, no NemoClaw.

### What we build (Hermes does memory/chat/messaging for free)

- **`canvasmind` MCP server** that Hermes calls:
  - `ingest(folder)`: a vision model captions/tags each image or animation → Token Factory embeddings → local LanceDB. Data stays local.
  - `recall(query)`: "that bouncy blue logo from last week"
  - `moodboard(brief)`: renders an HTML board
  - `style_profile()`: "you favor ease-out-back, muted palettes"
- **Hermes skills**: a daily trend digest via **Tavily**, filtered by the style profile and delivered to Telegram, plus "watch my sketches folder."
- **Cold start**: seed with your own sketches and the P1/P2 outputs.
- Scope it small: ingest + recall + one proactive skill is enough.

## Oct 29: videos and submissions

- Three videos of 3 minutes or less, three Devpost entries, three READMEs with the Nebius/NVIDIA usage and feedback sections.
- Create Devpost drafts early (Oct 20) and update them as you go, so no submission is ever blocked.

## Cut lines if time slips

1. Drop exporters and voice input.
2. CanvasMind shrinks to ingest + recall + digest.
3. If P2 is late, ship it CLI + MCP only and skip the dashboard.
4. Never cut a project's video. A finished smaller project beats an unfinished bigger one.

## Team: one human + Claude

Greenfield; nothing is reused, so prefer off-the-shelf pieces (tldraw, LangGraph, Hermes, LanceDB) over custom infrastructure.

| You (human only) | Claude |
| --- | --- |
| Accounts, credits, API keys (keep them in `.env`, never paste them in chat) | Scaffolding, backend, frontend, tests, READMEs |
| DSL design calls: verbs, naming, what "good motion" means | Draft DSL spec + reference implementation for your review |
| Taste checks on demos; recording and narrating videos | Video scripts and shot lists, Devpost descriptions |
| Devpost submission clicks | Feedback-section drafts from our logged friction |

Once the shared core is stable, independent pieces (for example the P3 MCP server vs. P1 frontend) can run in parallel Claude sessions.

## Today (Oct 17)

- [ ] Log in to Token Factory, claim builder credits, list Nemotron model IDs.
- [ ] Request Sandboxes beta access (gates P2).
- [ ] Get a Tavily API key.
- [ ] (Oct 18) Half-day NemoClaw + Hermes spike against Token Factory.
- [ ] Create 3 GitHub repos with the Apache-2.0 license + a `motionlang` package repo (or monorepo package).
