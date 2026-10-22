# CanvasMind plan · Personal AI track

Agreed Oct 20, 2026. Deadline Fri Oct 30, 10:00 am PT; judging Dec 1–15.

## What it is

An **always-on studio assistant** for a motion designer, running on their own machine. You message it from your phone (a photo of a sketch, a logo, a link, "make the sunrise logo feel calmer"). It files everything privately, learns your **motion style** from what you accept and reject, and acts on it:

- drafts animations in your style (MotionLang → GIF) and refines them by chat;
- sends a weekly motion/design trend digest (Tavily) filtered by your style, with a draft animation made from one trend;
- (stretch) asks MotionHarness to write a new verb when you ask for a motion MotionLang can't express.

Memory supports the product; *acting in your style* is the hook. It is chat-first with no canvas, so it is a different product from SketchDynamics. The cold-start problem shrinks because the user's SketchDynamics/MotionHarness outputs (`out/`) give it a real history from day one.

Rejected: the original "creative memory / mood board" concept (passive RAG over images, cold start, overlaps the SketchDynamics shape library, generic) and a "reference librarian" variant (simpler but barely uses MotionLang).

## Stack

| Layer | Role |
| --- | --- |
| **Hermes Agent** (Nous Research) | The assistant: chat, conversation memory, self-created skills, cron, Telegram |
| **OpenShell** | Sandbox around Hermes: filesystem/network/process policy; credentials held by OpenShell providers, never in the sandbox; inference routing |
| **NemoClaw** (alpha) | Installs and manages it all (`nemohermes` CLI) |
| **canvasmind** (`apps/canvasmind/`, ours) | Host-side service that owns the studio library and exposes a small MCP server (Streamable HTTP + bearer token) |
| **Token Factory** | All model calls (see below) |
| **Tavily** | Trend digest searches |

Inference: NemoClaw "Other OpenAI-compatible endpoint" (`NEMOCLAW_PROVIDER=custom`) → `https://api.tokenfactory.nebius.com/v1/`, Nemotron 3 Super 120B for Hermes. The API key is entered through NemoClaw's credential helper only.

### MCP reachability (the constraint that shapes the architecture)

NemoClaw only lets a Hermes sandbox reach MCP servers over **HTTPS with a certificate that chains to a trusted root**, with exactly one bearer token per server. It rejects `localhost`, `host.docker.internal`, `host.openshell.internal` and stdio servers. Private addresses need `--trusted-private-host`. Plan: canvasmind binds to loopback, and **Tailscale** (`tailscale serve`) puts it on `<mac>.<tailnet>.ts.net` with a real certificate (a CGNAT 100.x address, so it uses the trusted-private flow). Hermes then sees only canvasmind's tools, never the user's folders or keys.

### Where it runs

- **Development and the video:** the user's MacBook (M5, 48 GB), Docker Desktop.
- **Judging (Dec 1–15):** decide around Nov 25: keep the Mac awake and plugged in, or move to **any cloud Linux VM** for those two weeks. Hackathon credits cover Token Factory only, not VMs, so the provider is picked on price and convenience.
  - Requirements: root, Docker, a long-running process, outbound HTTPS; ideally 8 GB RAM (4 GB minimum) for Docker plus two OpenShell sandboxes.
  - Candidates: a Nebius CPU VM (~$72/month for 4 vCPU / 16 GB, about $36 for two weeks; nicest "all on Nebius" story), DreamHost DreamCompute, or any other VPS. DreamHost shared hosting and managed VPS don't qualify (no Docker or root); at most they could host the static studio page.
  - The demo bot's Telegram handle doesn't change with the host, and the studio page lives on its own host, so the Devpost entry doesn't depend on this.
- Setup is one script that works on macOS and Linux and assumes nothing provider-specific, so moving is "run the script, copy `~/CanvasMind/`, join the tailnet".

## Spike (Oct 21 morning, 4 hours)

Show the user the exact installer command before running it, and wait for their OK.

```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | NEMOCLAW_AGENT=hermes NEMOCLAW_SANDBOX_NAME=canvasmind bash -s -- --defer-onboarding
```

What it installs (read Oct 20): clones `github.com/NVIDIA/NemoClaw` at ref `lkg`, runs its `scripts/install.sh`: `npm install` + TypeScript build, links `nemoclaw`/`nemohermes` into the npm prefix or `~/.local/bin`, downloads the checksum-verified OpenShell gateway (Homebrew or user-local) and registers it as a user service, adds a PATH line to `~/.zshrc`. nvm only if Node < 22.19 (we have 26.5). Onboarding then builds a Docker sandbox image with Hermes. Removal: `nemohermes canvasmind destroy`, `nemohermes uninstall`.

| # | Step | Pass |
| --- | --- | --- |
| 1 | Start Docker Desktop, run the installer | `nemohermes --version` |
| 2 | `nemohermes onboard`, custom provider → Token Factory, Super 120B | Hermes chats **and makes a tool call** (function calling on Token Factory Nemotron is untested so far) |
| 3 | Telegram bot token from BotFather (the user creates it and enters it via the helper) | Message round-trips from the phone |
| 4 | Toy HTTPS MCP server with a `ping` tool on the Mac, via Tailscale | `nemohermes canvasmind mcp status` matches; Hermes calls `ping` |

Outcomes:
- All pass → full stack.
- 1–3 pass, 4 fails → host the canvasmind MCP somewhere with a public HTTPS cert (Nebius Serverless endpoint or any small VM with a domain + Let's Encrypt), with the bearer token.
- 1–2 fail → **fallback**: plain Hermes Agent on the Mac (Nous installer) pointed at Token Factory, canvasmind MCP over stdio. No OpenShell, so privacy rests on canvasmind's folder allowlist and keeping keys out of the agent. Later dates don't move.

Log every friction point in `docs/token-factory-feedback.md` (NemoClaw/OpenShell entries count too).

### Spike results (Oct 21): all four steps pass → full stack

| # | Result |
| --- | --- |
| 1 | NemoClaw v0.0.135 + OpenShell 0.0.116 (Homebrew) installed in 78 s. The installer needs `NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1` even with `--defer-onboarding` when there is no TTY. |
| 2 | Non-interactive onboarding with `NEMOCLAW_PROVIDER=custom`, `NEMOCLAW_ENDPOINT_URL=https://api.tokenfactory.nebius.com/v1`, `NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b`, `COMPATIBLE_API_KEY` → Hermes Agent v0.21.3 in sandbox `canvasmind`. Hermes ran a terminal tool via Nemotron. Its API is `http://127.0.0.1:8642/v1` (bearer from `nemohermes canvasmind gateway-token --quiet`; `nemohermes canvasmind recover` restores the forward). |
| 3 | Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` from `.env`) round-trips from the phone (bot `@kub1ai_bot`). |
| 4 | Toy FastMCP server on `127.0.0.1:8770`, `tailscale serve --bg --https=443 http://127.0.0.1:8770` → `https://liyiwei-m5.taileef945.ts.net/mcp`. `nemohermes canvasmind mcp add canvasmind --url … --env CANVASMIND_MCP_TOKEN --trusted-private-host liyiwei-m5.taileef945.ts.net` verified the credential on the wire; Hermes called `ping` and returned its output. |

Gotchas for the real server:
- The MCP SDK's DNS-rebinding guard rejects the tailnet hostname until it is in `TransportSecuritySettings(allowed_hosts=[…])`.
- Bearer check as a thin ASGI wrapper around `streamable_http_app()`.

Follow-ups:
- **Token cost**: a Hermes turn costs 23k prompt tokens, and 35k once the MCP tool list is loaded (Hermes's own system prompt and toolset). Trim Hermes toolsets we don't need, and keep canvasmind's tool descriptions short.
- **Egress**: onboarding enabled npm, pypi, huggingface and brew presets too; remove them for mitigation 1 (`nemohermes canvasmind policy remove`).
- **Docker** is flagged as under-provisioned at 8 GB; raise it to 16 GB before adding the demo sandbox.
- `--deny-tool` the admin tools (`forget`, `export`) on the public demo sandbox.

## Data, memory and privacy

| Store | Where | Contents |
| --- | --- | --- |
| Studio library | `~/CanvasMind/` on the host | Originals (sketch photos, SVGs, links), each animation's `.motion.json` + GIF, `library.sqlite` (captions, tags, FTS5 index), `style.json` (readable, editable motion style profile) |
| Feedback journal | `~/CanvasMind/journal.jsonl` | Every 👍/👎 with the reason; `style.json` is rebuilt from it, so the profile can explain itself |
| Hermes memory + skills | Inside the OpenShell sandbox | Conversation memory and Hermes's own skills. No files, no keys |

Which model sees what (all on Token Factory):

| Model | Used for | Sees |
| --- | --- | --- |
| Nemotron 3 Super 120B | Hermes chat/planning; MotionLang planning (structured output) | Messages, short tool results (ids, captions, style summary) |
| Nemotron 3.5 Super-VL | Captioning/tagging at filing time; checking rendered GIFs against the style | A downscaled image, once per item / render |
| Nemotron 3 Nano 30B (reasoning off) | Tagging, tweak routing, style-profile updates, recall re-ranking | Text only |
| Tavily (not a model) | Digest searches | Only queries built from the style profile, never files or captions |

Privacy line for the README and video: *files are stored only on your machine; models see short-lived text and downscaled images through Token Factory; nothing goes to any other AI provider.* Telegram is the weakest link (bot chats aren't end-to-end encrypted), so say that plainly and offer the Hermes dashboard over Tailscale as the private channel.

**No embeddings.** The only embedding model on Token Factory is Qwen3-Embedding-8B (not NVIDIA). For tens to hundreds of items, VL captions + SQLite FTS5 + Nano re-ranking the top 20 is enough, adds no dependencies, and keeps every model call on Nemotron.

Mitigations: now: (1) nothing public on the private instance: MCP reachable only on the tailnet, with a bearer token; OpenShell egress limited to Token Factory, Telegram and Tavily; (4) `forget(item)` and `export()`; (5) Hermes dashboard over Tailscale as the private channel. Stretch: (2) originals stay home and the agent's host keeps only a working set (previews, captions, motion files), which matters once anything runs off the Mac; (3) encrypt the library at rest with a key held by OpenShell's provider store.

## canvasmind MCP tools

| Tool | Does |
| --- | --- |
| `file(item, note)` | Store an attachment/URL/path, VL caption + tags, index |
| `recall(query)` | FTS5 search + Nano re-rank; returns ids, captions, thumbnails |
| `animate(brief or item, in_my_style=true)` | `motionlang` generate (style profile in the prompt) → bake → GIF; returns version id + GIF + a one-line "why" citing the profile |
| `tweak(version, request)` | `motionlang` tweak → new version + GIF |
| `feedback(version, verdict, why)` | Append to the journal; Nano updates `style.json` |
| `style_profile()` | The profile and the evidence behind each trait |
| `digest()` | Tavily searches from the profile → Super summary filtered by style, plus one draft animation |
| `forget(item)`, `export()` | Mitigation 4 |
| `new_verb(description)` (stretch) | Calls the MotionHarness MCP over HTTP as a client (no import) |

## Implementation notes (Oct 21)

- **Files between the sandbox and the host.** OpenShell caps one MCP request at 128 KB, and a model can't put image bytes in a tool call. So:
  - *In* (phone → library): Hermes caches Telegram attachments to a local path in the sandbox; the `canvasmind` skill runs `scripts/cm_upload.py <path>` with the Hermes venv's Python (an allowed binary for the MCP route). The script sends the file in 90 KB chunks via the `upload_part` / `upload_finish` tools, which `mcp_servers.canvasmind.tools.exclude` hides from the model.
  - *Out* (library → phone): tools return MCP image content blocks; Hermes caches them and its Telegram adapter sends them natively. GIFs will go the same way.
- **Hermes config** (`/sandbox/.hermes/config.yaml`, Hermes-owned keys, adopted on `mcp restart`; a rebuild may overwrite them, so an install script must reapply): `tools: {resources: false, prompts: false, exclude: [upload_part, upload_finish]}`. With resources on, Hermes listed the (empty) MCP resources and told the user the library was empty instead of calling `recall`.
- **Skill install**: `openshell sandbox upload canvasmind apps/canvasmind/hermes/skills/canvasmind /sandbox/.hermes/skills/canvasmind` (it nests the folder one level; move it up).
- **Recall**: FTS5 hits plus the 10–30 most recent items go to Nano, which scores each 0–3; only scores ≥ 2 are kept. Asking Nano to "list the matching ids" over-included (wind → sunrise, cloud) and missed the turbine.
- **Measured**: VL caption 1.4–2.5 s at ~420–950 prompt tokens per item; Nano recall 1.2–2.5 s at ~800 tokens; a Hermes turn that calls recall costs ~63k prompt tokens (two rounds of ~31k), so Hermes's own prompt dominates cost.

## Hermes skills (seeded; Hermes refines them)

1. **Animate in my style**: recall references → `animate` → send the GIF → ask for a verdict → `feedback`.
2. **Watch my studio**: hourly, file anything new in `~/CanvasMind/inbox` and the SketchDynamics/MotionHarness `out/` folders.
3. **Weekly motion digest**: cron, Monday 9:00.

Channel: Telegram (the only stable NemoClaw channel that strangers can use with zero setup; WhatsApp/WeChat are experimental QR flows, Discord/Slack need joining a server, LINE/Signal aren't supported). Hermes dashboard as the fallback.

## Core library needs (route to the core session, don't edit `packages/motionlang/`)

- **GIF rendering**: done Oct 20 by the core session (uncommitted at the time). Use `from motionlang.render import to_gif, to_frames` with `to_gif(check(doc).baked.to_json(), fps=20, width=480)`, or the CLI `motionlang render FILE --gif out.gif`. The app depends on `motionlang[render]>=0.1` (Pillow extra). Image actors aren't drawn.
- A style hook for `generate`/`tweak` (extra system-prompt guidance) if passing it through the intent text proves too weak.

## Demo video (≤ 3 min, mostly the phone screen)

| Time | Beat | Shows |
| --- | --- | --- |
| 0:00 | "My studio assistant lives on my own machine. Nobody else's." | Hook |
| 0:10 | Photo of a paper doodle → "file this, it's for the café rebrand" → caption + tags | VL, memory |
| 0:30 | "Animate it in my style" → GIF + "slow reveal + ease-out-back, since you liked those in 4 of your last 5" | Style memory acting |
| 0:55 | "Too bouncy" → new GIF → 👍; cut to the `style.json` / journal diff | Learning from feedback |
| 1:15 | "I've saved this as a skill: *animate-for-client*" | Self-created skills |
| 1:30 | "That sunrise logo from last week?" → finds the SketchDynamics output | Recall across apps |
| 1:50 | Monday 9:00 digest arrives unprompted, with one overnight draft | Always-on, proactive |
| 2:20 | Under the hood: OpenShell sandbox, an unlisted host blocked live, model routing | Privacy, technical depth |
| 2:45 | "Run your own, or try ours" | Close |

## Judge access

- **Demo studio**: a public Telegram bot (`@CanvasMindDemoBot`, name TBD) in its own OpenShell sandbox with its own library, seeded with sample sketches and SketchDynamics/MotionHarness outputs so recall and style work on the first message. Per-judge daily animation limit, a "reset" command, nightly wipe. Telegram polling is outbound, so the bot adds no inbound endpoint.
- **Studio page** (read-only): the demo library, style profile and journal, for judges who don't use Telegram. Hosted on Render like SketchDynamics so its URL doesn't depend on where the bot runs.
- **README**: self-hosting with `nemohermes` on any macOS (Apple silicon) or Linux machine, the privacy model, and how Nemotron/Token Factory is used.

## Schedule

| Day | Work |
| --- | --- |
| Mon Oct 20 | This plan; GIF renderer (landed the same day) |
| Tue Oct 21 | Spike (morning). Scaffold `apps/canvasmind`: library store, `file` / `recall` with VL captions |
| Wed Oct 22 | `animate` / `tweak` / `feedback`, style profile, GIFs; MCP wired into Hermes over HTTPS; phone photo → GIF end to end |
| Thu Oct 23 | Skills (animate-in-my-style, watch studio, weekly digest + cron); `forget` / `export`; seed from `out/` |
| Fri Oct 24 | Demo studio sandbox, rate limits, studio page. **Core feature freeze** |
| Sat Oct 25 | Buffer; stretch (`new_verb`, mitigations 2 and 3) |
| Sun Oct 26 | README, Devpost draft (`docs/canvasmind-submission.md`), feedback entries, video dry run |
| Mon Oct 27 | Record and edit the video |
| Tue Oct 28 | Submit; tag `canvasmind-submitted` |
| Wed Oct 29 | Buffer for any project |
| Thu Oct 30, 10:00 PT | Deadline |
| ~Nov 25 | Decide the judging host (Mac vs a cloud VM for Dec 1–15) |

Cut lines, in order: drop `new_verb` and mitigations 2/3 → drop the studio page → digest becomes a manual `/digest` with no cron. Never cut the video.

## Repo rules that apply

`apps/canvasmind/` depends on `motionlang[render]>=0.1` by name, never imports another app, and has its own README and `.env.example` (`NEBIUS_API_KEY`, `TAVILY_API_KEY`, `CANVASMIND_MCP_TOKEN`, `CANVASMIND_HOME`). Secrets stay in `.env` and OpenShell's provider store. All model calls go to Nemotron on Token Factory. Apache-2.0.

### Hermes wiring fixes (Oct 21, after the first phone test)

The first phone test failed twice; `uv run canvasmind hermes-setup` now applies all of these (rerun it after `nemohermes canvasmind rebuild`):
- **Photo → 400**: Hermes attached the photo as image input to Nemotron 3 Super (text-only). OpenShell's inference route rewrites *every* request to the configured model, so the sandbox can't reach Super-VL that way either. Fix: `agent.image_input_mode: text`. The agent gets the cached path, uploads it, and canvasmind's VL caption (on the host) is its description of the image. The `vision` toolset is dropped.
- **"show me the wind stuff" → web search**: tool search deferred the MCP tools, and the generic Hermes persona doesn't know the library exists. Fixes: `tools.tool_search.enabled: false`; a smaller Telegram/API toolset (web, terminal, file, skills, memory, session_search, cronjob, todo, nemoclaw); a CanvasMind `SOUL.md`.
- **SOUL.md location**: the gateway resolves the agent home from its session DB, and NemoClaw symlinks `state.db` into `~/.hermes/runtime/`, so SOUL.md must also be in `runtime/`.
- **`mcp restart` regenerates `mcp_servers.<name>`**, so per-server `tools` filters don't stick. canvasmind now hides its upload tools and advertises no resources/prompts server-side.
- **Cost**: with the smaller toolset a recall turn costs ~23k prompt tokens (was 63–74k).

### Step 3 (Oct 21): animation, tweaks, style

Decided with the user: **only SVG artwork is animated**. A photo can't be split into parts that move, and redrawing photos as vectors was poor: Super from a caption drew wrong parts in the wrong order, and Super-VL was crude. Photos and links stay references; asking to animate one gets "send the SVG".

- `animate(item_id, brief)`: import the SVG (720×720 canvas) → Super-VL names the parts from a numbered render and proposes an idea for an empty brief (~2 s) → Super (structured output) plans with the style summary appended (~25–35 s, at most one repair to stay inside Hermes's 120 s tool timeout) → GIF (480 px, 20 fps, ~200–450 KB) → a new `motion` item linked to its SVG.
- `tweak(item_id, request)`: pure speed requests ("slower", "a bit faster") rescale every duration, delay and stagger offset with no model (~1 s). Other requests go to motionlang's tweak (Nano edit ops, then a Super rewrite if needed; 25–40 s, since Nano's first edit often fails).
- `feedback` → `journal.jsonl`; `style.json` counts liked/disliked verbs, easings and durations, and Nano keeps ≤ 8 notes from the user's reasons. The planner gets a one-paragraph summary; replies cite the traits used.
- Telegram delivery needed a Hermes user plugin (`canvasmind_telegram`, installed by `hermes-setup`): it keeps `.svg` documents (Hermes drops them) and sends local GIFs with `send_animation` (Hermes sent them as photos, i.e. the first frame). Hermes loads the adapter module under two names, so the patch covers both classes.

### Step 4 (Oct 21): digest, studio watcher, your data, demo fixtures

- `digest(draft)`: Nano writes 3 search queries from the style profile → Tavily (`time_range: month`, ≤ 5 results each; Tavily sees only those queries) → Super picks 3 finds with a "why it fits your style" line and cites only URLs Tavily returned → optionally animates one library SVG after a find. Saved to `~/CanvasMind/digests/<date>.md`. Measured: queries 1.3 s, digest 10 s; the draft adds ~35–70 s and failed once on a planner slip (`"verb": "par"`; motionlang now rewrites such blocks before validation, Oct 21), in which case the digest goes out without it.
- Hermes cron jobs, (re)created by `canvasmind hermes-setup`, delivered to `telegram:<first TELEGRAM_ALLOWED_IDS>`:
  - `canvasmind-studio-scan`, every hour, `--no-agent --script cm_scan.py`: calls `scan_studio` over MCP; prints only when something new was filed, so it costs no tokens and is silent otherwise. Watches `~/CanvasMind/inbox` always, plus `CANVASMIND_WATCH` (colon-separated; a trailing `/**` scans recursively).
  - `canvasmind-digest`, Monday 09:00 local (Hermes schedules in UTC; the setup converts): an agent turn that calls `digest` and forwards the text and GIF.
- `forget(item_id)` deletes an item's row, search entry, files and feedback (the style is recounted); `export()` zips everything to `~/CanvasMind/exports/`.
- `canvasmind demo-reset --yes`: moves the library to `~/CanvasMind.bak-<time>` (never deleted) and files `apps/canvasmind/demo/` per `manifest.json`: our sunrise logo, three sunrise animations with 2 👍 / 1 👎 and reasons (backdated a week), and the "Morning Cup" café logo SVG drawn for the demo. Style notes from Nano now stick to the user's words (no invented "short duration").

### Public demo bot (Oct 22): https://t.me/CanvasMindBot

- A second NemoClaw sandbox, `canvasmind-demo`, for @CanvasMindBot, with Hermes open to every Telegram user (`TELEGRAM_ALLOW_ALL_USERS=true`, set by `canvasmind hermes-setup --sandbox canvasmind-demo --demo`). Your private bot and library are unchanged.
- A second library server: `canvasmind serve --port 8771 --per-user` with `CANVASMIND_HOME=~/CanvasMindDemo` and its own token (`CANVASMIND_DEMO_MCP_TOKEN`), reached at `https://<mac>.<tailnet>.ts.net:8443/mcp` (`tailscale serve --bg --https=8443 http://127.0.0.1:8771`); registered with `--deny-tool forget --deny-tool export --deny-tool scan_studio`.
- Per-user libraries: the Hermes plugin adds the caller's Telegram user id as a reserved `_cm_user` argument to every canvasmind call (and `--user` to the upload script); the server strips it and runs the call against `~/CanvasMindDemo/users/<id>/`, copied from a template library (the demo fixtures, captioned once a day) on first use and reset after a day. So each judge's 👍/👎 shapes their own style profile, and nobody sees another's uploads.
- Limits per user per UTC day, enforced in the plugin's `pre_tool_call`: animate 8, tweak 15, digest 2, uploads 15, links 10, recall 40, feedback 40; over the limit the call is blocked with a friendly message pointing to the self-host README. No payments (Telegram Stars is possible later).
- Prompt-only animation (`animate` with no item): the planner draws basic shapes and text itself.
- The persona adds a `/start` welcome with things to try.
- Hosting during judging (Dec 1–15): decide by ~Nov 25 (Mac kept awake, or a cloud VM running the same setup).
- Oct 22: planning from words alone dropped objects ("a blue square … and a red ball" → ball only). After planning, Nano checks that every object the request names is drawn; if not, and there's time, the planner gets one more pass naming them. All planner calls are capped at 6000 tokens (motionlang's new `max_tokens`), because under structured output Super sometimes writes a number that never ends until the cap; motionlang then finishes in JSON mode. Measured: 4/4 valid in 29–48 s. Model calls time out at 60 s, and `animate`/`tweak` answer within 100 s (Hermes's tool timeout is 120 s). Telegram's `/start` is swallowed by Hermes, so the greeting lives in the bot's BotFather description.
