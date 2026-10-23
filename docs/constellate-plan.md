# Constellate: plan (Physical AI track)

Agreed Oct 23. Deadline **Fri Oct 30, 10:00 am PT**; target is to submit **Thu Oct 29 evening**. App: `apps/constellate/` (replaces the "embodiedsketcher" working name).

**Pitch**: sketch or say a drone light show; Nemotron choreographs it, a deterministic compiler makes it flyable, and a physics simulation certifies it against wind before anything leaves the ground.

**Oct 23 update: cut line 4 taken.** Serverless Jobs need a separately billed Nebius AI Cloud tenant (Token Factory credits don't carry over), and the user chose not to add billing. The sweep and the MP4 run locally (parallel processes) with the same code; `constellate.jobs` and `constellate jobs-check` stay as the documented path for anyone with an AI Cloud project. The Jobs friction goes into the feedback section. Sections below are updated accordingly.

**Prizes**: Physical AI track award; bonus: Best Use of Tavily (real runtime call, below) and Most Valuable Feedback.

**Progress (Oct 24)**: the Sat–Wed milestones are done, ahead of schedule: show script, CAPT compiler, flight checker, wind sim + sweep + certificate, Nemotron plan/repair loop, night-sky MP4, Tavily site briefing, and the desktop web app (`constellate serve`, port 8780; CanvasMind holds 8770). Phone pad done too (QR pairing, live WebSocket sim, tilt/shake wind; slider fallback). Thu docs drafted: app README, docs/constellate-submission.md, docs/constellate-demo.md, export entry. Left: commit, video, gallery, submit, tag.

## Why this fits

- Drone shows are physical motion graphics, and most formations lie on a vertical plane, so 2D MotionLang fits: one actor per drone.
- Real show design is slow, expensive and safety-critical. The agent does the artistic part; deterministic code and a simulator do the safety part, and their findings drive the agent's repairs. That is the "sense and act" loop.
- No hardware: the video shows the key modules in action (the track allows this without hardware).

## 1. Core loop (decision 1)

```
intent (text/voice) + strokes / SVG / text / presets
   │
   ▼  Nemotron Ultra
show script  ── formations, holds, transitions, spins, LED colours (artistic choices only)
   │
   ▼  deterministic compiler
MotionLang document  ── one actor per drone, move / recolor verbs, orbit for spins
   │
   ├─► motionlang validator (conflicts, jumps, bounds)
   ├─► flight checker (separation, speed, acceleration, geofence, battery) ── ms
   ├─► quick sim (one calm run, local) ── s
   └─► sweep sim (local processes: wind × seeds, MP4) ── min
          │
          ▼  issues with computed hints
   Nemotron Nano/Super edit ops (or Ultra script rewrite) → recompile → recheck
          │
          ▼
   flight certificate
```

- **Nemotron writes the show script, not per-drone MotionLang.** 100 drones × many verbs is where an LLM is weak and adds nothing. The script is a small JSON (pydantic schema, structured output): formations (`stroke` id, `svg` asset, `text`, `preset`), durations, transition style (`direct`, `stagger`, `expand_then_morph`), spin, colours.
- **Compiler (deterministic)**: sample each formation into N target points at ≥ the minimum spacing; assign drones between consecutive formations with the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) on squared distance; emit synchronized straight-line `move` verbs. By CAPT (Turpin, Michael & Kumar 2014), min-sum-of-squares assignment with synchronized straight-line paths is collision-free when start and goal separations are large enough; this holds in 2D too.
- **Flight checker (app-level, on baked tracks)**: a uniform hash grid with cell size = minimum separation, checking only neighbouring cells per frame (O(N) per frame). Per-frame sampling is enough: at 5 m/s and 30 fps a drone moves ~17 cm per frame vs a 1.5 m separation. Checks: min separation, max speed, max acceleration, geofence/altitude ceiling, show duration vs battery. Issues use MotionLang's `Issue` shape (`code`, `message`, `hint`, `block`, `actor`, `t`), with hints that name exact blocks and numbers, e.g. "d17 and d42 come within 0.6 m at t=12.3 s during `heart_to_logo`; lengthen it to ≥ 4.2 s or use `expand_then_morph`".
- **Where the agent earns its place**: creative requests break CAPT's assumptions (a spin during a transition, ripple staggers, wiggles, a formation too dense for the drone count). The checker and sim catch it; Nemotron repairs with edit ops.
- **Core library**: unchanged. MotionLang's `no_overlap` is pairwise bounding boxes and `max_speed` is per actor, so swarm checks live in the app. A swarm-wide `no_overlap` / `min_distance` and `max_accel` are post-deadline core ideas (see [implementation-plan.md § After judging](implementation-plan.md#after-judging-dec-16-onward-shared-code-refactor)).
- MotionHarness could later write a `drone.*` verb plugin; nothing depends on it.

**Units**: canvas maps to metres (e.g. 1280×720 px = 128×72 m at 10 px/m); canvas bottom = minimum safe altitude. Defaults: 1.5 m separation, 8 m/s max speed, 6 m/s² max acceleration, 20% battery reserve (all overridable by the site briefing).

## 2. Simulation (decision 2)

One module, `constellate.sim`, with two entry points.

- **Model**: point-mass quadrotor in 3D (show plane x across / z up; y is depth, so wind pushes drones off-plane and separation is 3D). Thrust/weight ≈ 2, max tilt ≈ 35° → acceleration limits; max speed; linear drag. PD position controller with acceleration feedforward from the baked track; saturation makes drones fall behind when the plan asks for too much. Wind = steady + Ornstein–Uhlenbeck gusts; GPS noise of a few cm. Battery: power ∝ thrust^1.5 → remaining % vs reserve.
- **Local quick check**: one calm run, ~1 s, on every edit.
- **Sweep** (local, a process pool; the same entry point could run as a Nebius Serverless Job on `cpu-d3` via `constellate.jobs`, see the update at the top): Monte Carlo sweep (e.g. 5 wind speeds × 16 gust seeds), night-sky MP4 rendered from *simulated* positions (glow, LED colours, slight ground-level perspective), JSON report next to the show.
- **Outputs**: per drone and per transition: tracking error (max, RMS), saturation intervals, near misses (min 3D distance, time, pair), battery margin. Per show: a **flight certificate** ("safe up to 6 m/s wind; 0 near misses in 80 runs; worst separation 1.7 m"), worded as a planning aid, not a regulatory approval.
- **Feedback into repair**: sim findings become `Issue`s with computed hints. Required acceleration scales with 1/T², so the needed duration is T·√(a_peak/a_max). Gust near misses suggest scaling the formation up or lowering the show's wind limit. Nano handles single-number fixes; Ultra rewrites the script when a formation must change.

## 3. Interfaces (decision 3)

- **Desktop canvas first; works fully without a phone.** SketchDynamics-style canvas (mouse, trackpad, pen): draw a formation (sampled into N drones), drop in an SVG logo, type text, or pick a preset (heart, star, ring, grid); draw a path for the swarm to fly along. Text or voice prompt (Web Speech API) → show script chips → player with live flight-check badges → Simulate → heatmap, MP4, certificate. Canvas and voice code are copied from SketchDynamics, not imported.
- **Phone: optional sensor controller, paired by QR code.** The desktop shows a QR code; the phone opens `/pad?code=…` and streams sensors over a WebSocket. Unpaired or absent → nothing breaks.
  1. **"Be the wind"** (first): the live sim streams positions to the desktop at 30 Hz; shaking (accelerometer) injects a gust in proportion to how hard you shake; tilt (orientation) sets the steady wind direction. The swarm gets pushed, recovers, and the near-miss badge flashes when overdone.
  2. **Motion wand** (second, first cut): laser-pointer mapping (orientation → point on the show plane, timestamped at ~60 Hz) gives a timed stroke; Nemotron turns it into a flight path with the user's timing. Phone yaw can set a spin angle and speed. No ARCore needed (the moto g stylus 5G 2023 isn't confirmed on Google's ARCore list; accelerometer-integrated position drifts anyway).
- **Deployment**: no public hosting (not required for this track, and it avoids a third service to keep up through judging). Run on the Mac; the phone reaches it over **Tailscale HTTPS** (`tailscale serve`), since mic and motion sensors need a secure page. The Nebius CLI and credentials stay on the Mac. README documents `uv run constellate` with localhost or Tailscale.

## 4. Tavily site briefing, video, submission (decision 4)

**Site briefing (Best Use of Tavily)**: venue + date ("Marina Green, San Francisco, Oct 31, 9 pm") → Nano writes queries → Tavily searches airspace and drone-show rules (altitude ceiling, airport distance, crowd setback) and typical wind for the place and season → Super turns the results into **show limits** (geofence/ceiling, crowd-side margin, sweep wind range), each with a cited source. The results change the safety checks, not just the page.

**Video (≤ 3:00; ~2:20 of live modules)**

| Time | Beat | On screen |
| --- | --- | --- |
| 0:00–0:12 | Hook | Our night-sky MP4 (heart → spin → logo); show design is slow and safety-critical |
| 0:12–0:25 | Site briefing | Venue typed; Tavily results → cited limits |
| 0:25–0:55 | Design | Draw a heart, drop an SVG logo, say "heart, then it spins and morphs into the logo, end with THANK YOU"; Ultra script; compiler fills the player with 100 drones |
| 0:55–1:30 | Safety repair | Near miss during spin + morph and an acceleration overrun (times, pairs); Nemotron edit ops; badges go green; problem list before/after |
| 1:30–2:05 | Wind sweep | Sweep runs; wind × seed heatmap fills; failure at 8 m/s → hint → fix or wind limit; certificate |
| 2:05–2:35 | Phone | QR pairing; shake → live gust, recovery, near-miss badge; wand trace if built (else more sim) |
| 2:35–3:00 | Under the hood | Architecture card (Nemotron roles, Token Factory, Tavily, MotionLang, local physics sweep), per-call tokens/latency, repo link |

Cut model waits in editing; keep panel timers visible.

**Submission**

- `apps/constellate/`: README (setup; "How Nemotron, Token Factory and Nebius are used" table: Ultra = show script, Super = limits from Tavily results, Nano = repairs and tweaks; the sweep runs locally, with the Serverless Jobs path documented; Tavily use; feedback), `.env.example`, Apache-2.0 `LICENSE`, sample SVGs with recorded licenses.
- `docs/constellate-submission.md` (Devpost text, Physical AI track, built-with tags, gallery: night-sky frame, certificate, heatmap, repair diff) and `docs/constellate-demo.md` (measured timings, shot list, pre-take checklist).
- Friction from Jobs, Tavily and Nemotron calls → `docs/token-factory-feedback.md`.
- Add a `constellate` start entry to `scripts/export_submission.py`. The app README must not name the other unreleased projects (the export's privacy scan flags it).
- Tag `constellate-submitted` once submitted.

## 5. Schedule and cut lines (decision 5)

| Day | Build | Done when |
| --- | --- | --- |
| Fri 23 | Plan docs. **Human**: install and log in to the Nebius CLI, confirm the project ID, check `TAVILY_API_KEY` in `.env`. **Claude**: Jobs spike (hello-world container on a CPU preset; pick a registry), `apps/constellate` scaffold | A job runs and returns logs, or the fallback is known |
| Sat 24 | Headless core: show-script schema; formation sampling (stroke, SVG via `motionlang.importers.svg`, text, presets); Hungarian assignment; CAPT compiler → MotionLang; grid flight checker with hints; tests | `constellate compile show.json` → valid `.motion.json` + clean flight report for heart → logo, 100 drones |
| Sun 25 | Sim (point mass, controller, wind/gusts, battery) → sim issues with computed hints; Nemotron loop (Ultra script, Nano repair ops); night-sky MP4 renderer | CLI loop takes a deliberately unsafe show to green on its own |
| Mon 26 | Local sweep (process pool) + report + MP4, progress streamed to the app; Tavily site briefing → cited limits | One sweep yields a certificate; a venue lookup changes the limits |
| Tue 27 | Desktop web UI: canvas, SVG drop-in, voice, script chips, player + badges, sim panel, heatmap, certificate | Whole demo story works in the browser without a phone |
| Wed 28 | Phone pad: QR pairing, WebSocket live sim, shake/tilt wind. Wand only if done by ~3 pm | Gust shot works on the moto g over Tailscale |
| Thu 29 | **Feature freeze at noon.** Timed runs, README, submission + demo docs, feedback; record and edit video; submit in the evening | Devpost submitted, tag `constellate-submitted` |
| Fri 30 | Spare time only; resubmit if needed before 10:00 | |

**Cut lines, in order**

1. Motion wand (gusts carry the phone shot).
2. Cosmos: out of plan. The account has no Cosmos model; using it means deploying weights on a Nebius AI Cloud GPU (e.g. a world model for realistic show footage): 1–2 days of setup plus GPU cost for little score.
3. Phone pad entirely → a desktop wind slider takes its shot.
4. ~~Serverless Job → local sweep~~ **taken Oct 23** (no AI Cloud billing): same code, friction logged, said in the README.
5. MP4 → GIF/HTML via `motionlang render` / `preview`.
6. Text formations and voice (canvas, SVG and presets stay).

**Never cut**: compiler + flight checker + repair loop, the sim, the Tavily briefing, the video.

**Protecting the submitted apps**: no edits to `packages/motionlang`; new deps (scipy, a WebSocket server, video encoding) stay in `apps/constellate`; core ideas go on a post-deadline list; run `uv run pytest` daily. Code reused from other apps (Tavily search, canvas strokes, Web Speech voice) is copied with a source comment, not shared, until the post-judging refactor in [implementation-plan.md](implementation-plan.md#after-judging-dec-16-onward-shared-code-refactor).
