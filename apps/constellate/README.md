# Constellate

Sketch or say a drone light show. Nemotron choreographs it, a deterministic compiler makes it flyable, and a wind simulation on Nebius Serverless Jobs certifies it before anything leaves the ground.

Work in progress (Nebius x NVIDIA Global AI Hackathon, Physical AI track). License: Apache-2.0.

## Setup

```bash
uv sync
cp apps/constellate/.env.example .env   # fill in the keys; never commit them
uv run constellate jobs-check           # dry run: can this machine start Serverless Jobs?
```

## Try the headless core

```bash
uv run constellate compile apps/constellate/examples/heart-logo.show.json --preview out/constellate/unsafe.html
uv run constellate compile apps/constellate/examples/heart-logo-flyable.show.json -o out/constellate/show.motion.json --preview out/constellate/show.html
```

The first show is deliberately unsafe: the checker reports speed, acceleration and geofence problems, each with the exact fix. The second is the same show with those fixes applied.

How it works: a **show script** (formations + steps; what Nemotron will write) is compiled into a MotionLang document with one actor per drone. Drones are assigned to each new formation with the Hungarian algorithm on squared distance, and fly synchronized straight lines (CAPT: collision-free when formations are spaced at least √2 × the minimum separation). The **flight checker** then measures the baked tracks in metres: separation (a k-d tree per frame, so only nearby pairs are compared), speed, acceleration, geofence, formation density and battery time.
