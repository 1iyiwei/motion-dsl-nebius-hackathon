from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from constellate.live import MAX_STEADY, LiveSession, tilt_to_wind
from constellate.pipeline import load_show, run
from constellate.server import create_app

EX = Path(__file__).parent.parent / "src" / "constellate" / "examples"


def test_tilt_maps_to_wind():
    assert np.allclose(tilt_to_wind(2, -3), 0)  # roughly flat = calm
    right = tilt_to_wind(0, 45)
    assert right[0] == MAX_STEADY and right[1] == 0
    away = tilt_to_wind(-45, 0)
    assert away[1] == MAX_STEADY  # top tilted away from you blows the swarm away from the crowd
    assert np.linalg.norm(tilt_to_wind(-90, 90)) <= MAX_STEADY + 1e-9


def test_live_session_flies_and_gusts_push_drones():
    c = run(load_show(EX / "heart-logo-flyable.show.json"))
    calm, windy = LiveSession(c, seed=1), LiveSession(c, seed=1)
    windy.set_wind(10, 0)
    for k in range(240):
        if k % 30 == 0:
            windy.shake(1.0)
        a, b = calm.tick(), windy.tick()
    assert a["frame"] == b["frame"] == 240 and len(a["pos"]) == c.show.drones
    assert b["drift"] > a["drift"] * 3 and b["wind"][0] > 5
    windy.set_wind(0, 0)
    for _ in range(210):  # 7 s: a gust decays with a 2 s time constant
        f = windy.tick()
    assert f["gust"] < 1.0


def test_live_websockets_stream_frames_and_take_pad_input(tmp_path):
    # `with` keeps one event loop for the whole test, as uvicorn does; the live loop runs on it.
    with TestClient(create_app(media_dir=tmp_path)) as client:
        show = client.get("/api/examples/heart-logo-flyable").json()
        r = client.post("/api/live", json={"show": show}).json()
        assert r["code"] and "<svg" in r["qr"] and r["pad_url"].endswith(f"/pad?code={r['code']}")
        assert "wind pad" in client.get(f"/pad?code={r['code']}").text
        with client.websocket_connect(f"/ws/live/{r['code']}") as view, client.websocket_connect(f"/ws/pad/{r['code']}") as pad:
            f = view.receive_json()
            assert f["type"] == "frame" and len(f["pos"]) == show["drones"]
            pad.send_json({"tilt": [0, 45]})
            for _ in range(20):
                f = view.receive_json()
            assert f["wind"][0] > 5 and f["pad"]
            assert pad.receive_json()["type"] == "status"
        client.post(f"/api/live/{r['code']}/stop")
