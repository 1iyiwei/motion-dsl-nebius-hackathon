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
    studio.watch = [w]
    assert [f["title"] for f in studio.scan()["filed"]] == ["star artwork"]
    assert studio.scan()["already_filed"] == 1


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
    assert names == {"file_link", "recall", "show", "studio_status", "scan_studio"}  # upload tools callable but unlisted
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
