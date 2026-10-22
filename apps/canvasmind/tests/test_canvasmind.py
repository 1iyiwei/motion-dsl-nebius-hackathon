"""CanvasMind library, filing, recall and MCP server, against a scripted fake model (no tokens spent)."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from motionlang.agent.llm import LLM
from PIL import Image

from canvasmind import media
from canvasmind.library import Library, fts_query
from canvasmind.studio import Studio

SUNRISE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<circle cx="50" cy="60" r="20" fill="#f4a259"/><rect x="0" y="70" width="100" height="30" fill="#5b8e7d"/></svg>"""


class FakeBackend:
    """Captions by file name; re-ranks by returning candidates whose text contains a query word."""

    def __init__(self):
        self.seen: list[tuple[str, list]] = []

    def complete(self, model, messages, **kw):
        self.seen.append((model, messages))
        content = messages[-1]["content"]
        if isinstance(content, list):  # vision: caption
            text = next(p["text"] for p in content if p["type"] == "text")
            name = text.split("The file is called '")[1].split("'")[0]
            stem = Path(name).stem.replace(".motion", "")
            return json.dumps({"title": f"{stem} artwork", "caption": f"A drawing of a {stem}.", "kind": "sketch",
                               "tags": [stem, "orange", "flat"], "palette": ["#f4a259", "nope"]}), {"prompt_tokens": 100, "completion_tokens": 20}
        query = content.split('looking for something in their studio library: "')[1].split('"')[0].lower()
        ids = [line.split(" | ")[0][2:] for line in content.splitlines() if line.startswith("- ")
               and any(w in line.lower() for w in query.split())]
        scores = {i: 3 for i in ids} | {"not-an-id": 3}
        for line in content.splitlines():
            if line.startswith("- ") and (i := line.split(" | ")[0][2:]) not in scores:
                scores[i] = 1
        return json.dumps({"scores": scores}), {"prompt_tokens": 50, "completion_tokens": 10}


@pytest.fixture
def studio(tmp_path):
    backend = FakeBackend()
    s = Studio(Library(tmp_path / "home"), llm_factory=lambda: LLM(backend))
    s.backend = backend
    return s


def png_bytes(color="red", size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_file_image_stores_original_preview_thumb_and_caption(studio):
    filed = studio.file_bytes(png_bytes(), "sunrise.png", note="for the café rebrand")
    item = filed.item
    assert not filed.duplicate and item.kind == "image" and item.title == "sunrise artwork"
    assert item.tags[0] == "sketch" and "sunrise" in item.tags
    assert item.facts["palette"] == ["#f4a259"]  # non-hex colours dropped
    d = studio.lib.dir(item.id)
    assert (d / "original.png").read_bytes() == png_bytes()
    assert (d / "preview.jpg").is_file() and studio.lib.thumb(item.id)
    model, msgs = studio.backend.seen[0]
    assert "VL" in model and "café rebrand" in msgs[0]["content"][1]["text"]


def test_duplicates_are_not_filed_twice(studio):
    a = studio.file_bytes(png_bytes(), "a.png")
    b = studio.file_bytes(png_bytes(), "b.png")
    assert b.duplicate and b.item.id == a.item.id and len(studio.backend.seen) == 1


def test_svg_and_motion_files_get_previews(studio):
    svg = studio.file_bytes(SUNRISE.encode(), "sunrise.svg").item
    assert svg.kind == "svg"
    doc = json.loads((Path(__file__).parents[3] / "packages/motionlang/examples/ball_logo.motion.json").read_text())
    mot = studio.file_bytes(json.dumps(doc).encode(), "ball_logo.motion.json").item
    assert mot.kind == "motion" and mot.facts["verbs"]
    strip = Image.open(studio.lib.dir(mot.id) / "preview.jpg")
    assert strip.width > strip.height  # frames side by side
    assert "animation strip" in studio.backend.seen[-1][1][0]["content"][1]["text"]


def test_files_without_extension_are_sniffed(studio):
    assert studio.file_bytes(png_bytes(), "photo_123").item.filename == "photo_123.png"
    assert studio.file_bytes(SUNRISE.encode(), "logo").item.kind == "svg"


def test_unknown_file_types_are_refused(studio):
    with pytest.raises(ValueError, match="can't file"):
        studio.file_bytes(b"hello", "notes.txt")


def test_fts_query_quotes_user_text():
    assert fts_query('sunrise "logo" OR drop') == '"sunrise"* OR "logo"* OR "or"* OR "drop"*'
    assert fts_query("?") == ""


def test_recall_searches_then_reranks(studio):
    sun = studio.file_bytes(png_bytes("orange"), "sunrise.png").item
    studio.file_bytes(png_bytes("blue"), "wave.png")
    items, calls = studio.recall("sunrise logo")
    assert [i.id for i in items] == [sun.id] and calls[0].model.endswith("Nano-30B-A3B")


def test_recall_on_empty_library(studio):
    assert studio.recall("anything") == ([], [])


def test_chunked_upload_files_the_whole_file(studio):
    data = png_bytes(size=(400, 300))
    half = len(data) // 2
    studio.upload_part("upl_12345678", 0, data[:half])
    studio.upload_part("upl_12345678", 1, data[half:])
    item = studio.upload_finish("upl_12345678", "../../sneaky/sunrise.png", 2, note="from my phone").item
    assert item.filename == "sunrise.png" and item.source == "chat"
    assert (studio.lib.dir(item.id) / "original.png").read_bytes() == data
    assert not (studio.uploads / "upl_12345678").exists()


def test_upload_rejects_bad_ids_and_missing_parts(studio):
    with pytest.raises(ValueError):
        studio.upload_part("../etc", 0, b"x")
    studio.upload_part("upl_abcdefgh", 1, b"x")
    with pytest.raises(ValueError, match="missing a part"):
        studio.upload_finish("upl_abcdefgh", "a.png", 2)


def test_file_url_page_without_image_becomes_a_link(studio):
    html = b'<html><head><title>Kinetic type trends</title><meta name="description" content="Bold moving letters"></head></html>'
    studio.fetch = lambda url: (html, "text/html", url)
    item = studio.file_url("https://example.com/trends").item
    assert item.kind == "link" and item.title == "Kinetic type trends" and "link" in item.tags
    assert studio.file_url("https://example.com/trends").duplicate


def test_file_url_image(studio):
    studio.fetch = lambda url: (png_bytes("green"), "image/png", url)
    item = studio.file_url("https://cdn.example.com/x/leaf").item
    assert item.kind == "image" and item.filename == "leaf.png" and item.origin.endswith("/leaf")


def test_scan_files_watched_folders_once(studio, tmp_path):
    w = tmp_path / "watch"
    w.mkdir()
    (w / "star.png").write_bytes(png_bytes("yellow"))
    (w / "readme.txt").write_text("skip me")
    (w / "deep").mkdir()
    (w / "deep" / "moon.png").write_bytes(png_bytes("white"))
    studio.watch = [(w, False)]
    assert [f["title"] for f in studio.scan()["filed"]] == ["star artwork"]  # not recursive
    assert studio.scan()["already_filed"] == 1
    studio.watch = [(w, True)]
    assert [f["title"] for f in studio.scan()["filed"]] == ["moon artwork"]


def test_inbox_is_always_watched(tmp_path):
    s = Studio(Library(tmp_path / "home"), llm_factory=lambda: LLM(FakeBackend()), watch=[str(tmp_path / "x") + "/**"])
    assert s.watch[0] == (tmp_path / "home" / "inbox", False) and s.watch[1][1] is True
    (tmp_path / "home" / "inbox" / "leaf.png").write_bytes(png_bytes("green"))
    assert [f["title"] for f in s.scan()["filed"]] == ["leaf artwork"]


def test_item_ids_cannot_escape_the_library(studio):
    with pytest.raises(KeyError):
        studio.lib.get("../../etc/passwd")


# --- MCP server ---------------------------------------------------------------------------------------------

def rpc(client, method, params=None, token="t0k"):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})
    if r.status_code != 200:
        return r.status_code, None
    data = next(line[5:] for line in r.text.splitlines() if line.startswith("data:"))
    return 200, json.loads(data)


@pytest.fixture
def client(studio):
    from starlette.testclient import TestClient

    from canvasmind.mcp_server import create_app

    with TestClient(create_app(studio, "t0k", "my-mac.tail.ts.net", 8770), base_url="http://127.0.0.1:8770") as c:
        yield c


def test_mcp_requires_the_token(client):
    assert rpc(client, "tools/list", token="wrong")[0] == 401


def test_mcp_refuses_to_start_without_a_token(studio):
    from canvasmind.mcp_server import create_app

    with pytest.raises(RuntimeError):
        create_app(studio, "")


def test_mcp_tools_and_upload_flow(client, studio):
    _, res = rpc(client, "tools/list")
    names = {t["name"] for t in res["result"]["tools"]}
    assert names == {"file_link", "recall", "show", "studio_status", "scan_studio", "animate", "tweak", "feedback",
                     "style_profile", "digest", "forget", "export"}  # upload tools callable but unlisted
    _, init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "0"}})
    caps = init["result"]["capabilities"]
    assert "tools" in caps and "resources" not in caps and "prompts" not in caps

    data = png_bytes("orange", (200, 120))
    rpc(client, "tools/call", {"name": "upload_part", "arguments": {"upload_id": "upl_mcp00001", "index": 0,
                                                                    "data_b64": base64.b64encode(data).decode()}})
    _, res = rpc(client, "tools/call", {"name": "upload_finish", "arguments": {"upload_id": "upl_mcp00001",
                                                                               "filename": "sunrise.jpg", "parts": 1}})
    filed = json.loads(res["result"]["content"][0]["text"])["filed"]
    assert filed["title"] == "sunrise artwork"

    _, res = rpc(client, "tools/call", {"name": "file_link", "arguments": {"url": "/sandbox/.hermes/cache/images/img_1.jpg"}})
    assert "cm_upload.py /sandbox/.hermes/cache/images/img_1.jpg" in json.loads(res["result"]["content"][0]["text"])["message"]

    _, res = rpc(client, "tools/call", {"name": "recall", "arguments": {"query": "sunrise"}})
    content = res["result"]["content"]
    assert json.loads(content[0]["text"])["matches"][0]["id"] == filed["id"]
    assert content[1]["type"] == "image" and content[1]["mimeType"] == "image/jpeg"
    assert (studio.lib.home / "calls.jsonl").read_text().count("\n") == 2


def test_media_kind_of():
    assert media.kind_of("A.MOTION.JSON") == "motion" and media.kind_of("x.svg") == "svg"
    assert media.kind_of("x.jpeg") == "image" and media.kind_of("x.pdf") is None


# --- animation and style ------------------------------------------------------------------------------------

LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<g id="sun"><circle id="disc" cx="50" cy="45" r="18" fill="#f4a259"/></g><rect id="sea" x="10" y="70" width="80" height="12" fill="#2e86ab"/></svg>"""


def _plan(intent_text: str) -> dict:
    # with artwork the loop merges the SVG's actors in; from words alone the plan draws its own shapes
    actors = [] if "artwork" in intent_text else [
        {"id": "sun", "shape": {"kind": "group"}, "init": {"x": 360, "y": 360}},
        {"id": "disc", "shape": {"kind": "ellipse", "rx": 60, "ry": 60}, "style": {"fill": "#f4a259"}, "parent": "sun",
         "init": {"x": 0, "y": 0}}]
    return {"motion": "0.1", "canvas": {"width": 720, "height": 720, "fps": 30, "background": "#ffffff"},
            "actors": actors, "timeline": {"id": "t", "seq": [
                {"id": "b1", "verb": "scale", "actor": "disc", "to": 1.4, "duration": 0.8, "ease": "outBack"},
                {"id": "b2", "verb": "rotate", "actor": "sun", "by": 90, "duration": 1.2, "ease": "inOutSine"}]}}


class AnimBackend(FakeBackend):
    """Adds the planner (one fixed valid plan), part labels and edit ops to the fake model."""

    def complete(self, model, messages, **kw):
        content = messages[-1]["content"]
        if isinstance(content, list) and any("numbered" in p.get("text", "") for p in content if p["type"] == "text"):
            self.seen.append((model, messages))
            return json.dumps({"labels": {"1": "sun disc", "sea": "sea"}, "idea": "the sun rises and spins"}), {}
        system = messages[0]["content"] if messages[0]["role"] == "system" else ""
        if isinstance(system, str) and "edit" in system.lower() and '"ops"' in system:
            self.seen.append((model, messages))
            return json.dumps({"ops": [{"op": "set", "id": "b2", "field": "duration", "value": 2.4}]}), {}
        if isinstance(system, str) and system:
            self.seen.append((model, messages))
            return json.dumps(_plan(content if isinstance(content, str) else "")), {}
        return super().complete(model, messages, **kw)


@pytest.fixture
def astudio(tmp_path):
    backend = AnimBackend()
    s = Studio(Library(tmp_path / "home"), llm_factory=lambda: LLM(backend))
    s.backend = backend
    return s


def test_animate_svg_makes_a_gif_item(astudio):
    logo = astudio.file_bytes(LOGO.encode(), "sunrise.svg").item
    res = astudio.animate(logo.id, "")
    assert res.ok, res.note
    assert res.gif[:6] == b"GIF89a" and res.item.kind == "motion" and res.item.facts["parent"] == logo.id
    assert res.item.caption == "the sun rises and spins"  # VL's idea fills an empty brief
    assert set(res.item.facts["verbs"]) == {"scale", "rotate"} and "outBack" in res.item.facts["easings"]
    planner_msgs = next(m for _, m in astudio.backend.seen if m[0]["role"] == "system" and "sun disc" in m[-1]["content"])
    assert planner_msgs  # the planner saw VL's part labels
    assert astudio.lib.gif(res.item.id) == res.gif


def test_only_svgs_can_be_animated(astudio):
    from canvasmind.animation import NotAnimatable

    photo = astudio.file_bytes(png_bytes(), "photo.png").item
    with pytest.raises(NotAnimatable, match="only SVG"):
        astudio.animate(photo.id)


def test_tweak_makes_a_new_version(astudio):
    logo = astudio.file_bytes(LOGO.encode(), "sunrise.svg").item
    v1 = astudio.animate(logo.id, "spin the sun").item
    v2 = astudio.tweak(v1.id, "slower").item
    assert v2.id != v1.id and v2.facts["version"] == 2 and v2.facts["parent"] == v1.id and v2.title.endswith("(v2)")


def test_feedback_builds_the_style_profile(astudio):
    from canvasmind import style

    logo = astudio.file_bytes(LOGO.encode(), "sunrise.svg").item
    v1 = astudio.animate(logo.id, "spin the sun").item
    prof, _ = astudio.feedback(v1.id, "👍", "")
    assert prof["likes"] == 1 and prof["easings"]["outBack"] == {"liked": 1}
    assert "outBack easing" in style.summary(prof)
    res = astudio.animate(logo.id, "spin the sun again")
    assert "since you liked those in 1 of 1" in res.why
    planner_prompt = [m for _, m in astudio.backend.seen if m[0]["role"] == "system"][-1][-1]["content"]
    assert "motion style" in planner_prompt and "outBack" in planner_prompt
    with pytest.raises(ValueError):
        astudio.feedback(logo.id, "like")  # not an animation
    with pytest.raises(ValueError):
        astudio.feedback(v1.id, "meh")


def test_mcp_animate_returns_a_gif(astudio):
    from starlette.testclient import TestClient

    from canvasmind.mcp_server import create_app

    logo = astudio.file_bytes(LOGO.encode(), "sunrise.svg").item
    with TestClient(create_app(astudio, "t0k", "", 8770), base_url="http://127.0.0.1:8770") as c:
        _, res = rpc(c, "tools/call", {"name": "animate", "arguments": {"item_id": logo.id, "brief": "spin"}})
        content = res["result"]["content"]
        assert content[1]["type"] == "image" and content[1]["mimeType"] == "image/gif"
        photo = astudio.file_bytes(png_bytes(), "p.png").item
        _, res = rpc(c, "tools/call", {"name": "animate", "arguments": {"item_id": photo.id}})
        assert "only SVG" in json.loads(res["result"]["content"][0]["text"])["message"]


def test_tempo_requests_need_no_model():
    from canvasmind.animation import retime, tempo_factor

    assert tempo_factor("slower") == 1.5 and tempo_factor("Make it faster!") == round(1 / 1.5, 3)
    assert 1 < tempo_factor("a bit slower") < 1.5 < tempo_factor("much slower")
    assert tempo_factor("slower and bluer") is None and tempo_factor("make the sun slower") is None
    doc = {"timeline": {"id": "t", "stagger": [{"id": "a", "duration": 1.0, "delay": 0.2}], "offset": 0.1}}
    out = retime(doc, 2)
    assert out["timeline"]["offset"] == 0.2 and out["timeline"]["stagger"][0] == {"id": "a", "duration": 2.0, "delay": 0.4}
    assert doc["timeline"]["offset"] == 0.1  # the original is untouched


def test_slower_tweak_skips_the_model(astudio):
    logo = astudio.file_bytes(LOGO.encode(), "sunrise.svg").item
    v1 = astudio.animate(logo.id, "spin the sun").item
    n = len(astudio.backend.seen)
    v2 = astudio.tweak(v1.id, "slower").item
    assert len(astudio.backend.seen) == n and v2.facts["seconds"] > v1.facts["seconds"]


def test_forget_removes_files_search_and_feedback(astudio):
    logo = astudio.file_bytes(LOGO.encode(), "sunrise.svg").item
    anim = astudio.animate(logo.id, "spin the sun").item
    astudio.feedback(anim.id, "like", "")
    out = astudio.forget(anim.id)
    assert out["feedback_removed"] == 1 and astudio.style.profile()["likes"] == 0
    assert not astudio.lib.dir(anim.id).exists()
    with pytest.raises(KeyError):
        astudio.lib.get(anim.id)
    assert [i.id for i in astudio.lib.search("animation")] == []


def test_export_zips_the_library(astudio):
    import zipfile

    astudio.file_bytes(png_bytes(), "a.png")
    path = astudio.export()
    names = zipfile.ZipFile(path).namelist()
    assert any(n.endswith("library.sqlite") for n in names) and any(n.endswith("original.png") for n in names)
    assert not any("exports/" in n for n in names)


def test_demo_reset_files_fixtures_and_seeds_style(tmp_path):
    from canvasmind.demo import DEFAULT_FIXTURES, reset

    home = tmp_path / "home"
    old = Studio(Library(home), llm_factory=lambda: LLM(FakeBackend()))
    old.file_bytes(png_bytes(), "old.png")
    out = reset(home, DEFAULT_FIXTURES, lambda h: Studio(Library(h), llm_factory=lambda: LLM(FakeBackend())))
    assert out["backup"] and (Path(out["backup"]) / "library.sqlite").exists()  # moved aside, not deleted
    assert len(out["filed"]) == 5 and out["style"]["likes"] == 2 and out["style"]["dislikes"] == 1
    lib = Library(home)
    dates = sorted(i.created for i in lib.recent(10))
    assert dates[0] < dates[-1] - 5 * 86400  # backdated, so "last week" means something


def test_library_survives_demo_reset_while_running(tmp_path):
    """A running server keeps its Library; demo-reset moves the folder away and makes a new one."""
    import shutil

    from canvasmind.demo import DEFAULT_FIXTURES, reset

    home = tmp_path / "home"
    running = Studio(Library(home), llm_factory=lambda: LLM(FakeBackend()))
    running.file_bytes(png_bytes(), "old.png")
    reset(home, DEFAULT_FIXTURES, lambda h: Studio(Library(h), llm_factory=lambda: LLM(FakeBackend())))
    assert len(running.lib.recent(20)) == 5  # sees the new library
    running.file_bytes(png_bytes("blue"), "new.png")  # and can write to it
    shutil.rmtree(home)
    assert running.lib.recent(5) == []  # even a deleted library comes back empty, not broken


def test_prompt_only_animation(astudio):
    res = astudio.animate("", "a ball bounces in")
    assert res.ok and res.item.facts["parent"] is None and res.item.title == "a ball bounces in"
    from canvasmind.animation import NotAnimatable

    with pytest.raises(NotAnimatable):
        astudio.animate("", "")


def test_per_user_libraries_via_reserved_argument(tmp_path):
    from starlette.testclient import TestClient

    from canvasmind.demo import DEFAULT_FIXTURES
    from canvasmind.mcp_server import create_app
    from canvasmind.tenants import Tenants

    make = lambda h: Studio(Library(h), llm_factory=lambda: LLM(FakeBackend()))  # noqa: E731
    default = make(tmp_path / "home")
    tenants = Tenants(tmp_path / "home", DEFAULT_FIXTURES, make)
    with TestClient(create_app(default, "t0k", "", 8770, tenants), base_url="http://127.0.0.1:8770") as c:
        _, a = rpc(c, "tools/call", {"name": "studio_status", "arguments": {"_cm_user": "111"}})
        counts = json.loads(a["result"]["content"][0]["text"])["counts"]
        assert sum(counts.values()) == 5  # seeded from the fixtures on first use
        rpc(c, "tools/call", {"name": "upload_part", "arguments": {"_cm_user": "111", "upload_id": "upl_tenant01", "index": 0,
                                                                   "data_b64": base64.b64encode(png_bytes()).decode()}})
        rpc(c, "tools/call", {"name": "upload_finish", "arguments": {"_cm_user": "111", "upload_id": "upl_tenant01",
                                                                     "filename": "mine.png", "parts": 1}})
        _, b = rpc(c, "tools/call", {"name": "studio_status", "arguments": {"_cm_user": "222"}})
        assert sum(json.loads(b["result"]["content"][0]["text"])["counts"].values()) == 5  # user 222 doesn't see 111's upload
        _, a = rpc(c, "tools/call", {"name": "studio_status", "arguments": {"_cm_user": "111"}})
        assert sum(json.loads(a["result"]["content"][0]["text"])["counts"].values()) == 6
        _, d = rpc(c, "tools/call", {"name": "studio_status", "arguments": {}})
        assert json.loads(d["result"]["content"][0]["text"])["counts"] == {}  # no user: the default library
    assert (tmp_path / "home" / "users" / "111" / ".seeded").is_file()
    assert (tmp_path / "home" / "template" / ".seeded").is_file()  # seeded once, then copied per user
