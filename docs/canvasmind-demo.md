# CanvasMind: 3-minute demo plan

The video is mostly **the phone**: a screen recording of the Telegram chat with the bot (iOS: Control Center → Screen Recording; turn on Do Not Disturb first). Cut to the **Mac** only for the library and the "under the hood" beat. Cut the model waits in editing. Telegram's own timestamps and `~/CanvasMind/calls.jsonl` show the real latency, so speeding up is honest.

Plan and architecture: [canvasmind-plan.md](canvasmind-plan.md). Status of each beat: ✅ works today, 🔨 planned (see the plan's schedule).

## Before each take

1. On the Mac: Docker Desktop running; `nemohermes canvasmind status` shows the sandbox Ready; Tailscale is connected.
2. Start the library server: `uv run canvasmind serve`. `tailscale serve` keeps proxying `https://<mac>.<tailnet>.ts.net` to `127.0.0.1:8770`; check with `tailscale serve status`.
3. After any NemoClaw rebuild or upgrade: `uv run canvasmind hermes-setup` (skill, persona, Hermes settings).
4. In the Telegram chat: send `/new`, so earlier test turns don't steer the agent.
5. Library state for the take: `uv run canvasmind demo-reset --yes` (moves the real library to `~/CanvasMind.bak-<time>`), then restart `canvasmind serve`. It files `apps/canvasmind/demo/` per its manifest: the sunrise logo and three sunrise animations rated a week ago (2 👍 calm/hand-made, 1 👎 bouncy), and the café logo filed yesterday. The style line then reads "liked enter, draw_on, inOutSine … avoids bouncy, prefers calm".
6. Props: `apps/canvasmind/demo/cafe-logo.svg` ("Morning Cup") ready to send from Telegram Desktop or the phone's Files app, one café reference photo, and a link to a reference page.

## Measured so far (Oct 21)

| Step | Model | Result |
| --- | --- | --- |
| Telegram round trip, plain chat | Hermes on Super 120B | reply in ~7 s |
| File a photo from the chat | Hermes runs the upload script → canvasmind → Super-VL caption | caption in 1.4–2.5 s (~420–950 prompt tokens); whole turn ~33k prompt tokens |
| Recall ("show me the wind stuff") | Hermes → `recall` → Nano scores candidates 0–3 | Nano 1.2–2.5 s (one 20 s outlier); turn ~23k prompt tokens; thumbnails arrive as a Telegram media group |
| File a link | `file_link` → fetch → VL caption of the page image | works (a Wikimedia portrait) |
| Sticker | sticker arrives without a path → upload script `--latest` | works |
| Animate an SVG (sent as a file, or from the library) | VL labels parts (~2 s) → Super plans (25–35 s, ≤1 repair) → GIF | 31–40 s per GIF; plays inline in Telegram |
| "slower" / "faster" | no model: timeline rescaled | ~1 s |
| Other tweaks ("less bounce") | Nano edit ops → Super rewrite if needed | 25–40 s |
| 👍/👎 with a reason | journal + counts; Nano updates style notes | ~1 s |

Hermes's own system prompt and tool schemas dominate the cost of every turn (23–35k prompt tokens); the canvasmind calls are ~1k each.

## Shot list

| Time | Beat | On screen | Status | Voice-over idea |
| --- | --- | --- | --- | --- |
| 0:00–0:10 | Hook | Phone: a GIF of the café logo animating arrives in the chat | ✅ (needs the café logo as an SVG) | "My studio assistant lives on my own machine. Nobody else's." |
| 0:10–0:30 | File | Send the café logo **SVG** (drawn in SketchDynamics or Illustrator) with "file this, it's for the café rebrand", plus a reference photo → `💻 terminal …cm_upload.py` → "Filed as …" | ✅ | "Anything I send goes into my private library. Nemotron VL describes it, on my machine's side, so the agent never needs to see my files." |
| 0:30–0:55 | Animate in my style | "animate it in my style" → GIF + "slow reveal + ease-out-back, since you liked those in 4 of your last 5" | ✅ (seed a few 👍/👎 first so the style line appears) | "It doesn't just store things. It acts on them, in my style." |
| 0:55–1:15 | Feedback | "slower" → new GIF in ~1 s → 👍. Cut to the Mac: the `style.json` / journal diff | ✅ (phone 👍 still to be checked) | "Every 👍 and 👎 updates a style profile I can read and edit." |
| 1:15–1:30 | Skill | "I've saved this as a skill: *animate-for-client*" | 🔨 | "Hermes turns what works into reusable skills." |
| 1:30–1:50 | Recall | "that sunrise logo from last week?" → thumbnail of the SketchDynamics output | ✅ (demo-reset backdates it) | "It remembers across my tools: this came from SketchDynamics." |
| 1:50–2:20 | Proactive | Monday 9:00: the digest arrives unprompted (Tavily trends filtered by style + one draft animation) | ✅ (cron job `canvasmind-digest`; for the take run it now: `hermes cron run <id>` in the sandbox, or ask "send me this week's digest") | "Always on: it works while I don't." |
| 2:20–2:45 | Under the hood | Mac: architecture card (phone → Telegram → Hermes in the OpenShell sandbox → MCP over Tailscale → canvasmind + library), the sandbox policy, and **an unlisted host blocked live**; `calls.jsonl` with model, latency and tokens | 🔨 (find the cleanest way to show a live block: OpenShell's TUI or log) | "The agent runs in an NVIDIA OpenShell sandbox: it can't read my disk, can't hold my keys, and can only reach the hosts I allow. All models are NVIDIA Nemotron on Nebius Token Factory." |
| 2:45–3:00 | Close | Phone: "try the demo bot" QR / handle; README self-host one-liner; title card | 🔨 | "Run your own, or try ours." |

## Judge access (for the description, not the video)

A separate demo bot in its own sandbox, with a seeded library, per-user limits and a nightly wipe, plus a read-only studio page (see the plan). 🔨

## Known gaps (prioritised for the video)

1. **Only SVGs animate.** The opening prop must be an SVG of the café logo (not a paper doodle); a photo can be filed as a reference.
1b. **Non-speed tweaks take 25–40 s** (Nano's first edit often fails). Use "slower"/"faster" on camera, or cut the wait.
2. **Token cost per turn** (23–35k prompt tokens of Hermes overhead): trim further if the demo bot's budget needs it.
3. **The model sometimes reaches for the wrong tool** (web search, local file search, "send me a link"). The CanvasMind persona (`SOUL.md`, installed by `canvasmind hermes-setup`) fixed every case so far. Start each take with `/new`.
4. **Images can't go to the main model**: Super 120B is text-only and OpenShell routes every call from the sandbox to it, so the agent "sees" images only through canvasmind's VL captions. That's a privacy point worth saying in the video, not a gap to hide.
5. **Test items in the library** from Oct 21: `demo-reset` before recording.
6. **The digest's draft animation** can fail on a planner slip (then the digest goes out without it) and takes up to ~90 s in total; record the digest beat once and keep a good take.
7. **The studio scan** (hourly, silent unless something new is filed) is a nice 5-second insert: drop an SVG into `~/CanvasMind/inbox`, cut to "📥 Filed 1 new item from your studio".
