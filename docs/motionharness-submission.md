# MotionHarness: submission draft (Coding & Agentic Engineering)

Draft for the Devpost form (fields as in `docs/submission-form.md`). Submitted Oct 20 as https://devpost.com/software/motionharness (editable until the deadline).

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

Then you can **try the verb in a scene**: type a prompt ("a ball falls from the sky and wobbles on its way") and the planner that SketchDynamics uses plans it twice with the same cast, once with the new verb in its vocabulary and once with core verbs only, side by side. Both plans are validated in sandboxes, so nothing gets installed. You can then download the plugin, or install and uninstall it in one click to compare apps with and without it.

It runs as a CLI (`motionharness new-verb "…"`), a web dashboard (`motionharness serve`) and an MCP server (`motionharness mcp`), so Claude Code or Cursor can ask it for a tested verb.

### Inspiration

Building SketchDynamics, we added verbs by hand: write the math, write tests, look at it, notice the overshoot is wrong, fix it. MotionLang was designed so that adding a verb never touches the core and a verb is valid when it passes a contract suite. That makes "write a verb" a well-posed task for a coding agent with a sandbox. [fog (Liu & Chilton)](https://fog-motion-emotion.vercel.app/) has an LLM write motion functions on the fly; we wanted the same open vocabulary, but gated by tests and a visual check before any planner sees the new verb. [LogoMotion](https://vivian-liu.com/#/logomotion)'s visually grounded repair loop inspired the critic step.

### How we built it

- **Models (all on Nebius Token Factory)**: Nemotron 3 Super 120B writes and repairs code (reasoning on), plans the "try in a scene" documents, and checks that a verb's name reads as an action ("the actor *swings*", not "*pendulums*"; reasoning off, ~1 s); Nemotron 3 Ultra 550B is available per candidate; Nemotron 3.5 Super VL critiques rendered motion (reasoning off, JSON mode, ~2 s); Nemotron 3 Nano decides whether a task depends on an external spec (reasoning off, ~1 s), and Tavily fetches it.
- **Sandboxes**: a `Sandbox` interface with a Token Factory Sandboxes backend (contree-sdk): an environment image with Python, motionlang and the test tools is built once and cached by content hash. Every candidate is `run(disposable=False)` from an image and returns a new image, which is the next repair's branch point. Candidates run concurrently. A local subprocess backend with the same semantics (directories as images, secrets stripped) runs when Sandboxes isn't configured.
- **The prompt**: the plugin contract, the base class source and four core verbs as reference implementations, plus the document format, all generated from the installed library, so the agent always sees the current API.
- **Rendering**: Pillow draws the contact sheet from baked tracks (plain data from the sandbox), including SVG path flattening, so no browser is needed.
- **Dashboard**: FastAPI + vanilla JS, no build step. It polls the run record (`run.json`), which is also the CLI's and MCP's source of truth.
- **Hosting**: a hosted mode for public demos runs generated code only in Token Factory Sandboxes (and refuses runs while they're unavailable), disables installing into the server, caps runs, and adds an access code, a per-visitor rate limit and a daily token budget.
- **Tests**: 22 tests with a scripted fake model cover a repair, a critique-driven polish, a polish that breaks the tests and gets repaired, candidates that never pass, the contract gate on a good and a broken plugin, a fake Sandboxes client (checkpoint branching, platform errors, an inactive project), try-in-a-scene, hosted mode, the dashboard API (including path traversal) and the MCP tools.

### Challenges we ran into

- **Sandboxes access and a flaky control plane**: Sandboxes is in Beta behind project activation, so we built behind a `Sandbox` interface whose local backend has the same checkpoint-and-branch semantics, and the agent worked from day one. When activation arrived (Oct 20), the API returned intermittent 500s, and a run that uploaded ~33 small files concurrently failed every time. Packing each job into one tar (one upload per run) and retrying 5xx with backoff fixed it: environment image in ~15 s, each candidate in 8–10 s, three in parallel.
- **Hidden failures**: our first live run reported "verb not registered" because a relative path made the plugin invisible to the test process, which hid the real error (`from motionlang.model import Vec2`, a type that doesn't exist). Surfacing the import traceback to the model fixed most first-round failures.
- **Tests pass, motion is wrong**: the first spring candidate passed every test but was critically damped, so it never overshot. The vision critic caught it. Then it went the other way: VL accepted a follower that ended 20 px off the target as "overshoot". Passing exact measurements with the image, and telling the critic they win, fixed that.
- **Finding a library bug**: pendulum candidates kept failing their own tests with "changes nothing" (`E_NOOP`) although they swing 16°. The agent's failures led us to a real validator bug: the no-op check sampled five points, which landed on the swing's zero crossings.
- **Measurements that lie**: for a property that wobbles and returns to its start, "overshoot past the final value" divided by a near-zero travel and told the critic "8333% overshoot". Claude Code, calling MotionHarness over MCP, spotted it in the critique. Oscillating properties are now described by their peak deviation instead.
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

### Project Media
Image gallery
JPG, PNG or GIF format, 5 MB max file size. For best results, use a 3:2 ratio. Up to 15 images.

* Demo video URL
https://youtu.be/-WKw1R56PpE

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

https://motion-harness.dragoniac.com/

<!--
(also https://sketch-dynamics-nebius-hackathon.onrender.com/) Access code: `<code>` (this field is for judges only, not the public page). On the free plan the first visit may take about a minute while the server wakes up.
-->

* Which model(s) did you use, and why did you choose that size/variant?
e.g., Nemotron-4 340B, Nemotron-Mini, etc.

Nemotron 3 Super 120B-A12B, Nemotron 3 Ultra 550B-A55B, Nemotron 3 Nano 30B-A3B, Nemotron 3.5 Super VL

* How would you rate Nemotron's output quality for your use case? (1 = Poor, 10 = Excellent)
Please share what worked well or fell short — e.g., reasoning, instruction-following, domain knowledge, multilingual support.

**7.** Writing code: Nemotron 3 Super wrote complete plugins (module + tests + demo) that passed MotionLang's fixed contract suite, sometimes on the first try and usually within 1–3 repair rounds, and it used verbatim test failures well to fix its own code. What fell short: it imported names that don't exist, wrote tests with guessed thresholds, and sometimes picked parameters that make a verb do nothing; we added prompt rules and better error excerpts for each. Nemotron 3.5 Super VL was a good motion critic in ~2 s from a contact sheet (it caught a spring that never overshot although every test passed), but it needed exact measurements next to the image to stop it from "seeing" effects that aren't there. Nano was fast and reliable for JSON decisions once we asked for a named field rather than a yes/no.


* Did you fine-tune, prompt-engineer, or use Nemotron out of the box? What was your approach?
Use Nemotron out of the box, with prompt engineering and tool feedback instead of fine-tuning. The coding prompt is generated from the installed library (the plugin contract, the verb base class, four core verbs as reference implementations, the document format), so it always matches the current API. Each model gets the job that fits its size and is checked by a program: tests in a sandbox for the code, measured motion for the critic, a validator for the scene documents. Failures go back verbatim, with the offending code or document quoted.


* How did Nemotron's performance compare to other models you've used for similar tasks?

I haven't done a rigorous comparison yet, but my impression is that Nemotron excels at visual reasoning: VL analyzes SVGs into semantic components, Super and Ultra synthesize and validate motion parameters, and Nano handles quick motion edits.

* Which Nebius platform capabilities were most valuable to your project and how?
Please include detail on how you leveraged Nebius infrastructure to run Nemotron (e.g., GPU instance types, deployment setup, API access, scaling).

**Token Factory's OpenAI-compatible API** for the whole Nemotron range, with no GPUs or deployments to manage. That let us give each step its own model in the same run: Super 120B to write and repair code and plan scenes, Ultra 550B as an optional bigger coder, Nano 30B with reasoning off for one-second decisions, and Super VL to critique rendered motion. Three candidates are written in parallel, so concurrent requests mattered. JSON mode and the `enable_thinking` toggle let us trade quality for latency per call, and the dashboard shows every call's model, latency and tokens. **Token Factory Sandboxes** is the backend MotionHarness was designed around: its immutable images, where every run saves a new image you can run from again, map directly onto "branch candidates from one checkpoint and repair from the parent's state". Activation arrived on Oct 20: the environment image builds in ~15 s, each candidate runs in 8–10 s (three in parallel), and the dashboard's branch tree shows the real image UUIDs.


* How likely are you to recommend running Nemotron on Nebius to other developers? (1 = Unlikely, 10 = Highly Likely)
Please share why or provide additional feedback.

**8.** One OpenAI-compatible endpoint for the full Nemotron range, from Nano to Ultra plus VL, makes it easy to match model size to each task, and JSON and structured output worked across the family. What would make it a 10 is better documentation of model ids and the reasoning toggle, consistent errors for unsupported schema keywords, and a way to cap reasoning length.

* How would you rate your experience running Nemotron inference on Nebius compared to previous cloud or local development environments? (1 = Poor, 10 = Excellent)
Please share why or provide additional feedback.

**8.** No infrastructure to manage, an API the standard OpenAI SDK could talk to, and access to models far too large to run locally (Ultra 550B). The rough edges: exact model ids are only discoverable via `/v1/models` (the catalog page needs a login), and Super returned a bare 500 for a schema keyword where Ultra gave a clear 400.

* What additional features or improvements would have made the Nemotron on Nebius experience more effective for your project?

1. **A per-request reasoning budget** (max reasoning tokens). The same planning prompt took 22 s to 87 s depending on reasoning length; interactive apps need to cap that tail.
2. **Sandboxes: earlier activation and a steadier control plane.** Our agent was built around Sandboxes, but activation took most of the hackathon, and right after it the Files API returned bare 500s under concurrent uploads. The Python SDK's `run()` also lacks the `networking` option that REST has, which matters most for untrusted generated code.
3. **Structured output that supports, or cleanly ignores, `propertyNames`**, which Pydantic emits for `dict[Literal, …]`. Today Ultra returns a 400 and Super a bare 500 with no message.
4. **Documenting the reasoning toggle per model family.** `enable_thinking=false` works, while `/no_think` in the prompt made Nano spin to `max_tokens` and return nothing.
5. **Model ids in the public docs**, not only via `/v1/models`.
6. **A fix for a JSON-mode slip on Super:** about 1 in 4 replies came back as the whole document escaped into a single key.

* What do you most hope to see from the Nemotron team next?

Reliable structured output across every size (Ultra degenerated under constrained decoding where Super excelled), controllable reasoning length, and video or multi-frame input for the VL model, so it can judge motion from the animation itself rather than from a contact sheet of frames and curves.

* Did you use Tavily in your project?
To qualify: your project must make a functional, runtime call to the Tavily API as part of its solution.
Yes

<!-- Not part of the form (it only asks yes/no); for reference if asked how: Before writing code, Nemotron Nano decides whether the task depends on an external file format, product or API (for example a Lottie exporter, or matching After Effects' `wiggle` expression). If it does, the harness calls Tavily's search API at runtime with up to two queries and gives the results to the coding model as reference material; the dashboard's agent log shows each search and its result count. Tasks that only need math or physics (springs, pendulums, wobbles) skip the search. Code: `apps/motionharness/src/motionharness/harness.py` (`tavily_search`, `Harness.lookup_docs`). -->

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
