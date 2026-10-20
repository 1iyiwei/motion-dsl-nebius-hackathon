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
- **Judging (Dec 1–15):** decide around Nov 25: keep the Mac awake and plugged in, or move only the demo sandbox to a Nebius 4 vCPU / 16 GB CPU VM for those two weeks (~$36; hackathon credits cover Token Factory only, not VMs). The demo bot's Telegram handle doesn't change with the host, so the Devpost entry doesn't depend on this.
- Setup is one script that works on macOS and Linux, so moving to a VM is "run the script, copy `~/CanvasMind/`".

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
- 1–3 pass, 4 fails → host the canvasmind MCP somewhere with a public HTTPS cert (Nebius Serverless endpoint or a small VM), with the bearer token.
- 1–2 fail → **fallback**: plain Hermes Agent on the Mac (Nous installer) pointed at Token Factory, canvasmind MCP over stdio. No OpenShell, so privacy rests on canvasmind's folder allowlist and keeping keys out of the agent. Later dates don't move.

Log every friction point in `docs/token-factory-feedback.md` (NemoClaw/OpenShell entries count too).

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
| ~Nov 25 | Decide the judging host (Mac vs a VM for Dec 1–15) |

Cut lines, in order: drop `new_verb` and mitigations 2/3 → drop the studio page → digest becomes a manual `/digest` with no cron. Never cut the video.

## Repo rules that apply

`apps/canvasmind/` depends on `motionlang[render]>=0.1` by name, never imports another app, and has its own README and `.env.example` (`NEBIUS_API_KEY`, `TAVILY_API_KEY`, `CANVASMIND_MCP_TOKEN`, `CANVASMIND_HOME`). Secrets stay in `.env` and OpenShell's provider store. All model calls go to Nemotron on Token Factory. Apache-2.0.
