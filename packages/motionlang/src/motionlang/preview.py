"""Standalone HTML/SVG player for baked tracks (reference player; no dependencies)."""

from __future__ import annotations

import html
import json
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
<script>
const T = __TRACKS__;
const NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("stage");
svg.setAttribute("viewBox", `0 0 ${T.canvas.width} ${T.canvas.height}`);
const bg = document.createElementNS(NS, "rect");
Object.entries({ width: T.canvas.width, height: T.canvas.height, fill: T.canvas.background }).forEach(([k, v]) => bg.setAttribute(k, v));
svg.appendChild(bg);
// Packed tracks: a constant is stored once; a per-frame track is an array (matrices: array of arrays).
const idx = (i, n) => Math.min(Math.max(i, 0), n - 1);
const at = (v, i) => Array.isArray(v) ? v[idx(i, v.length)] : v;
const atM = (m, i) => Array.isArray(m[0]) ? m[idx(i, m.length)] : m;
function el(tag, attrs) { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; }
let nodes_n = 0;
const nodes = T.actors.map(a => {
  const g = a.geom; let e = null;
  if (g.tag === "rect") e = el("rect", { width: g.w, height: g.h, rx: g.r || 0 });
  else if (g.tag === "ellipse") e = el("ellipse", { cx: g.rx, cy: g.ry, rx: g.rx, ry: g.ry });
  else if (g.tag === "path") e = el("path", { d: g.d, pathLength: 1, "stroke-linecap": "round", "stroke-linejoin": "round", "fill-rule": g.fill_rule || "nonzero" });
  else if (g.tag === "text") { e = el("text", { x: 0, y: g.size * 0.8, "font-size": g.size, "font-family": g.font, "font-weight": g.weight }); e.textContent = g.text; }
  else if (g.tag === "image") e = el("image", { href: g.url, width: g.w, height: g.h });
  let clip = null;
  if (e) {
    e.setAttribute("stroke-width", g.width || a.style.stroke_width); svg.appendChild(e);
    const c = a.tracks.clip;
    if (Array.isArray(c[0]) || c.some(v => v > 0)) {  // reveal: a clip rect in the shape's own (bbox-local) space
      const cp = el("clipPath", { id: `clip-${nodes_n}`, clipPathUnits: "userSpaceOnUse" });
      clip = el("rect", {}); cp.appendChild(clip); svg.appendChild(cp);
      e.setAttribute("clip-path", `url(#clip-${nodes_n})`);
    }
  }
  nodes_n++;
  return { a, e, clip };
});
const scrub = document.getElementById("scrub"), time = document.getElementById("time"), play = document.getElementById("play"), loop = document.getElementById("loop");
scrub.max = T.frames - 1; loop.checked = true;
function frame(i) {
  for (const { a, e, clip } of nodes) {
    if (!e) continue;
    if (clip) {
      const [l, t, r, b] = atM(a.tracks.clip, i), W = a.geom.bw, H = a.geom.bh;
      const pad = Math.max(W, H);  // an untouched side doesn't cut strokes or glyph overhang
      const x0 = l > 0 ? l * W : -pad, y0 = t > 0 ? t * H : -pad, x1 = r > 0 ? (1 - r) * W : W + pad, y1 = b > 0 ? (1 - b) * H : H + pad;
      Object.entries({ x: x0, y: y0, width: Math.max(0, x1 - x0), height: Math.max(0, y1 - y0) }).forEach(([k, v]) => clip.setAttribute(k, v));
    }
    const m = atM(a.tracks.m, i), tr = a.tracks;
    e.setAttribute("transform", `matrix(${m.join(" ")})`);
    e.setAttribute("opacity", at(tr.opacity, i));
    const fill = at(tr.fill, i), stroke = at(tr.stroke, i), trim = at(tr.trim, i);
    e.setAttribute("fill", a.geom.tag === "path" && !a.geom.closed ? "none" : (fill ?? "none"));
    e.setAttribute("stroke", stroke ?? "none");
    if (a.geom.tag === "path") { e.setAttribute("stroke-dasharray", "1 1"); e.setAttribute("stroke-dashoffset", 1 - trim); }
  }
  scrub.value = i; time.textContent = `${Math.min(i / T.fps, T.duration).toFixed(2)} / ${T.duration.toFixed(2)}s`;
}
let playing = true, t0 = performance.now(), base = 0;
function tick(now) {
  requestAnimationFrame(tick);  // schedule first so one bad frame can't stop playback
  if (playing) {
    // rAF timestamps can precede t0 (Chrome passes the frame's start time), so clamp at 0.
    let i = base + Math.max(0, Math.floor((now - t0) / 1000 * T.fps));
    if (i >= T.frames) { if (loop.checked) { base = 0; t0 = now; i = 0; } else { i = T.frames - 1; playing = false; play.textContent = "Play"; } }
    frame(i);
  }
}
play.onclick = () => { playing = !playing; play.textContent = playing ? "Pause" : "Play"; base = +scrub.value; t0 = performance.now(); if (playing && base >= T.frames - 1) base = 0; };
scrub.oninput = () => { playing = false; play.textContent = "Play"; frame(+scrub.value); };
frame(0); requestAnimationFrame(tick);
</script>
</body>
</html>
"""


def preview_html(tracks: dict[str, Any], title: str = "MotionLang preview") -> str:
    data = json.dumps(tracks, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__TRACKS__", data)
