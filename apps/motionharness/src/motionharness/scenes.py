"""Try a shipped verb in a scene: plan the same prompt with and without it, side by side.

The planner (Nemotron, on the host) gets the system prompt that SketchDynamics' planner would get, built inside a
sandbox: from the winner's checkpoint (plugin installed) for "with", from the environment checkpoint for "without".
Every planned document is validated and baked in that same sandbox, so model-written verb code never runs here.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from motionlang.agent import Call
from motionlang.agent.llm import extract_json_text
from motionlang.agent.prompts import generation_user, repair_user
from motionlang.preview import preview_html

from .harness import MODELS, Harness
from .sandbox import Checkpoint, Job

CANVAS = {"width": 1280, "height": 720, "background": "#ffffff", "fps": 30}
SIDES = ("with", "without")


CONTAINERS = ("seq", "par", "stagger", "repeat")


def fill_block_ids(doc: dict) -> dict:
    """Give every timeline block without an id a fresh one (b1, b2, …). Models often forget the id on a block
    nested in a container (`repeat`'s child); that's bookkeeping, not part of what the plan means."""
    used: set[str] = set(a.get("id") for a in doc.get("actors") or [] if isinstance(a, dict))

    def collect(node):
        if isinstance(node, dict):
            if isinstance(node.get("id"), str):
                used.add(node["id"])
            for k in CONTAINERS:
                collect(node.get(k))
        elif isinstance(node, list):
            for n in node:
                collect(n)

    counter = [0]

    def fresh() -> str:
        while True:
            counter[0] += 1
            if (bid := f"b{counter[0]}") not in used:
                used.add(bid)
                return bid

    def fill(node):
        if isinstance(node, dict):
            if ("verb" in node or any(k in node for k in CONTAINERS)) and not (isinstance(node.get("id"), str) and node["id"]):
                node["id"] = fresh()
            for k in CONTAINERS:
                fill(node.get(k))
        elif isinstance(node, list):
            for n in node:
                fill(n)

    tl = doc.get("timeline")
    collect(tl)
    fill(tl)
    return doc


def _at(doc: Any, pointer: str) -> Any:
    node = doc
    for part in pointer.strip("/").split("/"):
        if part == "":
            continue
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def with_excerpts(errors: list[str], doc: dict | None) -> list[str]:
    """Quote the part of the document each error points at, so a repair can find it."""
    import re

    out = []
    for e in errors:
        m = re.search(r"\[at (/[^\]]*)\]", e)
        node = _at(doc, m.group(1)) if (m and doc is not None) else None
        out.append(e + (f"\n  that part of your document is: {json.dumps(node)[:400]}" if node is not None else ""))
    return out


def _scene_file() -> bytes:
    return (resources.files("motionharness") / "sandbox_files" / "scene.py").read_bytes()


class SceneTrial:
    """One prompt planned twice. Persisted to <run>/scenes/<n>.json after every step (the dashboard polls it)."""

    def __init__(self, run_dir: Path, prompt: str):
        self.dir = run_dir / "scenes"
        self.dir.mkdir(exist_ok=True)
        self.n = len(list(self.dir.glob("*.json"))) + 1
        self.data: dict[str, Any] = {
            "n": self.n, "prompt": prompt, "created": datetime.now().isoformat(timespec="seconds"), "status": "running",
            "sides": {s: {"status": "waiting", "attempts": [], "ok": False, "uses_verb": False, "verbs": [], "problems": []} for s in SIDES},
        }
        self._lock = threading.Lock()
        self.save()

    def update(self, side: str | None = None, **kw: Any) -> None:
        with self._lock:
            (self.data["sides"][side] if side else self.data).update(kw)
            self.save()

    def tokens(self) -> int:
        return sum(a["prompt_tokens"] + a["completion_tokens"] for s in self.data["sides"].values() for a in s["attempts"])

    def save(self) -> None:
        tmp = self.dir / f"{self.n}.json.tmp"
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.dir / f"{self.n}.json")


def list_trials(run_dir: Path) -> list[dict]:
    d = run_dir / "scenes"
    if not d.is_dir():
        return []
    out = []
    for p in sorted((p for p in d.glob("*.json") if p.stem.isdigit()), key=lambda p: int(p.stem)):
        try:
            out.append(json.loads(p.read_text()))
        except ValueError:
            continue
    return out


class SceneTrier:
    def __init__(self, harness: Harness, max_repairs: int = 2):
        self.h = harness
        self.max_repairs = max_repairs

    def _sandbox(self, base: Checkpoint, shell: str, files: dict[str, bytes], collect: str) -> bytes | None:
        r = self.h.sandbox.run(Job(base, {"scene.py": _scene_file(), **files}, shell, collect=[collect], timeout=120, label="scene"))
        return r.files.get(collect)

    def system_prompt(self, run_dir: Path, side: str, base: Checkpoint) -> str:
        cache = run_dir / "scenes" / f"system-{side}.txt"
        if cache.exists():
            return cache.read_text()
        py = self.h.sandbox.python
        raw = self._sandbox(base, f"{_py(py)} scene.py system", {}, "out/system.txt")
        if raw is None:
            raise RuntimeError(f"couldn't build the planner prompt in the {side} sandbox")
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(raw.decode())
        return raw.decode()

    def check(self, base: Checkpoint, doc: dict) -> dict:
        py = self.h.sandbox.python
        raw = self._sandbox(base, f"{_py(py)} scene.py check doc.json", {"doc.json": json.dumps(doc).encode()}, "out/scene.json")
        return json.loads(raw) if raw else {"ok": False, "errors": ["the sandbox returned nothing"], "verbs": [], "tracks": None}

    def plan(self, trial: SceneTrial, side: str, base: Checkpoint, verb: str, run_dir: Path, cast: list | None = None) -> dict | None:
        """Plan the trial's prompt on one side; returns the final document. `cast`: actors to reuse unchanged."""
        trial.update(side, status="planning")
        try:
            system = self.system_prompt(run_dir, side, base)
        except RuntimeError as e:
            trial.update(side, status="error", problems=[str(e)])
            return None
        user = generation_user(trial.data["prompt"], CANVAS)
        if cast:
            user += ("\n\nUse exactly these actors, unchanged (same ids, shapes, styles and init), and write the timeline "
                     "and constraints for them; another version of this scene uses the same cast, so only the motion should differ:\n"
                     + json.dumps(cast, separators=(",", ":")))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        model = MODELS["super"]
        for attempt in range(self.max_repairs + 1):
            t = time.time()
            try:
                text, usage = self.h.llm.backend.complete(model, messages, response_format={"type": "json_object"},
                                                          max_tokens=12000, temperature=0.3)
            except Exception as e:
                trial.update(side, status="error", problems=[f"planner call failed: {e}"])
                return None
            call = Call(f"scene {trial.n} {side}", model, time.time() - t, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            try:
                doc = json.loads(extract_json_text(text))
                doc["motion"], doc["canvas"] = "0.1", {**CANVAS, **{k: v for k, v in (doc.get("canvas") or {}).items() if k == "background"}}
                if cast:
                    doc["actors"] = json.loads(json.dumps(cast))  # the shared cast wins over anything the model changed
                fill_block_ids(doc)
                if side == "with" and verb in json.dumps(doc.get("timeline")):
                    doc["requires"] = sorted(set(doc.get("requires") or []) | {verb})
                trial.update(side, status="checking")
                res = self.check(base, doc)
            except (ValueError, TypeError) as e:
                doc, res = None, {"ok": False, "errors": [f"reply was not a JSON document ({e})"], "verbs": [], "tracks": None}
            a = {"kind": "plan" if attempt == 0 else "repair", "seconds": round(call.seconds, 1), "prompt_tokens": call.prompt_tokens,
                 "completion_tokens": call.completion_tokens, "ok": res["ok"], "problems": res["errors"][:6]}
            attempts = trial.data["sides"][side]["attempts"] + [a]
            trial.update(side, attempts=attempts, verbs=res.get("verbs", []), uses_verb=verb in res.get("verbs", []),
                         problems=res["errors"][:6], ok=res["ok"])
            if res.get("tracks"):
                (trial.dir / f"{trial.n}-{side}.html").write_text(preview_html(res["tracks"], title=f"{side} {verb}: {trial.data['prompt'][:60]}"))
                (trial.dir / f"{trial.n}-{side}.json").write_text(json.dumps(doc, indent=1))
                trial.update(side, preview=f"scenes/{trial.n}-{side}.html", doc=f"scenes/{trial.n}-{side}.json")
            if res["ok"]:
                break
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": repair_user(with_excerpts(res["errors"][:12], doc))}]
        trial.update(side, status="done" if trial.data["sides"][side]["ok"] else "failed")
        return doc if trial.data["sides"][side]["ok"] else None

    def run(self, run: dict, run_dir: Path, prompt: str) -> SceneTrial:
        """Synchronously plan `prompt` with and without the run's winning verb."""
        trial = SceneTrial(run_dir, prompt)
        self.start(run, run_dir, trial, background=False)
        return trial

    def start(self, run: dict, run_dir: Path, trial: SceneTrial, background: bool = True, on_done=None) -> None:
        winner = next((c for c in run["candidates"] if c["id"] == run.get("winner")), None)
        if not winner or not winner.get("checkpoint") or not (winner.get("report") or {}).get("passed"):
            trial.update(status="error", error="this run has no passing candidate to try")
            return
        backend = self.h.sandbox.name
        with_cp = Checkpoint(winner["checkpoint"], backend, "winner")
        env_cp = self.h.sandbox.environment()
        verb = run["task"]["verb"]

        def go() -> None:
            # Core-only first; its cast is then reused on the "with" side, so the two versions differ only in motion.
            base = self.plan(trial, "without", env_cp, verb, run_dir)
            cast = base.get("actors") if base else None
            trial.update(same_cast=bool(cast))
            self.plan(trial, "with", with_cp, verb, run_dir, cast=cast)
            trial.update(status="done")
            if on_done:
                on_done(trial)

        if background:
            threading.Thread(target=go, daemon=True).start()
        else:
            go()


def _py(py: str) -> str:
    return py if py == "python" else "'" + py.replace("'", "'\"'\"'") + "'"

