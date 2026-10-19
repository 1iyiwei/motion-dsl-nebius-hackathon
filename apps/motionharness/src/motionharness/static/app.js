// MotionHarness dashboard: polls the run record and draws the branch tree, tests, motion, code and model calls.

const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (m) => String(m || "").split("/").pop().replace("NVIDIA-", "").replace("Nemotron-3-", "N3 ").replace("nemotron-3-", "N3 ");
const MODEL_COLORS = { super: "#76b900", ultra: "#4c9aff", nano: "#f5a524", vl: "#a78bfa" };
const modelColor = (m) => { m = String(m).toLowerCase(); return m.includes("-vl") ? MODEL_COLORS.vl : m.includes("ultra") ? MODEL_COLORS.ultra : m.includes("nano") ? MODEL_COLORS.nano : MODEL_COLORS.super; };
const ICON = { start: "▶", docs: "🔎", sandbox: "📦", round: "⟳", candidate: "•", tests: "🧪", critique: "👁", naming: "✎", ship: "✅", error: "✖", end: "■" };
const EXAMPLES = [
  ["spring_follow", "add a `spring_follow` verb: the actor chases a target actor with spring physics, overshooting a little and settling when the target stops"],
  ["swing", "add a `swing` verb: the actor swings like a pendulum about a pivot point above it, with damping, and comes to rest"],
  ["squash_hop", "add a `squash_hop` verb: a single cartoon hop with anticipation squash before take-off and squash-and-stretch on landing"],
  ["shake", "add a `shake` verb: a camera-shake style decaying jitter of position and rotation with a frequency and a decay rate"],
];

const state = { run: null, runId: null, sel: null, tab: "tests", file: "verb.py", timer: null, sig: "" };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

function svg(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// ---- sidebar ---------------------------------------------------------------------------------------

async function loadInfo() {
  const info = await api("/api/info");
  const sb = info.sandbox.backend;
  $("#info").innerHTML = [
    `<span class="pill ${sb === "contree" ? "ok" : "warn"}">sandbox <b>${sb === "contree" ? "Token Factory Sandboxes" : "local subprocess"}</b></span>`,
    `<span class="pill">coder <b>${esc(short(info.coder))}</b></span>`,
    `<span class="pill">critic <b>${esc(short(info.vision))}</b></span>`,
    `<span class="pill ${info.tavily ? "ok" : ""}">docs <b>${info.tavily ? "Tavily" : "off"}</b></span>`,
  ].join("");
}

async function loadRuns() {
  const runs = await api("/api/runs");
  $("#runs").innerHTML = runs.map((r) => `<li data-id="${esc(r.id)}" class="${r.id === state.runId ? "on" : ""}">
      <i class="dot ${esc(r.status)}"></i>
      <div><div class="v">${esc(r.verb)}</div><div class="m">${esc(r.id.slice(0, 15))} · ${r.candidates} cand. · ${Math.round(r.seconds || 0)}s</div></div>
      <span class="m">${r.winner ? "→ " + esc(r.winner) : ""}</span></li>`).join("") || `<li class="m">No runs yet.</li>`;
  for (const li of $("#runs").querySelectorAll("li[data-id]")) li.onclick = () => openRun(li.dataset.id);
  return runs;
}

$("#examples").innerHTML = EXAMPLES.map(([k], i) => `<span class="chip" data-i="${i}">${k}</span>`).join("");
for (const c of $("#examples").querySelectorAll(".chip")) c.onclick = () => { $("#task").value = EXAMPLES[c.dataset.i][1]; };

$("#new").onsubmit = async (ev) => {
  ev.preventDefault();
  const task = $("#task").value.trim();
  if (task.length < 8) return $("#task").focus();
  $("#go").disabled = true;
  try {
    const models = $("#models").value ? $("#models").value.split(",") : [];
    const { id } = await api("/api/runs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, candidates: +$("#ncand").value, rounds: +$("#nrounds").value, models, critique: $("#critique").checked }),
    });
    state.sel = null;
    await openRun(id);
    loadRuns();
  } catch (e) {
    alert(e.message);
  } finally {
    $("#go").disabled = false;
  }
};

// ---- run --------------------------------------------------------------------------------------------

async function openRun(id) {
  if (state.runId !== id) { state.sel = null; state.sig = ""; }
  state.runId = id;
  history.replaceState(null, "", "#" + id);
  clearTimeout(state.timer);
  await poll();
}

async function poll() {
  try {
    const run = await api(`/api/runs/${encodeURIComponent(state.runId)}`);
    state.run = run;
    render(run);
    if (run.status === "running") state.timer = setTimeout(poll, 1000);
    else loadRuns();
  } catch (e) {
    console.error(e);
    state.timer = setTimeout(poll, 3000);
  }
}

function render(run) {
  $("#empty").hidden = true;
  $("#run").hidden = false;
  $("#r-verb").textContent = run.task.verb;
  $("#r-desc").textContent = run.task.description;
  const passed = run.candidates.filter((c) => c.status === "passed").length;
  const modelSecs = run.calls.reduce((a, c) => a + c.seconds, 0);
  $("#r-stats").innerHTML = [
    ["status", run.status, "status"], ["time", `${Math.round(run.seconds)}s`], ["candidates", `${passed}/${run.candidates.length} pass`],
    ["model calls", run.calls.length], ["tokens", (run.tokens || 0).toLocaleString()], ["model time", `${Math.round(modelSecs)}s`],
  ].map(([k, v, cls]) => `<div class="stat ${cls || ""}"><div class="k">${k}</div><div class="v ${cls ? esc(v) : ""}">${esc(v)}</div></div>`).join("");
  if (!state.sel || !run.candidates.find((c) => c.id === state.sel)) state.sel = run.winner || run.candidates.at(-1)?.id || null;
  drawTree(run);
  drawLog(run);
  drawCalls(run);
  const sig = JSON.stringify([state.sel, state.tab, state.file, run.candidates.find((c) => c.id === state.sel), run.winner, run.status]);
  if (sig !== state.sig) { state.sig = sig; drawDetail(run); }
}

// ---- branch tree --------------------------------------------------------------------------------------

function drawTree(run) {
  const el = $("#tree");
  el.innerHTML = "";
  const cands = run.candidates;
  const kids = {};
  for (const c of cands) (kids[c.parent || "env"] ||= []).push(c);
  const depth = {}, row = {};
  let next = 0;
  const visit = (id, d) => {
    depth[id] = d;
    const ch = kids[id] || [];
    if (!ch.length) { row[id] = next++; return row[id]; }
    const rows = ch.map((c) => visit(c.id, d + 1));
    row[id] = rows.reduce((a, b) => a + b, 0) / rows.length;
    return row[id];
  };
  visit("env", 0);
  const box = el.parentElement.clientWidth - 30 || 800, nW = 172, nH = 58, maxD = Math.max(1, ...Object.values(depth));
  const colW = Math.max(nW + 36, Math.min(240, (box - nW - 20) / maxD));
  const W = Math.max(box, 20 + maxD * colW + nW);  // wider than the card: it scrolls sideways
  const H = Math.max(200, next * (nH + 14) + 40);
  el.setAttribute("viewBox", `0 0 ${W} ${H}`);
  el.style.width = W + "px";
  el.style.height = H + "px";
  const pos = (id) => ({ x: 10 + depth[id] * colW, y: 30 + row[id] * (nH + 14) + (row.env !== undefined && id === "env" ? 0 : 0) });
  for (let d = 0; d <= maxD; d++) svg("text", { x: 10 + d * colW, y: 14, class: "round" }, el).textContent = d === 0 ? "checkpoint" : d === 1 ? "candidates" : `repair ${d - 1}`;
  for (const c of cands) {
    const a = pos(c.parent || "env"), b = pos(c.id);
    const x1 = a.x + (c.parent ? nW : 120), y1 = a.y + nH / 2, x2 = b.x, y2 = b.y + nH / 2, mx = (x1 + x2) / 2;
    svg("path", { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, class: `edge ${c.kind === "polish" ? "polish" : ""}` }, el);
  }
  // the environment checkpoint
  const e = pos("env");
  const g0 = svg("g", { class: "node", transform: `translate(${e.x},${e.y})` }, el);
  svg("rect", { width: 120, height: nH }, g0);
  svg("text", { x: 10, y: 21 }, g0).textContent = "📦 environment";
  svg("text", { x: 10, y: 39, class: "small" }, g0).textContent = run.sandbox?.backend === "contree" ? "Sandboxes image" : "local checkpoint";
  svg("text", { x: 10, y: 52, class: "small" }, g0).textContent = String(run.sandbox?.environment || "").slice(-14);
  for (const c of cands) {
    const p = pos(c.id);
    const g = svg("g", { class: `node ${c.id === state.sel ? "sel" : ""} ${c.id === run.winner ? "winner" : ""}`, transform: `translate(${p.x},${p.y})` }, el);
    g.onclick = () => { state.sel = c.id; render(state.run); };
    svg("rect", { width: nW, height: nH }, g);
    svg("circle", { cx: 12, cy: 15, r: 4.5, fill: statusColor(c.status) }, g);
    svg("text", { x: 22, y: 19 }, g).innerHTML = `<tspan font-weight="700">${esc(c.id)}</tspan> <tspan class="small">${esc(c.kind)}${c.kind === "write" && c.approach ? " · " + esc(c.approach) : ""}</tspan>`;
    svg("text", { x: 10, y: 34, class: "small" }, g).textContent = `${short(c.model)} · t=${c.temperature}${c.sandbox_seconds ? ` · ${c.sandbox_seconds}s` : ""}`;
    const rep = c.report || {};
    bar(g, 10, 42, 70, rep.contract, "contract suite");
    bar(g, 86, 42, 44, rep.task, "own tests");
    if (c.critique && c.critique.score) svg("text", { x: nW - 8, y: 50, "text-anchor": "end", fill: c.critique.accepted ? "#76b900" : "#f5a524", "font-weight": 700 }, g).textContent = `👁 ${c.critique.score}`;
    if (c.id === run.winner) svg("text", { x: nW - 8, y: 19, "text-anchor": "end", fill: "#76b900", "font-size": 11 }, g).textContent = "✓ shipped";
  }
}

function statusColor(s) {
  return { passed: "#76b900", failed: "#e5484d", error: "#e5484d" }[s] || "#f5a524";
}

function bar(g, x, y, w, s, label) {
  svg("rect", { x, y, width: w, height: 7, rx: 3, class: "bar-bg" }, g);
  if (!s || !s.total) return;
  const ok = (w * s.passed) / s.total;
  svg("rect", { x, y, width: ok, height: 7, rx: 3, class: "bar-ok" }, g);
  if (s.failed) svg("rect", { x: x + ok, y, width: w - ok, height: 7, rx: 3, class: "bar-bad" }, g);
  const t = svg("title", {}, g);
  t.textContent = `${label}: ${s.passed}/${s.total}`;
}

// ---- log --------------------------------------------------------------------------------------------

function drawLog(run) {
  const el = $("#log");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  el.innerHTML = run.events.map((e) => `<li class="${esc(e.type)}"><span class="t">${e.t.toFixed(1)}s</span><span>${ICON[e.type] || ""}</span><span>${esc(e.msg)}</span></li>`).join("");
  if (atBottom) el.scrollTop = el.scrollHeight;
}

// ---- model calls ------------------------------------------------------------------------------------

function drawCalls(run) {
  const el = $("#calls");
  el.innerHTML = "";
  const done = new Set(run.calls.map((c) => c.purpose));
  const pending = run.status === "running" ? run.events.filter((e) => e.type === "call_start" && !done.has(e.purpose))
    .map((e) => ({ purpose: e.purpose, model: e.model, t: e.t, seconds: Math.max(0.5, run.seconds - e.t), prompt_tokens: 0, completion_tokens: 0, think: true, pending: true })) : [];
  const calls = [...run.calls, ...pending];
  const W = el.clientWidth || 800, T = Math.max(run.seconds || 1, ...calls.map((c) => c.t + c.seconds), 1);
  const lanes = [];
  const lane = calls.map((c) => {
    let i = lanes.findIndex((end) => end <= c.t + 0.01);
    if (i < 0) { i = lanes.length; lanes.push(0); }
    lanes[i] = c.t + c.seconds;
    return i;
  });
  const H = 24 + Math.max(1, lanes.length) * 20;
  el.setAttribute("viewBox", `0 0 ${W} ${H}`);
  el.style.height = H + "px";
  const x = (t) => 70 + ((W - 80) * t) / T;
  svg("line", { x1: 70, x2: W - 10, y1: H - 16, y2: H - 16, class: "axis" }, el);
  for (let k = 0; k <= 5; k++) svg("text", { x: x((T * k) / 5), y: H - 3, "text-anchor": "middle" }, el).textContent = `${Math.round((T * k) / 5)}s`;
  calls.forEach((c, i) => {
    const y = 4 + lane[i] * 20;
    const r = svg("rect", { x: x(c.t), y, width: Math.max(3, x(c.t + c.seconds) - x(c.t)), height: 15, rx: 3, fill: modelColor(c.model),
      opacity: c.ok === false ? 0.35 : 0.9, class: c.pending ? "pending" : "" }, el);
    svg("title", {}, r).textContent = c.pending ? `${c.purpose} · ${short(c.model)} · waiting…` : `${c.purpose} · ${short(c.model)} · ${c.seconds}s · ${c.prompt_tokens}→${c.completion_tokens} tokens`;
    if (x(c.t + c.seconds) - x(c.t) > 60) svg("text", { x: x(c.t) + 4, y: y + 11, fill: "#0b0d10", style: "fill:#0b0d10" }, el).textContent = c.purpose;
  });
  svg("text", { x: 4, y: 15 }, el).textContent = "timeline";
  $("#ctable").innerHTML = `<tr><th>#</th><th>purpose</th><th>model</th><th>reasoning</th><th class="n">latency</th><th class="n">prompt</th><th class="n">completion</th></tr>` +
    calls.map((c, i) => `<tr><td>${i + 1}</td><td>${esc(c.purpose)}${c.ok === false ? " ✖" : ""}</td><td><i class="sw" style="background:${modelColor(c.model)}"></i>${esc(short(c.model))}</td>
      <td>${c.think ? "on" : "off"}</td><td class="n">${c.pending ? "…" : c.seconds.toFixed(1) + "s"}</td><td class="n">${c.pending ? "" : c.prompt_tokens.toLocaleString()}</td><td class="n">${c.pending ? "" : c.completion_tokens.toLocaleString()}</td></tr>`).join("");
}

// ---- candidate detail ---------------------------------------------------------------------------------

for (const b of $("#tabs").querySelectorAll("button")) b.onclick = () => {
  state.tab = b.dataset.tab;
  for (const x of $("#tabs").querySelectorAll("button")) x.classList.toggle("on", x === b);
  for (const p of document.querySelectorAll(".pane")) p.hidden = p.id !== "p-" + state.tab;
  if (state.run) render(state.run);
};

const fileUrl = (path) => `/api/runs/${encodeURIComponent(state.runId)}/file/${path}`;

function drawDetail(run) {
  const c = run.candidates.find((x) => x.id === state.sel);
  if (!c) { $("#d-head").innerHTML = `<span class="m">Waiting for candidates…</span>`; return; }
  const rep = c.report || {};
  const tag = (ok, text) => `<span class="tag ${ok ? "ok" : "bad"}">${esc(text)}</span>`;
  $("#d-head").innerHTML = `<span class="id">${esc(c.id)}</span><span class="tag">${esc(c.kind)}${c.parent ? " of " + esc(c.parent) : ""}</span>
    <span class="tag">${esc(short(c.model))} · t=${c.temperature}</span>${c.approach ? `<span class="tag">${esc(c.approach)}</span>` : ""}
    <span class="tag">${esc(c.status)}</span>${c.id === run.winner ? tag(true, "shipped") : ""}
    ${c.checkpoint ? `<span class="tag" title="${esc(c.checkpoint)}">checkpoint …${esc(String(c.checkpoint).slice(-10))}</span>` : ""}`;
  drawTests(c, rep);
  drawMotion(c);
  drawCode(c);
  drawDiff(c);
  drawShip(run);
}

function drawTests(c, rep) {
  const cases = rep.cases || [];
  const suite = (name, title) => {
    const cs = cases.filter((x) => x.suite === name);
    const s = rep[name] || {};
    return `<div class="suite"><h4><span>${title}</span><span>${s.passed || 0}/${s.total || 0}</span></h4>${cs.map((x) => {
      const ok = x.outcome === "passed";
      return `<div class="case"><span class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>${ok ? `<span>${esc(x.name)}</span>` :
        `<details><summary>${esc(x.name)}</summary><pre>${esc(x.message)}</pre></details>`}</div>`;
    }).join("") || `<div class="case"><span></span><span class="m">no results</span></div>`}</div>`;
  };
  const p = rep.probe || {};
  const checks = rep.cases ? [
    [rep.install_ok, "package installs"], [p.registered, `registers ${state.run.task.verb}`], [p.in_schema, "in JSON Schema"],
    [p.in_prompt, "in planner prompt"], [rep.demo_ok, "demo validates"],
  ] : [];
  $("#p-tests").innerHTML = (c.notes ? `<div class="notes">${esc(c.notes)}</div>` : "") +
    `<div class="probe">${checks.map(([ok, t]) => `<span class="tag ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"} ${esc(t)}</span>`).join("")}</div>` +
    `<div class="suites">${suite("contract", "Generic contract suite (SPEC §11)")}${suite("task", "Candidate's own tests")}</div>` +
    ((rep.problems || []).length && !rep.passed ? `<div class="problems"><div class="lbl">Sent back for repair</div><pre>${esc(rep.problems.join("\n\n"))}</pre></div>` : "") +
    (c.note ? `<div class="problems"><pre>${esc(c.note)}</pre></div>` : "");
}

function drawMotion(c) {
  if (!c.has_frames) { $("#p-motion").innerHTML = `<p class="m">No frames: the demo didn't bake${c.report ? "" : " yet"}.</p>`; return; }
  const cr = c.critique;
  $("#p-motion").innerHTML = `<div class="motion"><div><img src="${fileUrl(`candidates/${c.id}/frames.png`)}" alt="contact sheet"></div>
    <div><iframe sandbox="allow-scripts" src="${fileUrl(`candidates/${c.id}/preview.html`)}"></iframe>
    ${cr ? `<div class="critique"><div class="lbl">Nemotron VL critique</div><span class="score ${cr.accepted ? "ok" : "bad"}">${cr.score}/10</span>
      ${cr.accepted ? " accepted" : " wants changes"}<div class="notes">${esc(cr.observations || cr.error || "")}</div>
      ${(cr.issues || []).length ? `<ul>${cr.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
      ${cr.suggestion ? `<div class="notes">→ ${esc(cr.suggestion)}</div>` : ""}</div>` : `<p class="m">Not critiqued (only candidates that pass the tests are).</p>`}
    </div></div>`;
}

function drawCode(c) {
  const files = c.files || {};
  const names = Object.keys(files);
  if (!names.includes(state.file)) state.file = names[0] || "verb.py";
  $("#p-code").innerHTML = `<div class="code-files">${names.map((n) => `<button class="chip ${n === state.file ? "on" : ""}" data-f="${esc(n)}">${esc(n)}</button>`).join("")}</div>
    <pre class="code">${esc(files[state.file] || "(not written yet)")}</pre>`;
  for (const b of $("#p-code").querySelectorAll("[data-f]")) b.onclick = () => { state.file = b.dataset.f; render(state.run); };
}

function drawDiff(c) {
  const lines = (c.diff || "(no diff yet)").split("\n").map((l) => {
    const cls = l.startsWith("+++") || l.startsWith("---") ? "file" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return `<span class="${cls}">${esc(l)}</span>`;
  });
  $("#p-diff").innerHTML = `<div class="notes">${c.parent ? `Changes from ${esc(c.parent)}` : "The whole package (a new plugin)"}</div><pre class="diff">${lines.join("\n")}</pre>`;
}

function drawShip(run) {
  if (!run.winner) { $("#p-ship").innerHTML = `<p class="m">${run.status === "running" ? "Still working…" : "Nothing shipped."}</p>`; return; }
  const w = run.candidates.find((c) => c.id === run.winner);
  $("#p-ship").innerHTML = `<div class="ship-grid"><div>
      <div class="lbl">Plugin package</div>
      <p>${w.status === "passed" ? "✅" : "⚠️"} <b>${esc(run.task.dist)}</b> from candidate <b>${esc(run.winner)}</b>, registering <code>${esc(run.task.verb)}</code> under the <code>motionlang.verbs</code> entry point.</p>
      ${w.critique && !w.critique.accepted ? `<p>⚠️ It passes every test, but the critic wasn't satisfied (${w.critique.score}/10): ${esc((w.critique.issues || [])[0] || w.critique.observations || "")}</p>` : ""}
      <div class="cmd">uv pip install out/motionharness/${esc(run.id)}/plugin</div>
      <p class="notes">Installs into the workspace's venv, where motionlang is. motionlang isn't on PyPI yet, so with plain pip
        in another environment, install it first: <code>pip install packages/motionlang out/motionharness/${esc(run.id)}/plugin</code></p>
      <div class="cmd">uv run motionlang prompt | grep ${esc(run.task.verb)}</div>
      <p><a href="${fileUrl("report.md")}" target="_blank">report.md</a> · <a href="${fileUrl("plugin.diff")}" target="_blank">plugin.diff</a> ·
         <a href="${fileUrl("preview.html")}" target="_blank">preview.html</a> · <a href="${fileUrl("frames.png")}" target="_blank">frames.png</a></p>
    </div><div>${w.has_frames ? `<img style="width:100%;border-radius:8px" src="${fileUrl("frames.png")}">` : ""}</div></div>`;
}

// ---- boot -------------------------------------------------------------------------------------------

window.addEventListener("resize", () => state.run && render(state.run));
loadInfo().catch((e) => ($("#info").textContent = e.message));
loadRuns().then((runs) => {
  const id = location.hash.slice(1) || runs[0]?.id;
  if (id) openRun(id);
});
