import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from motionlang.agent.llm import LLM

from constellate.guard import Guard
from constellate.server import create_app


class Scripted:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, model, messages, **kw):
        return self.reply, {"prompt_tokens": 700, "completion_tokens": 300}


SCRIPT = json.dumps({"title": "t", "formations": {"h": {"kind": "preset", "shape": "heart", "fill": "fill"}},
                     "steps": [{"kind": "form", "id": "a", "formation": "h", "transition": 14}]})
PLAN = {"intent": "a heart", "drones": 40, "sources": {}, "limits": {}}


def client(guard, tmp_path):
    return TestClient(create_app(media_dir=tmp_path, llm_factory=lambda: LLM(Scripted(SCRIPT)), guard=guard))


def test_access_code_gates_model_and_cpu_endpoints_but_not_pages(tmp_path):
    c = client(Guard(access_code="sky"), tmp_path)
    assert c.get("/config").json()["access_required"] is True
    assert c.get("/").status_code == 200 and c.get("/api/examples").status_code == 200
    show = c.get("/api/examples/heart-logo-flyable").json()
    assert c.post("/api/check", json={"show": show}).status_code == 200  # loading an example stays open
    for url, body in (("/api/plan", PLAN), ("/api/brief", {"venue": "x", "date": "y"}), ("/api/sweep", {"show": show}),
                      ("/api/video", {"show": show}), ("/api/live", {"show": show})):
        r = c.post(url, json=body)
        assert r.status_code == 401 and "access code" in r.json()["detail"], url
    assert c.post("/api/plan", json=PLAN, headers={"X-Access-Code": "nope"}).status_code == 401
    r = c.post("/api/plan", json=PLAN, headers={"X-Access-Code": "sky"})
    assert r.status_code == 200 and json.loads(r.text.splitlines()[-1])["type"] == "result"


def test_rate_limit_and_daily_budget(tmp_path):
    g = Guard(per_hour=2, daily_tokens=1500)
    c = client(g, tmp_path)
    assert c.post("/api/plan", json=PLAN).status_code == 200  # 1000 tokens booked
    assert g.status()["tokens_left_today"] == 500
    assert c.post("/api/plan", json=PLAN).status_code == 200  # 2000 booked: over budget now
    r = c.post("/api/plan", json=PLAN)
    assert r.status_code == 429 and "budget" in r.json()["detail"]
    g2 = Guard(per_hour=1)
    c2 = client(g2, tmp_path)
    assert c2.post("/api/plan", json=PLAN).status_code == 200
    r = c2.post("/api/plan", json=PLAN)
    assert r.status_code == 429 and "Rate limit" in r.json()["detail"]


def test_heavy_jobs_are_capped():
    g = Guard(max_jobs=1)
    g.acquire_job()
    with pytest.raises(HTTPException) as e:
        g.acquire_job()
    assert e.value.status_code == 429
    g.release_job()
    with g.job():
        pass
    assert g._jobs == 0


def test_visitor_uses_the_proxys_address_only_behind_a_proxy():
    class Req:
        headers = {"x-forwarded-for": "6.6.6.6, 1.2.3.4"}
        client = type("C", (), {"host": "10.0.0.1"})()
    assert Guard(behind_proxy=True).visitor(Req()) == "1.2.3.4"
    assert Guard().visitor(Req()) == "10.0.0.1"
