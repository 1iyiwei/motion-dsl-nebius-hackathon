"""MotionHarness with a scripted model (no network) and the local sandbox backend."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from motionlang.agent import LLM
from motionharness.harness import Config, Harness
from motionharness.plugin import Source, Task, package_files, parse_reply, unified_diff
from motionharness.render import contact_sheet, path_polylines
from motionharness.runner import job_for, parse_result
from motionharness.sandbox import LocalSandbox

FIX = Path(__file__).parent / "fixtures"
GOOD = (FIX / "pulse_reply.md").read_text()
BROKEN = (FIX / "pulse_broken.md").read_text()


def crit(score: int, issues=()) -> str:
    return json.dumps({"score": score, "matches_intent": score >= 7, "observations": "a pulse", "issues": list(issues), "suggestion": "beat harder"})


class Scripted:
    """Replays canned replies (thread-safe) and records what it was asked."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def complete(self, model, messages, **kw):
        with self.lock:
            self.requests.append({"model": model, "messages": messages, **kw})
            return self.replies.pop(0), {"prompt_tokens": 100, "completion_tokens": 50}


def harness(tmp_path: Path, *replies: str) -> tuple[Harness, Scripted]:
    fake = Scripted(*replies)
    return Harness(LocalSandbox(tmp_path / "sb"), llm=LLM(fake), root=tmp_path / "runs", triage=False), fake


TASK = Task("add a `pulse` verb: a heartbeat-style scale pulse")


def test_task_names():
    assert (TASK.verb, TASK.module, TASK.dist, TASK.test_file) == ("mh.pulse", "mh_pulse", "motionlang-mh-pulse", "test_pulse.py")
    assert Task("add a spring_follow verb", namespace="acme").verb == "acme.spring_follow"
    assert Task("anything", name="acme.wobble").verb == "acme.wobble"
    with pytest.raises(ValueError):
        Task("x", name="Bad-Name")


def test_parse_reply_and_unchanged_files():
    src = parse_reply(GOOD)
    assert not src.missing() and src.notes.startswith("Design:")
    assert 'name = "mh.pulse"' in src.verb and "def test_registered" in src.tests and json.loads(src.demo)
    again = parse_reply("Tweaked the demo.\n\n### verb.py\n(unchanged)\n\n### demo.motion.json\n```json\n{}\n```", previous=src)
    assert again.verb == src.verb and again.tests == src.tests and again.demo == "{}"
    fenced = parse_reply("### verb.py\n```python\n(unchanged)\n```\n### test_verb.py\n```python\n(unchanged)\n```", previous=src)
    assert fenced.verb == src.verb and fenced.tests == src.tests and fenced.demo == src.demo


def test_package_and_diff():
    files = package_files(TASK, parse_reply(GOOD))
    assert 'pulse = "mh_pulse.verb"' in files["pyproject.toml"] and "src/mh_pulse/verb.py" in files
    assert "motionlang.testing.contract" in files["tests/test_contract.py"]
    diff = unified_diff({}, files)
    assert "+++ b/plugin/src/mh_pulse/verb.py" in diff and "--- /dev/null" in diff


def test_local_sandbox_gates_good_and_broken_plugins(tmp_path):
    sb = LocalSandbox(tmp_path)
    env = sb.environment()
    good, bad = sb.run_many([job_for(TASK, parse_reply(GOOD), sb, env, "good"), job_for(TASK, parse_reply(BROKEN), sb, env, "bad")])
    rg, rb = parse_result(TASK, good), parse_result(TASK, bad)
    assert rg.passed, rg.problems()
    assert rg.probe["in_schema"] and rg.probe["in_prompt"] and rg.suite("contract")["total"] == 6
    assert not rb.passed and rb.install_ok and rb.probe["registered"]
    assert any("identity_at_start" in p for p in rb.problems()), rb.problems()
    assert good.checkpoint and Path(good.checkpoint.id).is_dir() and good.checkpoint.id != env.id


def test_local_sandbox_hides_secrets(tmp_path, monkeypatch):
    from motionharness.sandbox import Job

    monkeypatch.setenv("NEBIUS_API_KEY", "should-not-leak")
    sb = LocalSandbox(tmp_path)
    r = sb.run(Job(sb.environment(), {}, 'echo "key=${NEBIUS_API_KEY:-none}"'))
    assert r.stdout.strip() == "key=none"


def test_slow_candidates_time_out_with_a_hint(tmp_path):
    from motionharness.sandbox import Job

    sb = LocalSandbox(tmp_path)
    r = sb.run(Job(sb.environment(), {}, "sleep 5", collect=["out/result.json"], timeout=0.5))
    assert r.exit_code == 124
    rep = parse_result(TASK, r)
    assert rep.timed_out and not rep.passed and "timed out" in rep.problems()[0]


def test_repairs_a_failing_candidate_then_ships(tmp_path):
    h, fake = harness(tmp_path, BROKEN, GOOD, crit(8))
    run = h.run(TASK, Config(candidates=1, max_rounds=2))
    assert run.status == "done" and run.winner == "c2"
    c1, c2 = run.candidates
    assert (c1.status, c2.status, c2.parent, c2.kind) == ("failed", "passed", "c1", "repair")
    assert c2.checkpoint.id != c1.checkpoint.id
    repair_prompt = fake.requests[1]["messages"][-1]["content"]
    assert "identity_at_start" in repair_prompt and "verbatim test output" in repair_prompt
    # the vision critic got the rendered contact sheet
    vision = fake.requests[2]
    assert vision["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert vision["response_format"] == {"type": "json_object"}
    out = run.dir
    assert "collect_ignore_glob" in (out.parent / "conftest.py").read_text()
    assert (out / "plugin" / "pyproject.toml").exists() and (out / "plugin" / "src" / "mh_pulse" / "verb.py").exists()
    assert (out / "frames.png").exists() and (out / "preview.html").exists()
    assert "mh.pulse" in (out / "report.md").read_text() and (out / "plugin.diff").read_text().startswith("--- /dev/null")
    record = json.loads((out / "run.json").read_text())
    assert [c["purpose"] for c in record["calls"]] == ["write c1", "repair c2", "critique c2"]
    assert record["status"] == "done" and any(e["type"] == "ship" for e in record["events"])


def test_critique_drives_a_polish(tmp_path):
    polished = GOOD.replace("amount: float = Field(0.2,", "amount: float = Field(0.35,")
    polished = polished[polished.index("### verb.py"):polished.index("### test_verb.py")] + "### test_verb.py\n(unchanged)\n\n### demo.motion.json\n(unchanged)\n"
    h, fake = harness(tmp_path, GOOD, crit(4, ["too subtle"]), polished, crit(9))
    run = h.run(TASK, Config(candidates=1, max_rounds=2))
    assert run.winner == "c2" and run.candidates[1].kind == "polish"
    assert run.candidates[0].critique["accepted"] is False and run.candidates[1].critique["accepted"] is True
    assert "too subtle" in fake.requests[2]["messages"][-1]["content"]
    assert "Field(0.35" in run.candidates[1].source.verb and run.candidates[1].source.tests == run.candidates[0].source.tests
    assert "-    amount: float = Field(0.2," in run.candidates[1].diff


def test_a_polish_that_breaks_tests_is_repaired(tmp_path):
    broken_verb = BROKEN[BROKEN.index("### verb.py"):BROKEN.index("### test_verb.py")] + "### test_verb.py\n(unchanged)\n### demo.motion.json\n(unchanged)\n"
    h, fake = harness(tmp_path, GOOD, crit(5, ["too subtle"]), broken_verb, GOOD, crit(8))
    run = h.run(TASK, Config(candidates=1, max_rounds=3))
    assert [(c.id, c.kind, c.parent, c.status) for c in run.candidates] == [
        ("c1", "write", None, "passed"), ("c2", "polish", "c1", "failed"), ("c3", "repair", "c2", "passed")]
    assert run.winner == "c3"
    prompt = fake.requests[3]["messages"][-1]["content"]
    assert "identity_at_start" in prompt and "too subtle" in prompt and "tests above broke" in prompt


def test_triage_flags_a_noun_verb_name(tmp_path):
    triage = json.dumps({"sentence": "the actor pulses", "natural": False, "action_name": "swing"})
    fake = Scripted(triage, GOOD, crit(9))
    h = Harness(LocalSandbox(tmp_path / "sb"), llm=LLM(fake), root=tmp_path / "runs")
    run = h.run(Task("add a `pendulum` verb: swings about a pivot", name="pulse"), Config(candidates=1))
    assert run.naming == {"name": "mh.pulse", "suggested": "mh.swing"} and run.status == "done"
    assert any(e["type"] == "naming" and "--name swing" in e["msg"] for e in run.events)
    assert '"the actor pulses"' in fake.requests[0]["messages"][0]["content"]
    from motionharness.prompts import naming_word

    assert [naming_word(n) for n in ("spring_to", "spring_follow", "pendulum", "move_along")] == ["spring", "follow", "pendulum", "move"]


def test_parallel_candidates_and_gives_up_cleanly(tmp_path):
    h, _ = harness(tmp_path, "no code here, sorry", BROKEN, "still nothing")
    run = h.run(TASK, Config(candidates=2, max_rounds=1, repair_width=1, critique=False))
    assert run.status == "failed" and len(run.candidates) == 3
    assert {c.status for c in run.candidates[:2]} == {"failed"}
    assert run.winner is not None  # the best attempt is still written out for inspection


def test_contact_sheet_and_paths():
    import motionlang

    doc = json.loads(parse_reply(GOOD).demo)
    doc["timeline"]["seq"][0] = {"id": "beat", "verb": "bounce", "actor": "heart", "height": 60, "count": 2}
    tracks = motionlang.check(doc).baked.to_json()
    img = contact_sheet(tracks, "bounce", "bounce")
    assert img.size == (1280, 720)
    polys = path_polylines("M0 0 L10 0 C10 10 20 10 20 0 Z m5 5 h3 v3 z")
    assert len(polys) == 2 and polys[0][-1] == (0.0, 0.0) and polys[1][0] == (5.0, 5.0)


def test_dashboard_api(tmp_path):
    from fastapi.testclient import TestClient

    from motionharness.server import create_app

    h, _ = harness(tmp_path, GOOD, crit(9))
    client = TestClient(create_app(tmp_path / "runs", harness=h))
    assert client.get("/api/info").json()["sandbox"]["backend"] == "local"
    rid = client.post("/api/runs", json={"task": "add a `pulse` verb: a heartbeat-style scale pulse", "candidates": 1}).json()["id"]
    for _ in range(300):
        run = client.get(f"/api/runs/{rid}").json()
        if run["status"] != "running":
            break
        time.sleep(0.1)
    assert run["status"] == "done" and run["winner"] == "c1"
    assert [r["id"] for r in client.get("/api/runs").json()] == [rid]
    png = client.get(f"/api/runs/{rid}/file/candidates/c1/frames.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert "sandbox" in client.get(f"/api/runs/{rid}/file/preview.html").headers["content-security-policy"]
    assert client.get(f"/api/runs/{rid}/file/../../etc/passwd").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404
    assert client.post("/api/runs", json={"task": "add a verb", "name": "Bad-Name"}).status_code == 422
    z = client.get(f"/api/runs/{rid}/plugin.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "motionlang-mh-pulse/pyproject.toml" in names and "motionlang-mh-pulse/src/mh_pulse/verb.py" in names
    assert "motionlang-mh-pulse/run/report.md" in names
    assert all(p["dist"] != "motionlang" for p in client.get("/api/installed").json())


class FakeContree:
    """Stands in for contree_sdk.Contree: an image is a dict of files; runs record what they were given."""

    def __init__(self, bundle: bytes, fail: set[str] = frozenset()):
        self.bundle, self.fail = bundle, fail
        self.runs: list[dict] = []
        self.images = _Images(self)


class _Images:
    def __init__(self, client):
        self.client = client
        self.store: dict[str, dict] = {}

    async def oci(self, ref):
        return _Image(self.client, f"oci:{ref}", {})

    async def use(self, uuid, strict=True):
        assert uuid in self.store, uuid
        return _Image(self.client, uuid, self.store[uuid])


class _Image:
    def __init__(self, client, uuid, files):
        self.client, self.uuid, self.files = client, uuid, files

    async def run(self, shell, files=None, cwd=None, timeout=None, disposable=True, truncate_output_at=None):
        c = self.client
        new = {**self.files, **(files or {})}
        uid = f"img-{len(c.runs)}"
        c.runs.append({"base": self.uuid, "shell": shell, "files": sorted(files or {}), "cwd": cwd, "disposable": disposable})
        if any(f in shell for f in c.fail):
            raise ConnectionError("sandbox API unavailable")
        if "pytest" in shell and "probe.py" in shell:
            new["/work/out/result.json"] = c.bundle
        c.images.store[uid] = new
        return _Result(uid if not disposable else None, new)


class _Result:
    def __init__(self, uuid, files):
        self.uuid, self.files, self.exit_code, self.stdout, self.stderr = uuid, files, 0, "probe done\n", ""

    async def read(self, path):
        return self.files[path]


def test_contree_backend_branches_from_checkpoints(tmp_path, monkeypatch):
    pytest.importorskip("contree_sdk")
    from motionharness.sandbox import ContreeSandbox

    local = LocalSandbox(tmp_path / "local")
    bundle = local.run(job_for(TASK, parse_reply(GOOD), local, local.environment(), "x")).files["out/result.json"]
    fake = FakeContree(bundle)
    sb = ContreeSandbox(cache=tmp_path / "cache.json")
    monkeypatch.setattr(sb, "_client", lambda: fake)

    env = sb.environment()
    first = fake.runs[0]
    assert first["base"].startswith("oci:docker.io/library/python") and not first["disposable"]
    assert "/opt/motionlang/src/motionlang/testing/contract.py" in first["files"] and "/work/test_contract.py" in first["files"]
    assert json.loads((tmp_path / "cache.json").read_text()) and ContreeSandbox(cache=tmp_path / "cache.json").environment().id == env.id

    a, b = sb.run_many([job_for(TASK, parse_reply(GOOD), sb, env, "c1"), job_for(TASK, parse_reply(BROKEN), sb, env, "c2")])
    assert {r["base"] for r in fake.runs[1:]} == {env.id} and all(not r["disposable"] and r["cwd"] == "/work" for r in fake.runs[1:])
    assert "/work/plugin/src/mh_pulse/verb.py" in fake.runs[1]["files"] and "pip install" in fake.runs[1]["shell"]
    assert parse_result(TASK, a).passed and a.checkpoint.id != env.id
    child = sb.run(job_for(TASK, parse_reply(GOOD), sb, a.checkpoint, "c3"))  # a repair branches from its parent's image
    assert fake.runs[-1]["base"] == a.checkpoint.id and child.checkpoint.id not in (env.id, a.checkpoint.id)

    fake.fail = {"pip install"}
    broken = sb.run(job_for(TASK, parse_reply(GOOD), sb, env, "c4"))
    assert broken.error and "ConnectionError" in broken.error and parse_result(TASK, broken).platform_error


def test_mcp_tools(tmp_path):
    pytest.importorskip("mcp")
    import anyio

    from motionharness.mcp_server import create_server

    h, _ = harness(tmp_path, GOOD, crit(8))
    server = create_server(tmp_path / "runs", harness=h)

    async def go():
        tools = {t.name for t in await server.list_tools()}
        assert tools == {"new_verb", "list_runs", "get_run", "read_artifact"}
        res = await server.call_tool("new_verb", {"task": "add a `pulse` verb: a heartbeat-style scale pulse", "candidates": 1})
        out = json.loads(res[0].text if isinstance(res, (list, tuple)) and hasattr(res[0], "text") else res[0][0].text)
        assert out["status"] == "done" and out["winner"] == "c1" and Path(out["plugin_dir"], "pyproject.toml").exists()
        report = await server.call_tool("read_artifact", {"run_id": out["id"], "path": "report.md"})
        assert "mh.pulse" in str(report)
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="not a readable artifact"):
            await server.call_tool("read_artifact", {"run_id": out["id"], "path": "../../secret"})

    anyio.run(go)


def test_auto_falls_back_to_local_when_sandboxes_are_inactive(tmp_path, monkeypatch):
    pytest.importorskip("contree_sdk")
    from motionharness.sandbox import ContreeSandbox, make_sandbox

    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "p")
    inactive = {"permissions": {"import": False, "spawn": False}, "limits": {}}
    monkeypatch.setattr(ContreeSandbox, "whoami", staticmethod(lambda timeout=10: (200, inactive)))
    sb = make_sandbox("auto", tmp_path)
    assert sb.name == "local" and "not activated" in sb.fallback_reason and "contree@nebius.com" in sb.describe()["fallback_reason"]
    with pytest.raises(RuntimeError, match="not activated"):
        make_sandbox("contree", tmp_path)
    monkeypatch.setattr(ContreeSandbox, "whoami", staticmethod(lambda timeout=10: (200, {"permissions": {"import": True, "spawn": True}})))
    assert make_sandbox("auto", tmp_path).name == "contree"
    monkeypatch.setattr(ContreeSandbox, "whoami", staticmethod(lambda timeout=10: (401, {"error": "bad key"})))
    assert "HTTP 401" in make_sandbox("auto", tmp_path).fallback_reason


def test_try_in_a_scene_plans_with_and_without_the_verb(tmp_path):
    from motionharness.scenes import SceneTrier, list_trials

    def scene(verb_block: dict) -> str:
        return json.dumps({"motion": "0.1", "actors": [{"id": "heart", "shape": {"kind": "ellipse", "rx": 40, "ry": 40}, "init": {"x": 640, "y": 360}}],
                           "timeline": {"id": "root", "seq": [verb_block]}})

    with_verb = scene({"id": "b1", "verb": "mh.pulse", "actor": "heart", "amount": 0.3, "beats": 2})
    broken = scene({"id": "b1", "verb": "mh.pulse", "actor": "heart"})  # unknown verb without the plugin
    core = scene({"id": "b1", "verb": "scale", "actor": "heart", "by": 0.3})
    h, fake = harness(tmp_path, GOOD, crit(9))
    run = h.run(TASK, Config(candidates=1))
    replies = {"with": [with_verb], "without": [broken, core]}

    class BySide:  # the two sides plan concurrently; answer each from its own script
        def complete(self, model, messages, **kw):
            side = "with" if "mh.pulse" in messages[0]["content"] else "without"
            return replies[side].pop(0), {"prompt_tokens": 1, "completion_tokens": 1}

    h.llm.backend = BySide()
    trial = SceneTrier(h).run(run.to_dict(), run.dir, "a heart beats")
    w, wo = trial.data["sides"]["with"], trial.data["sides"]["without"]
    assert trial.data["status"] == "done"
    assert w["ok"] and w["uses_verb"] and len(w["attempts"]) == 1 and (run.dir / w["preview"]).exists()
    assert wo["ok"] and not wo["uses_verb"] and [a["ok"] for a in wo["attempts"]] == [False, True]
    assert "mh.pulse" in (run.dir / "scenes" / "system-with.txt").read_text()
    assert "mh.pulse" not in (run.dir / "scenes" / "system-without.txt").read_text()
    assert [t["n"] for t in list_trials(run.dir)] == [1]


def test_hosted_mode_requires_sandboxes_and_guards_runs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from motionharness.guard import Guard
    from motionharness.server import create_app

    monkeypatch.delenv("NEBIUS_PROJECT_ID", raising=False)  # Sandboxes not configured
    client = TestClient(create_app(tmp_path / "paused", hosted=True))
    assert client.get("/healthz").json() == {"ok": True}
    info = client.get("/api/info").json()
    assert info["hosted"] and info["sandbox"]["backend"] == "unavailable" and "NEBIUS_PROJECT_ID" in info["sandbox"]["reason"]
    r = client.post("/api/runs", json={"task": "add a `pulse` verb: a heartbeat"})
    assert r.status_code == 503 and "Runs are paused" in r.json()["detail"]

    # With a working sandbox (here the local one stands in for Sandboxes), the guard and caps apply.
    h, _ = harness(tmp_path, GOOD, crit(9))
    client = TestClient(create_app(tmp_path / "runs", harness=h, hosted=True, guard=Guard(access_code="judges")))
    task = {"task": "add a `pulse` verb: a heartbeat-style scale pulse", "candidates": 5, "rounds": 5}
    assert client.post("/api/runs", json=task).status_code == 401
    rid = client.post("/api/runs", json=task, headers={"X-Access-Code": "judges"}).json()["id"]
    for _ in range(300):
        run = client.get(f"/api/runs/{rid}").json()
        if run["status"] != "running":
            break
        time.sleep(0.1)
    assert run["config"]["candidates"] == 3 and run["config"]["max_rounds"] == 3
    assert client.post(f"/api/runs/{rid}/install").status_code == 403
    assert client.get("/api/installed").json() == []
    assert client.get(f"/api/runs/{rid}/plugin.zip").status_code == 200
