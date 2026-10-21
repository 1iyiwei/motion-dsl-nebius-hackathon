# Token Factory / NVIDIA feedback log

Running notes for the submissions' required feedback section (and the Most Valuable Feedback prize).
Add an entry when something surprises us. Include the date, what happened, and the impact.

## 2026-10-17

- **Model catalog** (`GET /v1/models`, 28 models): NVIDIA models available are Nemotron-3 Nano 30B-A3B,
  Super 120B-A12B, Ultra 550B-A55B, Nemotron-3.5 Super-VL, and Nemotron-3.5 Lightning. The catalog web page
  requires login, so `/v1/models` is the only way to get exact ids from a script. Good, but the docs don't list ids.
- **Reasoning toggle**: `chat_template_kwargs: {"enable_thinking": false}` on Nano cut a small JSON edit from
  3.3 s / 630 tokens to 1.1 s / 114 tokens with the same answer. Putting `/no_think` in the system prompt instead
  made Nano spin to `max_tokens` (4000) and return empty content after 17 s. The toggle isn't in the Token Factory
  docs as far as we found. Documenting it per model family would save people time.
- **JSON mode** (`response_format: json_object`) worked on all five NVIDIA models on the first try. Reasoning
  arrives separately (`reasoning_content`), so `content` is clean JSON. 👍
- **Latency** (Ultra, ~3k-token prompt, JSON reply): 2–5 s typical for a full document, but repairs with long
  reasoning went up to 30 s / 10k tokens. A per-request reasoning budget would help interactive apps.
- **Sandboxes** is in Beta behind an access request. Access requested Oct 19 (see below for status).

## 2026-10-18

- **Vision + JSON mode**: `Nemotron-3.5-Super-VL` with an image_url (JPEG data URL, 1280×720) plus `response_format: json_object`
  and `enable_thinking: false` named all 8 parts of a logo correctly in 2–3 s (~1.4k prompt tokens). It worked on the first try.
- **Ultra latency variance**: the same logo-animation prompt (~4.5k prompt tokens) took 22 s, 32 s and 87 s for the first plan.
  The 87 s run spent 14k completion tokens reasoning. Long tails like this hurt an interactive app; a per-request reasoning
  budget (max reasoning tokens) would let us cap them.
- **Structured output (`response_format: json_schema`)**:
  - The grammar engine rejects JSON Schema's `propertyNames` keyword: Ultra returns a 400 "Grammar error: Unimplemented
    keys: ["propertyNames"]", and Super-120B returns a bare 500 with an empty message for the same schema. Pydantic emits
    `propertyNames` for `dict[Literal, ...]` fields, so this will be common. Supporting it, or ignoring unknown keywords
    with a warning, would help. A clear 400 from every model would help too.
  - With the keyword stripped, Super-120B under constrained decoding was the most reliable planner we measured: 6/6 valid
    logo animations after at most two cheap repairs, with no format slips. Ultra under the same schema degenerated
    (12k tokens of nested containers, unparseable), so constrained decoding isn't a drop-in for every model.
- **JSON-mode slip (Super-120B)**: about 1 in 4 plain `json_object` replies came back as the whole document escaped
  into a single key (`{"motion\":\"0.1\",...`). It's recoverable client-side, but it suggests a decoding issue.

## 2026-10-19 (MotionHarness)

- **Sandboxes access**: the Sandboxes API answers `400 Missing "Project" header` without a project ID, and the access
  page says a valid key can still come back `inactive` until the project is activated by email (contree@nebius.com).
  Applied for access on Oct 19. Oct 20: with `NEBIUS_PROJECT_ID` set, `whoami` returns HTTP 200 with every permission
  `false` (import, spawn, …): the "inactive" state, so activation by email is still pending. The first real call then fails
  with a bare `403 Forbidden` on `/v1/inspect/?tag=python:3.12-slim`; the SDK could surface "project not activated for
  Sandboxes" instead (it knows from whoami). MotionHarness now probes whoami and falls back to local with that reason. MotionHarness was built behind a small sandbox interface with a local
  subprocess fallback, so the agent works today and switches over when `NEBIUS_PROJECT_ID` is set. Hackathon teams
  in the Coding & Agentic track would benefit from Sandboxes being enabled with the hackathon credits by default.
- **Sandboxes docs**: the checkpoint/branch model (every `run(disposable=False)` returns a new immutable image; branch
  by running again from it) maps directly onto "try N candidate patches from one checkpoint, keep the passing one".
  The "Try two coding fixes and keep the passing branch" cookbook recipe was exactly our design. Friction points:
  - Two env var names for the same project ID (`NEBIUS_PROJECT_ID` for SDK/REST, `NEBIUS_AI_PROJECT` for the CLI).
  - The Python SDK's `run()` has no `networking` parameter, although REST has `networking.enabled=false`; untrusted
    generated code is exactly when you want the network off.
  - `run()` / file upload / `read()` are only documented in fragments spread over several pages; the reference for
    `ContreeImage.run` was the reliable source.
  - No way to see the effective limits (concurrency, timeout max) without a working project.
- **Nano ignores few-shot "none" examples in JSON mode**: asked "does this task need external docs? reply
  {"queries": [...]}" with an example showing `[]` for a spring verb, Nemotron Nano (reasoning off) still produced
  queries for the exact example task. Asking for a named field first (`{"external": "<name or none>", "queries": []}`)
  fixed it (correct on 4/4 probes: spring → none, pendulum → none, Lottie → Lottie, After Effects wiggle → AE).
- **Super-120B as a coding model**: with reasoning on, writing a ~100-line plugin + tests + demo took 20–130 s per
  candidate (3 in parallel). The first live run passed the full contract suite and its own tests on the first try;
  common slips were importing names that don't exist (`from motionlang.model import Vec2`) and putting `(unchanged)`
  inside a code fence when told it may mark files unchanged.
- **Nemotron-3.5-Super-VL as a motion critic**: given a contact sheet (onion skin + curves + keyframes), it correctly
  flagged a spring that never overshoots ("monotonic ease-in, no oscillation") on a candidate that passed every test,
  in ~2 s with reasoning off. Passing it exact measurements (overshoot %, settle time) next to the image keeps it
  from guessing about small effects.

## 2026-10-20 (Sandboxes, after activation)

- **Activation worked**: `whoami` now returns every permission `true`. The first real MotionHarness run on Sandboxes:
  environment image built in ~15 s (python:3.12-slim + motionlang + pytest/hypothesis, cached by UUID), then each
  candidate (pip install of the plugin, contract suite, its own tests, a probe) took 8–10 s, three in parallel, and
  repairs branched from their parent's saved image exactly as the docs describe. A 7-candidate run took 177 s end to end.
- **Intermittent 500s on the control plane**: right after activation, `GET /v1/whoami`, `POST /v1/files` and
  `GET /v1/files/<sha256>` each returned a bare `500 Internal Server Error` some of the time. It was worst while the
  SDK checked and uploaded ~33 small files concurrently for a single `run(files=…)`: that failed every time, while
  the same files uploaded one by one succeeded. Workaround: we pack each job's files into one tar (one upload per run)
  and retry 5xx with backoff; since then no failures. A 503/429 with a reason (or SDK-side upload concurrency limits)
  would make this diagnosable.
- **SDK details**: `run()` returns `uuid` as a `uuid.UUID`, not `str` (it breaks `json.dumps` of saved ids); the
  documented `files.upload_bytes_file` doesn't exist in contree-sdk 0.3.6 (only `files.upload`).

## 2026-10-21 (CanvasMind)

- **Function calling** (OpenAI `tools`): Super-120B, Nano-30B and Ultra-550B each returned a correct `recall({"query": "sunrise logo"})`
  tool call on the first try, both plain and streamed (tool-call deltas assemble cleanly), in 0.7–2.2 s. That's the main thing an agent
  framework such as Hermes needs, and it worked out of the box. Function calling isn't featured in the hackathon quick-start; a one-line
  "tools work on all Nemotron models" note would save agent builders a probe.
- **NemoClaw + Token Factory**: NemoClaw's "Other OpenAI-compatible endpoint" path (`NEMOCLAW_PROVIDER=custom`, `NEMOCLAW_ENDPOINT_URL`,
  `NEMOCLAW_MODEL`, `COMPATIBLE_API_KEY`) is how Token Factory plugs in; there is no Token Factory preset in the provider menu. The
  variable names are documented only in the NemoClaw repo docs (`docs/inference/set-up-openai-compatible-endpoint.mdx`), not on the hosted
  Hermes pages we found first.
- **NemoClaw spike (NVIDIA side)**: install + non-interactive Hermes onboarding against Token Factory worked on Apple silicon on
  the first real try (78 s install; sandbox build a few minutes). Friction points: the installer needs
  `NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1` even with `--defer-onboarding` in a non-TTY; onboarding silently adds npm/pypi/huggingface/brew
  egress presets that a privacy-focused assistant doesn't need; `nemohermes <name> agent` doesn't work for Hermes (you're told to POST to
  its OpenAI API on 8642 instead). Hermes turns cost 23–35k prompt tokens on Nemotron Super, because the agent's own system prompt and
  tool list are large, which makes prompt caching on Token Factory matter for personal agents.
- **MCP through OpenShell**: a host MCP server must be HTTPS with a publicly trusted certificate (localhost/host.docker.internal are
  rejected). Tailscale's `serve` (ts.net cert, CGNAT address) + `--trusted-private-host` was a clean way to do that, and OpenShell injects
  the bearer token at egress, so the sandbox never holds it.
- **Hermes Agent Telegram adapter (v0.21.3, under NemoClaw)**, two bugs we had to work around with a user plugin
  (`apps/canvasmind/src/canvasmind/hermes_plugin/canvasmind_telegram`):
  1. An `.svg` sent as a document (`image/svg+xml`) is routed to the raster image cache, which raises "Refusing to cache
     non-image data as .jpg"; the file is dropped and the agent only sees "could not be read as an image".
  2. `send_multiple_images` treats only non-`file://` URLs as animations, so a GIF returned by an MCP tool (cached
     locally) is sent as a photo in a media group and Telegram shows its first frame only.
  Also: the gateway resolves the agent home from its session DB, and NemoClaw symlinks `state.db` into
  `~/.hermes/runtime/`, so a customised `SOUL.md` in `~/.hermes/` is ignored until copied to `runtime/`.
- **OpenShell inference routing rewrites the model** on every request to the one configured at onboarding, so an agent
  in the sandbox can't use a second model (e.g. Nemotron VL for images) through `inference.local`. A per-sandbox list of
  allowed models on the same endpoint would let agents route by task (vision, fast edits) the way our host code does.
