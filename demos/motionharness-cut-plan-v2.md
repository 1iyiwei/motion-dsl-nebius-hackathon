# MotionHarness demo: v2 edit (2:57.7)

Output: `motionharness_demo_v2.mp4` (1920×1080, 30 fps). Sources: N = `motion-harness-wobble-nebius-sandbox.mp4` (the
dashboard, recorded Oct 20 with Token Factory Sandboxes active: the header pill reads "Token Factory Sandboxes" and the
environment node is a Sandboxes image), C = `motion-harness-claude.mp4` (Claude Code calling MotionHarness over MCP).
Cuts fall between sentences, using a local Whisper transcript (faster-whisper small.en) with word timings. Re-render with:

    uv run python demos/render.py demos/motionharness-v2.json demos/motionharness_demo_v2.mp4

Crops (source pixels): `ui` = the browser content (8, 118, 1600×900); `scene` = the two "Try in a scene" players
(362, 620, 856×482, a 2.2× close-up for the final comparison); `term` = the terminal (828, 248, 880×495). Waits are sped
up 8× (dashboard) or 16× (the MCP run), silent, and labelled with their speed.

| # | At | Src | In → out | Length | Crop | Content |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 0:00.0 | N | 0:00.9 → 0:10.4 | 9.5 s | ui | intro; 'here is the user interface' (caption “MotionHarness: a coding agent that extends MotionLang”) |
| 2 | 0:09.5 | N | 0:11.4 → 0:25.3 | 13.9 s | ui | types the task: a wobble verb, like jelly after landing |
| 3 | 0:23.4 | N | 0:26.5 → 0:35.7 | 9.2 s | ui | press the button: agents write and evaluate proposals |
| 4 | 0:32.6 | N | 0:37.4 → 0:46.2 | 8.8 s | ui | three separate sandboxes (caption “every candidate runs in Nebius Token Factory Sandboxes”) |
| 5 | 0:41.4 | N | 0:47.5 → 0:49.4 | 1.9 s | ui | 'the yellow boxes are running' |
| 6 | 0:43.3 | N | 0:49.4 → 1:13.1 | 3.0 s | ui | candidates run (8× speed, silent) |
| 7 | 0:46.3 | N | 1:13.1 → 1:15.2 | 2.1 s | ui | 'the green ones are successful' |
| 8 | 0:48.4 | N | 1:21.9 → 1:24.3 | 2.4 s | ui | 'the red ones indicate failures' |
| 9 | 0:50.8 | N | 1:24.3 → 1:36.9 | 1.6 s | ui | repairs and critique (8× speed, silent) |
| 10 | 0:52.3 | N | 1:36.9 → 1:52.4 | 15.5 s | ui | agent log; model calls with latency and tokens |
| 11 | 1:07.8 | N | 1:52.4 → 2:15.6 | 2.9 s | ui | the run finishes (8× speed, silent) |
| 12 | 1:10.7 | N | 2:15.6 → 2:28.2 | 12.6 s | ui | 'c1 looks good, shipped as the final version; now we can give it a try' |
| 13 | 1:23.3 | N | 2:34.4 → 2:47.3 | 12.9 s | ui | types: a ball falls from the sky, hits the ground, and wobbles |
| 14 | 1:36.2 | N | 2:51.1 → 3:05.4 | 14.3 s | ui | 'compare the new verb with the core library' |
| 15 | 1:50.5 | N | 3:05.9 → 3:42.4 | 4.6 s | ui | both sides plan (core-only first, same cast) (8× speed, silent) |
| 16 | 1:55.1 | N | 3:42.4 → 3:57.5 | 15.1 s | scene | 'without it the ball doesn't really wobble; the left shows a lively wobble' |
| 17 | 2:10.2 | C | 0:00.4 → 0:08.6 | 8.2 s | term | 'apply this MotionHarness MCP to Claude Code' (repo URL masked; caption “Claude Code using MotionHarness as an MCP tool”) |
| 18 | 2:18.4 | C | 0:11.8 → 0:16.0 | 4.2 s | term | claude mcp add (repo URL masked) |
| 19 | 2:22.6 | C | 1:08.8 → 1:12.8 | 4.0 s | term | start Claude Code |
| 20 | 2:26.6 | C | 1:17.4 → 1:27.7 | 10.3 s | term | types: use the motionharness tool to add a wobble verb |
| 21 | 2:36.9 | C | 1:27.7 → 3:21.9 | 7.1 s | term | the MCP run (16× speed, silent) |
| 22 | 2:44.0 | C | 3:22.0 → 3:24.0 | 2.0 s | term | 'we got the results back' |
| 23 | 2:46.0 | C | 3:28.4 → 3:39.0 | 10.6 s | term | Claude reports the run: tests, critique, plugin path |
| 24 | 2:56.6 | C | 3:48.5 → 3:49.6 | 1.1 s | term | 'thank you' |

Changes from v1: the dashboard part is the new Sandboxes take (the green pill, real image ids, c1 accepted at 9/10, and a
clearer comparison: without the new verb the ball doesn't really wobble). The Claude clip is trimmed to its essentials
(`claude mcp list` and the waits around it are cut) and opens on the terminal close-up, because the wide shot would show
the old "Sandboxes inactive" pill. The first lines of the terminal show `git push` output with the repository URL;
segments 17–18 paint over them.

Known issues: the Claude take predates two fixes, so its report quotes the critic's "8333% scale overshoot" (a measurement
bug, fixed) and its dashboard still says "Sandboxes inactive" (out of frame in this edit). Re-record it for a final cut.
A right-click menu flashes for about a second at 0:10.
