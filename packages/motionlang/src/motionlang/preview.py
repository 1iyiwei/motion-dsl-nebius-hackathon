"""Standalone HTML/SVG player for baked tracks (reference player; no dependencies)."""

from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from typing import Any

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg: #f4f4f2; --fg: #1d1d1b; --muted: #6b6b66; --line: #d8d8d2; --accent: #e4572e; }
  @media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #ececea; --muted: #9a9a94; --line: #34342f; } }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.4 system-ui, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; padding: 16px; }
  svg { width: 100%; height: auto; display: block; border: 1px solid var(--line); border-radius: 8px; }
  .bar { display: flex; gap: 12px; align-items: center; margin-top: 10px; }
  button { font: inherit; padding: 4px 12px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--fg); cursor: pointer; }
  input[type=range] { flex: 1; accent-color: var(--accent); }
  .t { font-variant-numeric: tabular-nums; color: var(--muted); min-width: 9ch; text-align: right; }
  h1 { font-size: 15px; font-weight: 600; margin: 0 0 10px; }
</style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <svg id="stage" xmlns="http://www.w3.org/2000/svg"></svg>
  <div class="bar">
    <button id="play">Pause</button>
    <input id="scrub" type="range" min="0" step="1" value="0">
    <span class="t" id="time"></span>
    <label><input id="loop" type="checkbox" checked> loop</label>
  </div>
</main>
<script type="module">
__PLAYER_JS__
const T = __TRACKS__;
const $ = (id) => document.getElementById(id);
const scrub = $("scrub"), time = $("time"), play = $("play"), loop = $("loop");
const player = new Player($("stage"), {
  onFrame: (i, t, T) => { scrub.value = i; time.textContent = `${t.toFixed(2)} / ${T.duration.toFixed(2)}s`; },
  onState: (playing) => { play.textContent = playing ? "Pause" : "Play"; },
});
player.load(T);
scrub.max = T.frames - 1;
loop.checked = true;
loop.onchange = () => { player.loop = loop.checked; };
play.onclick = () => player.toggle();
scrub.oninput = () => { player.pause(); player.seek(+scrub.value); };
player.play();
</script>
</body>
</html>
"""


def player_js_path() -> Path:
    """The packaged player (an ES module exporting `Player` and `el`), for apps to serve as a static file."""
    return Path(str(resources.files("motionlang").joinpath("static/player.js")))


PLAYER_JS: str = player_js_path().read_text()


def preview_html(tracks: dict[str, Any], title: str = "MotionLang preview") -> str:
    data = json.dumps(tracks, separators=(",", ":")).replace("</", "<\\/")
    return (
        _TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__PLAYER_JS__", PLAYER_JS.replace("</", "<\\/"))
        .replace("__TRACKS__", data)
    )
