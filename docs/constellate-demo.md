# Constellate: 3-minute demo plan

Run the app over Tailscale so the phone works: `uv run constellate serve --no-browser`, then `tailscale serve --bg --https=10000 http://127.0.0.1:8780`, and open `https://liyiwei-m5.taileef945.ts.net:10000/` in Chrome, full window. Record the screen; film the phone (or mirror it with scrcpy) for the live segment. Cut the model waits in editing: the agent log shows real latencies, so shortening the waits is honest. The track asks for ≥ 1 minute of the key modules in action when there's no hardware; this plan has about 2:20.

## Measured timings (Oct 24, live)

| Step | Model / work | Time |
| --- | --- | --- |
| Site briefing (Marina Green, Oct 31) | Nano queries (~2 s) + 4 Tavily searches + Super reads 13–15 sources (~3.5 s) | ~10 s; ceiling 122 m, setback 61 m, wind 5.1 m/s → design 8 m/s |
| Plan show from a request | Ultra, JSON mode | 2.5–4 s; compile + check another ~3 s |
| Make flyable, unsafe example | 1 Nano round (1.9 s) + sweep (~3 s) | ~8 s → certified to 8 m/s |
| Make flyable, drawn line + logo + ring | 4 Nano rounds (~1.5 s each) + sweep | ~20 s → certified to 10 m/s |
| Tweak ("use 80 drones", "end in blue") | Nano | ~1.3 s |
| Night-sky video, 85 s show | sim + render | ~11 s |
| Live mode | real-time sim, 30 fps | ~0.96× real time |

## Shot list

| Time | Beat | On screen | Voice-over idea |
| --- | --- | --- | --- |
| 0:00–0:12 | Hook | The night-sky MP4: heart → spin → logo in its colours → THANK YOU, over the tree line | "A drone show is motion graphics made of a hundred aircraft. Designing one is slow, expensive and safety-critical." |
| 0:12–0:27 | Site briefing | Type "Marina Green, San Francisco", "October 31, 9 pm", Brief; findings appear with ✓ and source links; the limits line updates | "Tavily finds the venue's rules and weather; Nemotron Super turns them into limits, each with a quote we check against its source." |
| 0:27–0:55 | Design | Draw a stroke, add the sample logo, say "the drones rise into my line, sweep into the logo, then finish as a slowly spinning blue ring", Plan show; the swarm flies on the stage | "Nemotron Ultra makes the artistic choices. A compiler assigns every drone and flies collision-free straight lines (CAPT)." |
| 0:55–1:25 | What would go wrong | Red step chips and badges; click the E_SEPARATION problem (the staggered sweep brings two drones 0.02 m apart): the stage jumps there and circles them; read one hint aloud | "Then the flight checker measures everything in metres, and every problem comes with the exact fix." |
| 1:25–1:55 | Make it flyable | Make flyable: the agent log shows Nano edits (`set /steps/1/style = "direct"`, sizes, durations), badges turn green, the heatmap fills, the certificate appears | "Nemotron Nano applies the fixes in about a second each; then 84 simulated flights in wind certify it: flyable up to 10 m/s." |
| 1:55–2:35 | Be the wind | Go live; scan the QR with the phone; tilt (streaks, drift lines), shake hard (the formation bends, a near miss is circled); split screen phone + big screen | "Live, the same physics runs in real time, and my phone is the wind. Tilt to steer it, shake for a gust." |
| 2:35–2:50 | Watch it | Render night-sky video; play a few seconds of the simulated flight | "And this is the flight, simulated in the design wind, from the ground." |
| 2:50–3:00 | Under the hood | Agent log totals (calls, tokens, seconds) and a title card: Nemotron Ultra · Super · Nano on Nebius Token Factory, Tavily, MotionLang | Track, repo link |

## Before each take

- Reload the page (Clear resets the canvas, but a reload also clears the agent log).
- Phone: Tailscale on, the pad page closed from the last take (a new session gets a new code); screen brightness up.
- Prefer the drawn-line example for the "what goes wrong" beat: its staggered sweep reliably produces an E_SEPARATION at 0.02 m. If Ultra plans it differently, load **Examples… → Heart, spin, logo** instead (speed, acceleration and airspace problems, one Nano round).
- For the live beat, hold a strong tilt, then shake: the gust is strongest when it adds to a steady wind.
- Keep the design wind at 8 m/s (from the briefing) so the certificate reads "up to 8" or "up to 10" rather than the default 6.
