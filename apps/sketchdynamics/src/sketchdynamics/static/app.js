import { Player, el } from "./player.js";

const $ = (id) => document.getElementById(id);
const stage = $("stage"), ink = $("ink");
const W = 1280, H = 720;

const state = {
  strokes: [],        // [{id, points: [[x, y], ...]}] in canvas coordinates
  versions: [],       // [{label, doc, tracks}]
  current: -1,
  busy: false,
  totals: { calls: 0, seconds: 0, tokens: 0 },
};

// ---------- player ----------
const player = new Player($("anim"), {
  onFrame: (i, t, T) => {
    $("scrub").value = i;
    $("time").textContent = `${t.toFixed(2)} / ${T.duration.toFixed(2)}s`;
  },
  onState: (playing) => { $("play").textContent = playing ? "❚❚" : "▶"; },
});
$("play").onclick = () => player.toggle();
$("scrub").oninput = () => { player.pause(); player.seek(+$("scrub").value); };
$("loop").onchange = () => { player.loop = $("loop").checked; };
$("show-ink").onchange = () => stage.classList.toggle("hide-ink", !$("show-ink").checked);
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !(e.target instanceof HTMLInputElement) && state.current >= 0) { e.preventDefault(); player.toggle(); }
});

// ---------- sketching ----------
function toCanvas(ev) {
  const p = stage.createSVGPoint();
  p.x = ev.clientX; p.y = ev.clientY;
  const q = p.matrixTransform(stage.getScreenCTM().inverse());
  return [Math.min(Math.max(q.x, 0), W), Math.min(Math.max(q.y, 0), H)];
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

// Keep strokes small (they go into the prompt) but faithful: simplify, cap the count.
function simplify(pts) {
  let out = rdp(pts, 2.5);
  const MAX = 48;
  if (out.length > MAX) out = Array.from({ length: MAX }, (_, i) => out[Math.round((i * (out.length - 1)) / (MAX - 1))]);
  return out.map(([x, y]) => [Math.round(x), Math.round(y)]);
}

const pathD = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");

function renderInk() {
  ink.replaceChildren();
  for (const s of state.strokes) {
    ink.appendChild(el("path", { d: pathD(s.points) }));
    const [x, y] = s.points[0];
    const label = el("text", { x: x + 8, y: y - 10 });
    label.textContent = s.id;
    ink.appendChild(label);
  }
  $("hint").hidden = state.strokes.length > 0 || state.current >= 0;
}

let drawing = null;
stage.addEventListener("pointerdown", (ev) => {
  if (state.busy || ev.button !== 0) return;
  try { stage.setPointerCapture(ev.pointerId); } catch { /* synthetic or already-released pointer */ }
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
    const n = state.strokes.reduce((m, s) => Math.max(m, +s.id.slice(1)), 0) + 1;
    state.strokes.push({ id: `s${n}`, points: pts });
  }
  renderInk();
}
stage.addEventListener("pointerup", endStroke);
stage.addEventListener("pointercancel", endStroke);
$("undo-stroke").onclick = () => { state.strokes.pop(); renderInk(); };
$("clear").onclick = () => {
  state.strokes = []; state.versions = []; state.current = -1;
  $("anim").replaceChildren(); stage.classList.remove("has-anim");
  player.T = null; player.pause();
  $("tweak-form").hidden = $("tweak-examples").hidden = true;
  setControls(false); renderInk(); renderVersions(); setStatus("");
  $("program-json").textContent = "";
};

// ---------- examples ----------
function chips(container, items, input) {
  container.replaceChildren(...items.map((t) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = t;
    b.onclick = () => { $(input).value = t; $(input).focus(); };
    return b;
  }));
}
chips($("examples"), [
  "a ball rolls along my line and bounces",
  "my line draws itself, then a star pops in at its end",
  "a red square slides along my curve, spinning, then fades out",
], "intent");
chips($("tweak-examples"), ["slower", "more bounce", "make it blue", "add a squash when it lands"], "instruction");

// ---------- panel ----------
const SHORT = (model) => (/ultra/i.test(model) ? "Ultra" : /nano/i.test(model) ? "Nano" : /super/i.test(model) ? "Super" : model.split("/").pop());
const PURPOSE = { generate: "plan", repair: "repair", edit: "edit ops", "edit-repair": "edit retry", regenerate: "regenerate" };

function newRun(kind, text) {
  const li = document.createElement("li");
  const title = document.createElement("div");
  title.className = "run-title";
  title.innerHTML = `${kind === "generate" ? "Generate" : "Tweak"} <b></b>`;
  title.querySelector("b").textContent = `“${text}”`;
  const calls = document.createElement("ol");
  calls.className = "calls";
  li.append(title, calls);
  $("runs").appendChild(li);
  return { li, calls, cards: [] };
}

function callCard(ev) {
  const li = document.createElement("li");
  const short = SHORT(ev.model);
  li.className = `call ${short.toLowerCase()}`;
  li.innerHTML = `<div class="call-head"><span class="model"></span><span class="purpose"></span><span class="metrics"></span></div>
    <div class="verdict pending">running…</div>`;
  li.querySelector(".model").textContent = short;
  li.querySelector(".purpose").textContent = `${PURPOSE[ev.purpose] ?? ev.purpose}${ev.think ? "" : " · no reasoning"}`;
  li._t0 = performance.now();
  li._timer = setInterval(() => {
    li.querySelector(".metrics").textContent = `${((performance.now() - li._t0) / 1000).toFixed(1)}s`;
  }, 100);
  return li;
}

function finishCard(li, a) {
  clearInterval(li._timer);
  li.querySelector(".metrics").textContent = `${a.seconds.toFixed(1)}s · ${a.prompt_tokens}→${a.completion_tokens} tok`;
  if (!("ok" in a)) { li.querySelector(".verdict").textContent = "validating…"; return; } // verdict arrives with the result
  const v = li.querySelector(".verdict");
  v.className = `verdict ${a.ok ? "ok" : "bad"}`;
  v.textContent = a.ok ? "✓ valid: validator found no errors" : `✗ ${a.problems.length} problem${a.problems.length === 1 ? "" : "s"} → sent back to the model`;
  li.querySelector(".problems")?.remove();
  if (!a.ok) {
    const ul = document.createElement("ul");
    ul.className = "problems";
    const items = a.issues.length ? a.issues : a.problems.map((p) => ({ message: p }));
    for (const it of items.slice(0, 6)) {
      const pi = document.createElement("li");
      if (it.code) { const c = document.createElement("code"); c.textContent = it.code + (it.block ? ` ${it.block}` : ""); pi.append(c, " "); }
      pi.append(it.message);
      if (it.hint) { const h = document.createElement("span"); h.className = "hint"; h.textContent = `hint: ${it.hint}`; pi.append(h); }
      ul.appendChild(pi);
    }
    if (items.length > 6) { const more = document.createElement("li"); more.textContent = `…and ${items.length - 6} more`; ul.appendChild(more); }
    li.appendChild(ul);
  }
}

function renderTotals() {
  const t = state.totals;
  $("totals").textContent = t.calls ? `${t.calls} call${t.calls === 1 ? "" : "s"} · ${t.seconds.toFixed(1)}s · ${t.tokens.toLocaleString()} tokens` : "No calls yet";
}

// ---------- requests ----------
function setControls(hasAnim) {
  for (const id of ["play", "scrub"]) $(id).disabled = !hasAnim;
}
function setBusy(on, text = "") {
  state.busy = on;
  $("busy").hidden = !on;
  $("busy-text").textContent = text;
  for (const id of ["gen", "tweak", "intent", "instruction", "clear", "undo-stroke"]) $(id).disabled = on;
}
function setStatus(text, bad = false) {
  $("status").textContent = text;
  $("status").classList.toggle("bad", bad);
}

async function request(url, body, log) {
  const res = await fetch(`${url}?stream=1`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 300)}`);
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "", result = null, open = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const ev = JSON.parse(buf.slice(0, nl));
      buf = buf.slice(nl + 1);
      if (ev.type === "call_start") {
        open = callCard(ev);
        log.cards.push(open);
        log.calls.appendChild(open);
        $("busy-text").textContent = `${SHORT(ev.model)} · ${PURPOSE[ev.purpose] ?? ev.purpose}…`;
      } else if (ev.type === "call_end") {
        if (open) finishCard(open, ev);
        state.totals.calls += 1; state.totals.seconds += ev.seconds; state.totals.tokens += ev.prompt_tokens + ev.completion_tokens;
        renderTotals();
      } else if (ev.type === "result") result = ev;
      else if (ev.type === "error") throw new Error(ev.detail);
    }
  }
  if (!result) throw new Error("the server closed the stream without a result");
  result.attempts.forEach((a, i) => log.cards[i] && finishCard(log.cards[i], a));
  if (result.note) {
    const n = document.createElement("div");
    n.className = "run-note"; n.textContent = result.note;
    log.li.appendChild(n);
  }
  return result;
}

function show(version) {
  const v = state.versions[version];
  state.current = version;
  stage.classList.add("has-anim");
  player.load(v.tracks);
  $("scrub").max = v.tracks.frames - 1;
  setControls(true);
  $("tweak-form").hidden = $("tweak-examples").hidden = false;
  $("program-json").textContent = JSON.stringify(v.doc, null, 2);
  renderInk(); renderVersions();
}

function renderVersions() {
  $("versions").replaceChildren(...state.versions.map((v, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `v${i + 1} ${v.label}`;
    b.title = v.label;
    b.className = i === state.current ? "current" : "";
    b.onclick = () => show(i);
    return b;
  }));
}

function accept(result, label) {
  const secs = `${result.seconds.toFixed(1)}s, ${result.attempts.length} call${result.attempts.length === 1 ? "" : "s"}`;
  if (!result.tracks) {
    setStatus(`No playable animation (${secs}). ${result.note || "The model's document didn't validate; see the panel."}`, true);
    return;
  }
  // Keep the version list linear from the one being edited.
  state.versions = state.versions.slice(0, state.current + 1);
  state.versions.push({ label, doc: result.doc, tracks: result.tracks });
  show(state.versions.length - 1);
  if (result.ok) {
    const via = result.path === "edit" ? "fast edit" : result.path === "regenerate" ? "regenerated" : "planned";
    setStatus(`${via} in ${secs}${result.doc?.meta?.title ? ` · ${result.doc.meta.title}` : ""}`);
  } else {
    setStatus(`Showing the closest attempt; it still has problems (${secs}). ${result.note}`, true);
  }
}

$("gen-form").onsubmit = async (e) => {
  e.preventDefault();
  const intent = $("intent").value.trim();
  if (!intent || state.busy) return;
  const strokes = Object.fromEntries(state.strokes.map((s) => [s.id, { points: s.points }]));
  setBusy(true, "Ultra · plan…"); setStatus("");
  try {
    const r = await request("/generate", { intent, strokes, canvas: { width: W, height: H } }, newRun("generate", intent));
    state.current = state.versions.length - 1; // new generation starts after the latest version
    accept(r, intent.length > 28 ? intent.slice(0, 26) + "…" : intent);
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { setBusy(false); }
};

$("tweak-form").onsubmit = async (e) => {
  e.preventDefault();
  const instruction = $("instruction").value.trim();
  if (!instruction || state.busy || state.current < 0) return;
  setBusy(true, "Nano · edit ops…"); setStatus("");
  try {
    const r = await request("/tweak", { doc: state.versions[state.current].doc, instruction }, newRun("tweak", instruction));
    accept(r, instruction.length > 20 ? instruction.slice(0, 18) + "…" : instruction);
    $("instruction").value = "";
  } catch (err) { setStatus(String(err.message || err), true); }
  finally { setBusy(false); }
};

renderInk(); renderTotals();
