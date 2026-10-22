# CanvasMind: submission draft (Personal AI)

Draft for the Devpost form (fields as in `docs/submission-form.md`). Not submitted yet. Open items are marked **TODO**.

## General info

**Project name** (≤60): CanvasMind

**Elevator pitch** (≤200): A private, always-on studio assistant for motion designers. Send it your works from your phone; it files them on your machine, learns your motion style and animates in it with NVIDIA Nemotron.

## Project details

Message CanvasMind on Telegram like a studio assistant: send a logo, a reference photo or a link, ask "animate the café logo in my style", say "slower" or "👍 love the calm pace", ask "that sunrise logo from last week?". Everything you send is filed in a library **on your own machine**, described by Nemotron VL so you can find it by meaning later. Every 👍/👎 updates a readable style profile, and every new animation follows it, with a line saying which of your preferences it used. Every Monday it sends a short motion-design digest, picked from the web for your style, with one of your logos animated after a trend it found.

The agent is Hermes Agent running inside an NVIDIA OpenShell sandbox, installed with NVIDIA NemoClaw. It can't read your disk or hold your API keys: it reaches your library only through a small MCP server on your machine, over your private Tailscale network, and every model call runs on Nemotron via Nebius Token Factory.

### What it does

- **Files what you send.** Photos, stickers, SVG logos, MotionLang animations and links go into `~/CanvasMind/` on your machine. Nemotron 3.5 Super VL writes a title, caption, tags and palette for each one; duplicates are recognised. A studio scan also files anything new in folders you choose (for example your SketchDynamics exports), hourly, without spending a token when nothing changed.
- **Finds it again.** "Something with wind", "everything for the café rebrand", "what I filed last week": full-text search plus Nemotron 3 Nano scoring each candidate, with thumbnails back in the chat.
- **Animates in your style.** Send or pick an SVG and describe the motion (or let it propose one). Nemotron VL names the logo's parts from a numbered render; Nemotron 3 Super writes a MotionLang animation, our declarative motion language, which a validator checks and repairs; the result comes back as a GIF that plays in the chat and is filed as a new version.
- **Learns from your reactions.** "Slower" retimes the animation instantly; other changes ("less bounce") go through Nano edit ops. 👍/👎 with a reason is recorded in a journal; `style.json` counts the verbs, easings and lengths you like and dislike, and Nano keeps a few notes in your own words ("avoids bouncy", "prefers hand-made"). The planner gets that summary on every new animation.
- **Works while you don't.** A Monday digest: Nano writes search queries from your style profile, Tavily searches the web (it only ever sees those queries), Super picks three finds with a "why it fits you" line and cites only real sources, and one of your SVGs is animated after a trend.
- **Your data, your call.** Forget an item (its files and any feedback on it go, and the profile is recounted) or export everything as a zip.

### Inspiration

Mood-board apps keep your references on someone else's server and never act on them. We wanted the opposite: a personal assistant that lives with your files, remembers your work across tools, and *does* something with it in your own taste. Our two other hackathon projects gave us the pieces: MotionLang, a motion language Nemotron can write and a validator can check (SketchDynamics), and the habit of verifying model output with programs rather than trusting it (MotionHarness). NVIDIA's NemoClaw/OpenShell stack gave us the "private and always on" part: a sandboxed agent that can only reach what you allow.

### How we built it

- **Agent**: Hermes Agent (Nous Research) in an NVIDIA OpenShell sandbox, installed and managed by NVIDIA NemoClaw on a MacBook (Apple silicon, Docker). Inference goes to Token Factory through NemoClaw's "Other OpenAI-compatible endpoint" option, so the key lives in OpenShell's provider store, never in the sandbox.
- **canvasmind** (Python, ours): a library (files on disk, SQLite with FTS5), the MotionLang animation and style code, and a Streamable HTTP MCP server bound to localhost. `tailscale serve` gives it an HTTPS address on the user's private tailnet, which OpenShell accepts as a trusted private host; OpenShell injects the bearer token at egress and restricts the agent to MCP methods.
- **Getting files across the sandbox boundary**: OpenShell caps an MCP request at 128 KB and a model can't put image bytes in a tool call, so a small script in the sandbox uploads attachments in chunks over the same MCP route (with tools hidden from the model). GIFs come back as MCP image content, which Hermes forwards to Telegram.
- **Models (all on Nebius Token Factory)**:

  | Job | Model |
  | --- | --- |
  | Hermes agent (chat, tool use, cron turns) | Nemotron 3 Super 120B |
  | Caption and tag filed items; name a logo's parts and propose an animation | Nemotron 3.5 Super VL (reasoning off, ~2 s) |
  | Plan MotionLang animations (structured output); write the digest | Nemotron 3 Super 120B |
  | Re-rank search results; style notes; digest queries; edit-op tweaks | Nemotron 3 Nano 30B (reasoning off, ~1–2 s) |

- **Setup as code**: `canvasmind hermes-setup` installs the skill, a CanvasMind persona, two Hermes cron jobs and a small Hermes plugin into the sandbox, and sets Hermes's own options; it's rerunnable after a NemoClaw rebuild. `canvasmind demo-reset` rebuilds a seeded library for demos.
- **Tests**: 29 tests with a scripted fake model cover filing, recall, chunked upload, the MCP server (auth, hidden tools, GIF content), animation, tweaks, style, forget/export, the studio scan and demo fixtures.

### Challenges we ran into

- **Making a sandbox reach the host.** OpenShell rejects `localhost`, `host.docker.internal` and stdio MCP servers; it wants HTTPS with a trusted certificate. Tailscale's `serve` plus NemoClaw's `--trusted-private-host` was the clean answer, and it keeps the library off the public internet.
- **One model per sandbox.** OpenShell routes every inference call from the sandbox to the model chosen at onboarding, so the agent can't use Nemotron VL for photos itself. We turned that into the design: the agent never sees images; canvasmind describes them on the user's side.
- **Hermes under NemoClaw**: tool search hid our MCP tools from the model (it searched the web for "current wind speed" instead), MCP resources made it report an empty library, and the persona file is read from `~/.hermes/runtime/`, not `~/.hermes/`. We fixed each in config, in the persona, or on the server side.
- **Two Telegram adapter bugs**: `.svg` files sent as documents were dropped as "non-image data", and GIFs from tools were sent as still photos (then as unnamed bytes). A small Hermes plugin keeps SVGs and sends local GIFs as animations; Hermes loads the adapter under two module names, so it patches both. Reported in our feedback.
- **Photos can't be animated**: redrawing a photo as an animatable vector was poor with every approach we tried, so CanvasMind animates SVG artwork only, and keeps photos as references.
- **Cost of an agent turn**: Hermes's own prompt and tool schemas cost 63–74k prompt tokens per turn at first; a trimmed toolset brought a typical turn to ~23–35k.

### Accomplishments that we're proud of

- A personal agent that is private by construction: sandboxed, keyless, reaching the user's files only through an 8-tool MCP door on their own network.
- A style profile you can read, edit and trace back to your own 👍/👎, that visibly steers the next animation.
- Proactive work that costs nothing when there's nothing to do (the hourly scan runs no model).
- Everything verified by programs: the MotionLang validator checks every animation before it reaches the chat.

### What we learned

- Agent frameworks are mostly prompt: Hermes's persona, tool exposure and image routing mattered more than our tool code for whether the right tool got called.
- "Private" is an architecture, not a setting: deciding which side sees images, keys and files shaped every component.
- Small, fast models carry most of the product: Nano does recall, notes, queries and edits in a second or two; the big model only plans.

### What's next

- A hosted demo studio for judges (separate sandbox, seeded library, per-user limits) and a read-only studio page.
- Running on any Linux VM (Nebius, or any other provider) for true always-on use; the setup is the same script.
- Feeding the style profile back into SketchDynamics, so the canvas app and the assistant share one taste.

## Built with

Python, Pydantic, NumPy, Pillow, SQLite (FTS5), MCP, Hermes Agent, NVIDIA NemoClaw, NVIDIA OpenShell, NVIDIA Nemotron 3 (Super 120B, Nano 30B), NVIDIA Nemotron 3.5 Super VL, Nebius Token Factory, Tavily, Telegram, Tailscale, MotionLang

## Try it out

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/canvasmind

### Project Media
Image gallery
JPG, PNG or GIF format, 5 MB max file size. For best results, use a 3:2 ratio. Up to 15 images.

**TODO**: phone screenshots (filing, recall thumbnails, a GIF, the style answer, the digest), the architecture card.

* Demo video URL

https://youtu.be/z8SF8A0VLks

## Additional info
For judges and organizers
Unless noted, additional info is for judges and hackathon organizers and will not appear on your public project page.

Upload a File
Upload a file as part of your submission - e.g., zip, pdf, word, apk, etc. To upload multiple files, put them in a zip file and upload the zip file. Limit: 35 MB.

* Submitter Type
individual, team, organization

* Organization Name (if applicable)
"N/A" if you're not representing an organization

Dragoniac

* Submitter Country of Residence
Appears in project gallery

* If you or any team members reside in Canada, please indicate the province below.
"N/A" if you don't reside in Canada.

* Which track are you submitting your project into?
Personal AI

* Is your project new or existing prior to August 26, 2026?
New

* Provide a URL to your public code repository for judging and testing on either GitHub, GitLab or Bitbucket.
Must include: approved OSI license, README with setup instructions, highlight how you’ve used NVIDIA Nemotron or other NVIDIA open source models and where Token Factory accelerated your workflow and any other Nebius Tools or Services used.

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/canvasmind

**TODO**: the app README is still a stub (setup with NemoClaw + Tailscale, how Nemotron/Token Factory is used, privacy model, feedback). Scheduled Oct 26.

Provide a URL to a working demo, hosted application, or test build of your Project.
Not required for Physical AI submissions

https://t.me/CanvasMindBot

<!-- For judges: open the link in Telegram and press Start. Each person gets their own demo library (a café logo, a sunrise logo, and a style profile from last week's ratings) that resets daily, with daily limits. Try "animate the café logo in my style", "a red ball drops in and bounces three times", 👍/👎 with a reason, "what's my style?", "what's new in motion design?". Running Dec 1–15. -->

* Which model(s) did you use, and why did you choose that size/variant?
e.g., Nemotron-4 340B, Nemotron-Mini, etc.

Nemotron 3 Super 120B-A12B (the Hermes agent, the animation planner with structured output, the digest), Nemotron 3.5 Super VL (seeing images: captions, part names), Nemotron 3 Nano 30B-A3B with reasoning off (fast judgements: recall re-ranking, style notes, search queries, small edits). Super is the agent because tool calling worked reliably and it was the most reliable MotionLang planner we measured; VL because the agent's own model can't see images in the sandbox; Nano for everything that needs to answer in a second or two.

* How would you rate Nemotron's output quality for your use case? (1 = Poor, 10 = Excellent)
Please share what worked well or fell short — e.g., reasoning, instruction-following, domain knowledge, multilingual support.

**7.** Tool calling worked out of the box on Super, Nano and Ultra (plain and streamed), which is what an agent framework needs. Super drove Hermes well once the right tools were visible, and wrote valid MotionLang animations in 20–35 s, usually on the first try or after one repair. VL captions were accurate and searchable in ~2 s, and it named logo parts correctly. What fell short: Nano's first edit for common tweaks often failed validation (we now retime "slower/faster" without a model), Super once used a container (`par`) as a verb (now rewritten deterministically in MotionLang), Nano over-generalised a style note ("calm pace" → "short duration") until we told it to stick to the user's words, and redrawing photos as vectors (VL or Super) was too crude to animate.

* Did you fine-tune, prompt-engineer, or use Nemotron out of the box? What was your approach?

Out of the box, with prompt engineering and programs that check the output. Each model gets the job that fits its size. The agent's behaviour is set by a persona and a skill (when to file, recall, animate, record feedback), not by fine-tuning. Model output is never trusted directly: animations go through the MotionLang validator and a repair loop, recall only returns ids that exist, the digest only cites URLs the search returned, and style notes are rebuilt from a journal of the user's own reactions.

* How did Nemotron's performance compare to other models you've used for similar tasks?

I haven't done a rigorous comparison yet, but my impression is that Nemotron excels at visual reasoning: VL analyzes SVGs into semantic components, Super and Ultra synthesize and validate motion parameters, and Nano handles quick motion edits.

* Which Nebius platform capabilities were most valuable to your project and how?
Please include detail on how you leveraged Nebius infrastructure to run Nemotron (e.g., GPU instance types, deployment setup, API access, scaling).

**Token Factory's OpenAI-compatible API** made the whole stack possible without a GPU: NemoClaw plugs it in as a custom endpoint, so Hermes in the OpenShell sandbox runs on Nemotron 3 Super with the key held by OpenShell, while canvasmind on the host calls Super VL, Super and Nano directly for captions, planning, recall and style. Per-call model choice, JSON and structured output, and the `enable_thinking` toggle let us keep the chat responsive: Nano and VL with reasoning off answer in 1–2 s, and only animation planning takes longer. Every call is logged with model, latency and tokens in `~/CanvasMind/calls.jsonl`.

* How likely are you to recommend running Nemotron on Nebius to other developers? (1 = Unlikely, 10 = Highly Likely)
Please share why or provide additional feedback.

**8.** One endpoint for the whole family (Nano to Ultra, plus VL), with tool calling and JSON working across it, is exactly what a personal agent needs. To make it a 10: a Token Factory preset in agent installers like NemoClaw, prompt caching for agent system prompts (Hermes sends 20–35k tokens of prompt per turn), and a reasoning-length cap.

* How would you rate your experience running Nemotron inference on Nebius compared to previous cloud or local development environments? (1 = Poor, 10 = Excellent)
Please share why or provide additional feedback.

**8.** Nothing to deploy, standard SDKs, and models that don't fit on a laptop, while the agent and the data stay on the laptop. The friction was in the glue rather than in inference: finding the exact model ids (`/v1/models`), and the custom-endpoint variable names, which are only in the NemoClaw repo.

* What additional features or improvements would have made the Nemotron on Nebius experience more effective for your project?

1. **Prompt caching** for long, repeated agent system prompts: most of a Hermes turn's 20–35k prompt tokens are identical every time.
2. **More than one model through an OpenShell inference route** (or a documented pattern for it), so a sandboxed agent can use VL for images and Nano for quick steps, not only the onboarding model.
3. **A Token Factory preset in NemoClaw's onboarding**, with the model ids filled in.
4. **A per-request reasoning budget**, to cap the long tail of planning latency.
5. **Model ids in the public docs**, not only via `/v1/models`.

* What do you most hope to see from the Nemotron team next?

A vision-capable Nemotron that is also a strong tool-calling agent, so a single model can run a personal agent that sees what the user sends; and multi-frame or video input for VL, so it can judge motion directly.

* Did you use Tavily in your project?
To qualify: your project must make a functional, runtime call to the Tavily API as part of its solution.
Yes

<!-- Not part of the form (it only asks yes/no); for reference if asked how: the weekly digest (`apps/canvasmind/src/canvasmind/digest.py`, `tavily_search`) calls Tavily's search API at runtime with three queries that Nemotron Nano writes from the user's style profile (time range: past month). Nemotron Super picks three finds from the results and cites only URLs Tavily returned; one of the user's SVGs is then animated after a find. Tavily never sees the user's files or captions, only those queries. It runs every Monday as a Hermes cron job and on demand ("what's new in motion design?"). -->

Builders & Brews - IRL Events City
If you're attending one of the 20 participating IRL hackathon events, select your city to be eligible for the City Winner Award.

* I, and, if applicable, all of my teammates, are at least the age of majority where I reside (e.g. 18 in the US).
* I, and, if applicable, all of my teammates, are not employees, representatives nor agents of the Promotion Entities of this hackathon (sponsor, administrator. or any affiliates).

* Which AI tools have you leveraged while working on this project?
Claude Code

* Describe the level of learning you/your team derived from the project
Significant

* Did you gain AI value that you can use in your career?
Yes

## Submit project
After submitting, you can still edit your project until the submission deadline.

Terms & conditions
* I, and all of my team members, have read and agree to be bound by the Official Rules and the Devpost Terms of Service.

## Feedback (for organizers)

See `docs/token-factory-feedback.md`, Oct 21 entries: function calling across Nemotron, NemoClaw + Token Factory setup, MCP through OpenShell, OpenShell's one-model inference route, and the two Hermes Telegram adapter bugs.
