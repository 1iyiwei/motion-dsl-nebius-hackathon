# Constellate

Sketch or say a drone light show. Nemotron choreographs it, a deterministic compiler makes it flyable, and a wind simulation certifies it before anything leaves the ground.

Work in progress (Nebius x NVIDIA Global AI Hackathon, Physical AI track). License: Apache-2.0.

## Setup

```bash
uv sync
cp apps/constellate/.env.example .env   # fill in the keys; never commit them
```

## Try it

```bash
# Site briefing: Tavily searches the venue's drone rules and typical wind; Nemotron reads them into cited limits (needs TAVILY_API_KEY)
uv run constellate brief "Marina Green, San Francisco" --date "October 31 2026, 9 pm" -o out/constellate/brief.json
# Nemotron Ultra writes a show script from a request (needs NEBIUS_API_KEY); --brief applies the briefing's limits
uv run constellate plan "The swarm rises into a red heart that slowly spins, then morphs into our logo, and ends by spelling THANK YOU in gold" \
    --svg orbit=apps/constellate/src/constellate/samples/orbit-mark.svg --brief out/constellate/brief.json -o out/constellate/planned.show.json
# Repair loop: flight check → Nemotron edit ops → wind sweep → more ops, until certified; then the night-sky video
uv run constellate fly out/constellate/planned.show.json --design-wind 8 --save out/constellate/flyable.show.json --mp4 out/constellate/show.mp4
# No model calls: check and sweep a script yourself
uv run constellate sweep apps/constellate/examples/heart-logo-flyable.show.json --design-wind 8
```

## The deterministic core (no model calls)

```bash
uv run constellate compile apps/constellate/examples/heart-logo.show.json --preview out/constellate/unsafe.html
uv run constellate compile apps/constellate/examples/heart-logo-flyable.show.json -o out/constellate/show.motion.json --preview out/constellate/show.html
```

The first show is deliberately unsafe: the checker reports speed, acceleration and geofence problems, each with the exact fix. The second is the same show with those fixes applied.

How it works: a **show script** (formations + steps; what Nemotron will write) is compiled into a MotionLang document with one actor per drone. Drones are assigned to each new formation with the Hungarian algorithm on squared distance, and fly synchronized straight lines (CAPT: collision-free when formations are spaced at least √2 × the minimum separation). The **flight checker** then measures the baked tracks in metres: separation (a k-d tree per frame, so only nearby pairs are compared), speed, acceleration, geofence, formation density and battery time.

**Simulation**: each drone is a point-mass quadrotor (thrust/weight 2, 40° tilt limit, drag) with a PID position controller that feeds forward the planned acceleration. Wind is a steady speed plus correlated gusts, part shared across the swarm and part per drone, and it can push drones off the show plane, so separation is measured in 3D. Battery use grows with thrust^1.5. The sweep runs 7 wind speeds × 12 gust seeds (84 flights) in a process pool, about 3 s for 100 drones on a laptop, and issues a **flight certificate** ("flyable up to 8 m/s; worst separation 1.63 m; max drift 0.73 m; 90% battery left"). It is a planning aid, not a regulatory approval.

**Repair loop**: problems from the flight checker and the sweep carry hints with computed numbers (the duration that fits, the size that keeps clear of the edge). Nemotron Nano (reasoning off) turns them into JSON-pointer edit ops on the show script in 1–2 s; if the same problems survive a round, Nemotron Ultra takes over with reasoning on. The model can't edit `limits` (the site's and the user's safety envelope), so it can't make a show pass by loosening it.

The night-sky MP4 is drawn from the *simulated* positions, seen from the ground, so wind drift shows on screen.

**Site briefing (Tavily)**: Nemotron Nano writes four searches for the venue and date (altitude limit, crowd setback, nearby airspace, typical wind for the month), Tavily runs them, and Nemotron Super reads the results into numbers, each with its source URL and a verbatim quote. The app checks that every quote really appears in the cited result. A verified number sets the limit (the ceiling sets the airspace height above a 10 m floor; typical wind × 1.3 sets the design wind the sweep must certify; the setback is reported in the certificate as the closest approach to the crowd). An unverified number is used only if it is *more* conservative than the default, so a misread page can tighten the limits but never loosen them. Airspace notes (for example "controlled airspace needs LAANC authorization") are shown with their sources, not acted on.

## Nebius Serverless Jobs (optional)

The sweep is written so one wind × seed shard is one function call (`constellate.sweep.run_one`), which is the unit a Serverless Job would run. With a billed Nebius AI Cloud project, `NEBIUS_CLOUD_PROJECT_ID` in `.env` and the `nebius` CLI logged in, `uv run constellate jobs-check` dry-runs a CPU job (`cpu-d3`; the CLI's default is an H100, so the launcher always sets the platform). We ran the sweep locally for the hackathon: AI Cloud billing is separate from Token Factory credits.
