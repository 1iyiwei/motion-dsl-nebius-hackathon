import { Player, el } from "/player.js"; // served from the motionlang package

const $ = (id) => document.getElementById(id);
const PPM = 10; // canvas pixels per metre, as in compiler.py
const stage = $("stage"), ink = $("ink"), hl = $("hl");
const state = {
  limits: null, strokes: [], svgs: {}, show: null, res: null, sweep: null, busy: false,
  calls: [], briefing: null, stepIssues: {},
};

const player = new Player($("anim"), {
  onFrame(i, t, T) {
    $("scrub").value = i;
    $("time").textContent = `${t.toFixed(1)} / ${T.duration.toFixed(1)} s`;
    markChip(t);
  },
  onState(playing) { $("play").textContent = playing ? "❚❚" : "▶"; },
});
$("play").onclick = () => player.toggle();
$("scrub").oninput = () => { player.pause(); player.seek(+$("scrub").value); };
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !(e.target instanceof HTMLInputElement) && player.T) { e.preventDefault(); player.toggle(); }
});

// ---------- canvas size follows the airspace ----------
const W = () => state.limits.width * PPM, H = () => state.limits.height * PPM;
// The view keeps the airspace's aspect ratio; zooming out shows drones that leave it (outside the dashed geofence).
let view = null;
function setView(cx, cy, w) {
  w = Math.min(Math.max(w, W() * 0.25), W() * 6);
  const h = w * H() / W();
  view = { cx, cy, w };
  stage.setAttribute("viewBox", `${cx - w / 2} ${cy - h / 2} ${w} ${h}`);
}
let sized = "";
function sizeStage() {
  for (const [k, v] of Object.entries({ width: W(), height: H() })) $("fence").setAttribute(k, v);
  if (sized !== `${W()}x${H()}`) { sized = `${W()}x${H()}`; setView(W() / 2, H() / 2, W() * 1.06); } // keep the user's zoom otherwise
  $("scale").textContent = `airspace ${state.limits.width} × ${state.limits.height} m · design wind ${state.limits.design_wind} m/s`;
}
function tracksExtent(T) {
  let x0 = 0, y0 = 0, x1 = W(), y1 = H();
  for (const a of T?.actors || []) {
    const ms = Array.isArray(a.tracks.m[0]) ? a.tracks.m : [a.tracks.m];
    for (const m of ms) { x0 = Math.min(x0, m[4]); y0 = Math.min(y0, m[5]); x1 = Math.max(x1, m[4] + a.geom.bw); y1 = Math.max(y1, m[5] + a.geom.bh); }
  }
  return [x0, y0, x1, y1];
}
function fitView() {
  const [x0, y0, x1, y1] = tracksExtent(player.T);
  const w = Math.max(x1 - x0, (y1 - y0) * W() / H()) * 1.06;
  setView((x0 + x1) / 2, (y0 + y1) / 2, w);
}
$("zoom-in").onclick = () => setView(view.cx, view.cy, view.w / 1.3);
$("zoom-out").onclick = () => setView(view.cx, view.cy, view.w * 1.3);
$("zoom-fit").onclick = fitView;

// ---------- sketching (copied from SketchDynamics app.js; moves to motionlang/static/sketch.js after judging) ----------
function toCanvas(ev) {
  const p = stage.createSVGPoint();
  p.x = ev.clientX; p.y = ev.clientY;
  const q = p.matrixTransform(stage.getScreenCTM().inverse());
  return [Math.min(Math.max(q.x, 0), W()), Math.min(Math.max(q.y, 0), H())];
}
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function rdp(pts, eps) {
  if (pts.length < 3) return pts;
  const [a, b] = [pts[0], pts[pts.length - 1]];
  const len = dist(a, b) || 1;
  let worst = 0, k = 0;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i];
    const d = Math.abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / len;
    if (d > worst) { worst = d; k = i; }
  }
  if (worst <= eps) return [a, b];
  return [...rdp(pts.slice(0, k + 1), eps).slice(0, -1), ...rdp(pts.slice(k), eps)];
}
function simplify(pts) {
  let out = rdp(pts, 2.5);
  const MAX = 64;
  if (out.length > MAX) out = Array.from({ length: MAX }, (_, i) => out[Math.round((i * (out.length - 1)) / (MAX - 1))]);
  return out.map(([x, y]) => [Math.round(x), Math.round(y)]);
}
const pathD = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
function renderInk() {
  ink.replaceChildren();
  for (const s of state.strokes) {
    ink.appendChild(el("path", { d: pathD(s.points) }));
    const t = el("text", { x: s.points[0][0] + 10, y: s.points[0][1] - 12 });
    t.textContent = s.id;
    ink.appendChild(t);
  }
  $("hint").hidden = state.strokes.length > 0 || !!player.T || Object.keys(state.svgs).length > 0;
  renderSources();
}
let drawing = null;
stage.addEventListener("pointerdown", (ev) => {
  if (state.busy || ev.button !== 0) return;
  try { stage.setPointerCapture(ev.pointerId); } catch { /* synthetic pointer */ }
  drawing = { pts: [toCanvas(ev)], node: el("path", {}) };
  ink.appendChild(drawing.node);
  $("hint").hidden = true;
});
stage.addEventListener("pointermove", (ev) => {
  if (!drawing) return;
  const coalesced = ev.getCoalescedEvents?.() ?? [];
  for (const e of coalesced.length ? coalesced : [ev]) {
    const p = toCanvas(e);
    if (dist(p, drawing.pts[drawing.pts.length - 1]) >= 3) drawing.pts.push(p);
  }
  drawing.node.setAttribute("d", pathD(drawing.pts));
});
function endStroke() {
  if (!drawing) return;
  const pts = simplify(drawing.pts);
  drawing = null;
  if (pts.length >= 2 && dist(pts[0], pts[pts.length - 1]) + pts.length > 6) {
    const n = state.strokes.reduce((m, s) => Math.max(m, +s.id.slice(1) || 0), 0) + 1;
    state.strokes.push({ id: `s${n}`, points: pts });
  }
  renderInk();
}
stage.addEventListener("pointerup", endStroke);
stage.addEventListener("pointercancel", endStroke);
$("undo-stroke").onclick = () => { state.strokes.pop(); renderInk(); };
$("clear").onclick = () => {
  state.strokes = []; state.svgs = {}; state.show = null; state.res = null; state.sweep = null;
  player.pause(); player.T = null; $("anim").replaceChildren(); hl.replaceChildren();
  $("script").hidden = true; $("play").disabled = $("scrub").disabled = true; $("video").disabled = true;
  $("player-video").hidden = true; $("time").textContent = "0.0 / 0.0 s"; $("scrub").value = 0;
  renderInk(); renderFlight(null); renderSweep(null); setStatus("");
};

const strokesMetres = () => Object.fromEntries(state.strokes.map((s) => [s.id, s.points.map(([x, y]) => [+(x / PPM).toFixed(2), +((H() - y) / PPM).toFixed(2)])]));

// ---------- artwork ----------
const slug = (name) => name.replace(/\.svg$/i, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "logo";
function addSvg(name, text) {
  if (!/<svg[\s>]/i.test(text)) { setStatus(`${name} isn't an SVG file`, true); return; }
  state.svgs[slug(name)] = text;
  renderInk();
  setStatus(`Added ${name} as artwork “${slug(name)}”. Mention it in the request, e.g. “then morph into the logo”.`);
}
function renderSources() {
  const box = $("sources");
  box.replaceChildren();
  for (const [id, text] of Object.entries(state.svgs)) {
    const s = document.createElement("span");
    s.className = "source";
    const img = document.createElement("img");
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(text);
    const x = document.createElement("button");
    x.textContent = "✕"; x.title = "Remove";
    x.onclick = () => { delete state.svgs[id]; renderInk(); };
    s.append(img, `svg “${id}”`, x);
    box.appendChild(s);
  }
  for (const st of state.strokes) {
    const s = document.createElement("span");
    s.className = "source";
    s.textContent = `stroke “${st.id}” (${st.points.length} pts)`;
    box.appendChild(s);
  }
}
$("logo-pick").onclick = () => $("logo-file").click();
$("logo-file").onchange = async (e) => { const f = e.target.files[0]; if (f) addSvg(f.name, await f.text()); e.target.value = ""; };
$("sample-logo").onclick = async () => addSvg("orbit.svg", await (await fetch("/api/samples/orbit-mark.svg")).text());
const wrap = document.querySelector(".stage-wrap");
wrap.addEventListener("dragover", (e) => { e.preventDefault(); wrap.classList.add("drop"); });
wrap.addEventListener("dragleave", () => wrap.classList.remove("drop"));
wrap.addEventListener("drop", async (e) => {
  e.preventDefault(); wrap.classList.remove("drop");
  for (const f of e.dataTransfer.files) {
    if (f.name.endsWith(".json")) { try { await loadShow(JSON.parse(await f.text())); } catch (err) { setStatus(String(err), true); } }
    else addSvg(f.name, await f.text());
  }
});

// ---------- server calls ----------
// A hosted copy may need an access code (GET /config says so); it's kept in this browser only.
const ACCESS_KEY = "constellate.access";
function headers() {
  let code = "";
  try { code = localStorage.getItem(ACCESS_KEY) || ""; } catch { /* storage blocked */ }
  return { "Content-Type": "application/json", ...(code ? { "X-Access-Code": code } : {}) };
}
async function failure(r) {
  if (r.status === 401) { $("access-code").hidden = false; $("access-code").focus(); }
  return new Error((await r.json().catch(() => ({}))).detail || `${r.status} ${r.statusText}`);
}
async function api(url, body) {
  const r = await fetch(url, { method: body ? "POST" : "GET", headers: headers(), body: body && JSON.stringify(body) });
  if (!r.ok) throw await failure(r);
  return r.json();
}
async function stream(url, body) {
  const r = await fetch(url, { method: "POST", headers: headers(), body: JSON.stringify(body) });
  if (!r.ok) throw await failure(r);
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "", result = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const ev = JSON.parse(buf.slice(0, nl)); buf = buf.slice(nl + 1);
      if (ev.type === "result") result = ev;
      else if (ev.type === "error" && !result) { logEvent(ev); throw new Error(ev.msg); }
      else onEvent(ev);
    }
  }
  if (!result) throw new Error("The server closed the connection before finishing (it may have restarted or run out of memory). Try again; if it keeps happening, check the server log.");
  return result;
}
function setBusy(on, text = "Working…") {
  state.busy = on;
  $("busy").hidden = !on; $("busy-text").textContent = text;
  for (const id of ["plan", "tweak", "fly", "brief", "video"]) if (on) $(id).disabled = true;
  if (!on) { $("plan").disabled = $("brief").disabled = false; $("tweak").disabled = $("fly").disabled = !state.show; $("video").disabled = $("live-start").disabled = !state.show; }
}
function setStatus(text, bad = false) { $("status").textContent = text; $("status").classList.toggle("bad", bad); }

// ---------- agent log ----------
function onEvent(ev) {
  if (ev.type === "ping") return; // keep-alive from the server
  if (ev.type === "sim_progress") { $("busy-text").textContent = `Simulating flights in wind: ${ev.done}/${ev.total}…`; return; }
  if (ev.type === "call_start") { $("busy-text").textContent = ev.msg + "…"; return; }
  if (ev.type === "sim_start") { $("busy-text").textContent = "Simulating flights in wind…"; }
  if (ev.type === "search") $("busy-text").textContent = ev.msg;
  if (ev.type === "flight" && ev.issues) renderFlight({ issues: ev.issues, stats: ev.stats, ok: !ev.issues.some((i) => (i.severity || "error") === "error") });
  if (ev.type === "sim") renderSweepEvent(ev);
  logEvent(ev);
}
function modelClass(m = "") { m = m.toLowerCase(); return m.includes("ultra") ? "ultra" : m.includes("nano") ? "nano" : "super"; }
function logEvent(ev) {
  const d = document.createElement("div");
  d.className = "ev";
  const pill = (cls, text) => `<span class="pill ${cls}">${text}</span>`;
  const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  if (ev.type === "call") {
    state.calls.push(ev);
    const mc = modelClass(ev.model);
    d.innerHTML = `${pill(mc, { ultra: "Ultra 550B", nano: "Nano 30B", super: "Super 120B" }[mc])}${esc(ev.purpose)} · ${ev.seconds}s · ${ev.tokens[0]}→${ev.tokens[1]} tok${ev.think ? "" : " · no reasoning"}`;
    renderTotals();
  } else if (ev.type === "search") d.innerHTML = pill("tavily", "Tavily") + esc(ev.msg.replace(/^Tavily: /, ""));
  else if (ev.type === "repair") d.innerHTML = pill("edit", "edit") + esc(ev.msg) + (ev.ops?.length ? `<code>${esc(ev.ops.map((o) => `${o.op || "set"} ${o.path}${"value" in o ? " = " + JSON.stringify(o.value) : ""}`).join("\n"))}</code>` : "");
  else if (ev.type === "error") d.innerHTML = pill("error", "error") + esc(ev.msg);
  else d.innerHTML = pill("check", ev.type) + esc(ev.msg);
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
}
function renderTotals() {
  const c = state.calls;
  const tok = c.reduce((s, x) => s + x.tokens[0] + x.tokens[1], 0), sec = c.reduce((s, x) => s + x.seconds, 0);
  $("totals").textContent = `${c.length} Nemotron calls · ${tok.toLocaleString()} tokens · ${sec.toFixed(1)} s of model time`;
}

// ---------- results ----------
async function applyResult(r) {
  if (live.code) stopLive();
  if (r.tracks_url) r.tracks = await api(r.tracks_url); // fetched separately: tracks are large
  state.res = r;
  state.show = r.show;
  $("drones").value = r.show.drones;
  state.limits = r.show.limits;
  sizeStage();
  if (r.tracks) {
    player.load(r.tracks);
    const [x0, y0, x1, y1] = tracksExtent(r.tracks);
    if (x0 < 0 || y0 < 0 || x1 > W() || y1 > H()) fitView(); // some drones leave the airspace: show them
    $("scrub").max = r.tracks.frames - 1;
    $("play").disabled = $("scrub").disabled = false;
    player.play();
  }
  hl.replaceChildren();
  $("hint").hidden = true;
  $("script").hidden = false;
  const { sources, limits, ...script } = r.show;
  $("script-json").textContent = JSON.stringify(script, null, 2);
  if (r.sweep) renderSweep(r.sweep); else renderSweep(null);
  renderFlight(r.flight);
  renderLimits();
}
async function loadShow(show) {
  setBusy(true, "Compiling and checking…");
  try { await applyResult(await api("/api/check", { show })); setStatus("Loaded. Press “Make flyable” to repair and simulate it."); }
  finally { setBusy(false); }
}

const BADGES = [
  ["Separation", ["E_SEPARATION", "E_DENSITY"], (s) => `${s.min_separation} m min`],
  ["Speed", ["E_SPEED"], (s) => `${s.peak_speed} m/s peak`],
  ["Acceleration", ["E_ACCEL"], (s) => `${s.peak_accel} m/s² peak`],
  ["Airspace", ["E_GEOFENCE"], () => "in bounds"],
  ["Battery", ["E_FLIGHT_TIME", "E_SIM_BATTERY"], (s) => `${Math.round((s.battery_used || 0) * 100)}% used`],
  ["Wind", ["E_SIM_TRACKING", "E_SIM_NEAR_MISS"], () => "sweep not run yet"],
];
function renderFlight(f) {
  const box = $("badges"), list = $("issues");
  box.replaceChildren(); list.replaceChildren();
  state.stepIssues = {};
  if (!f) return;
  const all = [...f.issues, ...(state.simIssues || [])];
  for (const [name, codes, val] of BADGES) {
    const hits = all.filter((i) => codes.includes(i.code));
    const bad = hits.some((i) => (i.severity || "error") === "error");
    const b = document.createElement("div");
    b.className = "badge " + (bad ? "bad" : hits.length ? "warn" : "ok");
    let v = val(f.stats || {});
    if (name === "Wind" && state.sweep) v = state.sweep.certificate.flyable ? `≤ ${state.sweep.certificate.certified_wind} m/s` : `fails at ${state.sweep.certificate.design_wind} m/s`;
    if (name === "Wind" && !state.sweep && !hits.length) { b.className = "badge"; b.title = "Make flyable runs the wind sweep; live mode always simulates wind"; }
    b.innerHTML = `<b>${name}</b>${hits.length ? `${hits.length} issue${hits.length > 1 ? "s" : ""}` : v}`;
    box.appendChild(b);
  }
  for (const i of all) {
    if (i.block) state.stepIssues[i.block] = (state.stepIssues[i.block] === "bad" || (i.severity || "error") === "error") ? "bad" : "warn";
    const d = document.createElement("div");
    d.className = "issue " + (i.severity || "error");
    d.innerHTML = `<span class="code">${i.code}</span> ${escapeHtml(i.message)}<span class="hint">→ ${escapeHtml(i.hint || "")}</span>`;
    d.onclick = () => focusIssue(i);
    list.appendChild(d);
  }
  if (!all.length) list.innerHTML = `<span class="muted">No problems: within the speed, acceleration, separation and airspace limits.</span>`;
  renderChips();
}
const escapeHtml = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

// ---------- issue focus: seek and ring the drones ----------
function actorCenter(id, frame) {
  const a = player.T?.actors.find((x) => x.id === id);
  if (!a) return null;
  const m = Array.isArray(a.tracks.m[0]) ? a.tracks.m[Math.min(frame, a.tracks.m.length - 1)] : a.tracks.m;
  const [bw, bh] = [a.geom.bw, a.geom.bh];
  return [m[0] * bw / 2 + m[2] * bh / 2 + m[4], m[1] * bw / 2 + m[3] * bh / 2 + m[5]];
}
function focusIssue(i) {
  if (!player.T || i.t == null) return;
  const frame = Math.round(i.t * player.T.fps);
  player.pause(); player.seek(frame);
  hl.replaceChildren();
  const ids = [...new Set([i.actor, ...(i.message.match(/\bd\d+\b/g) || [])].filter(Boolean))].slice(0, 2);
  const pts = ids.map((id) => actorCenter(id, frame)).filter(Boolean);
  if (pts.length === 2) hl.appendChild(el("line", { x1: pts[0][0], y1: pts[0][1], x2: pts[1][0], y2: pts[1][1] }));
  for (const p of pts) hl.appendChild(el("circle", { cx: p[0], cy: p[1], r: 22 }));
}

// ---------- script chips ----------
function chipTitle(step) {
  if (step.kind === "form") return [`→ ${step.formation}`, `${step.transition}s ${step.style !== "direct" ? step.style + " " : ""}+ hold ${step.hold}s`];
  if (step.kind === "spin") return [`⟳ spin ${step.turns}`, `${step.duration}s`];
  if (step.kind === "color") return [`● ${step.color}`, `${step.duration}s`];
  return [`hold`, `${step.duration}s`];
}
function renderChips() {
  const box = $("chips");
  box.replaceChildren();
  if (!state.res) return;
  for (const sp of state.res.steps) {
    const step = sp.index != null ? state.show.steps[sp.index] : null;
    const [title, meta] = step ? chipTitle(step) : ["↓ land", `${(sp.t1 - sp.t0).toFixed(1)}s (automatic)`];
    const c = document.createElement("button");
    c.type = "button";
    c.className = "chip" + (step ? "" : " land") + (state.stepIssues[sp.id] ? " " + state.stepIssues[sp.id] : "");
    c.dataset.t0 = sp.t0; c.dataset.t1 = sp.t1;
    c.innerHTML = `<b>${escapeHtml(title)}</b><span class="meta">${escapeHtml(sp.id)} · ${escapeHtml(meta)}</span>`;
    c.onclick = () => { if (player.T) { player.seek(Math.round(sp.t0 * player.T.fps)); hl.replaceChildren(); } };
    box.appendChild(c);
  }
}
function markChip(t) {
  for (const c of $("chips").children) c.classList.toggle("now", t >= +c.dataset.t0 && t < +c.dataset.t1);
}

// ---------- sweep ----------
function renderSweepEvent(ev) {
  state.sweep = { certificate: ev.certificate, by_wind: ev.by_wind, runs: null };
  state.simIssues = ev.issues || [];
  renderSweep(state.sweep);
  if (state.res) renderFlight(state.res.flight);
}
function renderSweep(sw) {
  const cert = $("certificate"), hm = $("heatmap");
  hm.replaceChildren();
  if (!sw) {
    state.sweep = null; state.simIssues = [];
    cert.innerHTML = `<span class="muted">Run “Make flyable” to simulate the show in wind.</span>`;
    return;
  }
  state.sweep = sw;
  const c = sw.certificate;
  cert.innerHTML = `<div class="verdict ${c.flyable ? "ok" : "bad"}">${c.flyable ? "Flyable" : "Not certified"} up to ${c.certified_wind ?? 0} m/s</div>
    <dl><dt>design wind</dt><dd>${c.design_wind} m/s</dd><dt>simulated flights</dt><dd>${c.runs}</dd>
    <dt>near misses</dt><dd>${c.near_misses_at_design_wind}</dd><dt>worst separation</dt><dd>${c.worst_separation} m</dd>
    <dt>max drift</dt><dd>${c.max_tracking_error} m</dd><dt>battery left</dt><dd>${Math.round(c.battery_left * 100)}%</dd>
    <dt>from crowd</dt><dd>≥ ${c.closest_to_crowd} m</dd></dl><div class="muted" style="font-size:11px;margin-top:4px">${escapeHtml(c.note)}</div>`;
  const winds = Object.keys(sw.by_wind).map(Number).sort((a, b) => b - a);
  for (const w of winds) {
    const row = document.createElement("div");
    row.className = "row" + (w === c.design_wind ? " design" : "");
    const v = sw.by_wind[String(w)] || sw.by_wind[w.toFixed(1)];
    const label = document.createElement("span");
    label.textContent = `${w} m/s`;
    row.appendChild(label);
    const runs = sw.runs ? sw.runs.filter((r) => r.wind === w) : null;
    const n = v.runs;
    for (let k = 0; k < n; k++) {
      const cell = document.createElement("span");
      let cls = "cell";
      if (runs) { const r = runs[k]; if (!r.ok) cls += r.near_pairs ? " near" : " drift"; cell.title = `seed ${r.seed}, gusts from ${r.direction}°: drift ${r.max_err} m, min sep ${r.min_sep} m`; }
      else if (k >= v.passed) cls += v.near_pairs ? " near" : " drift";
      cell.className = cls;
      row.appendChild(cell);
    }
    hm.appendChild(row);
  }
  const lg = document.createElement("div");
  lg.className = "legend";
  lg.innerHTML = `<span><i style="background:var(--ok)"></i>pass</span><span><i style="background:var(--warn)"></i>drift &gt; ${state.limits.max_tracking_error} m</span><span><i style="background:var(--bad)"></i>near miss</span>`;
  hm.appendChild(lg);
}

// ---------- briefing ----------
function renderLimits() {
  const l = state.limits;
  $("limits").innerHTML = `Limits: <b>${l.width}×${l.height} m</b> airspace · separation <b>${l.min_separation} m</b> · <b>${l.max_speed} m/s</b>, <b>${l.max_accel} m/s²</b> · design wind <b>${l.design_wind} m/s</b> · crowd <b>${l.crowd_setback} m</b>`;
  sizeStage();
}
function renderBriefing(b) {
  const box = $("briefing");
  box.replaceChildren();
  const names = { ceiling: "Altitude ceiling", crowd_setback: "Crowd setback", typical_wind: "Typical wind" };
  for (const f of b.findings) {
    const d = document.createElement("div");
    d.className = "finding " + (f.value == null ? "" : f.verified ? "ok" : "unverified");
    const src = f.source ? ` — <a href="${f.source.url}" target="_blank" rel="noopener">${escapeHtml(f.source.title || f.source.url)}</a>` : "";
    d.innerHTML = `<b>${names[f.topic]}</b>: ${f.value == null ? "not found" : `${f.value} ${f.unit}`} ${f.verified ? "✓" : f.value == null ? "" : "(unverified)"}${src}
      ${f.quote ? `<q>${escapeHtml(f.quote)}</q>` : ""}<span class="muted">${escapeHtml(f.note)}</span>`;
    box.appendChild(d);
  }
  for (const a of b.airspace) {
    const d = document.createElement("div");
    d.className = "finding";
    d.innerHTML = `⚠ ${escapeHtml(a.note)}${a.source ? ` — <a href="${a.source.url}" target="_blank" rel="noopener">source</a>` : ""}`;
    box.appendChild(d);
  }
}
$("brief-form").onsubmit = async (e) => {
  e.preventDefault();
  const venue = $("venue").value.trim(), date = $("date").value.trim();
  if (!venue || !date) { setStatus("Enter a venue and a date to brief.", true); return; }
  setBusy(true, "Searching the site's rules and weather…");
  try {
    const r = await stream("/api/brief", { venue, date });
    state.briefing = r.briefing;
    Object.assign(state.limits, r.briefing.limits);
    renderBriefing(r.briefing); renderLimits();
    setStatus(`Briefing applied: ${Object.keys(r.briefing.limits).length} limits set from cited sources.`);
    if (state.show) { state.show.limits = { ...state.show.limits, ...r.briefing.limits }; await loadShow(state.show); }
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { setBusy(false); }
};

// ---------- plan / tweak / fly ----------
$("plan-form").onsubmit = async (e) => {
  e.preventDefault();
  const intent = $("intent").value.trim();
  if (!intent) { setStatus("Say or type what the swarm should do.", true); return; }
  setBusy(true, "Nemotron is choreographing…");
  try {
    const r = await stream("/api/plan", { intent, drones: +$("drones").value || 100, sources: { strokes: strokesMetres(), svgs: state.svgs }, limits: state.limits });
    state.sweep = null; state.simIssues = [];
    await applyResult(r);
    setStatus(r.ok ? "Planned, and it already passes the flight check. Press “Make flyable” to simulate wind." : "Planned. The flight check found problems (red); press “Make flyable” to let Nemotron fix them.");
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { setBusy(false); }
};
$("tweak-form").onsubmit = async (e) => {
  e.preventDefault();
  const request = $("instruction").value.trim();
  if (!request || !state.show) return;
  setBusy(true, "Editing…");
  try {
    const r = await stream("/api/tweak", { show: state.show, request });
    state.sweep = null; state.simIssues = [];
    await applyResult(r);
    $("instruction").value = "";
    setStatus(`${r.note || "Changed."} ${r.ok ? "Flight check passes." : "The flight check found problems."}`);
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { setBusy(false); }
};
$("fly").onclick = async () => {
  if (!state.show) return;
  setBusy(true, "Checking…");
  try {
    const r = await stream("/api/fly", { show: state.show });
    await applyResult(r);
    const c = r.sweep?.certificate;
    setStatus(r.flyable ? `Flyable after ${r.rounds} repair round${r.rounds === 1 ? "" : "s"}: certified up to ${c.certified_wind} m/s.` : "Not flyable yet; see the problems on the right.", !r.flyable);
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { setBusy(false); }
};

// The fleet size is the user's call: changing it recompiles the current show (the repair loop never changes it).
$("drones").onchange = async () => {
  const n = Math.round(+$("drones").value);
  if (!state.show || !(n >= 10 && n <= 500) || n === state.show.drones || state.busy) return;
  state.sweep = null; state.simIssues = [];
  try { await loadShow({ ...state.show, drones: n }); setStatus(`Now ${n} drones. ${state.res.ok ? "Flight check passes." : "The flight check found problems; “Make flyable” fixes them."}`); }
  catch (err) { setStatus(String(err.message || err), true); }
};

// ---------- export ----------
const exportTitle = () => state.show?.title || "Drone show";
const exportSlug = () => exportTitle().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "drone-show";
function download(href, name) {
  const a = Object.assign(document.createElement("a"), { href, download: name });
  document.body.appendChild(a); a.click(); a.remove();
}
$("export-html").onclick = () => { if (state.res?.tracks_url) download(`${state.res.tracks_url}/export.html?title=${encodeURIComponent(exportTitle())}`, `${exportSlug()}.html`); };
$("export-motion").onclick = () => { if (state.res?.tracks_url) download(`${state.res.tracks_url}/export.motion.json?title=${encodeURIComponent(exportTitle())}`, `${exportSlug()}.motion.json`); };
$("export-script").onclick = () => {
  if (!state.show) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.show, null, 2)], { type: "application/json" }));
  download(url, `${exportSlug()}.show.json`);
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};

// ---------- video ----------
$("video").onclick = async () => {
  if (!state.show) return;
  $("video").disabled = true;
  $("video").textContent = "Rendering…";
  try {
    const { id } = await api("/api/video", { show: state.show });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const s = await api(`/api/video/${id}`);
      if (s.status === "done") { const v = $("player-video"); v.src = s.url; v.hidden = false; v.play(); setStatus(`Simulated flight in ${s.wind} m/s wind, seen from the ground.`); break; }
      if (s.status === "error") throw new Error(s.msg);
    }
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { $("video").textContent = "Render night-sky video"; $("video").disabled = !state.show; }
};

// ---------- live wind: real-time sim, the phone (or the sliders) is the wind ----------
const live = { ws: null, code: null, dots: [], nearTotal: 0 };
function fillAt(a, k) { const f = a.tracks.fill; return Array.isArray(f) ? f[Math.min(k, f.length - 1)] : f || "#ffffff"; }
function stopLive() {
  if (live.code) api(`/api/live/${live.code}/stop`, {}).catch(() => {});
  if (live.ws) { live.ws.onclose = null; live.ws.close(); }
  live.ws = live.code = null;
  for (const id of ["live", "ghosts", "streaks"]) $(id).replaceChildren();
  hl.replaceChildren(); $("wind-label").textContent = "";
  $("anim").style.display = "";
  $("live-on").hidden = true; $("live-idle").hidden = false;
  for (const id of ["plan", "tweak", "fly", "play"]) $(id).disabled = state.busy || (id !== "plan" && !state.show);
}
$("live-start").onclick = async () => {
  if (!state.show) return;
  $("live-start").disabled = true; $("live-start").textContent = "Starting…";
  try {
    const r = await api("/api/live", { show: state.show });
    live.code = r.code; live.nearTotal = 0;
    $("live-code").textContent = r.code;
    $("live-qr").innerHTML = r.qr;
    $("live-url").href = r.pad_url; $("live-url").textContent = r.pad_url;
    $("live-warn").hidden = r.reachable;
    $("live-idle").hidden = true; $("live-on").hidden = false;
    player.pause(); $("anim").style.display = "none";
    for (const id of ["tweak", "fly", "play"]) $(id).disabled = true;
    const g = $("live"); g.replaceChildren();
    live.dots = player.T.actors.map(() => g.appendChild(el("circle", { r: 5 })));
    const gh = $("ghosts"); gh.replaceChildren();
    live.ghosts = player.T.actors.map(() => [gh.appendChild(el("line", {})), gh.appendChild(el("circle", { r: 4 }))]);
    const st = $("streaks"); st.replaceChildren();
    live.streaks = Array.from({ length: 70 }, () => ({ x: Math.random(), y: Math.random(), k: 0.6 + Math.random() * 0.8, node: st.appendChild(el("line", {})) }));
    for (const [k, v] of [["wind-x", 0], ["wind-y", 0]]) $(k).value = v;
    live.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live/${r.code}`);
    live.ws.onmessage = (e) => drawLive(JSON.parse(e.data));
    live.ws.onclose = () => { setStatus("Live session ended.", true); stopLive(); };
    setStatus("Live: the swarm is flying the show in real time. Tilt or shake the phone, or use the sliders.");
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { $("live-start").textContent = "Go live"; $("live-start").disabled = !state.show; }
};
function drawLive(f) {
  if (f.type !== "frame" || !player.T) return;
  const T = player.T;
  f.pos.forEach(([x, z, depth], i) => {
    const c = live.dots[i];
    if (!c) return;
    c.setAttribute("cx", x * PPM); c.setAttribute("cy", H() - z * PPM);
    c.setAttribute("r", (5 * Math.min(1.8, Math.max(0.6, 1 - depth / 30))).toFixed(1)); // closer to the crowd looks bigger
    c.setAttribute("fill", fillAt(T.actors[i], f.frame));
  });
  // Where each drone should be (faint dot) and how far the wind has pushed it (line): drift you can see.
  f.pos.forEach(([x, z], i) => {
    const want = actorCenter(T.actors[i].id, f.frame), g = live.ghosts[i];
    if (!want || !g) return;
    const [ax, ay] = [x * PPM, H() - z * PPM];
    const d = Math.hypot(ax - want[0], ay - want[1]) / PPM;
    const show = d > 0.3;
    g[1].setAttribute("cx", want[0]); g[1].setAttribute("cy", want[1]);
    g[1].setAttribute("fill", show ? fillAt(T.actors[i], f.frame) : "none");
    for (const [k, v] of [["x1", want[0]], ["y1", want[1]], ["x2", ax], ["y2", ay]]) g[0].setAttribute(k, show ? v : 0);
    g[0].setAttribute("class", d > state.limits.max_tracking_error ? "far" : "");
  });
  drawStreaks(f);
  hl.replaceChildren();
  for (const [a, b] of f.near) {
    const [p, q] = [f.pos[a], f.pos[b]];
    hl.appendChild(el("line", { class: "near", x1: p[0] * PPM, y1: H() - p[1] * PPM, x2: q[0] * PPM, y2: H() - q[1] * PPM }));
    hl.appendChild(el("circle", { cx: p[0] * PPM, cy: H() - p[1] * PPM, r: 16 }));
  }
  live.nearTotal += f.near.length ? 1 : 0;
  $("lv-wind").textContent = Math.hypot(...f.wind).toFixed(0);
  $("lv-sep").textContent = f.min_sep.toFixed(1);
  $("lv-near").textContent = live.nearTotal;
  $("lv-near-box").classList.toggle("bad", f.near.length > 0);
  $("lv-drift").textContent = f.drift.toFixed(1);
  $("live-pad").classList.toggle("on", f.pad);
  $("scrub").value = f.frame;
  $("time").textContent = `${f.t.toFixed(1)} / ${T.duration.toFixed(1)} s · live`;
  markChip(f.t);
}
// Wind streaks drift across the sky at the wind's speed (across the show; depth wind is spelled out in the label).
function drawStreaks(f) {
  const vb = stage.viewBox.baseVal, [wx, wy] = f.wind, speed = Math.hypot(wx, wy);
  const dir = wx >= 0 ? 1 : -1, len = Math.min(140, 12 + Math.abs(wx) * 9);
  for (const s of live.streaks) {
    s.x += (wx * PPM * s.k / 30) / vb.width;
    s.x = ((s.x % 1) + 1) % 1;
    const x = vb.x + s.x * vb.width, y = vb.y + s.y * vb.height;
    s.node.setAttribute("x1", x); s.node.setAttribute("y1", y);
    s.node.setAttribute("x2", x - dir * len * s.k); s.node.setAttribute("y2", y);
    s.node.setAttribute("stroke-width", 2 + Math.min(4, f.gust / 3));
    s.node.setAttribute("opacity", Math.abs(wx) < 0.5 ? 0 : Math.min(0.6, 0.12 + Math.abs(wx) / 25 + f.gust / 40));
  }
  const lbl = $("wind-label");
  lbl.setAttribute("x", vb.x + vb.width * 0.02); lbl.setAttribute("y", vb.y + vb.height * 0.07);
  const across = Math.abs(wx) >= 0.5 ? (wx > 0 ? "→ right" : "← left") : "", depth = Math.abs(wy) >= 0.5 ? (wy > 0 ? "away from crowd" : "toward crowd") : "";
  lbl.textContent = speed < 0.5 ? "calm" : `wind ${speed.toFixed(0)} m/s ${[across, depth].filter(Boolean).join(", ")}${f.gust > 1 ? `  · gust ${f.gust.toFixed(0)}` : ""}`;
}
const liveSend = (m) => { if (live.ws?.readyState === 1) live.ws.send(JSON.stringify(m)); };
for (const id of ["wind-x", "wind-y"]) $(id).oninput = () => liveSend({ wind: [+$("wind-x").value, +$("wind-y").value] });
$("live-gust").onclick = () => liveSend({ shake: 0.75 });
$("live-calm").onclick = () => { $("wind-x").value = $("wind-y").value = 0; liveSend({ wind: [0, 0] }); };
$("live-stop").onclick = () => { stopLive(); setStatus("Live stopped."); };

// ---------- voice (copied from SketchDynamics app.js; moves to motionlang/static/voice.js after judging) ----------
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let listening = null;
function attachMic(buttonId, inputId) {
  const btn = $(buttonId), input = $(inputId);
  if (!Recognition) return;
  btn.hidden = false;
  btn.onclick = () => {
    if (listening) { listening.stop(); return; }
    const rec = new Recognition();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.onstart = () => { btn.classList.add("on"); setStatus("🎤 Listening… (click the mic again to stop)"); };
    rec.onresult = (e) => { input.value = [...e.results].map((r) => r[0].transcript).join("").trim(); };
    rec.onerror = (e) => { if (e.error !== "aborted") setStatus(`🎤 Speech recognition failed: ${e.error}`, true); };
    rec.onend = () => { btn.classList.remove("on"); listening = null; if (input.value) setStatus("🎤 Got it. Press Enter to send."); };
    listening = rec;
    rec.start();
  };
}
attachMic("mic-intent", "intent");
attachMic("mic-instruction", "instruction");

// ---------- start ----------
(async () => {
  const cfg = await api("/config");
  state.limits = cfg.limits;
  if (cfg.access_required) {
    const input = $("access-code");
    input.hidden = false;
    try { input.value = localStorage.getItem(ACCESS_KEY) || ""; } catch { /* storage blocked */ }
    input.onchange = () => { try { localStorage.setItem(ACCESS_KEY, input.value.trim()); } catch { /* ignore */ } };
  }
  renderLimits();
  if (!cfg.llm) setStatus("NEBIUS_API_KEY is not set: planning and repairs need it (examples and checks still work).", true);
  if (!cfg.tavily) { $("brief").disabled = true; $("brief").title = "TAVILY_API_KEY is not set"; }
  for (const ex of await api("/api/examples")) {
    const o = document.createElement("option");
    o.value = ex.name; o.textContent = ex.title;
    $("examples").appendChild(o);
  }
  $("examples").onchange = async () => {
    const name = $("examples").value;
    $("examples").value = "";
    if (name) { try { state.sweep = null; state.simIssues = []; await loadShow(await api(`/api/examples/${name}`)); } catch (err) { setStatus(String(err.message || err), true); } }
  };
  setBusy(false);
  renderInk();
})();
