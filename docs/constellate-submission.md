# Constellate: submission draft (Physical AI)

Draft for the Devpost form (fields as in `docs/submission-form.md`).
Fields marked **(your call)** are opinions or facts only you can give; the drafts there are suggestions.

## General info

**Project name** (≤60): Constellate

**Elevator pitch** (≤200): Sketch or say a drone light show. Nemotron choreographs it, a flight checker and a wind simulation make it safe and certify it, and your phone becomes the wind for a live test flight.

## Project details

Drone light shows are motion graphics made of physical things: a hundred quadrotors with thrust limits, batteries and wind to fight, flying metres apart in front of a crowd. Constellate lets you draw a shape or say "a red heart that slowly spins, then our logo, then THANK YOU in gold". NVIDIA Nemotron writes a show script, a deterministic compiler turns it into collision-free drone paths, and a flight checker and a physics simulation of the swarm in wind find what would go wrong, with the exact fix. Nemotron applies the fixes until the show is certified, a Tavily site briefing sets the limits from the venue's real rules and weather, and in live mode the phone in your hand is the wind.

### What it does

1. **Brief the site.** Type a venue and a date. Nemotron Nano writes four searches (altitude limit, crowd setback, nearby airspace, typical wind for the month), Tavily runs them, and Nemotron Super reads the results into numbers, each with a source link and a verbatim quote. For Marina Green, San Francisco: a 122 m ceiling ("400 feet AGL", flyusi.org), a 61 m crowd setback ("a 200 ft buffer to the audience", a local drone-show operator) and a 5.1 m/s typical wind (windy.app), so an 8 m/s design wind. The app checks every quote against its source; an unverified number may only tighten a limit, never loosen it.
2. **Design.** Draw on a night-sky canvas, drop in an SVG logo, type or say what should happen, pick the fleet size. Nemotron Ultra writes a show script: formations (presets, your strokes, your logo with its colours, text), transitions (direct, a staggered ripple, or expand-then-form), spins and colour changes. Artistic choices only.
3. **Compile.** The compiler assigns drones to each formation with the Hungarian algorithm and flies them along synchronized straight lines, which CAPT (Turpin, Michael & Kumar, 2014) proves collision-free when formations are spaced well enough. It writes a MotionLang document, one actor per drone, so the same validator, baker and player as our other MotionLang apps check and play it.
4. **Check.** A flight checker measures the planned tracks in metres: separation (a spatial index per frame), speed, acceleration, airspace, formation density and battery time. Every problem names the step and carries the fix as a number: "d0 reaches 32.5 m/s during heart_spin; limit 8 → set heart_spin's duration from 8 to 34.5 s". The duration comes from physics, not a guess: required acceleration scales with 1/T².
5. **Simulate.** Each drone is a point-mass quadrotor with a 40° tilt limit, thrust/weight 2 and drag, flown by a PID controller with acceleration feedforward through steady wind plus correlated gusts that differ from drone to drone. 84 flights (7 wind speeds × 12 gust seeds) take about 3 s. Drift, near misses and battery at the design wind become more problems with hints.
6. **Repair.** Nemotron Nano (reasoning off) turns the problems into JSON-pointer edits on the script in 1–3 s; if a round doesn't help, Nemotron Ultra with reasoning takes the next one. The model can't touch the limits or the fleet size, so it can't make a show pass by loosening the safety envelope.
7. **Certify and watch.** A flight certificate ("Flyable up to 8 m/s: worst separation 1.63 m, max drift 0.5 m, 90% battery left, ≥ 29.7 m from the crowd"), a wind × gust heatmap of every simulated flight, and a night-sky video of the *simulated* flight seen from the ground.
8. **Be the wind.** Go live: the swarm flies the show in real time while a phone, paired by QR code, is the wind. Tilt it to steer a steady wind, shake it for a gust. Streaks show the wind, each drone's planned spot stays as a faint dot with a line to where the wind pushed it, and near misses are circled.

### Inspiration

Our other hackathon projects are built on MotionLang, a declarative 2D motion language whose validator gives errors written for an LLM repair loop. A drone light show is the most physical motion graphic there is: the formation is a vertical picture, each drone is an actor, and the validator's job becomes safety. Show design today is slow, expensive and safety-critical, and the creative part (what should the sky show?) is tangled up with the part that must be exactly right (will two drones meet?). We wanted the model to do only the first, and code and physics to do the second, then hand their findings back to the model.

### How we built it

- **Models (all on Nebius Token Factory)**: Nemotron 3 Ultra 550B writes show scripts (JSON mode, 2.5–4 s) and takes over repairs that stall (reasoning on); Nemotron 3 Nano 30B (reasoning off) turns checker and simulator hints into edit ops (1.2–3 s), applies user tweaks and writes the briefing searches; Nemotron 3 Super 120B reads search results into cited limits (~3.5 s for ~4k tokens). Every call's model, latency and tokens show in the app's agent log.
- **Tavily**: four runtime searches per site briefing, and the results set limits the simulator enforces.
- **MotionLang**: the compiler's output is a MotionLang document, validated and baked by the shared library; the browser plays it with MotionLang's own player.
- **Compiler and checker**: SciPy's Hungarian solver and k-d trees, NumPy, Pillow rasterization for formation sampling (arc-length sampling for outlines, k-means plus relaxation for fills).
- **Simulation**: a vectorized NumPy quadrotor swarm, run in a process pool for the sweep and stepped in real time for live mode; imageio-ffmpeg renders the night-sky MP4 from simulated positions with a ground-level camera.
- **App**: FastAPI with streamed progress (NDJSON) and WebSockets for live mode, vanilla JS with no build step, a phone pad page using DeviceOrientation and DeviceMotion, served over Tailscale HTTPS so phones may use their sensors; a segno QR code pairs the phone.
- **Tests**: 35 tests with a scripted fake model, plus one live test: samplers, CAPT separation, every checker code and its hint (applying the hints fixes the unsafe example), the sim and sweep, the repair loop and its protected paths, quote verification, the web API, video, and the live WebSockets.

### Challenges we ran into

- **Hints that fought each other.** A ring too dense for 100 drones got "make it bigger" from the density check and "make it smaller" from the airspace check, and the repair loop bounced for four rounds. Capping every size hint at what fits the airspace, and suggesting a filled shape when even that isn't enough, fixed it.
- **A physical limit the model mustn't touch.** A drawn line can't hold 100 drones at safe spacing, and the first hint said "use 50 drones", which the model isn't allowed to change. A line can now be a band of drones, and the hint says so.
- **Making wind visible.** The first live mode was physically right and visually boring: a good controller cancels steady wind in a second or two, and a metre of drift is a few pixels. Wind streaks, ghost dots at the planned positions and gustier, more varied wind made the effect obvious without faking the physics.
- **Serverless Jobs.** The sweep was meant to run as Nebius Serverless Jobs, but those need a separately billed AI Cloud project: Token Factory's project id is rejected, and creating a job before billing fails as a bare `PermissionDenied` even as a dry run. We kept the job launcher (with a guard against the CLI's H100 default) and ran the sweep locally.
- **Real phone sensors.** Motion sensors need HTTPS and the phone on the same network path, which meant Tailscale on the phone; the QR code also had to be scalable, not fixed-size, to scan.

### Accomplishments that we're proud of

- A clean split of responsibility: the model chooses, the compiler guarantees what it can, the checker and the simulator verify the rest, and every finding comes back as a fix the model can apply.
- One repair round, in under two seconds, turns a show that would fly at 32 m/s and leave the airspace into one certified for 8 m/s wind.
- Cited limits from real sources, checked against those sources, that can only make the show safer.
- A phone in your hand as a physical input to a physical simulation, live.

### What we learned

- Hints with computed numbers turn a small, fast model into a reliable repairer; the expensive model is only needed when a fix doesn't take.
- Safety envelopes must be enforced by the app, not requested in the prompt.
- Physically faithful and visually legible are two separate requirements, and the second needs its own design.

### What's next

- Export to drone-show formats (waypoints with LED colours per drone) and a hardware-in-the-loop test with a few real drones.
- 3D formations (MotionLang is 2D today; the simulator is already 3D).
- The sweep on Nebius Serverless Jobs for thousand-drone shows, and wind fields from real forecasts instead of a single design wind.
- The motion wand: perform a path with the phone and let Nemotron turn it into show steps.

## Built with

Python, FastAPI, Pydantic, NumPy, SciPy, Pillow, imageio-ffmpeg, WebSockets, segno, pytest, JavaScript, Web Speech API, DeviceOrientation / DeviceMotion, Tailscale, NVIDIA Nemotron 3 (Ultra 550B, Super 120B, Nano 30B), Nebius Token Factory, Tavily, MotionLang

## Try it out

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/constellate

### Project Media

Image gallery (3:2, ≤ 5 MB each), suggested:
0. The architecture card: `demos/constellate/architecture-card.png` (`uv run python demos/constellate/architecture-card.py`).
1. The night-sky video frame: the logo in its artwork colours over the tree line.
2. The app after "Make flyable": green badges, the certificate and the heatmap.
3. The unsafe example: red step chips and the problem list with hints, one problem focused on the stage.
4. Live mode: wind streaks and drift lines, with the phone pad beside it.
5. The site briefing card with its cited findings.

Load the v2 video (about 4.5 minutes) containing all the key moments above plus the result html animations under [here](../demos/constellate/).

* Demo video URL: (to add; see `docs/constellate-demo.md`)

https://youtu.be/l9vzaz39vT8

## Additional info

* Submitter Type: individual
* Organization Name: Dragoniac
* Which track: Physical AI
* New or existing prior to August 26, 2026: New
* Public code repository: https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/constellate
* Working demo: not required for Physical AI. The app runs locally (`uv run constellate serve`); the video shows the key modules in action, as the track allows when there is no hardware.
https://constellate-1siz.onrender.com/

* Which model(s) did you use, and why that size/variant?

Nemotron 3 Ultra 550B-A55B for the creative step that needs the most judgement (writing a show script from a loose request) and for repairs that a small model couldn't fix; Nemotron 3 Nano 30B-A3B with reasoning off for everything that should be instant and mechanical (turning computed hints into edits, applying a user's tweak, writing search queries); Nemotron 3 Super 120B-A12B for reading a dozen search results into cited numbers, where it was accurate and fast with reasoning off.

* How would you rate Nemotron's output quality for your use case? (1–10) **(your call)**

**8.** Ultra's show scripts were valid on the first try in every live run and made sensible artistic choices (a staggered ripple into a logo, colours that match the request). Nano applied hint lists precisely, and once a hint named an exact field and value it almost never deviated. Super's extraction quoted sources verbatim, which our substring check confirmed. Short of a 10: Nano follows hints literally even when two of them conflict (we fixed the hints), and unit conversions needed the factors in the prompt.

* Fine-tune, prompt-engineer, or out of the box?

Out of the box, with prompt engineering and tool feedback. The design puts the precision in code: the model writes a small script, and a compiler, a checker and a simulator turn its choices into numbers and its mistakes into hints with exact values. The prompts describe the script format and the rules (artistic choices only; never touch the limits), and the app enforces the rules.

* How did Nemotron compare to other models you've used? **(your call)**

No rigorous comparison. Nano with reasoning off stood out for latency: a repair round in under two seconds keeps an interactive tool interactive, and escalating to Ultra only when needed kept quality without the wait.

* Which Nebius platform capabilities were most valuable?

**Token Factory's OpenAI-compatible API across the Nemotron range** let each step use the size it needs in the same session: Ultra to choreograph, Super to read sources, Nano to repair and tweak in about a second with reasoning off. JSON mode worked for all three, and the `enable_thinking` toggle let us trade quality for latency per call; the agent log shows every call's model, latency and tokens. We prepared the wind sweep for **Nebius Serverless Jobs** (one shard per function call, a CPU-only launcher) but ran it locally, because AI Cloud billing is separate from Token Factory credits.

* How likely are you to recommend running Nemotron on Nebius? (1–10) **(your call)**

**8.** One endpoint for Nano through Ultra makes model-size routing trivial. What would make it a 10: a path from a Token Factory account to Serverless Jobs, clearer errors when billing isn't active, and a per-request reasoning budget.

* Experience compared to previous environments (1–10) **(your call)**

**8.** Nothing to deploy, standard SDK, models far too large to run locally. The friction was all on the Serverless Jobs side (project ids, billing, the H100 default).

* What would have made Nemotron on Nebius more effective?

1. A path from Token Factory to Serverless Jobs: today they're separate accounts, ids and billing, and the job CLI rejects Token Factory project ids.
2. Clear errors: job creation before billing is activated fails as a bare `PermissionDenied`, even with `--dry-run`.
3. A safe default platform for `nebius ai job create` (it defaults to an H100 with a 250 GiB disk).
4. Read-only JSON paths in structured output, so an app can guarantee a model won't edit its safety limits.
5. A per-request reasoning budget.

* What do you most hope to see from the Nemotron team next? **(your call)**

Reasoning budgets per request, and Cosmos or GR00T on Token Factory, so a project like this could add a learned world model or a robot policy next to the analytic simulator.

* Did you use Tavily? Yes.

<!-- Not part of the form; for reference: the site briefing (`apps/constellate/src/constellate/briefing.py`, `brief`, `tavily_search`) runs four Tavily searches per venue at runtime; Super extracts cited numbers; the app verifies each quote and applies verified values (or unverified ones that are more conservative) to the show's limits, which the flight checker and the wind sweep then enforce. The web app's briefing card links every source. -->

* IRL event city: **(your call)**
* Age and eligibility checkboxes: **(your call)**
* AI tools used: Claude Code
* Level of learning: Significant **(your call)**
* Gained AI value for your career: Yes **(your call)**

## Before submitting

- [x] Commit and push; check the repo link opens `apps/constellate`.
- [x] Record the video (`docs/constellate-demo.md`) and add its URL.
- [x] Gallery images (5, 3:2).
- [x] Tag the repo `constellate-submitted`.
- [ ] Optional: `python3 scripts/export_submission.py constellate` for a standalone public repo.
