// MotionLang baked-tracks player (ES module, no dependencies).
// Shipped with the motionlang package: Python gets the source from motionlang.preview.PLAYER_JS
// (or the file path from motionlang.preview.player_js_path()). preview_html embeds this same file.
//
//   import { Player } from "./player.js";
//   const p = new Player(svgOrGroup, { onFrame(i, t, T) {}, onState(playing) {} });
//   p.load(tracks); p.play(); p.pause(); p.toggle(); p.seek(i); p.loop = false;

const NS = "http://www.w3.org/2000/svg";
const idx = (i, n) => Math.min(Math.max(i, 0), n - 1);
// Packed tracks: a constant is stored once; a per-frame track is an array (vector tracks: array of arrays).
const at = (v, i) => (Array.isArray(v) ? v[idx(i, v.length)] : v);
const atV = (m, i) => (Array.isArray(m[0]) ? m[idx(i, m.length)] : m);
let instances = 0;

export function el(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

export class Player {
  constructor(layer, { onFrame = () => {}, onState = () => {} } = {}) {
    this.layer = layer; // an <svg> or a <g> inside one
    this.onFrame = onFrame;
    this.onState = onState;
    this.T = null;
    this.nodes = [];
    this.playing = false;
    this.loop = true;
    this.i = 0;
    this.t0 = null;
    this.base = 0;
    this.uid = `ml${++instances}`; // keeps clipPath ids unique when a page has several players
    requestAnimationFrame(this._tick);
  }

  load(T) {
    this.T = T;
    const svg = this.layer.ownerSVGElement || this.layer;
    if (svg === this.layer) svg.setAttribute("viewBox", `0 0 ${T.canvas.width} ${T.canvas.height}`);
    this.layer.replaceChildren();
    this.layer.appendChild(el("rect", { width: T.canvas.width, height: T.canvas.height, fill: T.canvas.background || "#fff" }));
    this.nodes = T.actors.map((a, n) => {
      const g = a.geom;
      let e = null;
      if (g.tag === "rect") e = el("rect", { width: g.w, height: g.h, rx: g.r || 0 });
      else if (g.tag === "ellipse") e = el("ellipse", { cx: g.rx, cy: g.ry, rx: g.rx, ry: g.ry });
      else if (g.tag === "path") e = el("path", { d: g.d, pathLength: 1, "stroke-linecap": "round", "stroke-linejoin": "round", "fill-rule": g.fill_rule || "nonzero" });
      else if (g.tag === "text") {
        e = el("text", { x: 0, y: g.size * 0.8, "font-size": g.size, "font-family": g.font, "font-weight": g.weight });
        e.textContent = g.text;
      } else if (g.tag === "image") e = el("image", { href: g.url, width: g.w, height: g.h });
      let clip = null;
      if (e) {
        e.setAttribute("stroke-width", g.width || a.style.stroke_width);
        this.layer.appendChild(e);
        const c = a.tracks.clip;
        if (c && (Array.isArray(c[0]) || c.some((v) => v > 0))) {
          // reveal: a clip rect in the shape's own bbox-local space (it moves with the shape's transform)
          const id = `${this.uid}-clip-${n}`;
          const cp = el("clipPath", { id, clipPathUnits: "userSpaceOnUse" });
          clip = el("rect");
          cp.appendChild(clip);
          this.layer.appendChild(cp);
          e.setAttribute("clip-path", `url(#${id})`);
        }
      }
      return { a, e, clip };
    });
    this.seek(0);
  }

  get frames() { return this.T ? this.T.frames : 0; }

  frame(i) {
    const T = this.T;
    if (!T) return;
    this.i = idx(i, T.frames);
    for (const { a, e, clip } of this.nodes) {
      if (!e) continue;
      const tr = a.tracks, g = a.geom;
      e.setAttribute("transform", `matrix(${atV(tr.m, this.i).join(" ")})`);
      e.setAttribute("opacity", at(tr.opacity, this.i));
      const fill = at(tr.fill, this.i), stroke = at(tr.stroke, this.i), trim = at(tr.trim, this.i);
      e.setAttribute("fill", g.tag === "path" && !g.closed ? "none" : (fill ?? "none"));
      e.setAttribute("stroke", stroke ?? "none");
      if (g.tag === "path") {
        e.setAttribute("stroke-dasharray", "1 1");
        e.setAttribute("stroke-dashoffset", 1 - trim);
      }
      if (clip) {
        const [l, t, r, b] = atV(tr.clip, this.i), W = g.bw, H = g.bh;
        const pad = Math.max(W, H); // an untouched side doesn't cut strokes or glyph overhang
        const x0 = l > 0 ? l * W : -pad, y0 = t > 0 ? t * H : -pad;
        const x1 = r > 0 ? (1 - r) * W : W + pad, y1 = b > 0 ? (1 - b) * H : H + pad;
        clip.setAttribute("x", x0);
        clip.setAttribute("y", y0);
        clip.setAttribute("width", Math.max(0, x1 - x0));
        clip.setAttribute("height", Math.max(0, y1 - y0));
      }
    }
    this.onFrame(this.i, Math.min(this.i / T.fps, T.duration), T);
  }

  seek(i) { this.base = idx(i, this.frames); this.t0 = null; this.frame(this.base); }

  play() {
    if (!this.T) return;
    if (this.i >= this.frames - 1) this.i = 0;
    this.base = this.i; this.t0 = null; this.playing = true; this.onState(true);
  }

  pause() { this.playing = false; this.onState(false); }

  toggle() { this.playing ? this.pause() : this.play(); }

  _tick = (now) => {
    requestAnimationFrame(this._tick); // schedule first so one bad frame can't stop playback
    if (!this.playing || !this.T) return;
    // Chrome passes the frame's start time, which can precede a t0 taken with performance.now();
    // taking t0 from the first rAF timestamp and clamping at 0 keeps playback monotonic.
    if (this.t0 === null) this.t0 = now;
    let i = this.base + Math.max(0, Math.floor(((now - this.t0) / 1000) * this.T.fps));
    if (i >= this.T.frames) {
      if (this.loop) { this.base = 0; this.t0 = now; i = 0; }
      else { i = this.T.frames - 1; this.pause(); }
    }
    this.frame(i);
  };
}
