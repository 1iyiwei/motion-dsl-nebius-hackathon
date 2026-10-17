"""Agent loop tests with a scripted fake model (no network). Live tests run with RUN_LIVE=1."""

from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path

import pytest

from motionlang.agent import LLM, generate, tweak
from motionlang.agent.llm import extract_json_text, model_for

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "ball_logo.motion.json").read_text())


class Scripted:
    """Replays canned replies and records what it was asked."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def complete(self, model, messages, **kw):
        self.requests.append({"model": model, "messages": [dict(m) for m in messages], **kw})
        return self.replies.pop(0), {"prompt_tokens": 10, "completion_tokens": 5}


def doc_with(**changes) -> dict:
    d = json.loads(json.dumps(EXAMPLE))
    d.update(changes)
    return d


def test_packaged_example_matches_repo_example():
    packaged = resources.files("motionlang").joinpath("examples_data/ball_logo.motion.json").read_text()
    assert json.loads(packaged) == EXAMPLE


def test_extract_json_text_strips_reasoning_and_fences():
    assert extract_json_text('<think>hmm {no}</think>```json\n{"a": 1}\n```') == '{"a": 1}'
    with pytest.raises(ValueError):
        extract_json_text("sorry, I can't")


def test_generate_repairs_until_valid():
    off_screen = doc_with()
    off_screen["timeline"]["seq"][1]["height"] = 900  # bounce far above the canvas
    fake = Scripted("not json at all", json.dumps(off_screen), json.dumps(EXAMPLE))
    out = generate("ball rolls and bounces", llm=LLM(fake))
    assert out.ok and out.path == "generate"
    assert [a.kind for a in out.attempts] == ["generate", "repair", "repair"]
    assert "not valid JSON" in out.attempts[0].problems()[0]
    assert out.attempts[1].problems()[0].startswith("E_BOUNDS")
    # the validator's words reach the model verbatim
    last_user = fake.requests[2]["messages"][-1]["content"]
    assert "E_BOUNDS" in last_user and "hint:" in last_user
    assert all(r["model"] == model_for("planner") for r in fake.requests)


def test_generate_normalizes_app_policy():
    bare = {k: v for k, v in EXAMPLE.items() if k not in ("constraints", "motion", "meta")}
    out = generate("x", canvas={"width": 1280, "height": 720}, llm=LLM(Scripted(json.dumps(bare))))
    assert out.ok
    assert out.doc["motion"] == "0.1" and out.doc["meta"]["intent"] == "x"
    assert {"type": "in_bounds", "margin": 8} in out.doc["constraints"]


def test_generate_gives_up_after_max_repairs():
    bad = json.dumps({"timeline": {"id": "r", "verb": "teleport", "actor": "ball"}})
    out = generate("x", llm=LLM(Scripted(bad, bad)), max_repairs=1)
    assert not out.ok and len(out.attempts) == 2


def test_generate_stops_when_repairs_stall():
    off_screen = doc_with()
    off_screen["timeline"]["seq"][1]["height"] = 900
    still_off = doc_with()
    still_off["timeline"]["seq"][1]["height"] = 800  # different numbers, same problem
    fake = Scripted(json.dumps(off_screen), json.dumps(still_off), json.dumps(EXAMPLE))
    out = generate("x", llm=LLM(fake), max_repairs=3)
    assert not out.ok and len(out.attempts) == 2 and "same problem" in out.note
    assert out.best is out.attempts[-1]


def test_cli_never_overwrites_output_with_a_rejected_doc(tmp_path, monkeypatch):
    from motionlang import cli
    from motionlang.agent import loop

    bad = doc_with()
    bad["timeline"]["seq"][1]["height"] = 900
    monkeypatch.setattr(loop, "LLM", lambda: LLM(Scripted(json.dumps(bad), json.dumps(bad))))
    target = tmp_path / "x.motion.json"
    target.write_text("{}")
    assert cli.main(["generate", "x", "-o", str(target)]) == 1
    assert target.read_text() == "{}"
    assert json.loads((tmp_path / "x.rejected.motion.json").read_text())["timeline"]["seq"][1]["height"] == 900


def test_tweak_fast_path_uses_editor_without_reasoning():
    ops = {"ops": [{"op": "set", "id": "hop", "field": "height", "value": 60}]}
    fake = Scripted(json.dumps(ops))
    out = tweak(EXAMPLE, "lower bounces", llm=LLM(fake))
    assert out.ok and out.path == "edit" and out.doc["timeline"]["seq"][1]["height"] == 60
    req = fake.requests[0]
    assert req["model"] == model_for("editor")
    assert req["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_tweak_retries_with_reasoning_then_escalates():
    bad_ops = json.dumps({"ops": [{"op": "remove", "id": "nope"}]})
    too_high = json.dumps({"ops": [{"op": "set", "id": "hop", "field": "height", "value": 900}]})
    regenerated = doc_with()
    regenerated["timeline"]["seq"][0]["duration"] = 3.0
    fake = Scripted(bad_ops, too_high, json.dumps(regenerated))
    out = tweak(EXAMPLE, "slower roll", llm=LLM(fake))
    assert out.ok and out.path == "regenerate"
    assert [a.kind for a in out.attempts] == ["edit", "edit-repair", "regenerate"]
    assert "E_EDIT" in out.attempts[0].problems()[0] and out.attempts[1].problems()[0].startswith("E_BOUNDS")
    assert "extra_body" not in fake.requests[1]  # the retry reasons
    assert fake.requests[2]["model"] == model_for("planner")


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE"), reason="set RUN_LIVE=1 to call Token Factory")
def test_live_generate_and_tweak():
    out = generate("a blue square slides in from the left, spins once, then fades out")
    assert out.ok, [a.problems() for a in out.attempts]
    t = tweak(out.doc, "make the spin slower")
    assert t.ok, [a.problems() for a in t.attempts]
