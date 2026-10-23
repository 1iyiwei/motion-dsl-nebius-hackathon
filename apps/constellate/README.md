# Constellate

**Sketch or say a drone light show. Nemotron choreographs it, a deterministic compiler makes it flyable, and a wind simulation certifies it before anything leaves the ground. Then your phone becomes the wind.**

Nebius x NVIDIA Global AI Hackathon, Physical AI track. License: Apache-2.0.

Drone light shows are motion graphics made of physical things: a few hundred quadrotors with thrust limits, batteries and wind to fight, flying metres apart in front of a crowd. Designing one today means weeks of specialist work in tools built for the safety problem, not the creative one. Constellate splits the job the way it should be split: the model makes the artistic choices, and deterministic code and a physics simulation make it safe, then hand their findings back to the model to repair.

```
"a red heart that slowly spins, then our logo, then THANK YOU in gold"  +  a sketch / an SVG logo
        │
        ▼  Nemotron 3 Ultra
show script        formations, transitions, spins, colours (artistic choices only)
        │
        ▼  deterministic compiler (Hungarian assignment + CAPT straight-line paths)
MotionLang         one actor per drone; validated by the MotionLang validator
        │
        ├─► flight checker   separation, speed, acceleration, airspace, density, battery   (ms)
        └─► wind sweep       84 simulated flights: 7 wind speeds × 12 gust seeds           (~3 s)
                │
                ▼  problems with computed hints ("lengthen heart_spin from 8 to 34.5 s")
        Nemotron 3 Nano edit ops (Ultra if a fix doesn't take) → recompile → recheck
                │
                ▼
        flight certificate + night-sky video of the simulated flight + live mode (the phone is the wind)
```

## Run it

```bash
uv sync
cp apps/constellate/.env.example .env     # add NEBIUS_API_KEY (Token Factory) and TAVILY_API_KEY; never commit them
uv run constellate serve                  # http://127.0.0.1:8780/
uv run pytest apps/constellate            # tests use a scripted model: no key needed
```

Pick **Examples… → Heart, spin, logo** to start from a deliberately unsafe show, or draw and ask for your own.

### The phone (optional): live wind

Phones expose motion sensors only to HTTPS pages, so serve the app over your [Tailscale](https://tailscale.com) tailnet, and put the phone on the same tailnet (install the Tailscale app and sign in with the same account; `tailscale status` on the computer should then list it):

```bash
uv run constellate serve --no-browser
tailscale serve --bg --https=10000 http://127.0.0.1:8780   # https://<your-machine>.<tailnet>.ts.net:10000/
```

Open the app from that https address on the computer too: the pairing QR code points at the address the page was opened from (or set `CONSTELLATE_PUBLIC_URL`). Android Chrome needs no permission prompt; iOS Safari asks when you tap Start. Without a phone, the live card has sliders and a Gust button.

### Hosting it (Render or similar)

- **Build:** `pip install uv && uv sync --frozen --no-dev`
- **Start:** `uv run --frozen --no-dev constellate serve --host 0.0.0.0 --port $PORT --no-browser`. When `$PORT` is set, `serve` already defaults to `0.0.0.0` and that port, so the flags just make it explicit. Without them it listens on `127.0.0.1:8780`, which a hosting proxy can't reach ("no open ports detected on 0.0.0.0").
- **Health check:** `/healthz`. **Environment:** `NEBIUS_API_KEY`, `TAVILY_API_KEY`, and `PYTHON_VERSION=3.12.7`.
- Behind the host's proxy, the app trusts `X-Forwarded-*` headers (automatic when `$PORT` is set, or with `CONSTELLATE_BEHIND_PROXY=1`), so the pairing QR code points at the public `https://` address, and any phone can be the wind without Tailscale.
- **Guardrails** (all off by default; [`render.yaml`](../../render.yaml) at the repo root turns them on): `CONSTELLATE_ACCESS_CODE` (model calls, sweeps, videos and live sessions need it; the page shows a field next to the title), `CONSTELLATE_RATE_LIMIT` (model requests per visitor per hour), `CONSTELLATE_DAILY_TOKENS` (all visitors, per UTC day), `CONSTELLATE_MAX_JOBS` (sweeps, videos and live sessions at once) and `CONSTELLATE_SWEEP_WORKERS` (processes per sweep; small instances report the host's CPU count, not theirs). On a 512 MB instance use 1 job and 1 worker: a plan or a sweep peaks near 300 MB (the show is baked 10 drones at a time, `CONSTELLATE_BAKE_CHUNK`), and each extra sweep worker adds ~220 MB. Loading examples and the flight check stay open without a code. Counters live in memory, so a restart resets them: the access code is the real gate.

## The web app

- **Canvas**: a night-sky stage sized to the airspace, with the geofence dashed. Draw a shape for the swarm to form, drop an SVG logo or use the sample, or just say it (Web Speech API). − / Fit / + zoom out to show drones that leave the airspace, and the view fits them automatically.
- **Plan show** (Ultra) turns the request into a show script; the compiler flies it with the chosen number of drones (10–500), and the flight check marks problems on the step chips and as badges. Click a problem to jump to that moment and circle the drones involved.
- **Tweak** ("slower spin", "end in blue", "use 150 drones") is a Nano edit, about 1.5 s.
- **Make flyable** runs the repair loop, then the wind sweep: a certificate ("Flyable up to 8 m/s: worst separation 1.63 m, max drift 0.5 m, 90% battery left, ≥ 29.7 m from the crowd") and a wind × gust heatmap of all 84 simulated flights.
- **Render night-sky video**: an MP4 of the *simulated* flight in the design wind, seen from the ground (about 11 s to render an 85 s show).
- **Site briefing** (Tavily): a venue and a date become cited limits (see below).
- **Go live**: the swarm flies the show in real time while the phone is the wind. Tilt sets a steady wind (up to 12 m/s); a shake adds a gust that dies down over about two seconds. Streaks show the wind, a faint dot marks where each drone should be with a line to where the wind has pushed it, and near misses are circled.
- **Agent log**: every Nemotron call (model, latency, tokens), every Tavily search, and every edit the model made.

## The command line

```bash
uv run constellate brief "Marina Green, San Francisco" --date "October 31 2026, 9 pm" -o out/brief.json
uv run constellate plan "The swarm rises into a red heart that slowly spins, then morphs into our logo, and ends by spelling THANK YOU in gold" \
    --svg orbit=apps/constellate/src/constellate/samples/orbit-mark.svg --brief out/brief.json -o out/show.json
uv run constellate fly out/show.json --save out/flyable.show.json --mp4 out/show.mp4 --report out/report.json
uv run constellate sweep apps/constellate/src/constellate/examples/heart-logo-flyable.show.json --design-wind 8   # no model calls
uv run constellate compile apps/constellate/src/constellate/examples/heart-logo.show.json --preview out/unsafe.html  # no model calls
```

## How it works

**The show script** ([`show.py`](src/constellate/show.py)) is what Nemotron writes: named formations (a preset heart, star, ring, circle, grid, spiral or wave; a drawn stroke; an SVG; text) and steps (`form` with a transition style and a hold, `spin`, `color`, `hold`), in metres on a vertical show plane. The limits (airspace, separation, speed, acceleration, battery, design wind, crowd setback) sit next to it, and the model can't edit them.

**Formations** ([`formations.py`](src/constellate/formations.py)): each shape is rasterized, and N drones are spread over its fill or outline with k-means plus a relaxation that pushes close pairs apart; outlines of presets and strokes are sampled evenly by arc length. An SVG logo keeps its artwork colours on the drones; a drawn line can be a single file of drones or a band.

**The compiler** ([`compiler.py`](src/constellate/compiler.py)) assigns drones to each new formation with the Hungarian algorithm on squared distance and flies them along synchronized straight lines (inOutSine). By CAPT (Turpin, Michael & Kumar, 2014), that is collision-free when the start and goal formations are spaced at least √2 × the minimum separation. `stagger` (a ripple) and `expand` break the synchronization on purpose, for the look; spins are `orbit`s about the formation centre; landing back on the launch grid is automatic. The output is an ordinary [MotionLang](../../packages/motionlang) document, one actor per drone, which MotionLang's own validator checks and its player plays.

**The flight checker** ([`flight.py`](src/constellate/flight.py)) measures MotionLang's baked tracks in metres: separation while moving (a k-d tree per frame, so only nearby pairs are compared), speed, acceleration, airspace, formation density (the CAPT precondition) and battery time. Every problem names a script step and carries a hint with computed numbers. Required acceleration scales with 1/T², so the duration that fits is T·√(a/a_max); the size that clears an edge or spaces drones is computed from the formation itself and capped at what fits the airspace, so two hints never contradict each other.

**The simulation** ([`sim.py`](src/constellate/sim.py)): each drone is a point-mass quadrotor (thrust/weight 2, 40° tilt limit, drag) with a PID position controller that feeds forward the planned acceleration. Wind is a steady speed plus Ornstein–Uhlenbeck gusts, partly shared across the swarm and partly per drone, and it can push drones off the show plane, so separation is measured in 3D. Battery use grows with thrust^1.5. 100 drones through an 85 s show take about 0.25 s.

**The sweep and certificate** ([`sweep.py`](src/constellate/sweep.py)): 7 wind speeds × 12 gust seeds in a process pool, about 3 s on a laptop. The certificate states the highest wind every seed survives, with the worst separation, drift, battery and distance to the crowd, and is worded as what it is: a simulation-based planning aid, not a regulatory approval. Failures at or below the design wind become problems with hints (a longer step, so less speed is spent flying into the wind, or a larger formation for gust room).

**The repair loop** ([`agent.py`](src/constellate/agent.py)): problems go to Nemotron Nano (reasoning off), which replies with JSON-pointer edit ops on the script; the app applies them atomically and rechecks. If the same problems survive a round, the next round goes to Ultra with reasoning on. `/limits`, `/sources` and `/drones` are off-limits to repairs, so the agent can't make a show pass by loosening the safety envelope or shrinking the fleet. The user can change the fleet size, and so can a tweak the user asks for.

**The site briefing** ([`briefing.py`](src/constellate/briefing.py)): Nano writes four searches for the venue and date (altitude limit, crowd setback, nearby airspace, typical wind for the month), Tavily runs them, and Super reads the results into numbers, each with its source URL and a verbatim quote. The app checks that every quote really appears in the cited result. A verified number sets its limit: the ceiling sets the airspace height above a 10 m floor, typical wind × 1.3 becomes the design wind the sweep must certify, and the setback goes into the certificate. An unverified number is used only if it is *more* conservative than the default, so a misread page can tighten the limits but never loosen them. Airspace notes ("most of San Francisco sits within Class B airspace requiring LAANC authorization") are shown with their sources, not acted on.

**Live mode** ([`live.py`](src/constellate/live.py)): the same swarm model stepped in real time at 30 frames a second on the server and streamed over a WebSocket to the big screen; the phone's pad ([`static/pad.html`](src/constellate/static/pad.html)) sends tilt (DeviceOrientation) and shakes (DeviceMotion) over another.

## How Nemotron / Token Factory is used

| Job | Model (Token Factory) | Notes |
| --- | --- | --- |
| Write the show script | `nvidia/Nemotron-3-Ultra-550b-a55b`, JSON mode | 2.5–4 s, ~0.7k → 0.5–1k tokens; an invalid script goes back with its validation error |
| Apply checker and sim hints as edit ops | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | 1.2–3 s per round, ~1.3–1.8k → 70–230 tokens; one round fixes a typical unsafe show |
| Repair when a round didn't help | `nvidia/Nemotron-3-Ultra-550b-a55b`, reasoning on | ~3.5 s; makes the bigger change (it turned a stuck wind problem into a much slower spin) |
| Tweak ("slower spin", "use 150 drones") | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | ~1.3 s |
| Write the briefing searches | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | ~2 s |
| Read search results into cited limits | `nvidia/nemotron-3-super-120b-a12b`, reasoning off, JSON mode | ~3.5 s for ~4k tokens of snippets from 13–15 sources |

All calls go through `motionlang.agent`'s OpenAI-compatible client pointed at Token Factory, and every call's model, latency and tokens appear in the web app's agent log and the CLI output. Models can be overridden with `MOTION_PLANNER_MODEL`, `MOTION_EDITOR_MODEL` and `CONSTELLATE_BRIEFER_MODEL`.

**Tavily** is called at runtime by the site briefing (four `search` calls per briefing). Its results set the airspace height, the design wind and the crowd distance, each with a citation the page links to.

**Nebius Serverless Jobs (optional path)**: the sweep is written so one wind × seed shard is one function call (`constellate.sweep.run_one`), the unit a Serverless Job would run. With a billed Nebius AI Cloud project (`NEBIUS_CLOUD_PROJECT_ID`) and the `nebius` CLI logged in, `uv run constellate jobs-check` dry-runs a CPU job; the launcher always sets a CPU platform (`cpu-d3`), because the CLI's default is an H100. We ran the sweep locally for the hackathon: AI Cloud billing is separate from Token Factory credits.

## Tests

```bash
uv run pytest apps/constellate                     # ~70 s; a scripted fake model, no key or network needed
RUN_LIVE=1 uv run pytest apps/constellate -m live  # one live plan + repair on Token Factory (costs tokens)
```

They cover the samplers, CAPT separation on direct transitions, every checker code and its hint (including that applying the hints fixes the unsafe example), the sim and the sweep, the repair loop and its protected paths, the briefing's quote check, the web API, video rendering, and the live WebSockets.

## Layout

```
src/constellate/
  show.py         show script + limits (pydantic)
  formations.py   shapes → N drone positions
  compiler.py     Hungarian + CAPT → MotionLang document
  flight.py       flight checker and its hints
  sim.py          point-mass quadrotor swarm, wind, battery
  sweep.py        Monte Carlo wind sweep, certificate, sim issues
  agent.py        plan / make_flyable / tweak (Nemotron)
  briefing.py     Tavily site briefing → cited limits
  live.py         real-time sim for the phone pad
  video.py        night-sky MP4 from simulated positions
  server.py       FastAPI app, NDJSON streaming, WebSockets
  jobs.py         Nebius Serverless Jobs launcher (optional)
  cli.py          constellate serve | brief | plan | fly | sweep | compile | jobs-check
  static/         index.html, app.js, app.css, pad.html (vanilla JS, no build step)
  examples/       heart-logo (unsafe) and heart-logo-flyable show scripts
  samples/        orbit-mark.svg (ours, Apache-2.0)
```

The canvas and voice code are adapted from the SketchDynamics app in the same repo (apps never import each other).

## Feedback on Token Factory and the NVIDIA stack

- **Nano with reasoning off is the right tool for "apply these hints".** Turning eight checker problems into five JSON-pointer ops took 1.9 s and ~200 output tokens, reliably. Ultra in plain JSON mode wrote valid show scripts on the first try in 2.5–4 s.
- **Escalating by model size works.** When Nano's fix didn't clear a wind problem, one Ultra round with reasoning on made the bigger change that got the show certified.
- **The app has to protect the safety envelope.** "Lower the design wind" is the easiest way for a model to make a check pass, so the op applier enforces read-only paths. A per-request "read-only JSON paths" option for structured output would make this a platform guarantee.
- **Asking for a verbatim quote and checking it with a substring match** is a cheap guard against invented numbers, and Tavily's snippets are short enough that a whole briefing fits one Super prompt. Unit conversion (mph → m/s) came out right only once the prompt listed the factors.
- **Serverless Jobs and Token Factory are separate worlds.** A Token Factory account's `aiproject-…` id is rejected by `nebius ai job` ("expected project"); creating a job without AI Cloud billing fails as a bare `PermissionDenied`, even with `--dry-run`; and `nebius ai job create` defaults to an H100 with a 250 GiB disk, so a forgotten flag makes a CPU simulation an expensive GPU job. Saying "billing not activated" and defaulting to the cheapest platform would help.
