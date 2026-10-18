# SketchDynamics: 3-minute demo plan

Run `uv run sketchdynamics` and record in Chrome, full window. Cut the model waits in editing: the timers in the panel show the real latency, so speeding up the waits is honest. Default planner: Super 120B. Keep **Retry with Ultra** in reserve.

Live timings measured Oct 18 (Super unless noted):

| Mode | Example | Result |
| --- | --- | --- |
| Prompt only | "a red ball drops in, bounces three times, then rolls off to the right while a blue square spins in the corner" | valid, 1 call, 16 s |
| Sketch + prompt | hill-shaped stroke + "a ball rolls along my line and bounces at the end" | valid, 1 call, 7.7 s |
| Tweak | "slower" | Nano, 1–2 calls, 2–6 s |
| SVG | wind turbine: VL rig (rotor about the hub) + "the turbine blades spin around the hub…" | VL 3 s; plan valid after 1–2 repairs, 30–50 s |
| SVG | cloud bolt + "the cloud drifts in from the left, then the bolt strikes down and flashes twice" | 2 calls, 32 s (with `enter`) |
| SVG + sketch path | cloud bolt + zigzag + "the lightning bolt zigzags down along my line s1 while the cloud stays still, then the bolt fades out" | follows the drawn line exactly; valid after 1 repair, 41 s (core runs: 16–21 s ×3) |

## Shot list

| Time | Beat | On screen | Voice-over idea |
| --- | --- | --- | --- |
| 0:00–0:12 | Hook | Finished wind turbine spinning; cut to the empty canvas | "Motion design without keyframes: sketch it, say it." |
| 0:12–0:30 | Idea | Panel and MotionLang program side by side | "Nemotron doesn't draw frames. It writes a small motion program, and a validator checks it before you see it." |
| 0:30–0:50 | 1. Prompt only | Type the ball + square prompt, Generate, play | Point at the single Super call and the "valid" verdict |
| 0:50–1:20 | 2. Sketch + prompt | Draw a hill, "a ball rolls along my line…", play; tweak "slower" then "more bounce"; click v1/v2 chips | "Small changes go to Nemotron Nano as edit ops, in about a second or two." |
| 1:20–2:05 | 3. Import SVG | Wind turbine: parts split, VL names them, rotor group with ◎ hub pivot (hover to show the crosshair), idea chips; pick one, Generate; panel shows a validator error and its repair | "Nemotron VL reads the artwork, rigs it and proposes motion; the planner animates the rig." |
| 2:05–2:30 | 4. SVG + motion path | Cloud bolt: take the bolt out of the group (one click, regrouping is yours), draw a zigzag, "the bolt strikes down along my line" | "Models propose, you decide: every grouping is editable." |
| 2:30–2:50 | Under the hood | Panel totals, MotionLang JSON, Export → HTML player opened in a new tab | "Every call runs on Nemotron via Nebius Token Factory; the output is an open, verifiable format." |
| 2:50–3:00 | Close | Sunrise rising, then the title card | Track, team, repo link |

Before each take: **Clear**, then reload. For SVG shots, drag files in from `apps/sketchdynamics/src/sketchdynamics/static/samples/`. Draw strokes slowly with a mouse or tablet; they're simplified automatically.

## Known gaps (prioritised for the video)

1. ~~Blocker for mode 4~~: fixed in the core library (stroke paths are in canvas space for grouped parts too).
2. **VL grouping varies between runs** (cloud + bolt sometimes one group). In the video, use it to show editing: one click to take a part out of its group.
3. ~~"Talk"~~: done. Mic buttons use the browser's Web Speech API (Chrome sends the audio to Google's speech service; Nemotron still sees only text). Good shot for mode 2: speak the tweak.
4. **Latency**: 8–50 s per plan. Cut in editing; the panel's per-call timers keep it honest.
5. **Intent misses**: valid but not what was asked (e.g. a "motion lines pulse" applied to the rotor). The planned MoVer-style intent check would catch these.
6. **Video/GIF export**: only HTML player + JSON today. MP4/GIF via a Nebius Serverless job is on the roadmap.

## More SVG examples worth collecting

Prefer CC0/MIT/public-domain sources and record each in `apps/sketchdynamics/src/sketchdynamics/static/samples/README.md`.

- A **wordmark** (text converted to paths): letters drop in one by one, a group per word.
- A **gear or clock**: rotation about a pivot (clock hands each with their own pivot).
- A **car or bike**: wheels as separate groups, each pivoting on its axle, while the body drives along a sketched road.
- A **rocket**: `enter` from the bottom, flame `wiggle`, exit through the top.
- A **heart or like icon**: pulse/bounce, the simplest "feels alive" case.
- A **mascot** with limbs: shows the current limit (one level of groups, no joint chains).
