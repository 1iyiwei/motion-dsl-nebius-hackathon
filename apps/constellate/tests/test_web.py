import json

from fastapi.testclient import TestClient

from motionlang.agent.llm import LLM

from constellate.server import create_app


class Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, model, messages, **kw):
        return self.replies.pop(0), {"prompt_tokens": 3, "completion_tokens": 2}


def client(replies=(), tmp_path=None):
    return TestClient(create_app(media_dir=tmp_path, llm_factory=lambda: LLM(Scripted(replies))))


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_pages_examples_and_check(tmp_path):
    c = client(tmp_path=tmp_path)
    assert "Constellate" in c.get("/").text
    assert c.get("/player.js").status_code == 200
    assert c.get("/healthz").json() == {"status": "ok"}
    names = [e["name"] for e in c.get("/api/examples").json()]
    assert "heart-logo" in names
    show = c.get("/api/examples/heart-logo").json()
    assert show["sources"]["svgs"]["orbit"].startswith("<svg")
    r = c.post("/api/check", json={"show": show}).json()
    assert r["motion_ok"] and not r["ok"] and r["frames"] > 100
    tracks = c.get(r["tracks_url"]).json()  # served separately, merged from per-chunk bakes
    assert tracks["frames"] == r["frames"] and len(tracks["actors"]) == show["drones"]
    assert c.get("/api/tracks/nope").status_code == 404
    assert {i["code"] for i in r["flight"]["issues"]} >= {"E_SPEED", "E_GEOFENCE"}
    assert r["steps"][-1]["id"] == "land"
    assert c.get("/api/examples/../server").status_code == 404


def test_plan_streams_events_then_a_checked_result(tmp_path):
    script = {"title": "t", "formations": {"h": {"kind": "preset", "shape": "heart", "fill": "fill"}},
              "steps": [{"kind": "form", "id": "a", "formation": "h", "transition": 14}]}
    c = client([json.dumps(script)], tmp_path)
    evs = events(c.post("/api/plan", json={"intent": "a heart", "drones": 60, "sources": {}, "limits": {"design_wind": 4}}))
    assert evs[0]["type"] == "call_start" and any(e["type"] == "call" and e["model"] for e in evs)
    res = evs[-1]
    assert res["type"] == "result" and res["show"]["drones"] == 60 and res["show"]["limits"]["design_wind"] == 4
    assert len(c.get(res["tracks_url"]).json()["actors"]) == 60


def test_fly_reports_rounds_and_errors_stream_as_events(tmp_path):
    c = client(["not json", "still not json", "nope"], tmp_path)
    show = c.get("/api/examples/heart-logo").json()
    evs = events(c.post("/api/fly", json={"show": show}))
    assert evs[-1]["type"] == "error" and "valid ops" in evs[-1]["msg"]


def test_video_renders_in_the_background(tmp_path):
    c = client(tmp_path=tmp_path)
    show = c.get("/api/examples/heart-logo-flyable").json()
    show["steps"] = show["steps"][:1]
    show["steps"][0]["hold"] = 0.5
    vid = c.post("/api/video", json={"show": show}).json()["id"]
    import time

    for _ in range(120):
        s = c.get(f"/api/video/{vid}").json()
        if s["status"] != "rendering":
            break
        time.sleep(0.5)
    assert s["status"] == "done", s
    assert c.get(s["url"]).status_code == 200
