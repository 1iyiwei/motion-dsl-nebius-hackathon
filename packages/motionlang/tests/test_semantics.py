"""Timing and the composition rule."""

import pytest

from motionlang import check


def final(doc, actor, prop):
    r = check(doc)
    assert r.ok, r.report.summary()
    return r.baked.props[actor][prop][-1]


def value_at(doc, actor, prop, t):
    r = check(doc)
    assert r.ok, r.report.summary()
    return r.baked.ev.value(actor, prop, t)


def test_seq_par_stagger_timing(doc_factory):
    doc = doc_factory(
        {"id": "m1", "verb": "move", "actor": "a", "by": [10, 0], "duration": 1},
        {"id": "p", "par": [
            {"id": "m2", "verb": "move", "actor": "a", "by": [10, 0], "duration": 2},
            {"id": "f", "verb": "fade", "actor": "a", "to": 0.5, "duration": 0.5, "delay": 0.25},
        ]},
        {"id": "s", "stagger": [
            {"id": "r1", "verb": "rotate", "actor": "a", "by": 10, "duration": 1},
            {"id": "r2", "verb": "rotate", "actor": "b", "by": 10, "duration": 1},
        ], "offset": 0.5},
    )
    r = check(doc)
    assert r.ok, r.report.summary()
    spans = {lf.block.id: (lf.start, lf.end) for lf in r.baked.sched.leaves}
    assert spans["m1"] == (0, 1)
    assert spans["m2"] == (1, 3)
    assert spans["f"] == (1.25, 1.75)
    assert spans["r1"] == (3, 4) and spans["r2"] == (3.5, 4.5)
    assert r.baked.duration == 4.5


def test_rel_plus_rel_add(doc_factory):
    doc = doc_factory({"id": "p", "par": [
        {"id": "m1", "verb": "move", "actor": "a", "by": [100, 0]},
        {"id": "m2", "verb": "move", "actor": "a", "by": [0, 50]},
    ]})
    assert final(doc, "a", "x") == pytest.approx(400)
    assert final(doc, "a", "y") == pytest.approx(350)


def test_abs_after_rel_absorbs_offset(doc_factory):
    doc = doc_factory(
        {"id": "m1", "verb": "move", "actor": "a", "by": [100, 0]},
        {"id": "m2", "verb": "move", "actor": "a", "to": [200, 200]},
    )
    assert final(doc, "a", "x") == pytest.approx(200)
    assert value_at(doc, "a", "x", 1.0) == pytest.approx(400)  # continuous at the seam


def test_rel_layers_on_abs(doc_factory):
    doc = doc_factory({"id": "p", "par": [
        {"id": "m", "verb": "move", "actor": "a", "to": [500, 300], "duration": 2, "ease": "linear"},
        {"id": "w", "verb": "move", "actor": "a", "by": [0, -40], "duration": 1, "ease": "linear"},
    ]})
    assert value_at(doc, "a", "x", 1.0) == pytest.approx(400)
    assert value_at(doc, "a", "y", 1.0) == pytest.approx(260)
    assert final(doc, "a", "y") == pytest.approx(260)


def test_rel_straddling_abs_start_is_continuous(doc_factory):
    doc = doc_factory({"id": "p", "par": [
        {"id": "w", "verb": "move", "actor": "a", "by": [0, -100], "duration": 2, "ease": "linear"},
        {"id": "m", "verb": "move", "actor": "a", "to": [300, 300], "duration": 1, "delay": 1},
    ]})
    before = value_at(doc, "a", "y", 0.999999)
    after = value_at(doc, "a", "y", 1.000001)
    assert after == pytest.approx(before, abs=1e-3)
    # abs takes over from 250, eases to 300, and the remaining relative motion (-50) adds on top
    assert final(doc, "a", "y") == pytest.approx(250)


def test_repeat_accumulates_relative(doc_factory):
    doc = doc_factory({"id": "rep", "repeat": {"id": "r", "verb": "rotate", "actor": "a", "by": 90, "duration": 0.5}, "count": 3})
    assert final(doc, "a", "rotation") == pytest.approx(270)
    assert check(doc).baked.duration == pytest.approx(1.5)


def test_repeat_inf_schedules_one_cycle(doc_factory):
    doc = doc_factory({"id": "rep", "repeat": {"id": "r", "verb": "rotate", "actor": "a", "by": 90, "duration": 0.5}, "count": "inf"})
    r = check(doc)
    assert r.ok and r.baked.sched.loops and r.baked.to_json()["loop"]


def test_move_along_orient_turns_with_path(doc_factory):
    doc = doc_factory({"id": "m", "verb": "move_along", "actor": "a", "path": {"points": [[300, 300], [400, 300], [400, 400]]}, "orient": True})
    assert final(doc, "a", "x") == pytest.approx(400)
    assert final(doc, "a", "y") == pytest.approx(400)
    assert final(doc, "a", "rotation") == pytest.approx(90)


def test_bounce_returns_to_rest(doc_factory):
    doc = doc_factory({"id": "hop", "verb": "bounce", "actor": "a", "height": 100, "count": 2})
    r = check(doc)
    ys = r.baked.props["a"]["y"]
    assert min(ys) == pytest.approx(200, abs=1)
    assert ys[-1] == pytest.approx(300) and r.baked.props["a"]["sx"][-1] == pytest.approx(1)


def test_spring_settles_on_target_with_overshoot(doc_factory):
    doc = doc_factory({"id": "s", "verb": "spring_to", "actor": "a", "prop": "scale", "to": 2, "stiffness": 200, "damping": 10})
    sx = check(doc).baked.props["a"]["sx"]
    assert sx[-1] == pytest.approx(2)
    assert max(sx) > 2.1


def test_follow_and_orbit_track_other_actor(doc_factory):
    doc = doc_factory({"id": "p", "par": [
        {"id": "mb", "verb": "move", "actor": "b", "by": [0, 100], "duration": 2, "ease": "linear"},
        {"id": "f", "verb": "follow", "actor": "a", "target": "b", "lag": 0.5, "duration": 3},
    ]})
    assert final(doc, "a", "x") == pytest.approx(550)
    assert final(doc, "a", "y") == pytest.approx(400)


def test_camera_zoom_changes_screen_bounds(doc_factory):
    doc = doc_factory({"id": "c", "verb": "camera", "to": {"zoom": 2, "x": 300, "y": 300}})
    r = check(doc)
    x0, y0, x1, y1 = r.baked.aabb_screen["a"][-1]
    assert (x1 - x0) == pytest.approx(80) and (x0 + x1) / 2 == pytest.approx(400)
    assert r.baked.aabb_world["a"][-1][2] - r.baked.aabb_world["a"][-1][0] == pytest.approx(40)


def test_parent_transform_and_opacity(doc_factory):
    doc = doc_factory({"id": "f", "verb": "fade", "actor": "g", "to": 0.5})
    doc["actors"] = [
        {"id": "g", "shape": {"kind": "group"}, "init": {"x": 100, "y": 100}},
        {"id": "kid", "parent": "g", "shape": {"kind": "rect", "w": 10, "h": 10}, "init": {"x": 50, "y": 0}, "init_note": None},
    ]
    doc["actors"][1].pop("init_note")
    r = check(doc)
    assert r.ok, r.report.summary()
    assert r.baked.pos["kid"][-1].tolist() == pytest.approx([150, 100])
    assert r.baked.opacity["kid"][-1] == pytest.approx(0.5)


def test_drawn_shapes_default_to_their_drawn_position(doc_factory):
    r = check(doc_factory({"id": "d", "verb": "draw_on", "actor": "line"}))
    x0, y0, x1, y1 = r.baked.aabb_world["line"][0]
    assert x0 == pytest.approx(300, abs=0.5) and x1 == pytest.approx(520, abs=0.5)
    assert r.baked.props["line"]["trim"][0] == 0 and r.baked.props["line"]["trim"][-1] == 1
