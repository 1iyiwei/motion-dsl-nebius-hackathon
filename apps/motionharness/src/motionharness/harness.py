"""The agent loop.

    write      N candidates in parallel (different approaches / temperatures), each a plugin module, its own
               tests and a demo scene
    test       each candidate runs in a sandbox branched from one prepared checkpoint: install the package,
               run the generic contract suite + its own tests, bake the demo
    critique   passing candidates' demos are rendered to a contact sheet; Nemotron VL reviews the motion
    repair     the best failing candidates (or a passing one the critic didn't like) are rewritten from the
               test output / critique, branching from their own sandbox checkpoint
    ship       the winner becomes an installable plugin package + diff + test report + preview

Every step is an event in the run record (run.json), which the CLI prints and the dashboard draws.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from motionlang.agent import LLM, Call
from motionlang.agent.llm import extract_json_text, load_env
from motionlang.preview import preview_html

from . import prompts
from .plugin import NAME as NAME_RX, Report, Source, Task, package_files, parse_reply, report_markdown, unified_diff
from .render import contact_sheet, jpeg_data_url, motion_metrics, png_bytes
from .runner import job_for, parse_result
from .sandbox import Checkpoint, Sandbox

MODELS = {
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "vl": "nvidia/Nemotron-3.5-Super-VL",
}
APPROACHES = [
    ("closed form", "Derive a closed-form expression for the motion so sample() is exact at any u; keep the parameters few and meaningful."),
    ("physical", "Model the underlying physics explicitly (state the equations in the note), then check the contract: identity at start, no jump at the end, finite for extreme params."),
    ("minimal", "Write the simplest implementation that fully satisfies the task, with conservative parameter bounds and defensive numerics."),
]


def coder_model() -> str:
    return os.environ.get("MOTIONHARNESS_CODER_MODEL", MODELS["super"])


@dataclass
class Config:
    candidates: int = 3
    models: list[str] = field(default_factory=list)  # per candidate; default: the coder model for all
    max_rounds: int = 3  # repair rounds after the first
    repair_width: int = 2  # failing candidates repaired per round
    critique: bool = True
    accept_score: int = 7
    docs: bool = True  # look up external docs with Tavily when the task needs them
    timeout: float = 240  # per sandbox run; a verb this slow to bake is a defect in itself

    def model(self, i: int) -> str:
        m = self.models[i % len(self.models)] if self.models else coder_model()
        return MODELS.get(m, m)


@dataclass
class Candidate:
    id: str
    round: int
    kind: str  # write | repair | polish (repair after a critique)
    model: str
    temperature: float
    approach: str = ""
    parent: str | None = None
    status: str = "writing"  # writing | testing | critiquing | passed | failed | error
    source: Source | None = None
    report: Report | None = None
    critique: dict | None = None
    checkpoint: Checkpoint | None = None
    sandbox_seconds: float | None = None
    note: str = ""
    has_frames: bool = False
    files: dict[str, str] = field(default_factory=dict)  # package files
    diff: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.report and self.report.passed)

    def rank(self) -> tuple:
        crit = self.critique or {}
        return (self.passed, bool(crit.get("accepted")), crit.get("score", 0), *(self.report.score() if self.report else (False,)))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "round": self.round, "kind": self.kind, "model": self.model, "temperature": self.temperature,
            "approach": self.approach, "parent": self.parent, "status": self.status, "note": self.note,
            "notes": self.source.notes if self.source else "", "report": self.report.to_dict() if self.report else None,
            "critique": self.critique, "checkpoint": self.checkpoint.id if self.checkpoint else None,
            "sandbox_seconds": self.sandbox_seconds, "has_frames": self.has_frames,
            "files": self.source.as_dict() if self.source else {}, "diff": self.diff,
        }


class Run:
    """One task's run: its candidates, model calls and events, persisted to `dir/run.json` on every change."""

    def __init__(self, task: Task, config: Config, root: Path, sandbox: Sandbox, on_event: Callable[[dict], None] | None = None):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.id = f"{stamp}-{task.name}"
        self.dir = Path(root) / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        guard = Path(root) / "conftest.py"
        if not guard.exists():  # generated plugins carry tests; a repo-wide pytest run must not collect them
            guard.write_text('"""MotionHarness runs: keep pytest out of generated plugins."""\ncollect_ignore_glob = ["*"]\n')
        self.task, self.config, self.sandbox = task, config, sandbox
        self.candidates: list[Candidate] = []
        self.calls: list[dict] = []
        self.events: list[dict] = []
        self.status = "running"
        self.winner: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.env: dict = {}
        self.docs: dict | None = None
        self.naming: dict | None = None  # {"name", "suggested"} when the verb name reads as a noun
        self.on_event = on_event
        self._lock = threading.RLock()

    def event(self, kind: str, msg: str, **data: Any) -> None:
        e = {"t": round(time.time() - self.started, 2), "type": kind, "msg": msg, **data}
        with self._lock:
            self.events.append(e)
            self.save()
        if self.on_event:
            self.on_event(e)

    def add_call(self, call: Call, candidate: str | None = None, ok: bool | None = None) -> None:
        with self._lock:
            self.calls.append({"purpose": call.purpose, "model": call.model, "seconds": round(call.seconds, 2),
                               "prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens,
                               "think": call.think, "candidate": candidate, "ok": ok, "t": round(time.time() - self.started - call.seconds, 2)})
            self.save()

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "task": self.task.to_dict(), "status": self.status, "winner": self.winner,
                "config": {k: v for k, v in self.config.__dict__.items()}, "sandbox": {**self.sandbox.describe(), **self.env},
                "started": self.started, "seconds": round((self.finished or time.time()) - self.started, 1),
                "docs": self.docs, "naming": self.naming, "candidates": [c.to_dict() for c in self.candidates], "calls": self.calls,
                "events": self.events, "tokens": sum(c["prompt_tokens"] + c["completion_tokens"] for c in self.calls),
            }

    def save(self) -> None:
        with self._lock:
            tmp = self.dir / "run.json.tmp"
            tmp.write_text(json.dumps(self.to_dict(), indent=1))
            tmp.replace(self.dir / "run.json")


class Harness:
    def __init__(self, sandbox: Sandbox, llm: LLM | None = None, root: Path = Path("out/motionharness"), tavily: Any = None,
                 triage: bool = True):
        """triage: a fast check (Super, reasoning off) that the verb name reads as an action, before writing."""
        load_env()
        self.triage = triage
        self.sandbox = sandbox
        self.llm = llm or LLM()
        self.root = Path(root)
        self.tavily = tavily

    # ---- model calls ------------------------------------------------------------------------------

    def _complete(self, run: Run, model: str, purpose: str, messages: list[dict], cand: str | None = None, *,
                  temperature: float = 0.3, think: bool = True, json_mode: bool = False, max_tokens: int = 16000) -> str:
        kw: dict[str, Any] = {"max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        if not think:
            kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        run.event("call_start", f"{purpose} → {model.split('/')[-1]}", candidate=cand, model=model, purpose=purpose)
        t = time.time()
        try:
            text, usage = self.llm.backend.complete(model, messages, **kw)
        except Exception as e:
            call = Call(purpose, model, time.time() - t, 0, 0, think)
            run.add_call(call, cand, ok=False)
            raise RuntimeError(f"{purpose} failed: {type(e).__name__}: {str(e)[:300]}") from e
        call = Call(purpose, model, time.time() - t, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), think)
        run.add_call(call, cand, ok=True)
        return text

    # ---- steps --------------------------------------------------------------------------------------

    def check_name(self, run: Run) -> None:
        """Verbs read as actions ("the lamp swings"); a noun name (pendulum) reads as an object to draw. Advice only."""
        task = run.task
        word = prompts.naming_word(task.name)
        try:
            text = self._complete(run, MODELS["super"], "name check", [{"role": "user", "content": prompts.NAMING.format(
                name=task.name, description=task.description, word=word)}], think=False, json_mode=True, max_tokens=200, temperature=0)
            reply = json.loads(extract_json_text(text))
        except Exception as e:  # advice; never fail the run on it
            run.event("naming", f"name check skipped: {e}")
            return
        better = str(reply.get("action_name") or "").strip().strip("`").lower()
        if reply.get("natural") is False and better and better != task.name and NAME_RX.match(better):
            run.naming = {"name": task.verb, "suggested": f"{task.namespace}.{better}"}
            run.event("naming", f"\"the actor {word}s\" doesn't read as a verb, so a planner may take `{task.name}` for an object "
                                f"to draw; consider `{better}` (rerun with --name {better})", suggested=better)

    def lookup_docs(self, run: Run) -> str:
        if not (run.config.docs and self.tavily):
            return ""
        try:
            text = self._complete(run, MODELS["nano"], "docs? (decide)", [{"role": "user", "content": prompts.DOCS.format(description=run.task.description)}],
                                  think=False, json_mode=True, max_tokens=300, temperature=0)
            reply = json.loads(extract_json_text(text))
            external = str(reply.get("external") or "none").strip()
            queries = [q for q in reply.get("queries", []) if isinstance(q, str)][:2] if external.lower() not in ("none", "") else []
        except Exception as e:  # docs are a bonus; never fail the run on them
            run.event("docs", f"doc lookup skipped: {e}")
            return ""
        if not queries:
            run.docs = {"queries": [], "results": []}
            run.event("docs", "no external spec or API involved: no doc lookup")
            return ""
        results = []
        for q in queries:
            t = time.time()
            try:
                hits = self.tavily(q)
            except Exception as e:
                run.event("docs", f"Tavily search failed: {e}")
                continue
            results += [{**h, "query": q} for h in hits]
            run.event("docs", f"Tavily: {q!r} → {len(hits)} results in {time.time() - t:.1f}s")
        run.docs = {"queries": queries, "results": [{k: r.get(k) for k in ("title", "url", "query")} for r in results]}
        return "\n\n".join(f"[{r.get('title')}]({r.get('url')})\n{(r.get('content') or '')[:1500]}" for r in results[:4])

    def develop(self, run: Run, c: Candidate, messages: list[dict], base: Checkpoint, previous: Source | None, parent_files: dict[str, str]) -> None:
        """write → test → (render, critique) for one candidate. Runs in a worker thread."""
        task = run.task
        try:
            text = self._complete(run, c.model, f"{c.kind} {c.id}", messages, c.id, temperature=c.temperature)
        except RuntimeError as e:
            c.status, c.note = "error", str(e)
            run.event("candidate", f"{c.id}: model call failed", candidate=c.id)
            return
        c.source = parse_reply(text, previous)
        (run.dir / "candidates" / c.id).mkdir(parents=True, exist_ok=True)
        (run.dir / "candidates" / c.id / "reply.md").write_text(text)
        if c.source.missing():
            c.status, c.note = "failed", f"reply is missing {', '.join(c.source.missing())}"
            c.report = Report(platform_error=None)
            c.report.install_log = c.note
            run.event("candidate", f"{c.id}: {c.note}", candidate=c.id)
            return
        c.files = package_files(task, c.source)
        c.diff = unified_diff(parent_files, c.files)
        c.status = "testing"
        run.event("candidate", f"{c.id}: running tests in the sandbox (from {base.label or base.id[:12]})", candidate=c.id)
        r = self.sandbox.run(job_for(task, c.source, self.sandbox, base, c.id, timeout=run.config.timeout))
        c.sandbox_seconds = round(r.seconds, 1)
        c.checkpoint = r.checkpoint
        c.report = parse_result(task, r)
        tracks = (c.report.probe.get("demo") or {}).get("tracks")
        if tracks:
            self._render(run, c, tracks)
        cs, ts = c.report.suite("contract"), c.report.suite("task")
        c.status = "passed" if c.passed else ("error" if c.report.platform_error else "failed")
        run.event("tests", f"{c.id}: contract {cs['passed']}/{cs['total']}, own tests {ts['passed']}/{ts['total']}, demo {'ok' if c.report.demo_ok else 'invalid'} "
                  f"({c.sandbox_seconds}s in {self.sandbox.name})", candidate=c.id, passed=c.passed)
        if c.passed and run.config.critique and c.has_frames:
            self.critique(run, c)

    def _render(self, run: Run, c: Candidate, tracks: dict) -> None:
        d = run.dir / "candidates" / c.id
        try:
            img = contact_sheet(tracks, f"{run.task.verb} · candidate {c.id}", run.task.verb)
            (d / "frames.png").write_bytes(png_bytes(img))
            (d / "preview.html").write_text(preview_html(tracks, title=f"{run.task.verb} · {c.id}"))
            c.has_frames = True
        except Exception as e:  # a bad demo shouldn't crash the run; the tests already judged it
            c.note = f"render failed: {type(e).__name__}: {e}"

    def critique(self, run: Run, c: Candidate) -> None:
        from PIL import Image

        c.status = "critiquing"
        run.event("critique", f"{c.id}: Nemotron VL reviews the rendered motion", candidate=c.id)
        img = Image.open(run.dir / "candidates" / c.id / "frames.png").convert("RGB")
        summary = c.report.probe.get("summary", "") if c.report else ""
        tracks = (c.report.probe.get("demo") or {}).get("tracks") if c.report else None
        metrics = motion_metrics(tracks, run.task.verb) if tracks else []
        try:
            text = self._complete(run, os.environ.get("MOTION_VISION_MODEL", MODELS["vl"]), f"critique {c.id}",
                                  prompts.critic_messages(run.task, summary, jpeg_data_url(img), metrics), c.id, think=False, json_mode=True, max_tokens=800, temperature=0.2)
            crit = json.loads(extract_json_text(text))
            score = int(float(crit.get("score", 0)))
            issues = [str(i) for i in crit.get("issues") or [] if str(i).strip()]
            c.critique = {"score": score, "matches_intent": bool(crit.get("matches_intent", True)), "issues": issues,
                          "observations": str(crit.get("observations", ""))[:600], "suggestion": str(crit.get("suggestion", ""))[:600],
                          "metrics": metrics}
            c.critique["accepted"] = score >= run.config.accept_score and c.critique["matches_intent"]
        except Exception as e:
            c.critique = {"score": 0, "issues": [], "error": str(e)[:300], "accepted": True}  # don't block on the critic
        c.status = "passed"
        verdict = "accepted" if c.critique.get("accepted") else "wants changes"
        run.event("critique", f"{c.id}: {c.critique.get('score')}/10, {verdict}", candidate=c.id, critique=c.critique)

    # ---- the loop -----------------------------------------------------------------------------------

    def run(self, task: Task, config: Config | None = None, on_event: Callable[[dict], None] | None = None, run: Run | None = None) -> Run:
        config = config or Config()
        run = run or Run(task, config, self.root, self.sandbox, on_event)
        run.event("start", f"task: {task.description} → {task.verb}")
        try:
            self._loop(run)
        except Exception as e:
            run.status = "error"
            run.event("error", f"{type(e).__name__}: {e}")
        finally:
            run.finished = time.time()
            if run.status == "running":
                run.status = "done" if run.winner and next(c for c in run.candidates if c.id == run.winner).passed else "failed"
            run.event("end", f"{run.status}: winner {run.winner or 'none'} after {len(run.candidates)} candidates, "
                             f"{len(run.calls)} model calls, {round(run.finished - run.started)}s")
        return run

    def _loop(self, run: Run) -> None:
        cfg = run.config
        if self.triage:
            self.check_name(run)
        docs = self.lookup_docs(run)
        t = time.time()
        run.event("sandbox", f"preparing the {self.sandbox.name} environment (motionlang + test tools)…")
        env = self.sandbox.environment()
        run.env = {"environment": env.id, "env_seconds": round(time.time() - t, 1)}
        run.event("sandbox", f"environment checkpoint {env.id if self.sandbox.name != 'local' else 'ready'} ({time.time() - t:.1f}s)", checkpoint=env.id)

        fresh = []
        for i in range(cfg.candidates):
            label, hint = APPROACHES[i % len(APPROACHES)]
            fresh.append(Candidate(f"c{i + 1}", 0, "write", cfg.model(i), round(0.2 + 0.3 * (i % 3), 2), label))
        run.candidates += fresh
        run.event("round", f"round 0: writing {len(fresh)} candidates in parallel")
        self._develop_all(run, [(c, prompts.write_messages(run.task, dict(APPROACHES)[c.approach], docs), env, None, {}) for c in fresh])

        for rnd in range(1, cfg.max_rounds + 1):
            passing = sorted((c for c in run.candidates if c.passed), key=lambda c: c.rank(), reverse=True)
            if passing and (passing[0].critique is None or passing[0].critique.get("accepted")):
                break
            work = []
            if passing:  # tests pass but the critic wants changes: polish the best one
                p = passing[0]
                tip = self._tip(run, p)
                if tip is p:
                    work.append((p, "polish", [], p.critique))
                elif not tip.passed and tip.source and not tip.source.missing():
                    work.append((tip, "repair", tip.report.problems() if tip.report else [tip.note], p.critique))  # the polish broke something
                elif tip.passed and tip.critique and not tip.critique.get("accepted"):
                    work.append((tip, "polish", [], tip.critique))  # keep refining along the same branch
                else:
                    break  # nothing usable to continue from: keep the best
            else:
                leaves = [c for c in run.candidates if c.source and not c.source.missing() and not any(k.parent == c.id for k in run.candidates)]
                leaves.sort(key=lambda c: c.rank(), reverse=True)
                for p in leaves[: cfg.repair_width]:
                    work.append((p, "repair", p.report.problems() if p.report else [p.note], None))
                if not work:  # nothing parseable at all: start over with fresh candidates
                    c = Candidate(f"c{len(run.candidates) + 1}", rnd, "write", cfg.model(rnd), 0.3, "minimal")
                    run.candidates.append(c)
                    self._develop_all(run, [(c, prompts.write_messages(run.task, dict(APPROACHES)["minimal"], docs), env, None, {})])
                    continue
            jobs = []
            for p, kind, problems, crit in work:
                c = Candidate(f"c{len(run.candidates) + 1}", rnd, kind, p.model, 0.2, p.approach, parent=p.id)
                run.candidates.append(c)
                jobs.append((c, prompts.repair_messages(run.task, p.source, problems, crit), p.checkpoint or env, p.source, p.files))
            run.event("round", f"round {rnd}: {', '.join(f'{k} {p.id}' for p, k, _, _ in work)}")
            self._develop_all(run, jobs)

        best = max(run.candidates, key=lambda c: c.rank(), default=None)
        if best and best.source and not best.source.missing():
            run.winner = best.id
            self.ship(run, best)

    @staticmethod
    def _tip(run: Run, c: Candidate) -> Candidate:
        """The newest descendant along c's most recent branch (c itself if it has none)."""
        while True:
            kids = [k for k in run.candidates if k.parent == c.id]
            if not kids:
                return c
            c = kids[-1]

    def _develop_all(self, run: Run, jobs: list[tuple]) -> None:
        with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
            for f in [pool.submit(self.develop, run, *j) for j in jobs]:
                f.result()
        run.save()

    def ship(self, run: Run, c: Candidate) -> None:
        """Write the winner as an installable package with its diff, test report and preview."""
        report = c.report.to_dict() if c.report else {}
        files = package_files(run.task, c.source, report)
        out = run.dir / "plugin"
        for rel, text in files.items():
            p = out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (run.dir / "plugin.diff").write_text(unified_diff({}, files, prefix=run.task.dist))
        cand_dir = run.dir / "candidates" / c.id
        for name in ("frames.png", "preview.html"):
            if (cand_dir / name).exists():
                (run.dir / name).write_bytes((cand_dir / name).read_bytes())
        (run.dir / "report.md").write_text(self.report_md(run, c))
        run.event("ship", f"{c.id} → {out} ({'passed' if c.passed else 'did NOT pass'}; diff, report.md, preview.html)", candidate=c.id)

    @staticmethod
    def report_md(run: Run, c: Candidate) -> str:
        r = c.report.to_dict() if c.report else {}
        lines = [f"# MotionHarness report: `{run.task.verb}`", "", f"> {run.task.description}", "",
                 f"Winner: **{c.id}** ({c.kind}, {c.model.split('/')[-1]}, approach: {c.approach}). "
                 f"Status: **{'passed' if c.passed else 'failed'}**.", "", report_markdown(r), ""]
        if c.critique:
            lines += [f"Vision critique (Nemotron VL): **{c.critique.get('score')}/10**. {c.critique.get('observations', '')}", ""]
            lines += [f"- {i}" for i in c.critique.get("issues", [])]
        lines += ["", "## Candidates", "", "| id | parent | kind | model | contract | own tests | critique | status |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for k in run.candidates:
            rr = k.report.to_dict() if k.report else {"contract": {}, "task": {}}
            fmt = lambda s: f"{s.get('passed', 0)}/{s.get('total', 0)}"  # noqa: E731
            lines.append(f"| {k.id} | {k.parent or ''} | {k.kind} | {k.model.split('/')[-1]} | {fmt(rr['contract'])} | {fmt(rr['task'])} | "
                         f"{(k.critique or {}).get('score', '')} | {k.status} |")
        calls = run.calls
        lines += ["", f"Model calls: {len(calls)}, {sum(x['prompt_tokens'] + x['completion_tokens'] for x in calls)} tokens, "
                      f"{sum(x['seconds'] for x in calls):.0f}s of model time. Sandbox: {run.sandbox.name}.", ""]
        lines += ["## Tests (winner)", ""] + [f"- {'✅' if x['outcome'] == 'passed' else '❌'} [{x['suite']}] {x['name']}" for x in r.get("cases", [])]
        return "\n".join(lines) + "\n"


def tavily_search(api_key: str | None = None) -> Callable[[str], list[dict]] | None:
    """Tavily web search as a function, or None without a key."""
    key = api_key or os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    import httpx

    def search(query: str) -> list[dict]:
        r = httpx.post("https://api.tavily.com/search", json={"query": query, "max_results": 3, "search_depth": "basic"},
                       headers={"Authorization": f"Bearer {key}"}, timeout=30)
        r.raise_for_status()
        return [{"title": x.get("title"), "url": x.get("url"), "content": x.get("content")} for x in r.json().get("results", [])]

    return search



def delete_run(root: Path, run_id: str) -> dict:
    """Remove a finished run: its folder (plugin, scenes, reports) and the local sandbox images its candidates made.

    Token Factory Sandboxes images are left to the service's retention policy."""
    import shutil

    root = Path(root).resolve()
    d = (root / run_id).resolve()
    if d.parent != root or not (d / "run.json").exists():
        raise FileNotFoundError(f"no such run: {run_id}")
    record = json.loads((d / "run.json").read_text())
    if record.get("status") == "running":
        raise RuntimeError("the run is still in progress")
    sb = record.get("sandbox") or {}
    sandbox_root = Path(sb.get("root") or root / ".sandbox").resolve()
    images = 0
    for c in record.get("candidates", []):
        cp = c.get("checkpoint")
        if not cp or sb.get("backend") != "local":
            continue
        p = Path(cp).resolve()
        if p.parent == sandbox_root and p.name.startswith("img-") and p.is_dir():
            shutil.rmtree(p)
            images += 1
    shutil.rmtree(d)
    return {"deleted": run_id, "sandbox_images": images}
