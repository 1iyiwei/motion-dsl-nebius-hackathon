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

Building SketchDynamics, we added verbs by hand: write the math, write tests, look at it, notice the overshoot is wrong, fix it. MotionLang was designed so that adding a verb never touches the core and a verb is valid when it passes a contract suite. That makes "write a verb" a well-posed task for a coding agent with a sandbox. [fog (Liu & Chilton)](https://fog-motion-emotion.vercel.app/) has an LLM write motion functions on the fly; we wanted the same open vocabulary, but gated by tests and a visual check before any planner sees the new verb. [LogoMotion](https://vivian-liu.com/#/logomotion)'s visually grounded repair loop inspired the critic step.

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

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/motionharness

- Code: TODO public repo (export with `python3 scripts/export_submission.py motionharness`)


### Project Media
Image gallery
JPG, PNG or GIF format, 5 MB max file size. For best results, use a 3:2 ratio. Up to 15 images.

* Demo video URL
https://youtu.be/Yiqjqel_Z0Q


## Additional info
For judges and organizers
Unless noted, additional info is for judges and hackathon organizers and will not appear on your public project page.

Upload a File
Upload a file as part of your submission - e.g., zip, pdf, word, apk, etc. To upload multiple files, put them in a zip file and upload the zip file. Limit: 35 MB.

* Submitter Type
individual, team, organization

* Organization Name (if applicable)
"N/A" if you're not representing an organization

Dragoniac

* Submitter Country of Residence
Appears in project gallery

* If you or any team members reside in Canada, please indicate the province below.
"N/A" if you don't reside in Canada.

* Which track are you submitting your project into?
Coding and agentic engineering

* Is your project new or existing prior to August 26, 2026?
New

* Provide a URL to your public code repository for judging and testing on either GitHub, GitLab or Bitbucket.
Must include: approved OSI license, README with setup instructions, highlight how you’ve used NVIDIA Nemotron or other NVIDIA open source models and where Token Factory accelerated your workflow and any other Nebius Tools or Services used.

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/motionharness

Provide a URL to a working demo, hosted application, or test build of your Project.
Not required for Physical AI submissions

https://sketch-dynamics.dragoniac.com/

<!--
(also https://sketch-dynamics-nebius-hackathon.onrender.com/) Access code: `<code>` (this field is for judges only, not the public page). On the free plan the first visit may take about a minute while the server wakes up.
-->

* Which model(s) did you use, and why did you choose that size/variant?
e.g., Nemotron-4 340B, Nemotron-Mini, etc.

Four Nemotron models, one per job, all through Nebius Token Factory:
- **Nemotron 3 Super 120B-A12B** is the default planner. With structured output constrained to our JSON Schema, it was the most reliable planner we measured on logo scenes (6/6 valid after at most two cheap repairs), at lower cost than Ultra.
- **Nemotron 3 Ultra 550B-A55B** is the stronger fallback planner, one click away ("Retry with Ultra"). It's best at hard multi-step plans in plain JSON mode.
- **Nemotron 3 Nano 30B-A3B**, with reasoning turned off, handles fast edits ("slower", "more bounce") as a few edit ops in 1–3 s. It escalates to reasoning, then to the planner, only when needed. It also spots objects a request needs a drawing for (about 1 s).
- **Nemotron 3.5 Super VL** reads imported artwork: it names parts, groups rigid pieces, picks rotation pivots and suggests animations, in 2–4 s. It also scores candidate shapes found on the web from one numbered grid image.
- Super also draws a simple SVG icon when no found shape fits, in about 3 s.

* How would you rate Nemotron's output quality for your use case? (1 = Poor, 10 = Excellent)
Please share what worked well or fell short — e.g., reasoning, instruction-following, domain knowledge, multilingual support.

**8.** Worked well: JSON mode and structured output were clean on every model; instruction-following on a novel DSL was strong given only a generated reference and one example; and the VL model's part naming and grouping were accurate once given numbered marks. Fell short: models sometimes copied validator hint wording literally (inventing fields or verbs), occasionally mixed coordinate frames, and could produce plans that are valid but miss the intent. Latency varied a lot when reasoning ran long.

* Did you fine-tune, prompt-engineer, or use Nemotron out of the box? What was your approach?
Use Nemotron out of the box.

No fine-tuning: the models are used as served, with prompt engineering around them. The system prompt is generated from the verb registry (the reference for each verb, plus rules and one example document). Planning uses structured output constrained to the MotionLang JSON Schema on Super. The core of the approach is a **validator-in-the-loop**: deterministic checks whose error messages are written as repair instructions (an exact edit at a JSON pointer), fed back to the model as edit ops. For artwork we use **set-of-marks** prompting: a second image with each part numbered.

* How did Nemotron's performance compare to other models you've used for similar tasks?

Nemotron works much better than some open-weight models that we ran locally, such as qwen2.5vl:32b.

* Which Nebius platform capabilities were most valuable to your project and how?
Please include detail on how you leveraged Nebius infrastructure to run Nemotron (e.g., GPU instance types, deployment setup, API access, scaling).

**Token Factory's OpenAI-compatible API**, serving the whole Nemotron family behind one key and one client, which made a different model per job trivial: a per-request model override picks Super, Ultra, Nano or VL. Most valuable features:
- **JSON mode** and **structured output** (`json_schema`), so the planner returns schema-valid documents.
- **The reasoning toggle** (`chat_template_kwargs.enable_thinking=false`), which made Nano edits about 3× faster (3.3 s to 1.1 s on a typical edit).
- **Multimodal input** (image data URLs) on Super VL.
- **Per-call usage counts**, which the app shows live for every call.

There's no GPU provisioning or deployment on our side: Token Factory is serverless inference, so scaling is the platform's job.

* How likely are you to recommend running Nemotron on Nebius to other developers? (1 = Unlikely, 10 = Highly Likely)
Please share why or provide additional feedback.

**8.** One OpenAI-compatible endpoint for the full Nemotron range, from Nano to Ultra plus VL, makes it easy to match model size to each task, and JSON and structured output worked across the family. What would make it a 10 is better documentation of model ids and the reasoning toggle, consistent errors for unsupported schema keywords, and a way to cap reasoning length.

* How would you rate your experience running Nemotron inference on Nebius compared to previous cloud or local development environments? (1 = Poor, 10 = Excellent)
Please share why or provide additional feedback.

**8.** No infrastructure to manage, an API the standard OpenAI SDK could talk to, and access to models far too large to run locally (Ultra 550B). The rough edges: exact model ids are only discoverable via `/v1/models` (the catalog page needs a login), and Super returned a bare 500 for a schema keyword where Ultra gave a clear 400.

* What additional features or improvements would have made the Nemotron on Nebius experience more effective for your project?

1. **A per-request reasoning budget** (max reasoning tokens). The same planning prompt took 22 s to 87 s depending on reasoning length; interactive apps need to cap that tail.
2. **Structured output that supports, or cleanly ignores, `propertyNames`**, which Pydantic emits for `dict[Literal, …]`. Today Ultra returns a 400 and Super a bare 500 with no message.
3. **Documenting the reasoning toggle per model family.** `enable_thinking=false` works, while `/no_think` in the prompt made Nano spin to `max_tokens` and return nothing.
4. **Model ids in the public docs**, not only via `/v1/models`.
5. **A fix for a JSON-mode slip on Super:** about 1 in 4 replies came back as the whole document escaped into a single key.

* What do you most hope to see from the Nemotron team next?

**[check]** Reliable structured output across every size (Ultra degenerated under constrained decoding where Super excelled), controllable reasoning length, and a VL model that can look at rendered animation frames to judge motion, which would close our loop from "valid" to "matches the intent".

* Did you use Tavily in your project?
To qualify: your project must make a functional, runtime call to the Tavily API as part of its solution.
Yes. When a prompt names an object the planner can't draw from basic shapes ("a crystal drops from the sky"), and the app's shape library has none, the server calls Tavily's search API at runtime, restricted to icon sites (`include_domains`: svgrepo.com, commons.wikimedia.org), to find SVG files of it. The app fetches and sanitizes them, Nemotron VL ranks them, and the user picks one to animate. The side panel shows the Tavily step with its result counts. Code: `apps/sketchdynamics/src/sketchdynamics/shapes.py` (`WebSearch`).

Builders & Brews - IRL Events City
If you're attending one of the 20 participating IRL hackathon events, select your city to be eligible for the City Winner Award.

* I, and, if applicable, all of my teammates, are at least the age of majority where I reside (e.g. 18 in the US).
* I, and, if applicable, all of my teammates, are not employees, representatives nor agents of the Promotion Entities of this hackathon (sponsor, administrator. or any affiliates).

* Which AI tools have you leveraged while working on this project?
Claude Code

* Describe the level of learning you/your team derived from the project
Significant

* Did you gain AI value that you can use in your career?
Yes

## Submit project
After submitting, you can still edit your project until the submission deadline.

Terms & conditions
* I, and all of my team members, have read and agree to be bound by the Official Rules and the Devpost Terms of Service.

## Feedback (for organizers)

See `docs/token-factory-feedback.md`, Oct 19 entries: Sandboxes access, the SDK's missing `networking` option, Nano and few-shot JSON, Super as a coding model, VL as a motion critic.
