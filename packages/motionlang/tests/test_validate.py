"""Every error code is reachable and carries a pointer/hint the repair loop can use."""

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
