# SketchDynamics demo: v1 edit (3:02)

Output: `sketchdynamics_demo_v1.mp4` (1920×1080, 30 fps). Sources: M = `sketch_dynamics.mp4`, S = `sunrise.mp4`.
Cuts fall between sentences, using a local Whisper transcript with word timings. The edit list is `sketchdynamics-v1.json`; re-render with:

    python3 render.py sketchdynamics-v1.json sketchdynamics_demo_v1.mp4

| # | Src | In → out | Length | Content |
| --- | --- | --- | --- | --- |
| 1 | M | 0:00.0 → 0:12.6 | 12.6 s | hook: wind turbine; 'design motion without any keyframes' |
| 2 | M | 0:13.4 → 0:17.3 | 3.9 s | 'Let's start with a simple example with an empty canvas.' |
| 3 | M | 0:24.1 → 0:38.7 | 14.6 s | dictation: red ball + blue square |
| 4 | M | 1:32.5 → 1:36.9 | 4.4 s | 'here is the resulting motion' |
| 5 | M | 1:55.4 → 2:14.1 | 18.7 s | draw a stroke + dictate 'a ball rolls along the curve…' |
| 6 | M | 2:23.6 → 2:27.1 | 3.5 s | ball rolls; 'That's pretty nice' |
| 7 | M | 5:53.6 → 6:15.6 | 22.0 s | cloud bolt: load, VL groups, ungroup |
| 8 | M | 6:29.0 → 6:42.2 | 13.2 s | add a stroke, modify the prompt |
| 9 | M | 6:57.3 → 6:59.6 | 2.3 s | 'Generate the result. Now…' |
| 10 | M | 8:18.9 → 8:24.8 | 5.9 s | '…it's doing a self-repair based on one mistake it found by itself' |
| 11 | M | 9:36.8 → 9:44.7 | 7.9 s | cloud bolt result; 'follows the prompt and the motion path I drew' |
| 12 | S | 0:01.0 → 0:08.9 | 7.9 s | sunrise: import |
| 13 | S | 0:18.9 → 0:25.3 | 6.4 s | reads the suggested prompt |
| 14 | S | 0:28.2 → 0:43.6 | 15.4 s | edits it by voice; generate |
| 15 | S | 1:03.0 → 1:12.6 | 9.6 s | sunrise result |
| 16 | M | 11:47.5 → 11:56.5 | 9.0 s | gallery: heart |
| 17 | M | 11:59.5 → 12:05.5 | 6.0 s | gallery: ant on a drawn path |
| 18 | M | 12:08.0 → 12:15.0 | 7.0 s | gallery: snail |
| 19 | M | 12:17.3 → 12:28.7 | 11.4 s | gallery: LOVE letters; 'Thank you' |

Cut from M: the repeated ball prompt (0:46–0:55), all model waits, the "moving a bit slower" regeneration (2:30–3:00), the wind turbine run (3:00–5:53), the reading of the cloud's suggested prompt (6:16–6:29), and the failed sunrise run (9:46–11:47, replaced by S).
