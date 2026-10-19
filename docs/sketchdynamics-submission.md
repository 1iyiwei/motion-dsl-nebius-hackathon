# Submission form

## Project overview
Please respect our [Community Guidelines](https://info.devpost.com/guidelines?_gl=1*1fgq1l6*_gcl_au*MTMxNDQ4NTA5My4xNzkyMjA5NTc2*_ga*MjAzNzM5NDY4Ny4xNzkyMjA5NTc3*_ga_0YHJK3Y10M*czE3OTIzMDcwNDMkbzgkZzEkdDE3OTIzMDc4MTAkajIyJGwwJGgw).

### General info
* Project name
60 characters limit.
You can change this at any time.

SketchDynamics

* Elevator pitch
200 characters limit.
Provide a short tagline for the project. You can change this later.

SketchDynamics turns simple prompts, hand-drawn motion curves, or SVG imports into polished animations. It analyzes your input, provides intelligent defaults, and lets you tweak the results.

## Project details
For public project page
Information entered below will appear on your public project page.

Say what should happen, draw rough strokes, or drop in an SVG logo, and get a playable animation. Then refine it by typing short changes: "slower", "more bounce", "make it blue".

Under the hood the app writes a program in MotionLang (motionlang), a declarative motion language built for LLMs. NVIDIA Nemotron models on Nebius Token Factory write and edit it, and a deterministic validator checks it. The validator's errors carry hints, and those go back to the model until the animation is valid. The side panel shows every model call as it happens.

### Project Story
* About the project
Be sure to write what inspired you, what you learned, how you built your project, and the challenges you faced. Format your story in Markdown, with LaTeX support for math.

#### What it does
SketchDynamics is an experimental animation tool that combines the power of LLMs with the precision of motion DSLs to create engaging animations from simple inputs like text prompts, SVG logos, or hand-drawn motion curves. It's designed to make animation creation accessible to everyone, from beginners to professionals.

#### Inspiration

The core UI features are inspired by several projects, including [MoVer](https://mover-dsl.github.io/), [LogoMotion](https://vivian-liu.com/#/logomotion), [DrawTalk](https://ktrosenberg.github.io/drawtalking/), [OmniLottie](https://github.com/OpenVGLab/OmniLottie), and [Adobe Fresco motion](https://helpx.adobe.com/fresco/using/apply-motion-to-artwork.html).


#### How we built it

SketchDynamics is a thin, inspectable app on top of **MotionLang**, a declarative 2D motion language we designed for LLMs to write and machines to check.

**The language.** A MotionLang document lists *actors* (shapes, sketched strokes, or parts of an imported SVG, optionally grouped with a pivot), a *timeline* of 17 intent-level verbs (`move_along`, `bounce`, `spring_to`, `draw_on`, `reveal`, `enter`/`exit`, `orbit`, `camera`, …) arranged in `seq`/`par`/`stagger` blocks, and *constraints* (stay on screen, total duration, …). The model writes intent ("bounce three times", "wipe in from the left"), not keyframes. A deterministic Python baker turns that into per-frame tracks, and a small SVG player plays them. The JSON Schema and the verb reference in the system prompt are generated from the verb registry, so a new verb is usable the moment it's registered.

**The core loop** runs for every input, all on Nebius Token Factory:
1. **Plan**: **Nemotron 3 Super 120B** (the default, with structured output constrained to the MotionLang schema) or **Nemotron 3 Ultra 550B** (one click, or "Retry with Ultra") writes a MotionLang document from what you said and anything you drew or imported.
2. **Validate**: a tier-1 validator bakes the document and checks references, conflicting verbs, teleports, off-screen motion, invisible parts, and animations that change nothing (14 error codes). Every error carries a concrete fix: a JSON pointer and an exact edit.
3. **Repair**: the errors go back to the planner. Small problem sets are fixed with *edit ops* rather than a full rewrite, so repairs converge instead of drifting. The loop stops early if the same problems survive a round, and then shows the closest attempt that still plays.
4. **Refine**: "slower", "more bounce" go to **Nemotron 3 Nano 30B** with reasoning turned off, which answers in 1–3 s with a few id-addressed edit ops. If those fail it retries with reasoning, then escalates to the planner. Every result is a version you can go back to.

**Input modes, from simple to rich.** Each mode adds information to the same loop:
1. **Prompt only.** Type or say what should happen ("a red ball drops in, bounces three times, then rolls off to the right while a blue square spins in the corner"). The planner creates the actors (shapes, text) and the timeline from scratch. Speech goes through the browser's speech recognition, so the model always receives text.
2. **Sketch + prompt.** Strokes drawn on the canvas are captured as point lists in canvas coordinates, simplified (Ramer–Douglas–Peucker, at most 48 points) and labelled `s1`, `s2`, … on screen, so you can refer to them ("along my line s1"). The planner sees each stroke's points, end points and bounding box. The document keeps the strokes as data, and verbs refer to them by id: `move_along {"stroke": "s1"}` follows the line exactly, and a stroke can also be an actor that draws itself on.
3. **SVG + prompt.** The importer splits the file into parts (it breaks up compound paths and keeps holes with their shapes). **Nemotron 3.5 Super VL** gets two renderings, a clean one and one with each part numbered, and names every part, groups parts that move as one rigid piece (a rotor with its hub as the pivot), and suggests three animation ideas, the first of which pre-fills the prompt. You can regroup, rename, split parts or move the pivot by hand. The path data never goes to any model: models see part ids, labels, boxes and colours, so a detailed logo costs no more tokens than a simple one.
4. **SVG + sketch.** Draw a motion path over an imported logo ("the bolt zigzags down along my line while the cloud stays still"). Strokes always mean canvas space, so a part inside a group follows the line exactly where it was drawn, whatever its group's position.

**Objects without a shape.** The planner draws balls, boxes and text from basic shapes, so "a crystal drops from the sky" used to play as a ball. Before planning, **Nano** lists the objects a request shows that nothing on the canvas draws yet. For each one the app looks in its shape library, then searches the web with **Tavily**, restricted to icon sites with reusable files (SVG Repo, Wikimedia Commons). **Super VL** scores all candidates at once from a numbered grid, and if none reads well, **Super** draws a flat SVG icon and redraws it once with VL's critique. The user picks one, and it joins the scene as imported parts, so every mode above works with it. Found and drawn SVGs alike are reduced to plain paths by the importer, so nothing from the web runs in the page, and a pick can be saved to the library with its source and license.

**The app** is FastAPI plus vanilla JS with no build step. You sketch on a canvas, dictate with the browser's speech recognition or write the prompt into the text box, and import SVGs by drag and drop. A side panel streams every model call live: model, latency, tokens, and the validator verdict with the exact problems sent back for repair. Exports are a standalone HTML player or the MotionLang JSON. 288 tests run against a scripted fake model (and a fake Tavily), so the loop is tested without spending tokens.

**Relation to MoVer and LogoMotion.** LogoMotion has an LLM write animation *code* (JavaScript) and debug it by looking at rendered frames. MoVer verifies motion graphics against the prompt with a first-order-logic language. MotionLang sits between them. Like LogoMotion, we visually ground the artwork first (VL names, rigs and proposes motion). But the model writes a *constrained declarative program*, not code, so most failures are caught structurally before anything renders, with exact, machine-readable fixes. MoVer-style intent verification is the next layer: it would catch plans that are valid but miss the request.

#### Challenges we ran into

- **Repairs that drift.** Early on, each "fix" rewrote the whole document and often broke something else. Hint wording leaked into the output too: "fade it in (fade to 1)" became an invented `fade_to` verb. Making hints literal and structural (an exact edit at a JSON pointer) and repairing with edit ops instead of rewrites made the loop converge.
- **Icons aren't drawn as objects.** Real SVGs pack, say, all three turbine blades into one path per colour. We split compound paths (keeping holes), then gave the vision model a numbered overlay (set-of-marks) so it could tell 21 parts apart.
- **Coordinate frames.** Parts live inside groups, but users draw motion paths on the canvas. Making sketched paths always mean canvas space, whatever the parent, fixed a whole class of "follows my line, but 110 px off" bugs.
- **Latency and variance.** The same plan took 22 s or 87 s depending on how long the model reasoned. Fast edits on Nano without reasoning, structured output on Super, and streaming every call to the UI kept it usable and honest.
- **Valid ≠ intended.** A plan can pass every check and still not do what was asked (a move to where the part already is). New checks like `E_NOOP` catch some of these; the rest is what intent verification is for.

#### Accomplishments that we're proud of

- One prompt-to-animation loop that works across four input styles: text only, sketch plus text, SVG, and SVG plus a sketched motion path.
- Imported artwork gets rigged automatically (named parts, rigid groups, pivots) and stays fully editable by hand: group, ungroup, rename, split, set the pivot.
- A validator whose errors double as repair instructions, and a panel that shows every model call, so you can see why the result is valid instead of trusting it.
- A small, open, extensible motion language: 17 verbs, a spec, and a deterministic baker and player.

#### What we learned

Motion DSL really anchors the generation and validation process.

- A constrained language turns "is this animation right?" into checks a program can run, and those checks are the best prompt you can give a model: specific, local and actionable.
- Error-message wording matters as much as the system prompt: models copy hints literally.
- Matching each model to each job pays off: VL to read artwork, Super with structured output to plan, Nano without reasoning to edit, and Ultra as the fallback.

#### What's next

Turn this prototype into a real product!

<!--
- MotionHarness as a coding agent/copilot for anyone who wants to build their own animation authoring tools.
- CanvasMind as a personal agent to manage the assets and moodboards creators.
-->

* Built with
You can add up to 25 tags.
Languages, frameworks, platforms, cloud services, databases, APIS, etc.

python, javascript, fastapi, uvicorn, pydantic, numpy, svg, web-speech-api, uv, pytest, nvidia-nemotron, nemotron-3-super, nemotron-3-ultra, nemotron-3-nano, nemotron-3.5-super-vl, nebius-token-factory, openai-api, json-schema, tavily

"Try it out" links
Add links where people can try your project or see your code.
URL for demo site, app store listing, GitHub repo, etc.

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/sketchdynamics

### Project Media
Image gallery
JPG, PNG or GIF format, 5 MB max file size. For best results, use a 3:2 ratio. Up to 15 images.

* Demo video URL
Allowed platforms: YouTube or Vimeo only.
Video length: Only the first 3 minutes will be reviewed, so include everything needed to evaluate your project within that time.
Visibility: Set to public or unlisted with no password.

https://youtu.be/aVp8AO9TWcU

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
Best apps and agents

* Is your project new or existing prior to August 26, 2026?
New

* Provide a URL to your public code repository for judging and testing on either GitHub, GitLab or Bitbucket.
Must include: approved OSI license, README with setup instructions, highlight how you’ve used NVIDIA Nemotron or other NVIDIA open source models and where Token Factory accelerated your workflow and any other Nebius Tools or Services used.

https://github.com/1iyiwei/motion-dsl-nebius-hackathon/tree/main/apps/sketchdynamics

Provide a URL to a working demo, hosted application, or test build of your Project.
Not required for Physical AI submissions

https://sketch-dynamics.dragoniac.com/

<!--
(also https://sketch-dynamics-nebius-hackathon.onrender.com/) Access code: `<code>` (this field is for judges only, not the public page). On the free plan the first visit may take about a minute while the server wakes up.
-->

* Which model(s) did you use, and why did you choose that size/variant?
e.g., Nemotron-4 340B, Nemotron-Mini, etc.

Four Nemotron models, one per job, all through Nebius Token Factory:
- **Nemotron 3 Super 120B-A12B** is the default planner. With structured output constrained to our JSON Schema, it was the most reliable planner we measured on logo scenes (6/6 valid after at most two cheap repairs), at lower cost than Ultra.
- **Nemotron 3 Ultra 550B-A55B** is the stronger fallback planner, one click away ("Retry with Ultra"). It's best at hard multi-step plans in plain JSON mode.
- **Nemotron 3 Nano 30B-A3B**, with reasoning turned off, handles fast edits ("slower", "more bounce") as a few edit ops in 1–3 s. It escalates to reasoning, then to the planner, only when needed. It also spots objects a request needs a drawing for (about 1 s).
- **Nemotron 3.5 Super VL** reads imported artwork: it names parts, groups rigid pieces, picks rotation pivots and suggests animations, in 2–4 s. It also scores candidate shapes found on the web from one numbered grid image.
- Super also draws a simple SVG icon when no found shape fits, in about 3 s.

* How would you rate Nemotron's output quality for your use case? (1 = Poor, 10 = Excellent)
Please share what worked well or fell short — e.g., reasoning, instruction-following, domain knowledge, multilingual support.

**8.** Worked well: JSON mode and structured output were clean on every model; instruction-following on a novel DSL was strong given only a generated reference and one example; and the VL model's part naming and grouping were accurate once given numbered marks. Fell short: models sometimes copied validator hint wording literally (inventing fields or verbs), occasionally mixed coordinate frames, and could produce plans that are valid but miss the intent. Latency varied a lot when reasoning ran long.

* Did you fine-tune, prompt-engineer, or use Nemotron out of the box? What was your approach?
Use Nemotron out of the box.

No fine-tuning: the models are used as served, with prompt engineering around them. The system prompt is generated from the verb registry (the reference for each verb, plus rules and one example document). Planning uses structured output constrained to the MotionLang JSON Schema on Super. The core of the approach is a **validator-in-the-loop**: deterministic checks whose error messages are written as repair instructions (an exact edit at a JSON pointer), fed back to the model as edit ops. For artwork we use **set-of-marks** prompting: a second image with each part numbered.

* How did Nemotron's performance compare to other models you've used for similar tasks?

<!--
**[check: your experience]** _(We didn't run other providers in the app; Nemotron is the only runtime model. A comparison here should come from your own experience with other models.)_
-->
Nemotron works much better than some open-weight models that we ran locally, such as qwen2.5vl:32b.

* Which Nebius platform capabilities were most valuable to your project and how?
Please include detail on how you leveraged Nebius infrastructure to run Nemotron (e.g., GPU instance types, deployment setup, API access, scaling).

**Token Factory's OpenAI-compatible API**, serving the whole Nemotron family behind one key and one client, which made a different model per job trivial: a per-request model override picks Super, Ultra, Nano or VL. Most valuable features:
- **JSON mode** and **structured output** (`json_schema`), so the planner returns schema-valid documents.
- **The reasoning toggle** (`chat_template_kwargs.enable_thinking=false`), which made Nano edits about 3× faster (3.3 s to 1.1 s on a typical edit).
- **Multimodal input** (image data URLs) on Super VL.
- **Per-call usage counts**, which the app shows live for every call.

There's no GPU provisioning or deployment on our side: Token Factory is serverless inference, so scaling is the platform's job.

* How likely are you to recommend running Nemotron on Nebius to other developers? (1 = Unlikely, 10 = Highly Likely)
Please share why or provide additional feedback.

**8.** One OpenAI-compatible endpoint for the full Nemotron range, from Nano to Ultra plus VL, makes it easy to match model size to each task, and JSON and structured output worked across the family. What would make it a 10 is better documentation of model ids and the reasoning toggle, consistent errors for unsupported schema keywords, and a way to cap reasoning length.

* How would you rate your experience running Nemotron inference on Nebius compared to previous cloud or local development environments? (1 = Poor, 10 = Excellent)
Please share why or provide additional feedback.

**8.** No infrastructure to manage, an API the standard OpenAI SDK could talk to, and access to models far too large to run locally (Ultra 550B). The rough edges: exact model ids are only discoverable via `/v1/models` (the catalog page needs a login), and Super returned a bare 500 for a schema keyword where Ultra gave a clear 400.

* What additional features or improvements would have made the Nemotron on Nebius experience more effective for your project?

1. **A per-request reasoning budget** (max reasoning tokens). The same planning prompt took 22 s to 87 s depending on reasoning length; interactive apps need to cap that tail.
2. **Structured output that supports, or cleanly ignores, `propertyNames`**, which Pydantic emits for `dict[Literal, …]`. Today Ultra returns a 400 and Super a bare 500 with no message.
3. **Documenting the reasoning toggle per model family.** `enable_thinking=false` works, while `/no_think` in the prompt made Nano spin to `max_tokens` and return nothing.
4. **Model ids in the public docs**, not only via `/v1/models`.
5. **A fix for a JSON-mode slip on Super:** about 1 in 4 replies came back as the whole document escaped into a single key.

* What do you most hope to see from the Nemotron team next?

**[check]** Reliable structured output across every size (Ultra degenerated under constrained decoding where Super excelled), controllable reasoning length, and a VL model that can look at rendered animation frames to judge motion, which would close our loop from "valid" to "matches the intent".

* Did you use Tavily in your project?
To qualify: your project must make a functional, runtime call to the Tavily API as part of its solution.
Yes. When a prompt names an object the planner can't draw from basic shapes ("a crystal drops from the sky"), and the app's shape library has none, the server calls Tavily's search API at runtime, restricted to icon sites (`include_domains`: svgrepo.com, commons.wikimedia.org), to find SVG files of it. The app fetches and sanitizes them, Nemotron VL ranks them, and the user picks one to animate. The side panel shows the Tavily step with its result counts. Code: `apps/sketchdynamics/src/sketchdynamics/shapes.py` (`WebSearch`).

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