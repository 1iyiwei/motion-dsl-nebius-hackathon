# MotionHarness: 3-minute demo plan

Run `uv run motionharness serve` and record the dashboard in Chrome, full window (1440 px or wider, so the tree and log sit side by side). A run takes 2–5 minutes, mostly Super writing code with reasoning on. Start the run, then speed up the waits in editing: the log's timestamps and the model-call timeline show the real latency, so the speed-up is honest. Keep a finished run in the sidebar as a fallback for every shot.

Before recording: `uv run motionharness sandbox-check` (steps in the app README, "Turn on Token Factory Sandboxes"). If Sandboxes access is active, the header pill reads **Token Factory Sandboxes** and the checkpoint ids in the tree are real image UUIDs. Record the run on Sandboxes if at all possible: it's the track's headline requirement.

**Plan while access is pending:**

1. **Now**: record a full take on the local backend as the fallback. Everything except the header pill and the checkpoint ids looks the same on either backend.
2. **When access lands**: re-record only the shots where the sandbox shows (the header pill, the environment node, a tree node's checkpoint UUID, ideally one complete run) and splice them in.
3. **Cutoff Oct 27**: if access still hasn't arrived, submit the local take. Say once, plainly: "the Sandboxes backend is built and tested; while the project awaits activation, local subprocesses stand in."

**Scope**: the video is the dashboard only. Install/uninstall are buttons on the Ship tab now, and the CLI, `rm`, `sandbox-check`, MCP setup and hosting live in the app README. The one optional exception is a 5–10 s MCP clip near the end (Claude Code calling `new_verb`), because "other coding agents can use it as a tool" fits the track.

Live runs measured Oct 19 (local backend, Super 120B ×3, VL critic):

| Task | Result |
| --- | --- |
| `spring_follow` (first run) | c1 passed every test on the first try; VL: 6/10 "no visible overshoot, critically damped" → polish. 217 s, 6 calls |
| `spring_follow` (Super, Super, Ultra) | Ultra candidate wrote in 23 s; all three failed own tests or the demo; c1 passed, VL 4/10 twice. 63 s |
| `spring_follow` (repairs) | round 0: 0/3 pass (import errors, wrong document format) → 2 repair rounds → c7 passes 6/6 + 3/3. 292 s, 9 calls, 113k tokens |
| `spring_follow` (later prompts) | 0/3 pass in round 0 → 3 repair rounds → c9 passes 6/6 + 3/3; critic 4/10 "no overshoot" (correct: the measurements say 0.0%). 517 s, one candidate took 355 s in the sandbox (since capped at 240 s) |
| **`spring_follow` (final, hero take)** | c2 passes 6/6 + 4/4 on the first try; VL rejects it 3× ("0.0% overshoot"); three polishes along one branch take the measured overshoot from 0.0% → 0.0% → 0.1% → **6.2%**; c6 accepted 8/10. **104 s, 11 calls.** Run `20261019-185659-spring_follow` |
| `shake` | 0/3 in round 0 (import errors) → two branches converge, c8 and c9 both pass 6/6 + 3/3, critic 9/10 each. **101 s, 12 calls**. Good live demo |
| `pendulum` (3 runs before the core fix) | own tests kept failing on a false `E_NOOP` from the core validator (5-point sampling hit the swing's zero crossings); reported, fixed in core Oct 19 |
| `pendulum` (after the fix; now offered as `swing`, since verbs should read as actions) | c6 and c7 pass 6/6 + 3/3 by round 2 (122 s), but VL scores 2–4/10 and the polish doesn't convince it; ships with the critic unsatisfied. Not a video task yet |

## Shot list

| Time | Beat | On screen | Voice-over idea |
| --- | --- | --- | --- |
| 0:00–0:12 | Hook | A finished run: the branch tree with a green "shipped" node, then the live preview of the new verb | "This verb didn't exist ten minutes ago. An agent wrote it, tested it, watched it, and fixed it." |
| 0:12–0:30 | The problem | SPEC §11 contract list, then a core verb's source | "Technical artists extend motion systems with new behaviours. Each one has to be continuous, deterministic, bounded… MotionLang writes that down as a test suite." |
| 0:30–0:50 | Ask | In the sidebar's **New verb** box, click the `spring_follow` example chip under the text box (or `shake` for a fast, clean run); it fills in the task text. Then press **Write, test & critique**. The log starts; three model bars grow on the timeline | "Nemotron Super writes three candidates in parallel, each with a different approach: closed form, physical, minimal." |
| 0:50–1:25 | Test in sandboxes | Tree nodes turn red/green with contract and own-test bars. Open a failing node: the failing test with its traceback; the "Sent back for repair" box | "Each candidate is a sandbox run branched from one prepared checkpoint. The contract suite is fixed; the model also writes tests for what *this* verb should do." |
| 1:25–1:55 | Repair | A repair node appears to the right of its parent; Diff tab shows the fix | "Failures go back verbatim. The repair branches from its parent's own checkpoint, so the tree you see is the tree of sandbox images." |
| 1:55–2:25 | The critic | Motion tab on c2: every test green, but VL says 4/10, "0.0% overshoot". Click along the polish chain c4 → c5 → c6: overshoot 0.0% → 0.1% → 6.2%, then 8/10 accepted | "Passing tests isn't the same as looking right. Nemotron VL watches the motion, with exact measurements next to the image, and sends it back." |
| 2:25–2:35 | Ship | Ship tab: the package card, **Download plugin (.zip)**, **Install here** turning into "installed here" | "The winner is a real package with an entry point: download it, or install it here in one click." |
| 2:35–2:50 | Try in a scene | "Try in a scene" tab: "a puppy runs across the yard and its red balloon chases after it and bobs to a stop". Same cast on both sides; the left plan uses `mh.spring_follow` (badge), the right one falls back to `follow` | "And the payoff: the planner SketchDynamics uses, with and without the new verb, on the same scene." (~20 s; the core-only side plans first) |
| 2:50–3:00 | Under the hood + close | Model-call table (Super, Nano, VL with latency and tokens). Optional 5–10 s: Claude Code calling the MCP tool `new_verb` (see "The MCP clip" below) | "Every call runs on NVIDIA Nemotron via Nebius Token Factory, and other coding agents can call it as a tool." |

## The MCP clip (optional, 5–10 s on screen)

Shows another coding agent using MotionHarness as a tool. Record it separately and cut the wait.

**One-time setup** (already done Oct 20; registered in local scope for `apps/motionharness`, so it only appears in Claude Code sessions started there):

```bash
cd ~/Trees/nebius-hackathon/apps/motionharness
claude mcp add motionharness -- uv run --directory . motionharness mcp
claude mcp list        # motionharness: … ✔ Connected
```

**Recording:**

1. Have the dashboard running (`uv run motionharness serve`) in a browser window next to the terminal.
2. Start Claude Code from that folder, with a tool timeout long enough for a run (1–5 min):

   ```bash
   cd ~/Trees/nebius-hackathon/apps/motionharness && MCP_TOOL_TIMEOUT=600000 claude
   ```

3. At the Claude Code prompt, type:

   > Use the motionharness tool to add a `wobble` verb: the actor wobbles like jelly after landing and settles.

4. Allow `motionharness - new_verb` when asked. On screen: the tool call, then its result (status, each candidate's contract and own-test counts, the critic's score, and the plugin path).
5. Cut to the dashboard: the same run is in the sidebar, since the CLI, the dashboard and MCP share one runs folder (`out/motionharness` at the repo root). Click it and the full branch tree is there.

Voice-over: "Other coding agents can use it too: here Claude Code calls MotionHarness over MCP, and the run shows up in the same dashboard."

## Known gaps (prioritised for the video)

1. **Sandboxes access pending** (applied Oct 19). The backend is implemented and unit-tested against a fake client; the first real run needs `NEBIUS_PROJECT_ID` and `motionharness sandbox-check --run` (builds and caches the environment image).
2. **Latency**: Super with reasoning takes 20–130 s per candidate. Cut in editing. Ultra was faster on the one run we tried (23 s).
3. **The critic can be fooled by the picture**: before we passed it measurements it accepted a spring that ended offset from the target as "overshoot". The measurements now win, but keep an eye on its verdicts in recorded takes.
4. **Repairs are per candidate**; there's no merge of two half-good candidates.
5. **Name verbs as actions** (`swing`, not `pendulum`): a noun reads as an object to SketchDynamics' planner and shape finder. The run's first step (Super, ~1 s) flags such names (✎ in the log) and suggests one.
6. **Exporters** (Lottie, Blender) aren't a task kind yet; Tavily doc lookup is wired in for them (Nano decides when a task depends on an external spec).
