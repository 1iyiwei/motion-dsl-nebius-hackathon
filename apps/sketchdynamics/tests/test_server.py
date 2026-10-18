"""Backend tests with a scripted fake model (no network)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motionlang.agent import LLM, model_for
from sketchdynamics.server import create_app

SUPER, ULTRA = "nvidia/nemotron-3-super-120b-a12b", "nvidia/Nemotron-3-Ultra-550b-a55b"
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
    for f in ("app.js", "app.css", "samples/sunrise.svg"):
        assert c.get(f"/static/{f}").status_code == 200
    player = c.get("/player.js")  # motionlang's packaged player
    assert player.status_code == 200 and "export class Player" in player.text
    assert player.headers["content-type"].startswith("text/javascript")


def test_generate_returns_doc_tracks_and_attempts():
    c, fake = client_for("not json", json.dumps(with_bounce(900)), json.dumps(DOC))
    r = c.post("/generate", json={"intent": "a ball rolls along my line and bounces", "strokes": STROKES, "canvas": {"width": 1280, "height": 720}})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] and out["path"] == "generate" and out["note"] == ""
    assert [a["kind"] for a in out["attempts"]] == ["generate", "repair", "repair"]
    assert all(a["role"] == "planner" and a["model"] == SUPER for a in out["attempts"])
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
    assert [e["type"] for e in events] == ["call_start", "attempt", "call_start", "attempt", "result"]
    assert events[0]["purpose"] == "generate" and events[0]["model"] == SUPER
    # verdicts arrive as soon as each reply is validated, not only with the result
    assert not events[1]["ok"] and events[1]["issues"][0]["code"] == "E_BOUNDS" and events[1]["completion_tokens"] == 20
    assert events[3]["ok"] and events[3]["kind"] == "repair"
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


SAMPLE_SVG = (Path(__file__).parent.parent / "src/sketchdynamics/static/samples/sunrise.svg").read_text()


def imported_logo(c: TestClient) -> dict:
    r = c.post("/import-svg", json={"svg": SAMPLE_SVG})
    assert r.status_code == 200
    return r.json()


def test_import_svg_returns_assets_and_a_still_preview():
    logo = imported_logo(client_for()[0])
    assert {"disc", "ray1", "wave1", "wave2"} <= set(logo["assets"])
    assert logo["assets"]["disc"]["d"] and len(logo["assets"]["disc"]["bbox"]) == 4
    # the file's <g> groups, flattened; the one-part "sun" group isn't a group worth keeping
    assert [(g["id"], g["parts"]) for g in logo["groups"]] == [
        ("rays", ["ray1", "ray2", "ray3", "ray4", "ray5"]), ("sea", ["wave1", "wave2"])]
    assert {a["id"]: a.get("parent") for a in logo["actors"]}["ray3"] == "rays"
    assert logo["dropped"] == []
    preview = logo["preview"]
    assert preview["report"]["ok"] and len(preview["tracks"]["actors"]) == len(logo["actors"])


@pytest.mark.parametrize("svg, why", [("<svg", "not a valid"), ('<svg xmlns="http://www.w3.org/2000/svg"><text>Hi</text></svg>', "no drawable")])
def test_import_svg_rejects_unusable_files(svg, why):
    r = client_for()[0].post("/import-svg", json={"svg": svg})
    assert r.status_code == 422 and why in r.json()["detail"]


def test_generate_with_logo_never_sends_path_data_and_merges_assets_back():
    c0, _ = client_for()
    logo = imported_logo(c0)
    plan = {
        "motion": "0.1", "actors": logo["actors"],
        "timeline": {"id": "root", "seq": [{"id": "in", "verb": "reveal", "actor": "disc", "direction": "left_to_right", "duration": 0.8}]},
    }
    c, fake = client_for(json.dumps(plan))
    out = c.post("/generate", json={"intent": "the sun wipes in", "assets": logo["assets"], "actors": logo["actors"]}).json()
    assert out["ok"], out["attempts"]
    prompt = fake.requests[0]["messages"][-1]["content"]
    assert "disc" in prompt and logo["assets"]["disc"]["d"] not in prompt
    assert out["doc"]["assets"] == logo["assets"]
    disc = next(a for a in out["tracks"]["actors"] if a["id"] == "disc")
    assert isinstance(disc["tracks"]["clip"][0], list)  # the wipe is baked for the player


def test_label_parts_uses_the_vision_model_and_keeps_only_known_ids():
    c0, _ = client_for()
    assets = imported_logo(c0)["assets"]
    reply = {"description": "a sun over waves", "labels": {"disc": "sun disc", "wave1": "upper wave", "bogus": "x", "ray1": 3},
             "groups": [{"label": "Sun", "parts": ["disc", "ray1", "ray2", "nope"]}, {"label": "lonely", "parts": ["wave1"]}]}
    c, fake = client_for(json.dumps(reply))
    out = c.post("/label-parts", json={"image": "data:image/jpeg;base64,AAAA", "assets": assets, "groups": [{"parts": ["wave1", "wave2"]}]}).json()
    assert out["labels"] == {"disc": "sun disc", "wave1": "upper wave"}
    assert out["groups"] == [{"id": "sun", "label": "Sun", "parts": ["disc", "ray1", "ray2"]}]
    assert '[["wave1", "wave2"]]' in fake.requests[0]["messages"][0]["content"][1]["text"]
    assert out["description"] == "a sun over waves" and out["call"]["model"] == model_for("vision")
    req = fake.requests[0]
    assert req["model"] == model_for("vision")
    content = req["messages"][0]["content"]
    assert content[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
    assert '"disc"' in content[1]["text"] and assets["disc"]["d"] not in content[1]["text"]


def test_label_parts_failure_is_soft():
    c, _ = client_for("I see a sun")
    out = c.post("/label-parts", json={"image": "data:image/png;base64,AAAA", "assets": {"disc": {"bbox": [0, 0, 1, 1]}}}).json()
    assert out["labels"] == {} and out["groups"] is None and "ValueError" in out["error"] and out["call"]["model"] == model_for("vision")
    assert c.post("/label-parts", json={"image": "http://example.com/x.png", "assets": {}}).status_code == 422


def test_failed_generate_shows_the_closest_playable_attempt_not_the_last():
    nearly = json.dumps(with_bounce(900))  # one E_BOUNDS problem, still bakes
    unparseable_doc = json.dumps({"motion": "0.1", "actors": []})  # schema errors only
    c, _ = client_for(nearly, unparseable_doc, unparseable_doc)
    out = c.post("/generate", json={"intent": "x", "strokes": STROKES}).json()
    assert not out["ok"] and out["tracks"] is not None
    assert out["doc"]["timeline"]["seq"][1]["height"] == 900


def screen_matrices(tracks: dict) -> dict:
    return {a["id"]: a["tracks"]["m"] for a in tracks["actors"] if a["geom"]["tag"] != "group"}


def test_regroup_moves_nothing_on_screen():
    from motionlang.importers import import_svg

    c, _ = client_for()
    raw = import_svg(SAMPLE_SVG, fit=(1280, 720))
    original = c.post("/bake", json={"doc": {
        "motion": "0.1", **{k: raw[k] for k in ("canvas", "assets", "actors")},
        "timeline": {"id": "root", "seq": [{"id": "still", "verb": "hold", "duration": 0.1}]}}}).json()
    assert original["report"]["ok"]
    logo = imported_logo(c)
    sun = {"label": "rising sun", "parts": ["disc", "ray1", "ray2", "ray3", "ray4", "ray5"]}
    out = c.post("/regroup", json={"assets": logo["assets"], "groups": [sun, logo["groups"][1]], "actors": logo["actors"]}).json()
    assert [g["id"] for g in out["groups"]] == ["rising_sun", "sea"]
    parents = {a["id"]: a.get("parent") for a in out["actors"]}
    assert parents["ray4"] == parents["disc"] == "rising_sun" and parents["rising_sun"] == "logo"
    assert out["preview"]["report"]["ok"]
    want, got = screen_matrices(original["tracks"]), screen_matrices(out["preview"]["tracks"])
    for part in raw["assets"]:
        assert got[part] == pytest.approx(want[part], abs=0.02), part
    # and with no groups at all, every part hangs off the logo root
    flat = c.post("/regroup", json={"assets": logo["assets"], "groups": []}).json()
    assert {a.get("parent") for a in flat["actors"] if a["shape"]["kind"] == "asset"} == {"logo"}


def test_group_ids_never_collide_with_parts():
    from sketchdynamics.grouping import normalize_groups

    assets = {"sun": {}, "ray": {}, "logo_mark": {}}
    out = normalize_groups([{"label": "Sun", "parts": ["sun", "ray"]}, {"id": "logo", "parts": ["logo_mark", "sun"]}], assets)
    assert out == [{"id": "sun_2", "label": "Sun", "parts": ["sun", "ray"]}]  # "logo" lost "sun" to the first group, then had 1 part


def test_grouped_generate_sends_the_tree_to_the_planner():
    c0, _ = client_for()
    logo = imported_logo(c0)
    grouped = c0.post("/regroup", json={"assets": logo["assets"], "groups": [
        {"label": "sun", "parts": ["disc", "ray1", "ray2", "ray3", "ray4", "ray5"]}]}).json()
    rise = {"motion": "0.1", "actors": grouped["actors"], "constraints": [{"type": "in_bounds", "margin": 8, "ignore": ["rise"]}],
            "timeline": {"id": "root", "seq": [{"id": "rise", "verb": "move", "actor": "sun", "by": [0, -60], "duration": 1}]}}
    c, fake = client_for(json.dumps(rise))
    out = c.post("/generate", json={"intent": "the sun rises", "assets": logo["assets"], "actors": grouped["actors"]}).json()
    assert out["ok"], out["attempts"]
    prompt = fake.requests[0]["messages"][-1]["content"]
    assert '"sun"' in prompt and '"parent":"sun"' in prompt
    m = {a["id"]: a["tracks"]["m"] for a in out["tracks"]["actors"]}
    # a ray and the disc rise together: same vertical displacement over the move
    assert m["ray3"][-1][5] - m["ray3"][0][5] == pytest.approx(m["disc"][-1][5] - m["disc"][0][5], abs=0.01)


def centre_at(actor: dict, frame: int) -> tuple[float, float]:
    m = actor["tracks"]["m"]
    a, b, c, d, e, f = m[frame] if isinstance(m[0], list) else m
    w, h = actor["geom"]["bw"] / 2, actor["geom"]["bh"] / 2
    return a * w + c * h + e, b * w + d * h + f


@pytest.mark.parametrize("pivot, fixed", [("disc", True), (None, False)])
def test_group_rotates_about_its_pivot_part(pivot, fixed):
    c, _ = client_for()
    logo = imported_logo(c)
    sun = {"label": "sun", "parts": ["disc", "ray1", "ray2", "ray3", "ray4", "ray5"], **({"pivot": pivot} if pivot else {})}
    g = c.post("/regroup", json={"assets": logo["assets"], "groups": [sun]}).json()
    assert g["groups"][0].get("pivot") == pivot
    assert g["preview"]["report"]["ok"]  # still drawn where it was, pivot or not
    doc = {"motion": "0.1", "canvas": {"width": 1280, "height": 720}, "assets": logo["assets"], "actors": g["actors"],
           "timeline": {"id": "root", "seq": [{"id": "spin", "verb": "rotate", "actor": "sun", "by": 90, "duration": 1}]}}
    t = c.post("/bake", json={"doc": doc}).json()["tracks"]
    disc = next(a for a in t["actors"] if a["id"] == "disc")
    x0, y0 = centre_at(disc, 0)
    x1, y1 = centre_at(disc, -1)
    assert (abs(x1 - x0) < 0.5 and abs(y1 - y0) < 0.5) == fixed


def test_pivot_must_be_one_of_the_groups_parts_or_a_point():
    from sketchdynamics.grouping import normalize_groups

    assets = {k: {} for k in "abcd"}
    out = normalize_groups([{"label": "x", "parts": ["a", "b"], "pivot": "c"}, {"label": "y", "parts": ["c", "d"], "pivot": [3, 4]}], assets)
    assert out == [{"id": "x", "label": "x", "parts": ["a", "b"]}, {"id": "y", "label": "y", "parts": ["c", "d"], "pivot": [3.0, 4.0]}]


def test_label_parts_sends_the_marked_image_and_file_name():
    c, fake = client_for(json.dumps({"labels": {}, "groups": []}))
    assets = {"p_1": {"bbox": [0, 0, 5, 5], "fill": "#000", "label": "p", "meta": {"source": "p"}}, "q": {"bbox": [9, 9, 2, 2]}}
    c.post("/label-parts", json={"image": "data:image/jpeg;base64,AA", "marked": "data:image/jpeg;base64,BB", "name": "wind-energy.svg", "assets": assets})
    content = fake.requests[0]["messages"][0]["content"]
    assert [p["image_url"]["url"] for p in content[:2]] == ["data:image/jpeg;base64,AA", "data:image/jpeg;base64,BB"]
    text = content[2]["text"]
    assert '#1 "p_1"' in text and '#2 "q"' in text and '"wind-energy.svg"' in text
    assert 'named "p"' not in text  # an inherited source id isn't a real name


def test_label_parts_maps_mark_numbers_back_to_part_ids():
    reply = {"labels": {"#1": "blade", "2": "hub"}, "groups": [{"label": "rotor", "parts": ["#1", 2], "pivot": "#2"}]}
    c, _ = client_for(json.dumps(reply))
    assets = {"a": {"bbox": [0, 0, 5, 5]}, "b": {"bbox": [9, 9, 2, 2]}}
    out = c.post("/label-parts", json={"image": "data:image/jpeg;base64,AA", "marked": "data:image/jpeg;base64,BB", "assets": assets}).json()
    assert out["labels"] == {"a": "blade", "b": "hub"}
    assert out["groups"] == [{"id": "rotor", "label": "rotor", "parts": ["a", "b"], "pivot": "b"}]


@pytest.mark.parametrize("planner, model, fmt", [(None, SUPER, "json_schema"), ("super", SUPER, "json_schema"), ("ultra", ULTRA, "json_object")])
def test_planner_choice_picks_the_model_and_decoding(planner, model, fmt):
    c, fake = client_for(json.dumps(DOC))
    body = {"intent": "x", "strokes": STROKES, **({"planner": planner} if planner else {})}
    out = c.post("/generate", json=body).json()
    assert out["ok"] and out["attempts"][0]["model"] == model
    assert fake.requests[0]["model"] == model and fake.requests[0]["response_format"]["type"] == fmt


def test_tweak_escalation_uses_the_chosen_planner():
    bad = json.dumps({"ops": [{"op": "remove", "id": "nope"}]})
    c, fake = client_for(bad, bad, json.dumps(DOC))
    out = c.post("/tweak", json={"doc": DOC, "instruction": "x", "planner": "ultra"}).json()
    assert out["ok"] and out["path"] == "regenerate"
    assert fake.requests[0]["model"] == model_for("editor") and fake.requests[2]["model"] == ULTRA
    assert c.post("/tweak", json={"doc": DOC, "instruction": "x", "planner": "gpt"}).status_code == 422


def test_split_part_separates_subpaths_in_place():
    c, _ = client_for()
    assets = {
        "ring": {"kind": "path", "d": "M0,0 L10,0 L10,10 L0,10 Z M3,3 L3,7 L7,7 L7,3 Z", "fill": "#123", "bbox": [0, 0, 10, 10], "label": "hub"},
        "blade": {"kind": "path", "d": "M20,0 L30,0 L25,9 Z", "fill": "#123", "bbox": [20, 0, 10, 9]},
    }
    groups = [{"label": "rotor", "parts": ["blade", "ring"], "pivot": "ring"}]
    out = c.post("/split-part", json={"assets": assets, "groups": groups, "part": "ring"}).json()
    assert out["pieces"] == ["ring_1", "ring_2"] and list(out["assets"]) == ["ring_1", "ring_2", "blade"]
    assert out["assets"]["ring_2"]["bbox"] == [3, 3, 4, 4] and out["assets"]["ring_2"]["label"] == "hub 2"
    assert out["groups"] == [{"id": "rotor", "label": "rotor", "parts": ["blade", "ring_1", "ring_2"], "pivot": [5.0, 5.0]}]
    assert out["preview"]["report"]["ok"]
    single = c.post("/split-part", json={"assets": assets, "part": "blade"})
    assert single.status_code == 422 and "single shape" in single.json()["detail"]


def test_label_parts_returns_motion_ideas():
    ideas = ["The rotor spins up slowly.", "  The tower   bounces in, then the rotor whirls. ", 7, "", "Lightning strikes.", "extra"]
    c, fake = client_for(json.dumps({"labels": {}, "suggestions": ideas}))
    out = c.post("/label-parts", json={"image": "data:image/jpeg;base64,AA", "assets": {"a": {"bbox": [0, 0, 1, 1]}}}).json()
    assert out["suggestions"] == ["The rotor spins up slowly.", "The tower bounces in, then the rotor whirls.", "Lightning strikes."]
    assert "Suggest 3 animations" in fake.requests[0]["messages"][0]["content"][-1]["text"]


def test_export_html_is_a_standalone_player():
    c, _ = client_for()
    r = c.post("/export.html", json={"doc": DOC, "title": "Roll & Bounce!"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert 'filename="roll-bounce.html"' in r.headers["content-disposition"]
    assert "class Player" in r.text and "<script src" not in r.text  # the player is inlined: no server needed
    assert '"frames"' in r.text
    assert c.post("/export.html", json={"doc": {"motion": "0.1"}}).status_code == 422
