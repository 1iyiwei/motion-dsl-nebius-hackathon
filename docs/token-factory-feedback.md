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
  Applied for access on Oct 19; waiting. MotionHarness was built behind a small sandbox interface with a local
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
