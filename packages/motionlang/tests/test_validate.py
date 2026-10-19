"""Every error code is reachable and carries a pointer/hint the repair loop can use."""

import copy
import json
from pathlib import Path

import pytest

from motionlang import check, validate

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.motion.json"))


def codes(doc):
    return [i.code for i in validate(doc).errors]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path):
    r = validate(json.loads(path.read_text()))
    assert r.ok, r.summary()


def test_schema_errors_point_at_the_field(doc_factory):
    r = validate(doc_factory({"id": "m", "verb": "move", "actor": "a", "to": [1, 2], "duraton": 1}))
    (e,) = r.errors
    assert e.code == "E_SCHEMA" and "duraton" in e.message and e.pointer == "/timeline/seq/0/duraton"
    assert e.hint.startswith("did you mean `duration`?")


def test_unknown_param_lists_what_the_verb_accepts(doc_factory):
    (e,) = validate(doc_factory({"id": "w", "verb": "wiggle", "actor": "a", "count": 3})).errors
    assert e.code == "E_SCHEMA" and e.message == "`count` is not a parameter of wiggle"
    assert "amplitude, frequency, props, seed" in e.hint and "belongs to bounce" in e.hint
    (e,) = validate(doc_factory({"id": "w", "verb": "wiggle", "actor": "a", "amplitud": 3})).errors
    assert e.hint.startswith("did you mean `amplitude`?")


def test_to_xor_by(doc_factory):
    assert codes(doc_factory({"id": "m", "verb": "move", "actor": "a", "to": [1, 2], "by": [1, 1]})) == ["E_SCHEMA"]
    assert codes(doc_factory({"id": "m", "verb": "move", "actor": "a"})) == ["E_SCHEMA"]


def test_bad_ease(doc_factory):
    r = validate(doc_factory({"id": "m", "verb": "move", "actor": "a", "by": [1, 2], "ease": "easeInOut"}))
    assert [e.code for e in r.errors] == ["E_SCHEMA"] and "inOutCubic" in r.errors[0].message


def test_duplicate_ids(doc_factory):
    doc = doc_factory({"id": "a", "verb": "hold"})
    assert codes(doc) == ["E_DUP_ID"]
    doc = doc_factory({"id": "h", "verb": "hold"}, {"id": "h", "verb": "hold"})
    assert codes(doc) == ["E_DUP_ID"]


def test_unknown_references(doc_factory):
    assert codes(doc_factory({"id": "m", "verb": "teleport", "actor": "a"})) == ["E_REF"]
    assert codes(doc_factory({"id": "m", "verb": "fade", "actor": "ghost", "to": 0})) == ["E_REF"]
    assert codes(doc_factory({"id": "m", "verb": "move_along", "actor": "a", "path": {"stroke": "s9"}})) == ["E_REF"]
    assert codes(doc_factory({"id": "m", "verb": "follow", "actor": "a", "target": "a"})) == ["E_REF"]


def test_wrong_target(doc_factory):
    assert codes(doc_factory({"id": "d", "verb": "draw_on", "actor": "a"})) == ["E_SCHEMA"]


def test_conflict(doc_factory):
    doc = doc_factory({"id": "p", "par": [
        {"id": "m1", "verb": "move", "actor": "a", "to": [0, 0]},
        {"id": "m2", "verb": "move_along", "actor": "a", "path": {"points": [[300, 300], [0, 0]]}},
    ]})
    r = validate(doc)
    (e,) = r.errors
    assert e.code == "E_CONFLICT" and "a.x/y" in e.message and "seq" in e.hint


def test_discontinuity(doc_factory):
    doc = doc_factory({"id": "m", "verb": "move_along", "actor": "a", "path": {"points": [[0, 0], [100, 0]]}})
    r = validate(doc)
    assert {e.code for e in r.errors} == {"E_DISCONTINUITY"} and "relative" in r.errors[0].hint
    doc["timeline"]["seq"][0]["path"]["points"][0] = [300, 300]
    assert validate(doc).ok


def test_bounds_blames_the_active_block(doc_factory):
    doc = doc_factory(
        {"id": "ok", "verb": "move", "actor": "a", "by": [10, 0]},
        {"id": "hop", "verb": "bounce", "actor": "a", "height": 400},
        constraints=[{"type": "in_bounds", "margin": 0}],
    )
    (e,) = validate(doc).errors
    assert e.code == "E_BOUNDS" and e.block == "hop" and "top" in e.message


def test_invisible_actors_may_leave_the_canvas(doc_factory):
    doc = doc_factory(
        {"id": "f", "verb": "fade", "actor": "a", "to": 0, "duration": 0.1},
        {"id": "m", "verb": "move", "actor": "a", "by": [2000, 0]},
        constraints=[{"type": "in_bounds"}],
    )
    assert validate(doc).ok


def test_in_bounds_ignore_allows_deliberate_exits(doc_factory):
    doc = doc_factory(
        {"id": "exit", "verb": "move", "actor": "a", "by": [2000, 0]},
        constraints=[{"type": "in_bounds", "ignore": ["exit"]}],
    )
    assert validate(doc).ok
    doc["constraints"][0]["ignore"] = []
    (e,) = validate(doc).errors
    assert e.code == "E_BOUNDS" and "ignore" in e.hint
    doc["constraints"][0]["ignore"] = ["nope"]
    assert "E_REF" in codes(doc)


def test_camera_framing_never_breaks_in_bounds(doc_factory):
    doc = doc_factory(
        {"id": "z", "verb": "camera", "to": {"zoom": 4, "x": 300, "y": 300}},
        constraints=[{"type": "in_bounds", "margin": 8}],
    )
    assert validate(doc).ok  # actor b (at x=550) is pushed out of frame by the zoom; that's the point


def test_speed_jerk_overlap_ends_duration(doc_factory):
    doc = doc_factory(
        {"id": "m", "verb": "move", "actor": "a", "to": [560, 300], "duration": 0.2, "ease": "linear"},
        {"id": "m2", "verb": "move", "actor": "a", "by": [0, 100], "duration": 0.2, "ease": "linear"},
        constraints=[
            {"type": "max_speed", "actor": "a", "px_per_s": 500},
            {"type": "smooth", "actor": "a", "max_jerk": 1e5},
            {"type": "no_overlap", "a": "a", "b": "b"},
            {"type": "ends_at", "actor": "a", "pos": [0, 0]},
            {"type": "total_duration", "max": 0.3},
        ],
    )
    assert sorted(codes(doc)) == ["E_DURATION", "E_ENDS_AT", "E_JERK", "E_OVERLAP", "E_SPEED"]


def test_follow_cycle_is_reported(doc_factory):
    doc = doc_factory({"id": "p", "par": [
        {"id": "f1", "verb": "follow", "actor": "a", "target": "b", "lag": 0},
        {"id": "f2", "verb": "follow", "actor": "b", "target": "a", "lag": 0},
    ]})
    (e,) = validate(doc).errors
    assert e.code == "E_REF" and "cycle" in e.hint


def test_slow_spring_and_huge_timeline_are_rejected(doc_factory):
    r = validate(doc_factory({"id": "s", "verb": "spring_to", "actor": "a", "to": 2, "stiffness": 400, "damping": 0.5}))
    assert [e.code for e in r.errors] == ["E_SCHEMA"] and "damping" in r.errors[0].message
    assert codes(doc_factory({"id": "h", "verb": "hold", "duration": 1000})) == ["E_DURATION"]


def test_report_serializes(doc_factory):
    r = check(doc_factory({"id": "m", "verb": "move", "actor": "a", "to": [1, 2], "x": 1})).report
    d = r.to_dict()
    assert d["ok"] is False and d["errors"][0]["code"] == "E_SCHEMA"
    json.dumps(d)


def test_bounds_blames_and_exempts_a_parent_groups_move(doc_factory):
    doc = doc_factory({"id": "slide", "verb": "move", "actor": "g", "by": [900, 0]})
    doc["actors"].append({"id": "g", "shape": {"kind": "group"}, "init": {"x": 0, "y": 0}})
    doc["actors"][0]["parent"] = "g"
    doc["constraints"] = [{"type": "in_bounds"}]
    errs = check(doc).report.errors
    e = next(e for e in errs if e.actor == "a")
    assert e.block == "slide" and "on parent 'g'" in e.hint and 'add "slide" to constraints[0].ignore' in e.hint
    import re

    m = re.search(r'change slide\'s "by" to about \[(-?\d+), (-?\d+)\] \(g moves in canvas coordinates\)', e.hint)
    assert m, e.hint
    fixed = copy.deepcopy(doc)
    fixed["timeline"]["seq"][0]["by"] = [int(m.group(1)), int(m.group(2))]
    assert not [x for x in check(fixed).report.errors if x.code == "E_BOUNDS"]  # the suggested number works
    doc["constraints"][0]["ignore"] = ["slide"]
    assert not [e for e in check(doc).report.errors if e.actor == "a" and e.code == "E_BOUNDS"]


def test_noop_verb_is_an_error_with_a_start_pose_hint(doc_factory):
    doc = doc_factory({"id": "rise", "verb": "move", "actor": "a", "to": [300, 300]})  # already there
    e = check(doc).report.errors[0]
    assert e.code == "E_NOOP" and e.block == "rise" and '"verb": "enter", "actor": "a"' in e.hint
    doc["actors"][0]["init"]["y"] = 400  # a real starting pose makes it a rise
    assert check(doc).ok
    orbit = doc_factory({"id": "o", "verb": "orbit", "actor": "a", "center": [400, 300]})  # ends where it began, but moves
    assert check(orbit).ok


def test_oscillating_verb_sampled_at_its_zero_crossings_is_not_a_noop(doc_factory):
    """A damped swing whose zero crossings fall on u = 0, .25, .5, .75, 1 still moves (MotionHarness report)."""
    import math

    from pydantic import Field

    from motionlang.verbs import base
    from motionlang.verbs.base import Verb, VerbParams

    class SwingParams(VerbParams):
        amplitude: float = Field(20.0)

    class Swing(Verb):
        """Damped pendulum swing (test only)."""

        name = "test.swing"
        Params = SwingParams
        uses_ease = False
        examples = [{"duration": 4}]

        def mode(self, p):
            return "rel"

        def props(self, p):
            return ("rotation",)

        def sample(self, p, u, t, ctx):
            return {"rotation": p.amplitude * math.exp(-0.5 * t) * math.sin(math.pi * t)}  # zero at t = 0, 1, 2, 3, 4

    registry = base.load_plugins()
    registry["test.swing"] = Swing()
    try:
        doc = doc_factory({"id": "s", "verb": "test.swing", "actor": "a", "duration": 4}, requires=["test.swing"])
        assert "E_NOOP" not in {e.code for e in check(doc).report.errors}
    finally:
        registry.pop("test.swing")
