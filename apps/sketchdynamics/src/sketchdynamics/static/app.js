import { Player, el } from "/player.js"; // served from the motionlang package

const $ = (id) => document.getElementById(id);
const stage = $("stage"), ink = $("ink");
const W = 1280, H = 720;

const state = {
  strokes: [],        // [{id, points: [[x, y], ...]}] in canvas coordinates
  versions: [],       // [{label, doc, tracks}]
  current: -1,
  busy: false,
  logo: null,         // {name, assets, actors, dropped, description} from /import-svg (+ /label-parts)
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
  $("hint").hidden = state.strokes.length > 0 || state.current >= 0 || !!state.logo;
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
  state.strokes = []; state.versions = []; state.current = -1; state.logo = null;
  $("anim").replaceChildren(); stage.classList.remove("has-anim", "has-logo");
  renderParts(); setExamples();
  player.T = null; player.pause();
  $("tweak-form").hidden = $("tweak-examples").hidden = true;
  setControls(false); renderInk(); renderVersions(); setStatus("");
  $("program-json").textContent = "";
};

// ---------- examples ----------
function chips(container, items, input, prefix = "") {
  const head = prefix ? [Object.assign(document.createElement("span"), { className: "chips-prefix", textContent: prefix })] : [];
  container.replaceChildren(...head, ...items.map((t) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = t;
    b.onclick = () => { $(input).value = t; $(input).focus(); };
    return b;
  }));
}
function setExamples() {
  const ideas = state.logo?.suggestions?.length ? state.logo.suggestions : null;
  chips($("examples"), ideas || (state.logo ? [
    "the logo builds itself piece by piece, then settles with a little bounce",
    "the main shape wipes in, the small parts pop in one after another, then the whole logo breathes",
    "a playful intro: parts drop in from above with squash and stretch",
  ] : [
    "a ball rolls along my line and bounces",
    "my line draws itself, then a star pops in at its end",
    "a red square slides along my curve, spinning, then fades out",
  ]), "intent", ideas ? "Ideas from Nemotron VL:" : "");
  chips($("tweak-examples"), state.logo
    ? ["slower", "more playful", "reveal the main shape left to right", "end with a gentle pulse"]
    : ["slower", "more bounce", "make it blue", "add a squash when it lands"], "instruction");
}
setExamples();

// ---------- panel ----------
const SHORT = (model) => (/vl/i.test(model) ? "Super VL" : /ultra/i.test(model) ? "Ultra" : /nano/i.test(model) ? "Nano" : /super/i.test(model) ? "Super" : model.split("/").pop());
const MODEL_CLASS = { "Super VL": "vl", Ultra: "ultra", Nano: "nano", Super: "super" };
const PURPOSE = { generate: "plan", repair: "repair", "repair-edit": "repair via edit ops", edit: "edit ops", "edit-repair": "edit retry", regenerate: "regenerate", label: "name the parts" };
const RUN_TITLE = { generate: "Generate", tweak: "Tweak", logo: "Logo" };

function newRun(kind, text) {
  const li = document.createElement("li");
  const title = document.createElement("div");
  title.className = "run-title";
  title.innerHTML = `${RUN_TITLE[kind]} <b></b>`;
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
  li.className = `call ${MODEL_CLASS[short] ?? ""}`;
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
  if (!("ok" in a)) return;
  if (a.kind) li.querySelector(".purpose").textContent = `${PURPOSE[a.kind] ?? a.kind}${a.think ? "" : " · no reasoning"}`;
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

function addTotals(c) {
  state.totals.calls += 1; state.totals.seconds += c.seconds; state.totals.tokens += c.prompt_tokens + c.completion_tokens;
  renderTotals();
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
  for (const id of ["gen", "tweak", "intent", "instruction", "mic-intent", "mic-instruction", "clear", "undo-stroke", "logo-pick", "planner"]) $(id).disabled = on;
}
function setStatus(text, bad = false, retry = null) {
  $("status").textContent = text;
  $("status").classList.toggle("bad", bad);
  if (retry) $("status").append(" ", button(retry.label, retry.run, "retry"));
}

// Super-120B plans by default (cheaper, reliable with structured output); Ultra-550B is one click away.
try { $("planner").value = localStorage.getItem("sd.planner") || "super"; } catch { /* storage blocked */ }
$("planner").onchange = () => { try { localStorage.setItem("sd.planner", $("planner").value); } catch { /* ignore */ } };

// After a failed run planned by Super, offer the same request again with Ultra.
function retryWithUltra() {
  const last = state.lastRun;
  if (!last || $("planner").value === "ultra") return null;
  return {
    label: "Retry with Ultra",
    run: () => {
      $("planner").value = "ultra"; $("planner").onchange();
      if (last.kind === "tweak") { state.current = last.base; $("instruction").value = last.text; $("tweak-form").requestSubmit(); }
      else { $("intent").value = last.text; $("gen-form").requestSubmit(); }
    },
  };
}

// A hosted copy may need an access code (GET /config says so); it's kept in this browser only.
const ACCESS_KEY = "sd.access";
const jsonHeaders = () => {
  let code = "";
  try { code = localStorage.getItem(ACCESS_KEY) || ""; } catch { /* storage blocked */ }
  return { "Content-Type": "application/json", ...(code ? { "X-Access-Code": code } : {}) };
};
async function failure(res) {
  const text = await res.text();
  let detail = text;
  try { detail = JSON.parse(text).detail ?? text; } catch { /* not JSON */ }
  if (res.status === 401) { $("access-code").hidden = false; $("access-code").focus(); }
  return Object.assign(new Error(typeof detail === "string" ? detail.slice(0, 300) : `${res.status} ${res.statusText}`), { status: res.status });
}
fetch("/config").then((r) => r.json()).then((c) => {
  if (!c.access_required) return;
  const input = $("access-code");
  input.hidden = false;
  try { input.value = localStorage.getItem(ACCESS_KEY) || ""; } catch { /* storage blocked */ }
  input.onchange = () => { try { localStorage.setItem(ACCESS_KEY, input.value.trim()); } catch { /* ignore */ } };
}).catch(() => {});

async function request(url, body, log) {
  const res = await fetch(`${url}?stream=1`, { method: "POST", headers: jsonHeaders(), body: JSON.stringify(body) });
  if (!res.ok) throw await failure(res);
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
      } else if (ev.type === "attempt") { // the reply has been validated: show the verdict now
        if (open) finishCard(open, ev);
        addTotals(ev);
      } else if (ev.type === "result") result = ev;
      else if (ev.type === "error") throw new Error(ev.detail);
    }
  }
  if (!result) throw new Error("the server closed the stream without a result");
  const last = result.attempts.at(-1);
  if (open && last && !last.ok) {
    open.querySelector(".verdict").textContent = `✗ ${last.problems.length} problem${last.problems.length === 1 ? "" : "s"}; stopped here`;
  }
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
  player.play();
  $("scrub").max = v.tracks.frames - 1;
  setControls(true);
  $("tweak-form").hidden = $("tweak-examples").hidden = false;
  $("program-json").textContent = JSON.stringify(v.doc, null, 2);
  renderInk(); renderVersions();
}

function download(blob, name) {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
const exportName = () => (state.versions[state.current]?.doc?.meta?.title || "animation").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "animation";
$("export-json").onclick = () => {
  const v = state.versions[state.current];
  if (v) download(new Blob([JSON.stringify(v.doc, null, 2)], { type: "application/json" }), `${exportName()}.motion.json`);
};
$("export-html").onclick = async () => {
  const v = state.versions[state.current];
  if (!v) return;
  try {
    const res = await fetch("/export.html", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc: v.doc, title: v.doc.meta?.title || "SketchDynamics animation" }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    download(await res.blob(), `${exportName()}.html`);
  } catch (err) { setStatus(`Couldn't export: ${err.message}`, true); }
};

function renderVersions() {
  $("export").hidden = state.current < 0;
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
    setStatus(`No playable animation (${secs}). ${result.note || "The model's document didn't validate; see the panel."}`, true, retryWithUltra());
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
    setStatus(`Showing the closest attempt; it still has problems (${secs}). ${result.note}`, true, retryWithUltra());
  }
}

$("gen-form").onsubmit = async (e) => {
  e.preventDefault();
  const intent = $("intent").value.trim();
  if (!intent || state.busy) return;
  const strokes = Object.fromEntries(state.strokes.map((s) => [s.id, { points: s.points }]));
  const logo = state.logo ? { assets: state.logo.assets, actors: state.logo.actors } : {};
  const planner = $("planner").value;
  state.lastRun = { kind: "generate", text: intent };
  setBusy(true, `${planner === "ultra" ? "Ultra" : "Super"} · plan…`); setStatus("");
  try {
    const r = await request("/generate", { intent, strokes, canvas: { width: W, height: H }, planner, ...logo }, newRun("generate", intent));
    state.current = state.versions.length - 1; // new generation starts after the latest version
    accept(r, intent.length > 28 ? intent.slice(0, 26) + "…" : intent);
  } catch (err) { setStatus(String(err.message || err), true, [401, 429].includes(err.status) ? null : retryWithUltra()); }
  finally { setBusy(false); }
};

$("tweak-form").onsubmit = async (e) => {
  e.preventDefault();
  const instruction = $("instruction").value.trim();
  if (!instruction || state.busy || state.current < 0) return;
  state.lastRun = { kind: "tweak", text: instruction, base: state.current };
  setBusy(true, "Nano · edit ops…"); setStatus("");
  try {
    const r = await request("/tweak", { doc: state.versions[state.current].doc, instruction, planner: $("planner").value }, newRun("tweak", instruction));
    accept(r, instruction.length > 20 ? instruction.slice(0, 18) + "…" : instruction);
    $("instruction").value = "";
  } catch (err) { setStatus(String(err.message || err), true, [401, 429].includes(err.status) ? null : retryWithUltra()); }
  finally { setBusy(false); }
};

// ---------- voice ----------
// "Sketch + talk": the browser's Web Speech API turns speech into the prompt or tweak text, which then goes
// through the same path as typing (Nemotron sees text only). Hidden where the browser lacks it.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let listening = null;

// A level meter on the mic button, from the same default microphone the speech service uses. If the
// meter stays flat while you talk, the browser is listening to the wrong (or a muted) input.
async function startMeter(btn) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    let peak = 0, raf = 0;
    const tick = () => {
      analyser.getByteTimeDomainData(data);
      const level = Math.min(1, Math.max(...data.map((v) => Math.abs(v - 128))) / 64);
      peak = Math.max(peak, level);
      btn.style.boxShadow = `0 0 0 ${2 + level * 10}px rgba(228, 87, 46, ${0.15 + level * 0.5})`;
      raf = requestAnimationFrame(tick);
    };
    tick();
    const device = stream.getAudioTracks()[0]?.label || "the default microphone";
    return {
      device,
      peak: () => peak,
      stop: () => { cancelAnimationFrame(raf); stream.getTracks().forEach((t) => t.stop()); ctx.close(); btn.style.boxShadow = ""; },
    };
  } catch (err) {
    console.warn("[mic] meter unavailable", err);
    return null;
  }
}

function attachMic(buttonId, inputId) {
  const btn = $(buttonId), input = $(inputId);
  if (!Recognition) return;
  btn.hidden = false;
  btn.onclick = async () => {
    if (listening) { listening.stop(); return; }
    const meter = await startMeter(btn);
    const rec = new Recognition();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true; // show words as they're recognized
    let heard = false, failed = false;
    // Say what's happening at every step: when speech fails, the status line shows where.
    const say = (text, bad = false) => { setStatus(`🎤 ${text}`, bad); console.info("[mic]", text); };
    const on = meter ? ` on “${meter.device}”` : "";
    rec.onstart = () => say(`Listening${on}… speak now (click the mic again to stop).`);
    rec.onaudiostart = () => say(`Microphone on${on}. Listening…`);
    rec.onspeechstart = () => say("Hearing speech…");
    rec.onresult = (e) => {
      heard = true;
      input.value = [...e.results].map((r) => r[0].transcript).join("").trim();
    };
    rec.onerror = (e) => {
      failed = true;
      console.warn("[mic] error", e.error, e.message);
      const why = {
        "not-allowed": "Microphone access is blocked. Allow it for this site (the icon in the address bar), and on macOS also for the browser in System Settings → Privacy & Security → Microphone.",
        "service-not-allowed": "The browser's speech service is turned off or unavailable here.",
        "audio-capture": "No microphone found. Check that one is connected and selected in the browser's settings.",
        "no-speech": meter && meter.peak() < 0.05
          ? `Didn't hear any speech, and “${meter.device}” picked up almost no sound. Pick another input: click the site icon in the address bar → Site settings → Microphone (Chrome), or check macOS System Settings → Privacy & Security → Microphone for the browser and the input level in System Settings → Sound.`
          : `Didn't hear any speech${on}, though the microphone picked up sound. Speak right after clicking, a little louder or closer.`,
        "network": "The speech service is unreachable. Chromium browsers other than Chrome (Brave, Arc, …) often lack Google's speech service: try Chrome or Safari, or type.",
        "language-not-supported": `Speech recognition doesn't support ${rec.lang}.`,
      }[e.error];
      if (e.error !== "aborted") say(why || `Speech recognition failed: ${e.error}`, true);
    };
    rec.onend = () => {
      listening = null; btn.classList.remove("listening"); input.focus();
      meter?.stop();
      if (heard) say(`Got it. Edit if needed, then press ${inputId === "intent" ? "Generate" : "Tweak"}.`);
      else if (!failed) say("Stopped without hearing any speech.", true);
    };
    listening = rec;
    btn.classList.add("listening");
    input.value = "";
    try { rec.start(); } catch (err) { listening = null; btn.classList.remove("listening"); meter?.stop(); say(`Couldn't start speech recognition: ${err.message}`, true); }
  };
}
attachMic("mic-intent", "intent");
attachMic("mic-instruction", "instruction");

renderInk(); renderTotals();

// ---------- logo import ----------
async function postJSON(url, body) {
  const res = await fetch(url, { method: "POST", headers: jsonHeaders(), body: JSON.stringify(body) });
  if (!res.ok) throw await failure(res);
  const data = await res.json().catch(() => ({}));
  return data;
}

// Parts and groups. A group moves as one rigid piece, so the planner animates "sun" instead of
// keeping six actors in formation. Click parts to select them, then Group; × dissolves a group.
function partChip(id, group) {
  const a = state.logo.assets[id];
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "part" + (state.logo.selected.has(id) ? " selected" : "");
  const dot = document.createElement("i");
  dot.style.background = a.fill || a.stroke || "#999";
  if (group?.pivot === id) chip.title = `${group.label} rotates about this part`;
  chip.append(...(group?.pivot === id ? ["◎ "] : []), dot, a.label && a.label !== id ? `${a.label} ` : "", Object.assign(document.createElement("code"), { textContent: id }));
  chip.onclick = () => {
    const sel = state.logo.selected;
    sel.has(id) ? sel.delete(id) : sel.add(id);
    renderParts();
  };
  chip.onmouseenter = () => highlight([id]);
  chip.onmouseleave = () => highlight([]);
  return chip;
}

function renderParts() {
  const box = $("parts");
  box.hidden = !state.logo;
  highlight([]);
  if (!state.logo) return box.replaceChildren();
  const L = state.logo;
  const head = document.createElement("div");
  head.className = "parts-head";
  head.textContent = `${L.name}: ${Object.keys(L.assets).length} parts, ${L.groups.length} group${L.groups.length === 1 ? "" : "s"}`
    + (L.description ? ` · ${L.description}` : "");
  const list = document.createElement("div");
  list.className = "part-list";
  const grouped = new Set(L.groups.flatMap((g) => g.parts));
  for (const g of L.groups) {
    const gbox = document.createElement("span");
    gbox.className = "group";
    const name = document.createElement("b");
    name.textContent = g.label;
    name.title = `group "${g.id}": moves as one piece. Click to add the selected parts; double-click to rename.`;
    name.onclick = () => {
      if (!L.selected.size || name.isContentEditable) return;
      const add = [...L.selected].filter((p) => !g.parts.includes(p));
      const groups = L.groups.map((h) => (h === g ? { ...h, parts: [...h.parts, ...add] } : { ...h, parts: h.parts.filter((p) => !L.selected.has(p)) }));
      regroup(groups, `added ${add.length} part${add.length === 1 ? "" : "s"} to ${g.label}`);
    };
    name.ondblclick = () => {
      name.contentEditable = "true"; name.focus();
      document.getSelection().selectAllChildren(name);
    };
    // Commit on Enter or when focus leaves; blur() alone doesn't fire a blur event if the page lacks focus.
    const commit = () => {
      if (!name.isContentEditable) return;
      name.contentEditable = "false";
      const label = name.textContent.trim();
      if (label && label !== g.label) regroup(L.groups.map((h) => (h === g ? { ...h, label } : h)), `renamed to ${label}`);
      else name.textContent = g.label;
    };
    name.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      if (e.key === "Escape") { name.textContent = g.label; commit(); }
    };
    name.onblur = commit;
    name.onmouseenter = () => highlight(g.parts, g);
    name.onmouseleave = () => highlight([]);
    const x = Object.assign(document.createElement("button"), { type: "button", className: "ungroup", textContent: "×", title: "Dissolve this group" });
    x.onclick = () => regroup(L.groups.filter((h) => h !== g), `dissolved ${g.label}`);
    gbox.append(name, ...g.parts.map((id) => partChip(id, g)), x);
    list.appendChild(gbox);
  }
  for (const id of Object.keys(L.assets)) if (!grouped.has(id)) list.appendChild(partChip(id));
  box.replaceChildren(head, list, groupTools());
  if (L.notes.length) {
    const n = document.createElement("div");
    n.className = "parts-notes";
    n.textContent = `On import: ${L.notes.join("; ")}`;
    box.appendChild(n);
  }
  if (L.dropped.length) {
    const d = document.createElement("div");
    d.className = "parts-dropped";
    d.textContent = `Simplified on import: ${L.dropped.join("; ")}`;
    box.appendChild(d);
  }
  highlight([]);
}

const subpaths = (d) => (d?.match(/[Mm]/g) || []).length;
const button = (text, onclick, cls = "") => Object.assign(document.createElement("button"), { type: "button", className: cls, textContent: text, onclick });

// What you can do with the current selection. Model groupings are a starting point: people read the same
// artwork differently, so every grouping decision stays editable.
function groupTools() {
  const L = state.logo;
  const tools = document.createElement("div");
  tools.className = "group-tools";
  const sel = [...L.selected];
  const groupOf = (id) => L.groups.find((g) => g.parts.includes(id));
  if (!sel.length) {
    tools.className += " hint";
    tools.textContent = "Grouped parts move as one piece. Click parts to select them; then group them, click a group's name to add them to it, take them out of their group, or split a part into its pieces. Double-click a group's name to rename it.";
    return tools;
  }
  if (sel.length >= 2) {
    const nameIn = Object.assign(document.createElement("input"), { placeholder: "new group name, e.g. sun" });
    const go = button(`Group ${sel.length} parts`, () => {
      const label = nameIn.value.trim() || L.assets[sel[0]].label || sel[0];
      // a part moves to the new group; groups left with one part dissolve on the server
      const rest = L.groups.map((g) => ({ ...g, parts: g.parts.filter((p) => !L.selected.has(p)) }));
      regroup([...rest, { label, parts: sel }], `grouped ${label}`);
    }, "primary");
    nameIn.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go.click(); } };
    tools.append(nameIn, go);
  }
  const owner = sel.length === 1 && groupOf(sel[0]);
  if (owner) {
    const spin = button(owner.pivot === sel[0] ? `${owner.label} rotates about this part` : `Rotate ${owner.label} about this part`,
      () => regroup(L.groups.map((g) => (g === owner ? { ...g, pivot: sel[0] } : g)), `${owner.label} now rotates about ${L.assets[sel[0]].label || sel[0]}`));
    spin.disabled = owner.pivot === sel[0];
    tools.append(spin);
  }
  const grouped = sel.filter(groupOf);
  if (grouped.length) {
    tools.append(button(grouped.length === 1 ? "Take out of its group" : `Take ${grouped.length} out of their groups`,
      () => regroup(L.groups.map((g) => ({ ...g, parts: g.parts.filter((p) => !L.selected.has(p)) })), `took ${grouped.length} part${grouped.length === 1 ? "" : "s"} out`)));
  }
  const n = sel.length === 1 ? subpaths(L.assets[sel[0]].d) : 0;
  if (n > 1) tools.append(button(`Split into ${n} pieces`, () => splitPart(sel[0])));
  tools.append(button("Clear selection", () => { L.selected.clear(); renderParts(); }));
  return tools;
}

async function splitPart(id) {
  if (state.busy) return;
  const L = state.logo;
  try {
    const r = await postJSON("/split-part", { assets: L.assets, groups: L.groups, actors: L.actors, part: id, canvas: { width: W, height: H } });
    Object.assign(L, { assets: r.assets, groups: r.groups, actors: r.actors });
    L.selected = new Set(r.pieces);
    if (state.current < 0 && r.preview.tracks) player.load(r.preview.tracks);
    renderParts();
    setStatus(`Split ${L.assets[r.pieces[0]].label?.replace(/ 1$/, "") || id} into ${r.pieces.length} pieces (selected). Generate again to use them.`);
  } catch (err) { setStatus(`Couldn't split: ${err.message}`, true); }
}

// Dashed boxes over parts on the stage: selected parts stay marked, hovered ones flash.
function highlight(hovered, group) {
  const hl = $("hl");
  hl.replaceChildren();
  if (!state.logo) return;
  if (group?.pivot) { // crosshair at the rotation centre
    const [x, y, w, h] = typeof group.pivot === "string" ? state.logo.assets[group.pivot].bbox : [...group.pivot, 0, 0];
    const cx = x + w / 2, cy = y + h / 2;
    hl.appendChild(el("path", { d: `M${cx - 18} ${cy}H${cx + 18}M${cx} ${cy - 18}V${cy + 18}`, class: "pivot" }));
  }
  const mark = (id, cls) => {
    const [x, y, w, h] = state.logo.assets[id].bbox;
    hl.appendChild(el("rect", { x: x - 6, y: y - 6, width: w + 12, height: h + 12, rx: 6, class: cls }));
  };
  for (const id of state.logo.selected) mark(id, "sel");
  for (const id of hovered) mark(id, "hov");
}

async function regroup(groups, why) {
  if (state.busy) return;
  const L = state.logo;
  try {
    const r = await postJSON("/regroup", { assets: L.assets, groups, actors: L.actors, canvas: { width: W, height: H } });
    L.groups = r.groups; L.actors = r.actors; L.selected.clear();
    if (state.current < 0 && r.preview.tracks) player.load(r.preview.tracks);
    renderParts();
    if (why) setStatus(`${why[0].toUpperCase()}${why.slice(1)}. Generate again to use the new grouping.`);
  } catch (err) { setStatus(`Couldn't regroup: ${err.message}`, true); }
}

// What the vision model sees: the logo exactly as the stage shows it, without the sketch layer.
// With `marked`, each part also gets a numbered tag on its outline (set-of-marks), so the model can tell
// which id is which shape instead of guessing from bounding boxes.
async function renderLogoImage(marked = false) {
  const svg = stage.cloneNode(true);
  svg.querySelector("#ink")?.remove();
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" }));
  try {
    const img = new Image();
    await new Promise((ok, fail) => { img.onload = ok; img.onerror = () => fail(new Error("could not render the logo")); img.src = url; });
    const c = Object.assign(document.createElement("canvas"), { width: W, height: H });
    const g = c.getContext("2d");
    g.fillStyle = "#fff"; g.fillRect(0, 0, W, H); g.drawImage(img, 0, 0, W, H);
    if (marked) drawMarks(g);
    return c.toDataURL("image/jpeg", 0.9);
  } finally { URL.revokeObjectURL(url); }
}

function drawMarks(g) {
  const placed = [];
  Object.values(state.logo.assets).forEach((a, i) => {
    const m = /M\s*(-?[\d.]+)[ ,]+(-?[\d.]+)/.exec(a.d || "");
    let [x, y] = m ? [+m[1], +m[2]] : [a.bbox[0] + a.bbox[2] / 2, a.bbox[1] + a.bbox[3] / 2];
    // nudge tags apart so neighbouring parts stay readable
    for (let k = 0; k < 12 && placed.some(([px, py]) => Math.hypot(px - x, py - y) < 26); k++) {
      x += 26 * Math.cos(k * 2.4); y += 26 * Math.sin(k * 2.4);
    }
    placed.push([x, y]);
    g.beginPath(); g.arc(x, y, 12, 0, 7);
    g.fillStyle = "#ffe066"; g.fill(); g.lineWidth = 2; g.strokeStyle = "#111"; g.stroke();
    g.fillStyle = "#111"; g.font = "bold 13px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(String(i + 1), x, y + 0.5);
  });
}

async function labelParts() {
  const log = newRun("logo", "name and group the parts");
  const card = callCard({ model: "nvidia/Nemotron-3.5-Super-VL", purpose: "label", think: false });
  log.calls.appendChild(card);
  setBusy(true, "Super VL · reading the logo…");
  try {
    const r = await postJSON("/label-parts", {
      image: await renderLogoImage(), marked: await renderLogoImage(true), name: state.logo.name,
      assets: state.logo.assets, groups: state.logo.groups,
    });
    if (r.call) { finishCard(card, r.call); addTotals(r.call); } else clearInterval(card._timer);
    const v = card.querySelector(".verdict");
    const n = Object.keys(r.labels).length;
    v.className = `verdict ${n ? "ok" : "bad"}`;
    const groups = r.groups ?? state.logo.groups;
    v.textContent = n
      ? `✓ named ${n} of ${Object.keys(state.logo.assets).length} parts` + (r.groups ? `, ${groups.length} rigid group${groups.length === 1 ? "" : "s"}: ${groups.map((g) => g.label).join(", ") || "none"}` : "")
      : `✗ no labels (${r.error || "empty reply"}); the planner will use the file's ids and groups`;
    for (const [id, label] of Object.entries(r.labels)) state.logo.assets[id].label = label;
    state.logo.description = r.description;
    state.logo.suggestions = r.suggestions || [];
    if (state.logo.suggestions.length) {
      setExamples();
      v.textContent += ` · ${state.logo.suggestions.length} motion ideas`;
      if (!$("intent").value.trim()) $("intent").value = state.logo.suggestions[0]; // a starting point: edit or replace it
    }
    setBusy(false);
    await regroup(groups); // also puts the new labels on the actors the planner sees
  } catch (err) {
    clearInterval(card._timer);
    card.querySelector(".verdict").textContent = `✗ ${err.message}`;
  } finally { setBusy(false); }
}

async function importLogo(svgText, name) {
  if (state.busy) return;
  setBusy(true, "Importing logo…"); setStatus("");
  try {
    const r = await postJSON("/import-svg", { svg: svgText, canvas: { width: W, height: H } });
    $("clear").onclick();
    $("intent").value = ""; // the old prompt described the previous artwork
    state.logo = { name, assets: r.assets, actors: r.actors, groups: r.groups, dropped: r.dropped, notes: r.notes || [], description: "", selected: new Set() };
    if (r.preview.tracks) { player.load(r.preview.tracks); stage.classList.add("has-logo"); }
    renderInk(); renderParts(); setExamples();
    setStatus(`Imported ${name}. Draw paths over it if you like, then say what should happen.`);
  } catch (err) { setStatus(`Couldn't import ${name}: ${err.message}`, true); setBusy(false); return; }
  setBusy(false);
  await labelParts();
  $("intent").focus();
}

async function importFile(file) {
  if (!file) return;
  if (!/svg/i.test(file.type) && !/\.svg$/i.test(file.name)) return setStatus("Logos must be SVG files for now.", true);
  importLogo(await file.text(), file.name);
}

$("logo-file").onchange = (e) => { importFile(e.target.files[0]); e.target.value = ""; };
$("logo-pick").onclick = () => $("logo-file").click();
const wrap = stage.parentElement;
wrap.addEventListener("dragover", (e) => { e.preventDefault(); wrap.classList.add("drop"); });
wrap.addEventListener("dragleave", () => wrap.classList.remove("drop"));
wrap.addEventListener("drop", (e) => { e.preventDefault(); wrap.classList.remove("drop"); importFile(e.dataTransfer.files[0]); });
