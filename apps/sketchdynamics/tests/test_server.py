"""Backend tests with a scripted fake model (no network)."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from motionlang.agent import LLM, model_for
from sketchdynamics.server import create_app

STROKES = {"s1": {"points": [[80, 560], [400, 380], [760, 600], [1000, 560]]}}
DOC = {
    "motion": "0.1",
    "canvas": {"width": 1280, "height": 720, "fps": 30},
    "strokes": STROKES,
    "actors": [{"id": "ball", "shape": {"kind": "ellipse", "rx": 28, "ry": 28}, "style": {"fill": "#e4572e"}, "init": {"x": 80, "y": 560}}],
    "timeline": {"id": "root", "seq": [
        {"id": "roll", "verb": "move_along", "actor": "ball", "path": {"stroke": "s1"}, "duration": 1.6},
        {"id": "hop", "verb": "bounce", "actor": "ball", "height": 90, "count": 3},
    ]},
    "constraints": [{"type": "in_bounds", "margin": 8}],
}


class Scripted:
    """Replays canned replies and records what it was asked."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def complete(self, model, messages, **kw):
        self.requests.append({"model": model, "messages": [dict(m) for m in messages], **kw})
        return self.replies.pop(0), {"prompt_tokens": 100, "completion_tokens": 20}


def client_for(*replies: str) -> tuple[TestClient, Scripted]:
    fake = Scripted(*replies)
    return TestClient(create_app(lambda: LLM(fake))), fake


def with_bounce(height: float) -> dict:
    d = copy.deepcopy(DOC)
    d["timeline"]["seq"][1]["height"] = height
    return d


def test_index_and_static_assets_are_served():
    c, _ = client_for()
    assert "SketchDynamics" in c.get("/").text
    for f in ("app.js", "player.js", "app.css"):
        assert c.get(f"/static/{f}").status_code == 200


def test_generate_returns_doc_tracks_and_attempts():
    c, fake = client_for("not json", json.dumps(with_bounce(900)), json.dumps(DOC))
    r = c.post("/generate", json={"intent": "a ball rolls along my line and bounces", "strokes": STROKES, "canvas": {"width": 1280, "height": 720}})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] and out["path"] == "generate" and out["note"] == ""
    assert [a["kind"] for a in out["attempts"]] == ["generate", "repair", "repair"]
    assert all(a["role"] == "planner" and a["model"] == model_for("planner") for a in out["attempts"])
    assert "not valid JSON" in out["attempts"][0]["problems"][0]
    assert out["attempts"][1]["issues"][0]["code"] == "E_BOUNDS" and out["attempts"][1]["issues"][0]["hint"]
    assert out["attempts"][2]["ok"] and out["attempts"][2]["prompt_tokens"] == 100
    assert out["tokens"] == 3 * 120
    assert out["doc"]["strokes"]["s1"]["points"][0] == [80, 560]
    assert out["tracks"]["frames"] > 1 and out["tracks"]["actors"][0]["id"] == "ball"
    assert out["report"]["ok"]
    # strokes reach the model in the prompt
    assert '"s1"' in fake.requests[0]["messages"][-1]["content"]


def test_generate_failure_still_returns_closest_attempt():
    c, _ = client_for(json.dumps(with_bounce(900)), json.dumps(with_bounce(800)))
    out = c.post("/generate", json={"intent": "x", "strokes": STROKES}).json()
    assert not out["ok"] and "same problem" in out["note"]
    assert out["tracks"] is not None and not out["report"]["ok"]


def test_tweak_fast_edit_path():
    ops = {"ops": [{"op": "set", "id": "roll", "field": "duration", "value": 3.2}]}
    c, fake = client_for(json.dumps(ops))
    out = c.post("/tweak", json={"doc": DOC, "instruction": "slower"}).json()
    assert out["ok"] and out["path"] == "edit"
    assert out["doc"]["timeline"]["seq"][0]["duration"] == 3.2
    a = out["attempts"][0]
    assert a["role"] == "editor" and a["model"] == model_for("editor") and a["think"] is False
    assert out["tracks"]["duration"] > 3.2
    assert fake.requests[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_tweak_escalates_to_planner():
    bad = json.dumps({"ops": [{"op": "remove", "id": "nope"}]})
    out = client_for(bad, bad, json.dumps(with_bounce(40)))[0].post("/tweak", json={"doc": DOC, "instruction": "lower"}).json()
    assert out["ok"] and out["path"] == "regenerate"
    assert [(a["kind"], a["role"], a["think"]) for a in out["attempts"]] == [
        ("edit", "editor", False), ("edit-repair", "editor", True), ("regenerate", "planner", True)]


def test_stream_emits_call_events_then_result():
    c, _ = client_for(json.dumps(with_bounce(900)), json.dumps(DOC))
    with c.stream("POST", "/generate?stream=1", json={"intent": "bounce", "strokes": STROKES}) as r:
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert [e["type"] for e in events] == ["call_start", "call_end", "call_start", "call_end", "result"]
    assert events[0]["purpose"] == "generate" and events[0]["model"] == model_for("planner")
    assert events[1]["completion_tokens"] == 20
    assert events[-1]["ok"] and len(events[-1]["attempts"]) == 2


def test_stream_reports_backend_errors():
    class Down:
        def complete(self, *a, **kw):
            raise ConnectionError("Token Factory unreachable")

    c = TestClient(create_app(lambda: LLM(Down())))
    with c.stream("POST", "/tweak?stream=1", json={"doc": DOC, "instruction": "slower"}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1] == {"type": "error", "detail": "ConnectionError: Token Factory unreachable"}
    assert c.post("/tweak", json={"doc": DOC, "instruction": "slower"}).status_code == 502


def test_bake_needs_no_model():
    c, fake = client_for()
    out = c.post("/bake", json={"doc": DOC}).json()
    assert out["report"]["ok"] and out["tracks"]["frames"] > 1 and not fake.requests
    broken = c.post("/bake", json={"doc": {"motion": "0.1"}}).json()
    assert broken["tracks"] is None and not broken["report"]["ok"]


@pytest.mark.parametrize("body", [{"intent": ""}, {"intent": "x", "strokes": {"s1": {"points": [[0, 0]]}}}])
def test_generate_rejects_bad_input(body):
    assert client_for()[0].post("/generate", json=body).status_code == 422
