"""Shapes for objects without one: library, web search (fake Tavily/Commons/SVG Repo), Nemotron drawing, placing, saving."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from motionlang.agent import LLM, model_for
from sketchdynamics.guard import Guard
from sketchdynamics.server import create_app
from sketchdynamics.shapes import Library, WebSearch, clean_svg, placed_parts

from test_server import SUPER, Scripted

GEM = '<svg viewBox="0 0 10 10"><polygon points="5,0 10,5 5,10 0,5" fill="#7fd"/></svg>'
TWO = ('<svg viewBox="0 0 20 10"><g id="rocket"><rect x="0" y="0" width="8" height="10" fill="#ccc"/>'
       '<circle cx="15" cy="5" r="4" fill="#f60"/></g></svg>')
EVIL = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" onload="alert(1)">'
        '<script>alert(2)</script><foreignObject><div>hi</div></foreignObject><image xlink:href="https://evil.example/x.png"/>'
        '<a href="javascript:alert(3)"><path d="M0 0H10V10Z" fill="red"/></a></svg>')


def library(tmp_path, writable=True) -> Library:
    (tmp_path / "gem.svg").write_text(GEM)
    (tmp_path / "README.md").write_text("# Sources\n\n## Ours (Apache-2.0)\n* [blue gems](gem.svg): test art\n* [ghost](missing.svg)\n")
    return Library(tmp_path, writable=writable)


def fake_web(fetched: list[str]) -> WebSearch:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        fetched.append(url)
        if url.startswith("https://api.tavily.com/search"):
            assert req.headers["authorization"] == "Bearer tvly-test"
            body = json.loads(req.content)
            assert body["include_domains"] == ["svgrepo.com", "commons.wikimedia.org"] and "crystal" in body["query"]
            return httpx.Response(200, json={"results": [
                {"url": "https://staging.svgrepo.com/svg/82364/ice-crystal?edit=true", "title": "Ice Crystal SVG Vector - SVG Repo"},
                {"url": "https://www.svgrepo.com/svg/111/big-crystal", "title": "Big Crystal SVG Vector"},
                {"url": "https://www.svgrepo.com/vectors/crystal/", "title": "Crystal vectors"},  # a listing, not one icon
                {"url": "https://commons.wikimedia.org/wiki/File:Crystal_icon.svg", "title": "File:Crystal icon.svg - Wikimedia Commons"},
                {"url": "https://www.svgrepo.com/svg/222/bad-crystal", "title": "Bad"},
            ]})
        if url.startswith("https://commons.wikimedia.org/w/api.php"):
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": "File:Crystal icon.svg", "imageinfo": [{
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Crystal_icon.svg?utm_source=x",
                "extmetadata": {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": '<a href="u">Jane</a>'}}}]}}}})
        if url == "https://www.svgrepo.com/show/82364/ice-crystal.svg":
            return httpx.Response(200, text=EVIL)
        if url == "https://www.svgrepo.com/show/111/big-crystal.svg":
            return httpx.Response(200, text="<svg>" + "x" * 500_000 + "</svg>")  # too big
        if url == "https://upload.wikimedia.org/wikipedia/commons/a/ab/Crystal_icon.svg":
            return httpx.Response(200, text=GEM)
        if url == "https://www.svgrepo.com/show/222/bad-crystal.svg":
            return httpx.Response(200, text="<html>not svg</html>")
        return httpx.Response(404)

    return WebSearch("tvly-test", httpx.Client(transport=httpx.MockTransport(handler)))


def client(*replies, tmp_path, web=None, guard=None, writable=True):
    fake = Scripted(*replies)
    app = create_app(lambda: LLM(fake), guard=guard or Guard(), web=web, library=library(tmp_path, writable))
    return TestClient(app), fake


def test_clean_svg_keeps_only_paths():
    svg, parts = clean_svg(EVIL)
    assert parts == 1 and svg.count("<path") == 1
    for bad in ("script", "onload", "foreignObject", "href", "evil", "javascript", "<a", "<image"):
        assert bad not in svg
    with pytest.raises(ValueError):
        clean_svg("<svg/>")


def test_library_matches_whole_words_and_plurals(tmp_path):
    lib = library(tmp_path)
    assert [c.title for c in lib.find("gem")] == ["blue gems"]
    assert lib.find("blue gem")[0].source == "library"
    assert lib.find("gemstone") == [] and lib.find("ghost") == []  # no partial words; listed file missing


def test_library_save_records_source_and_license(tmp_path):
    lib = library(tmp_path)
    with pytest.raises(ValueError, match="license"):
        lib.save("crystal", GEM, license=" ", source="svgrepo")
    f = lib.save("crystal", EVIL, license="CC0", source="commons", page_url="https://commons.wikimedia.org/wiki/File:X.svg", credit="Jane")
    assert f == "crystal.svg" and "script" not in (tmp_path / f).read_text()
    assert lib.save("crystal", GEM, license="Apache-2.0", source="nemotron") == "crystal-2.svg"
    readme = (tmp_path / "README.md").read_text()
    assert readme.count("## Found or drawn in SketchDynamics") == 1
    assert "* [crystal](crystal.svg): from Wikimedia Commons (https://commons.wikimedia.org/wiki/File:X.svg), by Jane; license: CC0" in readme
    assert "* [crystal](crystal-2.svg): drawn by Nemotron in SketchDynamics; license: Apache-2.0" in readme
    assert [c.title for c in lib.find("crystal")] == ["crystal", "crystal"]
    with pytest.raises(PermissionError):
        Library(tmp_path, writable=False).save("x", GEM, license="CC0", source="nemotron")


def test_needed_asks_nano_and_drops_what_is_on_the_canvas(tmp_path):
    c, fake = client(json.dumps({"objects": ["Crystal", "sun", "crystal", "the  paper plane.", "red ball", "Stars", "bounce"]}), tmp_path=tmp_path)
    out = c.post("/shapes/needed", json={"intent": "a crystal falls past the sun and a paper plane", "have": ["Sun"]}).json()
    assert out["objects"] == ["crystal", "paper plane"]
    assert fake.requests[0]["model"] == model_for("editor") and "Already on the canvas: Sun" in fake.requests[0]["messages"][0]["content"]
    assert out["call"]["purpose"] == "objects"


def test_needed_keeps_only_objects_the_request_names(tmp_path):
    c, _ = client(json.dumps({"objects": ["ball", "string", "hand"]}), json.dumps({"objects": ["frogs", "leaf", "water lily", "pond"]}), tmp_path=tmp_path)
    assert c.post("/shapes/needed", json={"intent": "a ball swings from left to right"}).json()["objects"] == []
    assert c.post("/shapes/needed", json={"intent": "two green frogs jump onto a lily pad"}).json()["objects"] == ["frogs"]


def test_needed_skips_parts_already_on_the_canvas(tmp_path):
    c, _ = client(json.dumps({"objects": ["blade", "wind turbine", "bird"]}), tmp_path=tmp_path)
    out = c.post("/shapes/needed", json={"intent": "the blades of the wind turbine spin while a bird flies by",
                                         "have": ["wind turbine blades and hub", "tower"]}).json()
    assert out["objects"] == ["bird"]


def test_find_prefers_the_library(tmp_path):
    fetched = []
    c, _ = client(tmp_path=tmp_path, web=fake_web(fetched))
    out = c.post("/shapes/find", json={"name": "gem"}).json()
    assert out["searched"] == "library" and out["candidates"][0]["title"] == "blue gems" and fetched == []


def test_find_without_a_tavily_key_says_so(tmp_path):
    c, _ = client(tmp_path=tmp_path, web=None)
    out = c.post("/shapes/find", json={"name": "crystal"}).json()
    assert out["candidates"] == [] and "TAVILY_API_KEY" in out["note"]
    assert c.get("/config").json()["web_search"] is False


def test_find_searches_icon_sites_and_cleans_what_it_fetches(tmp_path):
    fetched = []
    c, _ = client(tmp_path=tmp_path, web=fake_web(fetched))
    out = c.post("/shapes/find", json={"name": "crystal"}).json()
    assert out["searched"] == "web" and out["web"]["results"] == 5 and out["web"]["usable"] == 2 and out["web"]["skipped"] == 2
    by_source = {x["source"]: x for x in out["candidates"]}
    repo, commons = by_source["svgrepo"], by_source["commons"]
    assert repo["page_url"] == "https://www.svgrepo.com/svg/82364/ice-crystal" and repo["title"] == "Ice Crystal"
    assert repo["license"] == "" and not repo["license_verified"] and "script" not in repo["svg"]
    assert commons["license"] == "CC0" and commons["license_verified"] and commons["credit"] == "Jane" and commons["title"] == "Crystal icon"
    assert not any("vectors/crystal" in u or "evil" in u for u in fetched)  # listings skipped; nothing from inside a file fetched


def test_fetch_only_from_allowed_hosts():
    fetched = []
    web = fake_web(fetched)
    assert web._fetch("https://evil.example/x.svg") is None and web._fetch("http://www.svgrepo.com/show/1/x.svg") is None
    assert fetched == []


def test_judge_parses_scores_from_vision(tmp_path):
    reply = {"scores": [{"n": "#2", "score": 14, "note": "great"}, {"n": 1, "score": 3, "note": "a ball"}, {"n": 9, "score": 5}]}
    c, fake = client(json.dumps(reply), tmp_path=tmp_path)
    out = c.post("/shapes/judge", json={"name": "crystal", "image": "data:image/jpeg;base64,AAAA", "count": 3}).json()
    assert out["scores"] == [{"n": 1, "score": 3.0, "note": "a ball"}, {"n": 2, "score": 10.0, "note": "great"}, {"n": 3, "score": None, "note": ""}]
    assert fake.requests[0]["model"] == model_for("vision")


def test_draw_retries_once_with_the_importers_complaint(tmp_path):
    c, fake = client(json.dumps({"svg": "<svg></svg>"}), json.dumps({"svg": TWO}), tmp_path=tmp_path)
    out = c.post("/shapes/draw", json={"name": "rocket", "feedback": "looks like a pencil", "previous": GEM}).json()
    assert len(out["calls"]) == 2 and out["candidate"]["source"] == "nemotron" and out["candidate"]["parts"] == 2
    assert out["candidate"]["license"] == "Apache-2.0"
    assert all(r["model"] == SUPER for r in fake.requests)
    first, second = (r["messages"][0]["content"] for r in fake.requests)
    assert "looks like a pencil" in first and GEM in first and "could not be used" in second


def test_draw_gives_up_softly(tmp_path):
    c, _ = client("no json", "still none", tmp_path=tmp_path)
    out = c.post("/shapes/draw", json={"name": "rocket"}).json()
    assert out["candidate"] is None and "couldn't be used" in out["error"]


def test_place_on_an_empty_canvas_and_beside_a_logo(tmp_path):
    c, _ = client(tmp_path=tmp_path)
    one = c.post("/shapes/place", json={"name": "crystal", "svg": GEM}).json()
    assert one["added"] == ["crystal"] and one["assets"]["crystal"]["label"] == "crystal" and one["groups"] == []
    x, y, w, h = one["assets"]["crystal"]["bbox"]
    assert abs(x + w / 2 - 640) < 1 and abs(y + h / 2 - 360) < 1 and abs(h - 720 * 0.45) < 1
    assert one["preview"]["tracks"] is not None

    two = c.post("/shapes/place", json={"name": "Rocket", "svg": TWO, "assets": one["assets"], "groups": one["groups"], "actors": one["actors"]}).json()
    assert two["added"] == ["rocket_1", "rocket_2"] and two["groups"] == [{"id": "rocket", "label": "Rocket", "parts": ["rocket_1", "rocket_2"]}]
    rx = min(two["assets"][p]["bbox"][0] for p in two["added"])
    assert rx > x + w  # beside the crystal, not on it
    assert {a["id"] for a in two["actors"]} >= {"logo", "crystal", "rocket", "rocket_1", "rocket_2"}
    assert two["preview"]["report"]["ok"]


def test_place_avoids_id_collisions():
    existing = {"crystal": {"d": "M0 0H10V10Z", "bbox": [0, 0, 10, 10]}}
    parts, _, group = placed_parts(GEM, "crystal", existing, (1280, 720))
    assert list(parts) == ["crystal_2"] and group is None


def test_save_endpoint(tmp_path):
    c, _ = client(tmp_path=tmp_path)
    assert c.post("/shapes/save", json={"name": "crystal", "svg": GEM, "source": "svgrepo"}).status_code == 422
    assert c.post("/shapes/save", json={"name": "crystal", "svg": GEM, "license": "CC0", "source": "svgrepo"}).json() == {"file": "crystal.svg"}
    locked, _ = client(tmp_path=tmp_path, writable=False)
    assert locked.post("/shapes/save", json={"name": "x", "svg": GEM, "license": "CC0"}).status_code == 403
    assert locked.get("/config").json()["save_shapes"] is False


def test_model_backed_shape_calls_are_guarded(tmp_path):
    c, _ = client(tmp_path=tmp_path, web=fake_web([]), guard=Guard(access_code="sesame"))
    assert c.post("/shapes/needed", json={"intent": "a crystal"}).status_code == 401
    assert c.post("/shapes/find", json={"name": "crystal"}).status_code == 401  # Tavily credits are the host's too
    assert c.post("/shapes/find", json={"name": "gem"}).status_code == 200  # the library is free
    assert c.post("/shapes/draw", json={"name": "x"}).status_code == 401
