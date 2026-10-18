"""Assets (imported vector parts), the SVG importer, and the reveal verb."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from motionlang import check
from motionlang.agent import LLM, generate, tweak
from motionlang.agent import prompts
from motionlang.cli import main
from motionlang.importers import import_svg
from motionlang.paths import flatten_svg_path, path_bbox, transform_path

FIXTURES = Path(__file__).parent / "fixtures" / "svg"


def load(name: str, **kw) -> dict:
    return import_svg((FIXTURES / name).read_text(), **kw)


def as_doc(imported: dict, *blocks: dict) -> dict:
    return {
        "motion": "0.1",
        "canvas": {**imported["canvas"], "fps": 30},
        "assets": imported["assets"],
        "actors": imported["actors"],
        "timeline": {"id": "root", "seq": list(blocks) or [{"id": "still", "verb": "hold", "duration": 0.5}]},
    }


def world_boxes(doc: dict) -> dict[str, list[float]]:
    """Each asset actor's world AABB at t=0, as [x, y, w, h]."""
    r = check(doc)
    assert r.baked is not None, r.report.summary()
    out = {}
    for aid, boxes in r.baked.aabb_world.items():
        x0, y0, x1, y1 = boxes[0]
        out[aid] = [x0, y0, x1 - x0, y1 - y0]
    return out


# ---- paths -------------------------------------------------------------------------------


def test_transform_path_is_exact_for_arcs():
    d = "M0 0 a10 10 0 1 1 0 20 a10 10 0 1 1 0 -20 z"  # circle of radius 10 centred (0, 10)
    m = (2, 0, 0, 2, 100, 50)  # scale 2 then translate
    pts = flatten_svg_path(transform_path(d, m, precision=4))
    assert np.hypot(pts[:, 0] - 100, pts[:, 1] - 70) == pytest.approx(np.full(len(pts), 20), abs=0.05)
    assert all(c in "MCZ" for c in transform_path(d, m) if c.isalpha())


def test_rotated_ellipse_arc_endpoints():
    pts = flatten_svg_path("M10 10 A20 10 30 0 0 50 40")
    assert pts[-1].tolist() == pytest.approx([50, 40])


# ---- importer ----------------------------------------------------------------------------


def test_import_arcs_fixture():
    r = load("arcs.svg")
    assert r["canvas"] == {"width": 200, "height": 100}
    a = r["assets"]
    assert list(a) == ["pill", "dot", "tag"]
    assert a["pill"]["bbox"] == pytest.approx([0, 20, 140, 60], abs=0.05)
    assert a["dot"]["fill"] == "#ff6347"  # named colour → hex
    pts = flatten_svg_path(a["dot"]["d"])
    assert np.hypot(pts[:, 0] - 160, pts[:, 1] - 50) == pytest.approx(np.full(len(pts), 20), abs=0.05)
    assert a["tag"]["bbox"] == pytest.approx([140, 5, 50, 10], abs=0.05)
    for asset in a.values():
        assert not set(c for c in asset["d"] if c.isalpha()) - set("MLCQZ")  # arcs became cubics
    root = r["actors"][0]
    assert root["id"] == "logo" and root["shape"] == {"kind": "group"}
    assert all(x["parent"] == "logo" for x in r["actors"][1:])
    assert r["dropped"] == []


def test_import_nested_transforms_and_groups():
    r = load("nested.svg")
    a = r["assets"]
    assert a["bar"]["bbox"] == pytest.approx([45, 50, 10, 40], abs=0.05)  # translate(50,50) · rotate(90)
    assert a["hook"]["bbox"][0] == pytest.approx(50) and a["hook"]["bbox"][2] == pytest.approx(40)  # scale(2)
    assert a["hook"]["stroke_width"] == pytest.approx(6) and "fill" not in a["hook"]
    assert a["polygon1"]["bbox"] == pytest.approx([120, 40, 40, 30], abs=0.05)
    by_id = {x["id"]: x for x in r["actors"]}
    assert by_id["mark"]["parent"] == "logo" and by_id["bar"]["parent"] == "mark"
    assert by_id["Wordmark"]["shape"] == {"kind": "group"}  # inkscape:label names the group
    assert by_id["polygon1"]["parent"] == "Wordmark" and by_id["polygon1"]["init"]["opacity"] == 0.5


def test_import_styles_gradients_and_drops():
    r = load("styled.svg")
    a = r["assets"]
    assert set(a) == {"ellipse1", "leaf", "seed"}  # cls-1 is an auto class, not a name
    assert a["ellipse1"]["fill"] == "#0ea5e9"  # from the <style> sheet
    assert a["ellipse1"]["bbox"] == pytest.approx([20, 15, 60, 30], abs=0.05)  # viewBox origin (10, 10) removed
    assert a["leaf"]["fill_rule"] == "evenodd" and a["leaf"]["label"] == "Leaf"
    assert a["seed"]["fill"] == "#008000"  # inherited from <use>
    assert a["seed"]["bbox"] == pytest.approx([46, 48, 8, 4], abs=0.05)
    dropped = " | ".join(r["dropped"])
    assert "linearGradient fill on 'leaf'" in dropped and "filter on 'leaf'" in dropped and "<text>" in dropped


@pytest.mark.parametrize("name", ["arcs.svg", "nested.svg", "styled.svg"])
def test_imported_parts_stay_where_drawn(name):
    """Round trip: assets + suggested actors validate, bake, and each part sits on its own bbox."""
    r = load(name)
    boxes = world_boxes(as_doc(r))
    for aid, asset in r["assets"].items():
        assert boxes[aid] == pytest.approx(asset["bbox"], abs=0.05), aid
    implicit = as_doc(r)  # the explicit resting x/y equal the "stay where drawn" default
    for actor in implicit["actors"]:
        if actor["shape"]["kind"] == "asset":
            actor["init"] = {k: v for k, v in actor["init"].items() if k not in ("x", "y")}
    assert world_boxes(implicit) == pytest.approx(boxes)


def test_fit_scales_into_canvas():
    r = load("arcs.svg", fit=(1280, 720), margin=0.1)
    assert r["canvas"] == {"width": 1280, "height": 720}
    xs = [b for k in r["assets"] for b in (r["assets"][k]["bbox"][0], r["assets"][k]["bbox"][0] + r["assets"][k]["bbox"][2])]
    ys = [b for k in r["assets"] for b in (r["assets"][k]["bbox"][1], r["assets"][k]["bbox"][1] + r["assets"][k]["bbox"][3])]
    x, y, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    assert w == pytest.approx(1024, abs=0.1)  # width-limited: 80% of 1280
    assert x + w / 2 == pytest.approx(640, abs=0.1) and y + h / 2 == pytest.approx(360, abs=0.1)
    assert check(as_doc(r)).ok


def test_importer_rejects_bad_input():
    with pytest.raises(ValueError, match="entity"):
        import_svg('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "x">]><svg/>')
    with pytest.raises(ValueError, match="no drawable"):
        import_svg('<svg xmlns="http://www.w3.org/2000/svg"><text>hi</text></svg>')


def test_cli_import_svg(tmp_path, capsys):
    out = tmp_path / "logo.motion.json"
    assert main(["import-svg", str(FIXTURES / "styled.svg"), "--fit", "800x600", "--doc", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert check(doc).ok
    assert "dropped: linearGradient" in capsys.readouterr().err


# ---- assets in documents -----------------------------------------------------------------


def test_asset_document_validates_and_bakes():
    r = load("nested.svg")
    doc = as_doc(r,
                 {"id": "in", "verb": "fade", "actor": "bar", "to": 0.5},
                 {"id": "spin", "verb": "rotate", "actor": "mark", "by": 90})
    res = check(doc)
    assert res.ok, res.report.summary()
    tracks = res.baked.to_json()
    bar = next(a for a in tracks["actors"] if a["id"] == "bar")
    assert bar["geom"]["kind"] == "asset" and bar["geom"]["closed"] and bar["tracks"]["fill"] == "#e4572e"
    hook = next(a for a in tracks["actors"] if a["id"] == "hook")
    assert hook["geom"]["closed"] is False and hook["tracks"]["stroke"] == "#e4572e" and hook["geom"]["width"] == 6


def test_unknown_asset_ref_has_hint():
    r = load("arcs.svg")
    doc = as_doc(r)
    doc["actors"][1]["shape"]["ref"] = "pil"
    errs = check(doc).report.errors
    assert [e.code for e in errs] == ["E_REF"]
    assert "unknown asset 'pil'" in errs[0].message
    assert "did you mean `pill`" in errs[0].hint and "dot" in errs[0].hint
    assert errs[0].pointer == "/actors/1/shape/ref"


def test_bad_asset_path_data():
    doc = as_doc(load("arcs.svg"))
    doc["assets"]["dot"]["d"] = "hello"
    errs = check(doc).report.errors
    assert errs[0].code == "E_SCHEMA" and errs[0].pointer == "/assets/dot/d"


def test_style_overrides_asset_paint():
    doc = as_doc(load("arcs.svg"))
    doc["actors"][2]["style"] = {"fill": "#00ff00"}
    res = check(doc)
    dot = next(a for a in res.baked.to_json()["actors"] if a["id"] == "dot")
    assert dot["tracks"]["fill"] == "#00ff00"


# ---- reveal ------------------------------------------------------------------------------


def test_reveal_wipes_and_holds_hidden_until_its_turn():
    doc = as_doc(load("arcs.svg"),
                 {"id": "w", "verb": "hold", "duration": 1},
                 {"id": "r", "verb": "reveal", "actor": "dot", "direction": "left_to_right", "duration": 1, "ease": "linear"},
                 {"id": "c", "verb": "reveal", "actor": "pill", "direction": "from_center", "from": 1, "to": 0, "ease": "linear"})
    res = check(doc)
    assert res.ok, res.report.summary()
    ev = res.baked.ev
    assert ev.value("dot", "clip_r", 0.0) == pytest.approx(1)  # hidden before it starts
    assert ev.value("dot", "clip_r", 1.5) == pytest.approx(0.5)
    assert ev.value("dot", "clip_l", 1.5) == 0
    assert ev.value("pill", "clip_l", 0.5) == 0  # a conceal starts visible
    assert ev.value("pill", "clip_t", 2.5) == pytest.approx(0.25)
    clip = next(a for a in res.baked.to_json()["actors"] if a["id"] == "dot")["tracks"]["clip"]
    assert clip[0] == [0, 0, 1, 0] and clip[-1] == [0, 0, 0, 0]


def test_draw_on_later_in_timeline_starts_hidden():
    doc = as_doc(load("nested.svg"), {"id": "w", "verb": "hold", "duration": 0.5}, {"id": "d", "verb": "draw_on", "actor": "hook"})
    ev = check(doc).baked.ev
    assert ev.value("hook", "trim", 0.0) == 0 and ev.value("hook", "trim", 1.5) == 1


def test_reveal_rejects_groups():
    doc = as_doc(load("arcs.svg"), {"id": "r", "verb": "reveal", "actor": "logo"})
    errs = check(doc).report.errors
    assert errs[0].code == "E_SCHEMA" and "group" in errs[0].message


# ---- agent: assets go in as a summary, never as path data --------------------------------


class Scripted:
    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def complete(self, model, messages, **kw):
        self.requests.append({"model": model, "messages": [dict(m) for m in messages]})
        return self.replies.pop(0), {"prompt_tokens": 10, "completion_tokens": 5}


def _all_text(fake: Scripted) -> str:
    return "\n".join(m["content"] for r in fake.requests for m in r["messages"])


def test_prompt_summary_leaves_out_path_data():
    r = load("arcs.svg")
    text = prompts.generation_user("make the logo pop", {"width": 200, "height": 100}, None, r["assets"], r["actors"])
    assert '"pill"' in text and "#1d4ed8" in text and "bbox x 0, y 20, w 140, h 60" in text
    assert '"logo"' in text  # suggested hierarchy
    for asset in r["assets"].values():
        assert asset["d"] not in text and '"d"' not in text


def test_generate_merges_assets_and_never_sends_d():
    r = load("arcs.svg", fit=(1280, 720))
    reply = {
        "actors": r["actors"],
        "timeline": {"id": "root", "stagger": [
            {"id": "r1", "verb": "reveal", "actor": "pill"},
            {"id": "r2", "verb": "spring_to", "actor": "dot", "prop": "scale", "to": 1.2}]},
    }
    bad = json.loads(json.dumps(reply))
    bad["actors"][1]["shape"]["ref"] = "pil"
    fake = Scripted(json.dumps(bad), json.dumps(reply))
    out = generate("reveal the logo", assets=r["assets"], suggested_actors=r["actors"], llm=LLM(fake))
    assert out.ok, out.attempts[-1].problems()
    assert out.doc["assets"] == r["assets"]
    assert "did you mean `pill`" in fake.requests[1]["messages"][-1]["content"]
    sent = _all_text(fake)
    for asset in r["assets"].values():
        assert asset["d"] not in sent


def test_tweak_and_regenerate_hide_asset_data():
    r = load("arcs.svg", fit=(1280, 720))
    doc = as_doc(r, {"id": "r1", "verb": "reveal", "actor": "pill", "duration": 1})
    doc["canvas"]["fps"] = 60
    regenerated = {k: v for k, v in doc.items() if k != "assets"}
    regenerated["timeline"]["seq"][0]["duration"] = 2
    fake = Scripted('{"ops": "nope"}', '{"ops": "nope"}', json.dumps(regenerated))
    out = tweak(doc, "slower", llm=LLM(fake))
    assert out.ok and out.path == "regenerate"
    assert out.doc["assets"] == doc["assets"]
    sent = _all_text(fake)
    assert '"pill"' in sent
    for asset in r["assets"].values():
        assert asset["d"] not in sent


def test_path_bbox_matches_importer_bbox():
    r = load("nested.svg")
    for asset in r["assets"].values():
        assert list(path_bbox(asset["d"])) == pytest.approx(asset["bbox"], abs=0.01)
    assert math.isfinite(sum(sum(a["bbox"]) for a in r["assets"].values()))


def test_never_visible_actor_is_an_error():
    doc = as_doc(load("arcs.svg"), {"id": "r", "verb": "reveal", "actor": "dot"})
    doc["actors"][2]["init"] = {"opacity": 0}  # the mistake Nemotron made: hidden, and reveal doesn't fade it in
    errs = check(doc).report.errors
    assert [(e.code, e.actor) for e in errs] == [("E_INVISIBLE", "dot")]
    assert 'remove "opacity" from actors[2].init' in errs[0].hint
    assert '{"id": "dot_fade", "verb": "fade", "actor": "dot", "to": 1' in errs[0].hint and "block 'r'" in errs[0].hint
    doc["timeline"]["seq"].append({"id": "f", "verb": "fade", "actor": "dot", "to": 1})
    assert check(doc).ok


def test_compound_paths_split_into_parts_keeping_holes():
    r = load("compound.svg")
    a = r["assets"]
    assert [k for k in a if k.startswith("blades_")] == ["blades_1", "blades_2", "blades_3"]
    assert a["blades_2"]["bbox"] == pytest.approx([70, 10, 40, 30], abs=0.05)
    assert "hub" in a and a["hub"]["d"].count("M") == 2  # the hole stays with the ring
    assert "frame" in a and a["frame"]["d"].count("M") == 2 and a["frame"]["fill_rule"] == "evenodd"
    assert {"badge_1", "badge_2"} <= set(a)  # same winding, nonzero: an island, so its own part
    assert {"wind_1", "wind_2"} <= set(a)
    assert a["blades_3"]["meta"] == {"source": "blades"} and a["hub"]["meta"] == {"source": "hub"}
    groups = {x["id"]: x for x in r["actors"] if x["shape"]["kind"] == "group"}
    assert {"blades", "badge", "wind"} <= set(groups) and "hub" not in groups
    assert all(x["parent"] == "blades" for x in r["actors"] if x["id"].startswith("blades_"))
    assert "split blades into 3 parts" in r["notes"]
    assert check(as_doc(r)).ok
    boxes = world_boxes(as_doc(r))
    assert boxes["blades_1"] == pytest.approx(a["blades_1"]["bbox"], abs=0.05)  # pieces stay where drawn


def test_split_none_keeps_one_part_per_element_and_other_fixtures_are_unchanged():
    r = load("compound.svg", split="none")
    assert set(r["assets"]) == {"blades", "hub", "frame", "badge", "wind"} and r["notes"] == []
    for name in ("arcs.svg", "nested.svg", "styled.svg"):
        assert load(name)["notes"] == [] and load(name)["assets"] == load(name, split="none")["assets"]


# ---- enter / exit -------------------------------------------------------------------------


def test_enter_from_left_on_a_group_needs_no_ignore_and_hides_before():
    r = load("nested.svg", fit=(1280, 720))
    doc = as_doc(r, {"id": "wait", "verb": "hold", "duration": 0.5},
                 {"id": "in", "verb": "enter", "actor": "mark", "from": "left", "duration": 1, "ease": "linear"},
                 {"id": "out", "verb": "exit", "actor": "Wordmark", "to": "bottom", "duration": 0.5})
    doc["constraints"] = [{"type": "in_bounds", "margin": 8}]
    res = check(doc)
    assert res.ok, res.report.summary()
    b = res.baked
    rest = r["assets"]["bar"]["bbox"]
    x0 = lambda t: b.aabb_world["bar"][b.frame_at(t)][0]  # noqa: E731
    assert b.aabb_world["bar"][0][2] < 0  # hidden off the left edge before its entrance starts
    assert x0(1.5) == pytest.approx(rest[0], abs=0.5)  # children of the group land where they were drawn
    assert x0(1.0) < rest[0]
    assert b.aabb_world["polygon1"][-1][1] > 720  # the exit leaves through the bottom


def test_enter_distance_accounts_for_a_scaled_parent(doc_factory):
    doc = doc_factory({"id": "in", "verb": "enter", "actor": "a", "from": "left"})
    doc["actors"].append({"id": "g", "shape": {"kind": "group"}, "init": {"x": 0, "y": 0, "sx": 0.5, "sy": 0.5}})
    doc["actors"][0]["parent"] = "g"
    doc["constraints"] = [{"type": "in_bounds"}]
    res = check(doc)
    assert res.ok, res.report.summary()
    assert res.baked.aabb_world["a"][0][2] < 0  # starts fully off-canvas despite the half-scale parent


def test_enter_scale_and_fade_start_from_zero(doc_factory):
    doc = doc_factory({"id": "pop", "verb": "enter", "actor": "a", "from": "scale"},
                      {"id": "show", "verb": "enter", "actor": "b", "from": "fade"})
    res = check(doc)
    assert res.ok, res.report.summary()
    ev = res.baked.ev
    assert ev.value("a", "sx", 0.0) == 0 and ev.value("b", "opacity", 0.5) == 0 and ev.value("b", "opacity", 2.0) == 1
