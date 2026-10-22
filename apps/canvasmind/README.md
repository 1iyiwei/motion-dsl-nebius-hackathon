# CanvasMind

License: Apache-2.0 · Nebius x NVIDIA Global AI Hackathon, **Personal AI** track

<a href="https://youtu.be/z8SF8A0VLks"><img src="https://img.youtube.com/vi/z8SF8A0VLks/mqdefault.jpg" width="320" alt="CanvasMind demo video"></a>

**Try it:** [t.me/CanvasMindBot](https://t.me/CanvasMindBot) (a public demo: your own library, reset daily, with daily limits).

A private, always-on studio assistant for motion designers. You message it on Telegram like a studio assistant: send a logo, a reference photo or a link, say "animate the café logo in my style", "slower", "👍 love the calm pace", "that sunrise logo from last week?". Everything you send is filed in a library **on your own machine** and described by NVIDIA Nemotron VL so you can find it by meaning. Every 👍/👎 updates a readable style profile, and every new animation follows it. Once a week it sends a short motion-design digest, picked from the web for your style, with one of your logos animated after a trend.

The agent is [Hermes Agent](https://github.com/NousResearch/hermes-agent) running inside an NVIDIA **OpenShell** sandbox, installed and managed by NVIDIA **NemoClaw**. It can't read your disk or hold your API keys: it reaches your library only through a small MCP server on your machine, over your private Tailscale network. Every model call runs on NVIDIA Nemotron via **Nebius Token Factory**. Animations are written in **MotionLang** ([`motionlang`](../../packages/motionlang)), a declarative motion language that a validator checks before anything reaches the chat.

```text
 phone ──Telegram──► Hermes Agent (NemoClaw / OpenShell sandbox: no keys, no host files, allow-listed egress)
                        │  Nemotron 3 Super via OpenShell's inference route (key held by OpenShell)
                        │  MCP over HTTPS on your tailnet, bearer token added by OpenShell at egress
                        ▼
                  canvasmind (this app, on your machine) ── ~/CanvasMind/  library.sqlite · items/ · style.json · journal.jsonl
                        │  Nemotron VL: captions, part names   Nemotron Nano: recall, style notes, edits
                        │  Nemotron Super: MotionLang plans    Tavily: digest searches (queries only)
                        ▼
                  GIF ──► back through Hermes ──► Telegram
```

## What it does

| You | CanvasMind |
| --- | --- |
| Send a photo, sticker, `.svg`, `.motion.json`, GIF or link | Files it under `~/CanvasMind/items/<id>/` with a VL caption, tags and palette; recognises duplicates |
| "something with wind", "everything for the café rebrand", "what I filed last week" | Full-text search, then Nano scores each candidate; thumbnails come back in the chat |
| "animate the café logo in my style" (an SVG) or "a red ball drops in and bounces" (words alone) | VL names the logo's parts; Super plans a MotionLang animation with your style profile; the validator checks and repairs it; a GIF comes back and is filed as a new version |
| "slower", "less bounce" | "slower/faster" retimes instantly with no model; other changes go through Nano edit ops (or a Super rewrite) |
| 👍/👎 with a reason | Recorded in `journal.jsonl`; `style.json` counts the verbs, easings and lengths you like and dislike, and Nano keeps short notes in your words |
| "what's my style?" | The profile and the evidence behind it |
| "what's new in motion design?" (and every Monday 09:00) | Nano writes queries from your style → Tavily → Super picks 3 finds with sources → one of your SVGs animated after a find |
| Drop files into `~/CanvasMind/inbox` (or watched folders) | An hourly scan files them; it runs no model when nothing is new and only messages you when it filed something |
| "forget that", "export everything" | Deletes an item, its files and its feedback; or zips everything to `~/CanvasMind/exports/` |

Only **SVG artwork** (or words alone) can be animated: an SVG splits into parts that move on their own, a photo doesn't. Photos are kept as references.

## Privacy model

- **Files stay on your machine.** The library lives in `~/CanvasMind/`. The agent's sandbox never sees it; it can only call the canvasmind tools.
- **Models see short-lived data through Token Factory only**: chat text and short tool results (ids, captions) for the agent; a downscaled image once per filed item for Nemotron VL; text for Nano. No other AI provider is involved.
- **Tavily sees only search queries** built from your style profile, never files or captions.
- **Keys**: the Token Factory key and the MCP bearer token are held by OpenShell and added at egress; the sandbox holds placeholders.
- **The weak link is the channel**: Telegram bot chats are not end-to-end encrypted, so Telegram's servers see what you send. Hermes's own dashboard over Tailscale is the private alternative.

## Set it up (your own, private instance)

Tested on macOS (Apple silicon, 48 GB) with Docker Desktop; NemoClaw also supports Linux, so the same steps work on a cloud VM. You need: [uv](https://docs.astral.sh/uv/), Docker, Node.js ≥ 22.19, a [Tailscale](https://tailscale.com) account, a Telegram account, a [Nebius Token Factory](https://tokenfactory.nebius.com) API key, and optionally a [Tavily](https://tavily.com) key for the digest.

### 1. The repo and `.env`

```bash
git clone https://github.com/1iyiwei/motion-dsl-nebius-hackathon && cd motion-dsl-nebius-hackathon
uv sync
cp apps/canvasmind/.env.example .env      # then fill it in; never commit it
```

Fill in `NEBIUS_API_KEY`, `TAVILY_API_KEY` (optional) and a random `CANVASMIND_MCP_TOKEN`:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

### 2. A Telegram bot

1. Message [@BotFather](https://t.me/BotFather): `/newbot`, pick a name and a username. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Message [@userinfobot](https://t.me/userinfobot) to get your numeric Telegram user id, and put it in `.env` as `TELEGRAM_ALLOWED_IDS` so that only you can use the bot.
3. Optional: `/setdescription` and `/setabouttext` in BotFather. Hermes ignores Telegram's `/start`, so the description (shown before someone presses Start) is where a greeting goes.

### 3. NemoClaw + Hermes (NVIDIA's installer, with OpenShell)

Start Docker, then install NemoClaw without onboarding. It clones `NVIDIA/NemoClaw`, builds the `nemoclaw`/`nemohermes` CLIs, installs the OpenShell gateway (Homebrew on macOS) and adds a PATH line to your shell profile. `NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1` accepts NemoClaw's third-party software terms; read them first.

```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | NEMOCLAW_AGENT=hermes NEMOCLAW_SANDBOX_NAME=canvasmind \
  NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_DISABLE_TELEMETRY=1 bash -s -- --defer-onboarding
```

Then onboard Hermes into a sandbox named `canvasmind`, with inference on Token Factory through NemoClaw's "Other OpenAI-compatible endpoint" and Telegram enabled. The keys are read from `.env` in a subshell and never printed:

```bash
(set -a; . ./.env; set +a
 NEMOCLAW_AGENT=hermes NEMOCLAW_SANDBOX_NAME=canvasmind NEMOCLAW_NON_INTERACTIVE=1 \
 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_DISABLE_TELEMETRY=1 \
 NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=https://api.tokenfactory.nebius.com/v1 \
 NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b COMPATIBLE_API_KEY="$NEBIUS_API_KEY" \
 nemohermes onboard --non-interactive)
nemohermes canvasmind status          # Phase: Ready, inference healthy
```

Message your bot now: Hermes should answer (it doesn't know about your library yet).

### 4. Tailscale (how the sandbox reaches your machine)

OpenShell only lets a sandbox reach MCP servers over HTTPS with a publicly trusted certificate, and it refuses `localhost`, `host.docker.internal` and stdio servers. `tailscale serve` gives your machine an HTTPS name on your private tailnet with a real certificate.

1. Install Tailscale (`brew install --cask tailscale` on macOS), sign in, and in the [admin console](https://login.tailscale.com/admin/dns) turn on **MagicDNS** and **HTTPS Certificates**.
2. Find your machine's name with `tailscale status --json | grep DNSName` (e.g. `my-mac.tail1234.ts.net`) and put it in `.env` as `CANVASMIND_PUBLIC_HOST`.
3. Proxy it to canvasmind's port:

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8770
   ```

### 5. Start canvasmind and connect it

```bash
uv run canvasmind serve                 # MCP on http://127.0.0.1:8770/mcp; library in ~/CanvasMind (CANVASMIND_HOME)
```

In another terminal, register it with the sandbox (OpenShell stores the token as a provider; the sandbox only sees a placeholder), then install the CanvasMind skill, persona, Hermes plugin, settings and cron jobs:

```bash
(set -a; . ./.env; set +a
 nemohermes canvasmind mcp add canvasmind --url "https://$CANVASMIND_PUBLIC_HOST/mcp" \
   --env CANVASMIND_MCP_TOKEN --trusted-private-host "$CANVASMIND_PUBLIC_HOST")
uv run canvasmind hermes-setup          # rerun after `nemohermes canvasmind rebuild`
```

`hermes-setup` is idempotent. It installs:

- the `canvasmind` skill and the upload script into `~/.hermes/skills/canvasmind/` (in the sandbox);
- a CanvasMind persona (`SOUL.md`, also copied to `~/.hermes/runtime/`, where the gateway reads it);
- the `canvasmind_telegram` Hermes plugin (see [Notes on Hermes and NemoClaw](#notes-on-hermes-and-nemoclaw));
- Hermes settings: `agent.image_input_mode: text`, tool search off, a smaller Telegram toolset;
- two cron jobs, delivered to your Telegram chat: `canvasmind-studio-scan` (hourly, a script with no model call, silent when nothing is new) and `canvasmind-digest` (Monday 09:00 local).

### 6. Try it

Send `/new`, then a photo or an `.svg` with "file this for the café rebrand"; "animate it in my style"; "slower"; "👍 calm is nice"; "what's my style?"; "what's new in motion design?". To start with a seeded library (the café and sunrise logos, three rated animations), run:

```bash
uv run canvasmind demo-reset --yes      # moves ~/CanvasMind to ~/CanvasMind.bak-<time> first; never deletes
```

To keep it always on, keep the machine awake and `canvasmind serve` running (a `launchd`/`systemd` service, or a cloud VM with the same steps).

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| The bot doesn't answer | `nemohermes canvasmind status`; `nemohermes canvasmind recover`; `nemohermes canvasmind channels status --channel telegram` |
| "nothing in your library" / it searches the web instead | `uv run canvasmind hermes-setup` again (a rebuild resets the persona and settings), then `/new` in the chat |
| New tools don't show up after an upgrade | `nemohermes canvasmind mcp restart canvasmind` |
| Tool calls fail with 401 | The token in `.env` and the one registered with `mcp add` differ: `nemohermes canvasmind mcp remove canvasmind`, then add it again |
| "Invalid Host header" from the MCP server | `CANVASMIND_PUBLIC_HOST` must match the Tailscale name (with `:port` if not 443) |
| GIFs arrive as still images or files | The `canvasmind_telegram` plugin isn't loaded: `uv run canvasmind hermes-setup` |
| Logs | `nemohermes canvasmind exec -- tail -50 /sandbox/.hermes/logs/agent.log` (or `gateway.log`); canvasmind's model calls are in `~/CanvasMind/calls.jsonl` |

## Run a public demo bot

The public demo ([@CanvasMindBot](https://t.me/CanvasMindBot)) is a second sandbox and a second library server next to your private one:

```bash
# a second bot from BotFather → .env: CANVASMIND_TELEGRAM_BOT_TOKEN; and a second random CANVASMIND_DEMO_MCP_TOKEN
(set -a; . ./.env; set +a
 NEMOCLAW_AGENT=hermes NEMOCLAW_SANDBOX_NAME=canvasmind-demo NEMOCLAW_NON_INTERACTIVE=1 \
 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 NEMOCLAW_DISABLE_TELEMETRY=1 \
 NEMOCLAW_PROVIDER=custom NEMOCLAW_ENDPOINT_URL=https://api.tokenfactory.nebius.com/v1 \
 NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b COMPATIBLE_API_KEY="$NEBIUS_API_KEY" \
 TELEGRAM_BOT_TOKEN="$CANVASMIND_TELEGRAM_BOT_TOKEN" TELEGRAM_ALLOWED_IDS="$TELEGRAM_ALLOWED_IDS" \
 nemohermes onboard --non-interactive)

tailscale serve --bg --https=8443 http://127.0.0.1:8771
(set -a; . ./.env; set +a
 CANVASMIND_HOME=$HOME/CanvasMindDemo CANVASMIND_MCP_TOKEN="$CANVASMIND_DEMO_MCP_TOKEN" \
 CANVASMIND_PUBLIC_HOST="$CANVASMIND_PUBLIC_HOST:8443" CANVASMIND_WATCH= \
 uv run canvasmind serve --port 8771 --per-user)

# in another terminal
(set -a; . ./.env; set +a
 nemohermes canvasmind-demo mcp add canvasmind --url "https://$CANVASMIND_PUBLIC_HOST:8443/mcp" \
   --env CANVASMIND_DEMO_MCP_TOKEN --trusted-private-host "$CANVASMIND_PUBLIC_HOST" \
   --deny-tool forget --deny-tool export --deny-tool scan_studio)
uv run canvasmind hermes-setup --sandbox canvasmind-demo --demo
```

What `--per-user` and `--demo` change:

- **Open to everyone**: `TELEGRAM_ALLOW_ALL_USERS=true` in the sandbox's Hermes environment.
- **One library per Telegram user**: the plugin adds the caller's user id to every canvasmind call as a reserved `_cm_user` argument (and `--user` to the upload script). The server strips it and runs the call against `~/CanvasMindDemo/users/<id>/`, copied on first use from a template library (the fixtures in `demo/`, captioned once a day) and reset after a day. Each user's 👍/👎 shapes their own style; nobody sees another's files.
- **Daily limits per user** (UTC): animate 8, tweak 15, digest 2, uploads 15, links 10, recall 40, feedback 40. Over a limit, the call is blocked with a message pointing here. Edit `DEMO` in `hermes_setup.py`.
- **No host access**: `forget`, `export` and `scan_studio` are blocked by the plugin and denied at OpenShell's MCP proxy. No cron jobs.
- **A welcome**: the persona answers "what can you do?" with things to try (set the same text as the bot's BotFather description).

A public bot spends your Token Factory credits and stores strangers' uploads on the host for a day. See the hardening list in the development repo's plan before advertising one widely.

## CLI

```bash
uv run canvasmind serve [--port 8770] [--per-user]   # the MCP server (bearer token required)
uv run canvasmind file PATH_OR_URL... [--note "…"]   # file things from the terminal
uv run canvasmind recall "the sunrise logo from last week"
uv run canvasmind scan                               # file new work from ~/CanvasMind/inbox and CANVASMIND_WATCH
uv run canvasmind status
uv run canvasmind demo-reset --yes                   # seeded demo library (moves the current one aside)
uv run canvasmind hermes-setup [--sandbox NAME] [--demo]
```

Configuration (`.env`): `CANVASMIND_HOME` (default `~/CanvasMind`), `CANVASMIND_PORT` (8770), `CANVASMIND_WATCH` (colon-separated folders; a trailing `/**` scans recursively), `CANVASMIND_PLANNER_MODEL` (default Super 120B), and `MOTION_VISION_MODEL` / `MOTION_EDITOR_MODEL` to swap the VL and Nano models.

## How Nemotron / Token Factory is used

| Job | Model (Token Factory) | Notes |
| --- | --- | --- |
| The agent (chat, tool calls, cron turns) | `nvidia/nemotron-3-super-120b-a12b` | Through NemoClaw's custom endpoint; tool calling worked out of the box. A turn costs ~25–35k prompt tokens, mostly Hermes's own prompt |
| Caption and tag filed items | `nvidia/Nemotron-3.5-Super-VL`, reasoning off, JSON mode | ~2 s, ~0.4–1k prompt tokens per item; SVGs and animations are rendered to a still or a 4-frame strip first |
| Name a logo's parts, propose an animation | `nvidia/Nemotron-3.5-Super-VL` | From a clean and a numbered render (~2 s) |
| Plan an animation | `nvidia/nemotron-3-super-120b-a12b`, structured output, capped at 6000 tokens | Via `motionlang.agent.generate`: validator errors go back for repair (≤ 2 rounds); 20–50 s |
| Check nothing was dropped (words-only animations) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | Objects the request names vs. the plan's actors; one more plan if one is missing (~1 s) |
| Recall, style notes, digest queries, small edits | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, reasoning off | 1–2 s each |
| Write the digest | `nvidia/nemotron-3-super-120b-a12b`, reasoning off | Picks 3 Tavily finds, cites only returned URLs (~5–10 s) |

All canvasmind calls go through `motionlang.agent`'s OpenAI-compatible client with a 60 s timeout per call, and `animate`/`tweak` answer within 100 s (Hermes gives a tool 120 s). Every call is logged with model, latency and tokens in `calls.jsonl`.

## Notes on Hermes and NemoClaw

Things we had to learn or work around (all applied by `hermes-setup`):

- **Getting files across the sandbox boundary.** OpenShell caps an MCP request at 128 KB, and a model can't put image bytes in a tool call. The skill's `cm_upload.py` (run with the Hermes venv's Python, an allowed binary for the MCP route) sends attachments in 90 KB chunks through hidden `upload_part`/`upload_finish` tools. GIFs come back as MCP image content, which Hermes forwards to Telegram.
- **One model per sandbox.** OpenShell routes every inference call from the sandbox to the model chosen at onboarding, so the agent can't use VL itself. With `agent.image_input_mode: text` it gets the photo's path instead, and canvasmind's VL caption is its description.
- **Tool search hid our tools** (the model searched the web for "current wind speed"), and MCP resources made it report an empty library: tool search is off, and canvasmind advertises no resources or prompts.
- **The persona file**: the gateway resolves the agent home from its session DB, which NemoClaw keeps in `~/.hermes/runtime/`, so `SOUL.md` is copied there too.
- **`mcp restart` regenerates the server's config entry**, so per-server settings don't stick; canvasmind hides its upload tools itself.
- **Telegram adapter fixes** (the `canvasmind_telegram` plugin): `.svg` documents were dropped as "non-image data" (we keep them as documents), and local GIFs were sent as photos (we send them with `send_animation`). Hermes imports the adapter under two module names, so the plugin patches both classes. The same plugin injects the user id and enforces the demo limits.
- **`/start` is swallowed** by Hermes ("acknowledge platform start pings without a reply"): put the greeting in the BotFather description.

## Tests

```bash
uv run pytest apps/canvasmind -q
```

34 tests run against a scripted fake model (no tokens spent): filing (images, SVGs, MotionLang files, links, stickers without a path), duplicates, recall, chunked upload, the MCP server (auth, hidden tools, GIF content, per-user libraries, the deadline), animation from SVGs and from words (with the missing-object replan), tweaks (including the model-free retime), the style profile, forget/export, the studio scan and the demo fixtures.

## Layout

```text
src/canvasmind/
  cli.py            serve · file · recall · scan · status · demo-reset · hermes-setup
  mcp_server.py     the MCP tools, bearer auth, host guard, hidden tools, 100 s deadline
  studio.py         filing (bytes, paths, links, chunked uploads), recall, scan, forget/export
  library.py        files on disk + SQLite/FTS5; reopens if the database is replaced underneath
  media.py          previews for images, SVGs (via motionlang) and animations; motion facts
  models.py         VL captions, Nano recall scoring, missing-object check
  animation.py      animate (SVG or words), tweak (retime or Nano/Super), GIFs via motionlang.render
  style.py          journal → style.json, the planner's style summary, the "style used" line
  digest.py         Tavily search → Super digest → draft animation
  tenants.py        per-user demo libraries (the `_cm_user` argument)
  demo.py           demo-reset from demo/manifest.json
  hermes_setup.py   installs everything below into a NemoClaw Hermes sandbox
  skill/            SKILL.md, SOUL.md (+ SOUL_demo.md), scripts/cm_upload.py, scripts/cm_scan.py
  hermes_plugin/canvasmind_telegram/   the Hermes plugin
demo/               cafe-logo.svg (drawn for this project, CC0), sunrise.svg, three rated sunrise animations, manifest.json
```

## Feedback on Token Factory and the NVIDIA stack

- Function calling worked on Nemotron Super, Nano and Ultra out of the box, plain and streamed, which is what an agent framework needs.
- NemoClaw + Token Factory works through the custom endpoint; a Token Factory preset with model ids in NemoClaw's onboarding would save a search through the NemoClaw repo docs.
- Structured output can run away on numbers: Super sometimes writes `360.0000000000000600830…` until `max_tokens`. Capping the planner at 6000 tokens and finishing in JSON mode fixed it for us; a digit cap in the grammar would fix it at the source.
- An agent's system prompt is resent on every turn (20–35k tokens with Hermes): prompt caching would cut most of that cost.
- OpenShell's inference route serves one model per sandbox; allowing a short list (Super for the agent, VL for images, Nano for quick steps) would let sandboxed agents route by task.
