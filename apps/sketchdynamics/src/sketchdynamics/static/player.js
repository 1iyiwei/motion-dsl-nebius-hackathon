// Baked-tracks player, ported from motionlang's reference player (motionlang/preview.py).
// TODO(core): replace with a player script exposed by motionlang (e.g. motionlang.preview.PLAYER_JS)
// so the two can't drift apart.

const NS = "http://www.w3.org/2000/svg";
const idx = (i, n) => Math.min(Math.max(i, 0), n - 1);
// Packed tracks: a constant is stored once; a per-frame track is an array (matrices: array of arrays).
const at = (v, i) => (Array.isArray(v) ? v[idx(i, v.length)] : v);
const atM = (m, i) => (Array.isArray(m[0]) ? m[idx(i, m.length)] : m);

export function el(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

export class Player {
  constructor(layer, { onFrame = () => {}, onState = () => {} } = {}) {
    this.layer = layer;
    this.onFrame = onFrame;
    this.onState = onState;
    this.T = null;
    this.nodes = [];
    this.playing = false;
    this.loop = true;
    this.i = 0;
    this.t0 = null;
    this.base = 0;
    requestAnimationFrame(this._tick);
  }

  load(T) {
    this.T = T;
    this.layer.replaceChildren();
    this.layer.appendChild(el("rect", { width: T.canvas.width, height: T.canvas.height, fill: T.canvas.background || "#fff" }));
    this.nodes = T.actors.map((a) => {
      const g = a.geom;
      let e = null;
      if (g.tag === "rect") e = el("rect", { width: g.w, height: g.h, rx: g.r || 0 });
      else if (g.tag === "ellipse") e = el("ellipse", { cx: g.rx, cy: g.ry, rx: g.rx, ry: g.ry });
      else if (g.tag === "path") e = el("path", { d: g.d, pathLength: 1, "stroke-linecap": "round", "stroke-linejoin": "round" });
      else if (g.tag === "text") {
        e = el("text", { x: 0, y: g.size * 0.8, "font-size": g.size, "font-family": g.font, "font-weight": g.weight });
        e.textContent = g.text;
      } else if (g.tag === "image") e = el("image", { href: g.url, width: g.w, height: g.h });
      if (e) {
        e.setAttribute("stroke-width", g.width || a.style.stroke_width);
        this.layer.appendChild(e);
      }
      return { a, e };
    });
    this.seek(0);
    this.play();
  }

  get frames() { return this.T ? this.T.frames : 0; }

  frame(i) {
    const T = this.T;
    if (!T) return;
    this.i = idx(i, T.frames);
    for (const { a, e } of this.nodes) {
      if (!e) continue;
      const tr = a.tracks;
      e.setAttribute("transform", `matrix(${atM(tr.m, this.i).join(" ")})`);
      e.setAttribute("opacity", at(tr.opacity, this.i));
      const fill = at(tr.fill, this.i), stroke = at(tr.stroke, this.i), trim = at(tr.trim, this.i);
      e.setAttribute("fill", a.geom.tag === "path" && !a.geom.closed ? "none" : (fill ?? "none"));
      e.setAttribute("stroke", stroke ?? "none");
      if (a.geom.tag === "path") {
        e.setAttribute("stroke-dasharray", "1 1");
        e.setAttribute("stroke-dashoffset", 1 - trim);
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
