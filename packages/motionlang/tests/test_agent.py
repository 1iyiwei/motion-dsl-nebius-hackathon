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


def test_on_attempt_reports_each_attempt_as_it_is_checked():
    off_screen = doc_with()
    off_screen["timeline"]["seq"][1]["height"] = 900
    seen = []
    out = generate("ball", llm=LLM(Scripted(json.dumps(off_screen), json.dumps(EXAMPLE))), on_attempt=lambda a: seen.append((a.kind, a.ok)))
    assert out.ok and seen == [("generate", False), ("repair", True)]
    seen.clear()
    tweak(EXAMPLE, "slower", llm=LLM(Scripted('{"ops": [{"op": "set", "id": "hop", "field": "height", "value": 60}]}')),
          on_attempt=lambda a: seen.append((a.kind, a.ok)))
    assert seen == [("edit", True)]


def test_edit_prompt_guards_constraint_fields():
    from motionlang.agent import prompts

    text = prompts.edit_system()
    assert "never invent fields" in text and "in_bounds has no max" in text


def _too_high() -> dict:
    d = doc_with()
    d["timeline"]["seq"][1]["height"] = 900
    return d


def test_near_valid_doc_is_repaired_with_edit_ops():
    fix = {"ops": [{"op": "set", "id": "hop", "field": "height", "value": 80}]}
    fake = Scripted(json.dumps(_too_high()), json.dumps(fix))
    out = generate("ball", llm=LLM(fake))
    assert out.ok and [a.kind for a in out.attempts] == ["generate", "repair-edit"]
    assert out.doc["timeline"]["seq"][1]["height"] == 80
    ask = fake.requests[1]["messages"][-1]["content"]
    assert "E_BOUNDS" in ask and "edit operations" in ask and '{"ops"' in ask


def test_failed_ops_are_retried_against_the_same_document():
    bad = {"ops": [{"op": "set", "id": "nope", "field": "height", "value": 80}]}
    fix = {"ops": [{"op": "set", "id": "hop", "field": "height", "value": 80}]}
    fake = Scripted(json.dumps(_too_high()), json.dumps(bad), json.dumps(fix))
    out = generate("ball", llm=LLM(fake))
    assert out.ok and [a.kind for a in out.attempts] == ["generate", "repair-edit", "repair-edit"]
    assert "were not applied" in fake.requests[2]["messages"][-1]["content"]


def test_patching_that_stalls_falls_back_to_one_rewrite_then_stops():
    noop = {"ops": [{"op": "set", "id": "hop", "field": "count", "value": 3}]}
    fake = Scripted(json.dumps(_too_high()), json.dumps(noop), json.dumps(_too_high()), json.dumps(_too_high()))
    out = generate("ball", llm=LLM(fake), max_repairs=5)
    assert not out.ok and [a.kind for a in out.attempts] == ["generate", "repair-edit", "repair"]
    assert "stopped" in out.note and len(fake.replies) == 1


def test_stall_signature_includes_the_pointer():
    from motionlang.agent.loop import Attempt
    from motionlang.agent.llm import Call
    from motionlang.validate import check

    a, b = doc_with(), doc_with()
    a["actors"][0]["init"]["bogus"] = 1
    b["actors"][1]["init"]["bogus"] = 1
    call = Call("x", "m", 0.0)
    sa, sb = (Attempt("repair", call, d, check(d).report).signature() for d in (a, b))
    assert sa != sb


def test_wrong_top_level_is_rewritten_not_patched():
    wrong = {"type": "animation", "code": "const x = 1;"}
    fake = Scripted(json.dumps(wrong), json.dumps(EXAMPLE))
    out = generate("ball", llm=LLM(fake))
    assert out.ok and [a.kind for a in out.attempts] == ["generate", "repair"]
    assert "edit operations" not in fake.requests[1]["messages"][-1]["content"]


def test_logo_prompt_shows_parts_once_as_a_tree():
    from motionlang.agent import prompts
    from motionlang.importers import import_svg

    svg = (Path(__file__).parent / "fixtures" / "svg" / "nested.svg").read_text()
    r = import_svg(svg)
    text = prompts.generation_user("x", r["canvas"], None, r["assets"], r["actors"])
    assert '  - actor "bar": asset "bar"' in text and "relative to mark" in text
    assert text.count('asset "bar"') == 1 and "M45" not in text


def test_timeline_given_as_a_list_or_under_animations_is_wrapped():
    for key in ("timeline", "animations"):
        d = doc_with()
        blocks = d.pop("timeline")["seq"]
        d[key] = blocks
        out = generate("ball", llm=LLM(Scripted(json.dumps(d))))
        assert out.ok and out.doc["timeline"] == {"id": "root", "seq": blocks}


def test_escaped_document_in_a_single_key_is_recovered():
    from motionlang.agent.loop import _parse_reply

    inner = json.dumps(EXAMPLE, separators=(",", ":"))
    wrapped = json.dumps({inner[2:-1]: None})  # what Super-120B sometimes sends: the doc escaped into one key
    doc, err = _parse_reply(wrapped)
    assert err is None and doc == EXAMPLE


def test_structured_mode_sends_the_decoding_schema():
    fake = Scripted(json.dumps(EXAMPLE))
    generate("ball", llm=LLM(fake), structured=True)
    fmt = fake.requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and "propertyNames" not in json.dumps(fmt["json_schema"]["schema"])


def test_ops_can_add_an_actor_and_unset_by_pointer():
    from motionlang import apply_edits

    actor = {"id": "extra", "shape": {"kind": "rect", "w": 10, "h": 10}, "init": {"x": 100, "y": 100}}
    new, issues = apply_edits(EXAMPLE, [{"op": "set", "pointer": "/actors/-", "value": actor},
                                        {"op": "unset", "pointer": "/meta/title"}])
    assert not issues and new["actors"][-1] == actor and "title" not in new["meta"]


def test_per_instance_model_overrides_and_structured_default():
    super_model = "nvidia/nemotron-3-super-120b-a12b"
    fake = Scripted(json.dumps(EXAMPLE))
    llm = LLM(fake, models={"planner": super_model})
    assert llm.model_for("planner") == super_model and llm.model_for("editor") == model_for("editor")
    generate("ball", llm=llm)
    assert fake.requests[0]["model"] == super_model and fake.requests[0]["response_format"]["type"] == "json_schema"
    fake = Scripted(json.dumps(EXAMPLE))
    generate("ball", llm=LLM(fake))  # default planner (Ultra): plain JSON mode
    assert fake.requests[0]["response_format"]["type"] == "json_object"
    fake = Scripted(json.dumps(EXAMPLE))
    generate("ball", llm=LLM(fake, models={"planner": super_model}), structured=False)
    assert fake.requests[0]["response_format"]["type"] == "json_object"


def test_suggested_actor_layout_survives_a_doc_that_drops_init():
    suggested = [
        {"id": "logo", "shape": {"kind": "group"}, "init": {"x": 640, "y": 360}},
        {"id": "sun", "shape": {"kind": "group"}, "parent": "logo", "init": {"x": 0, "y": -96}},
        {"id": "dot", "shape": {"kind": "ellipse", "rx": 10, "ry": 10}, "parent": "sun", "init": {"x": 0, "y": 0}},
        {"id": "extra", "shape": {"kind": "ellipse", "rx": 10, "ry": 10}, "parent": "logo", "init": {"x": 50, "y": 0}},
    ]
    # what Super-120B sends under structured output: init null, parent/shape repeated, one part left out
    reply = {"actors": [{"id": "logo", "shape": {"kind": "group"}, "init": None, "label": None},
                        {"id": "sun", "shape": {"kind": "group"}, "parent": "logo", "init": {"y": 200, "x": None}},
                        {"id": "dot", "shape": {"kind": "ellipse", "rx": 10, "ry": 10}, "parent": "sun"}],
             "timeline": {"id": "root", "seq": [{"id": "rise", "verb": "move", "actor": "sun", "to": [0, -96]}]}}
    out = generate("sun rises", llm=LLM(Scripted(json.dumps(reply))), suggested_actors=suggested, structured=False)
    assert out.ok, out.attempts[-1].problems()
    actors = {a["id"]: a for a in out.doc["actors"]}
    assert actors["logo"]["init"] == {"x": 640, "y": 360} and "label" not in actors["logo"]
    assert actors["sun"]["init"] == {"x": 0, "y": 200}  # the model's change wins; the rest is filled in
    assert actors["dot"]["init"] == {"x": 0, "y": 0} and "extra" in actors


def test_regenerate_fills_fields_but_does_not_restore_removed_actors():
    from motionlang.agent.loop import _with_suggested

    old = [{"id": "a", "shape": {"kind": "group"}, "init": {"x": 1, "y": 2}}, {"id": "b", "shape": {"kind": "group"}}]
    assert _with_suggested([{"id": "a", "init": None}], old, restore_missing=False) == [
        {"id": "a", "shape": {"kind": "group"}, "init": {"x": 1, "y": 2}}]


@pytest.mark.parametrize("bare", [
    {"op": "set", "id": "hop", "field": "height", "value": 80},  # one op, unwrapped
    [{"op": "set", "id": "hop", "field": "height", "value": 80}, {"op": "set", "id": "hop", "field": "count", "value": 3}],
])
def test_bare_ops_in_a_patch_round_are_applied(bare):
    text = json.dumps(bare)
    out = generate("ball", llm=LLM(Scripted(json.dumps(_too_high()), "```json\n" + text + "\n```")))
    assert out.ok and [a.kind for a in out.attempts] == ["generate", "repair-edit"]
    out = tweak(EXAMPLE, "lower bounce", llm=LLM(Scripted(text)))
    assert out.ok and out.path == "edit" and out.doc["timeline"]["seq"][1]["height"] == 80


@pytest.mark.parametrize("bad", [
    {"id": "b2", "verb": "par", "children": [{"id": "x1", "verb": "fade", "actor": "logo", "to": 0.5}]},
    {"id": "b2", "verb": "par", "par": [{"id": "x1", "verb": "fade", "actor": "logo", "to": 0.5}]},
])
def test_container_written_as_a_verb_is_rewritten(bad):
    d = doc_with()
    d["timeline"]["seq"].append(bad)
    out = generate("ball", llm=LLM(Scripted(json.dumps(d))))
    assert out.ok, out.attempts[-1].problems()
    assert out.doc["timeline"]["seq"][-1] == {"id": "b2", "par": [{"id": "x1", "verb": "fade", "actor": "logo", "to": 0.5}]}


def test_container_as_verb_hint_when_it_cannot_be_rewritten():
    from motionlang import check

    d = doc_with()
    d["timeline"]["seq"].append({"id": "b2", "verb": "par"})
    e = next(e for e in check(d).report.errors if e.block == "b2")
    assert "par is a container, not a verb" in e.hint and '{"id": "b2", "par": [' in e.hint


def test_max_tokens_reaches_the_planner_and_a_runaway_structured_reply_falls_back_to_json_mode():
    runaway = '{"motion": "0.1", "timeline": {"id": "root", "seq": [{"id": "s", "verb": "rotate", "actor": "logo", "by": 360.0000000000000600830' + "0" * 200
    fake = Scripted(runaway, json.dumps(EXAMPLE))
    out = generate("ball", llm=LLM(fake), structured=True, max_tokens=6000)
    assert out.ok and [a.kind for a in out.attempts] == ["generate", "repair"]
    assert [r["max_tokens"] for r in fake.requests] == [6000, 6000]
    assert [r["response_format"]["type"] for r in fake.requests] == ["json_schema", "json_object"]
