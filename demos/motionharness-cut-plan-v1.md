# MotionHarness demo: v1 edit (2:57.5)

Output: `motionharness_demo_v1.mp4` (1920×1080, 30 fps). Sources: W = `motion-harness-wobble.mp4` (the dashboard),
C = `motion-harness-claude.mp4` (Claude Code calling MotionHarness over MCP); both are 1920×1200 screen recordings.
Cuts fall between sentences, using a local Whisper transcript (faster-whisper small.en) with word timings.
The edit list is `motionharness-v1.json`; re-render with:

    uv run python demos/render.py demos/motionharness-v1.json demos/motionharness_demo_v1.mp4

Crops (source pixels): `ui` = the browser content (8, 118, 1600×900), `wide` = browser + terminal (0, 118, 1712×963),
`term` = the terminal window (828, 248, 880×495, a 2.2× close-up). Sped-up stretches are silent and labelled with
their speed, so the edit stays honest about how long things take.

| # | At | Src | In → out | Length | Crop | Content |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 0:00.0 | W | 0:01.4 → 0:13.3 | 11.9 s | ui | intro; 'this is the user interface' (caption “MotionHarness: a coding agent that extends MotionLang”) |
| 2 | 0:11.9 | W | 0:13.7 → 0:25.5 | 11.8 s | ui | text box: 'add a new verb like this' |
| 3 | 0:23.7 | W | 0:25.9 → 0:35.6 | 9.7 s | ui | green button: the agent generates and tests |
| 4 | 0:33.4 | W | 0:36.3 → 0:46.9 | 10.6 s | ui | three parallel candidates in separate sandboxes; yellow = running |
| 5 | 0:44.0 | W | 0:51.3 → 0:57.2 | 5.9 s | ui | green = passed, red = failed |
| 6 | 0:49.9 | W | 0:59.4 → 1:02.8 | 3.4 s | ui | 'one candidate for evaluation' |
| 7 | 0:53.3 | W | 1:02.8 → 1:22.8 | 4.0 s | ui | the run finishes (repairs, critique) (5× speed, silent) |
| 8 | 0:57.3 | W | 1:22.8 → 1:27.3 | 4.5 s | ui | 'we have a result; test using this new verb' |
| 9 | 1:01.8 | W | 1:39.9 → 1:52.3 | 12.4 s | ui | types: a ball falls from the sky and wobbles on its way |
| 10 | 1:14.2 | W | 1:54.8 → 2:06.0 | 11.2 s | ui | compare with the new verb vs core verbs only |
| 11 | 1:25.4 | W | 2:12.9 → 2:24.1 | 11.2 s | ui | model calls: bigger model plans, lighter models for easier tasks |
| 12 | 1:36.6 | W | 2:26.6 → 2:29.8 | 3.2 s | ui | the agent log |
| 13 | 1:39.8 | W | 4:01.0 → 4:23.1 | 5.5 s | ui | second scene trial plans both sides (the first trial, 2:30-4:03, is cut) (4× speed, silent) |
| 14 | 1:45.3 | W | 4:23.1 → 4:34.9 | 11.8 s | ui | 'the wobble on the left matches the description better' |
| 15 | 1:57.1 | C | 0:00.4 → 0:08.6 | 8.2 s | wide | 'apply this MotionHarness MCP to Claude Code' (repo URL masked; caption “Claude Code using MotionHarness as an MCP tool”) |
| 16 | 2:05.3 | C | 0:11.8 → 0:16.0 | 4.2 s | term | claude mcp add (repo URL masked) |
| 17 | 2:09.5 | C | 0:21.8 → 0:27.9 | 6.1 s | term | claude mcp list (repo URL masked) |
| 18 | 2:15.6 | C | 0:58.5 → 1:02.5 | 4.0 s | term | 'motionharness is listed' |
| 19 | 2:19.6 | C | 1:03.5 → 1:06.2 | 2.7 s | term | 'now let's run it' |
| 20 | 2:22.3 | C | 1:08.8 → 1:12.8 | 4.0 s | term | start Claude Code |
| 21 | 2:26.3 | C | 1:17.4 → 1:27.7 | 10.3 s | term | types: use the motionharness tool to add a wobble verb |
| 22 | 2:36.6 | C | 1:27.7 → 3:21.9 | 7.1 s | term | the MCP run (16× speed, silent) |
| 23 | 2:43.8 | C | 3:22.0 → 3:24.0 | 2.0 s | term | 'we got the results back' |
| 24 | 2:45.8 | C | 3:28.4 → 3:39.0 | 10.6 s | term | Claude reports the run: tests, critique, plugin path |
| 25 | 2:56.4 | C | 3:48.5 → 3:49.6 | 1.1 s | term | 'thank you' |

Cut from W: the change of mind while typing the scene prompt (1:28–1:40, "a ball bounces"), the first "Try in a scene"
trial, whose core-only side failed (2:30–4:01; the rerun's results are kept), and waits. Cut from C: the wait for
`claude mcp list` to connect (0:28–0:58) and the tail after Claude's report.

The first lines of the terminal in C show `git push` output with the repository URL; segments 15–17 paint over them.

Known issues in this take (worth fixing before a final cut):
- Sandboxes access wasn't active yet, so the header pill reads "local (Sandboxes inactive)". Re-record the
  sandbox-visible shots once access lands (see docs/motionharness-demo.md).
- Claude's report quotes the critic's "8333% scale overshoot": a measurement bug in MotionHarness (overshoot of a
  property that returns to its start divides by a near-zero travel), fixed after this recording.
