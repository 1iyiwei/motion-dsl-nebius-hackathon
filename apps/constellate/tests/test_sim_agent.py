"""Sim, sweep, video and the agent loop (scripted fake model; live test with RUN_LIVE=1)."""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from motionlang.agent.llm import LLM

from constellate import agent
from constellate.flight import tracks_m
from constellate.pipeline import load_show, run
from constellate.show import Show
from constellate.sim import Wind, simulate
from constellate.sweep import sim_issues, sweep
from constellate.video import baked_colors, frames, write_mp4

EX = Path(__file__).parent.parent / "src" / "constellate" / "examples"


@pytest.fixture(scope="module")
def flyable():
    return run(load_show(EX / "heart-logo-flyable.show.json"))


def test_sim_tracks_closely_in_calm_air_and_drifts_more_in_wind(flyable):
    times, plan = tracks_m(flyable.baked, flyable.show)
    calm = simulate(times, plan, flyable.show.limits.max_flight_time, Wind(speed=0), seed=1)
    windy = simulate(times, plan, flyable.show.limits.max_flight_time, Wind(speed=10, direction_deg=0), seed=1)
    assert calm.err.max() < 0.3
    assert windy.err.max() > calm.err.max() * 3
    hover = flyable.compiled.duration / flyable.show.limits.max_flight_time
    assert hover * 0.95 < calm.battery_used.max() < hover * 1.3
    assert calm.pos.shape == (len(times), flyable.show.drones, 3)


def test_sweep_certifies_the_flyable_example(flyable):
    sw = sweep(flyable.show, flyable.compiled, flyable.baked, winds=(0.0, 6.0), seeds=2, workers=1)
    cert = sw.certificate()
    assert cert["flyable"] and cert["certified_wind"] == 6.0 and cert["runs"] == 4
    assert "not a regulatory approval" in cert["note"]


def test_sim_issue_in_strong_wind_has_a_hint(flyable):
    d = flyable.show.model_dump()
    d["limits"]["design_wind"] = 12.0
    show = Show.model_validate(d)
    c = run(show)
    sw = sweep(show, c.compiled, c.baked, winds=(12.0,), seeds=3, workers=1)
    issues = sim_issues(show, c.compiled, c.baked, sw)
    assert issues and all(i.hint for i in issues)
    assert {i.code for i in issues} <= {"E_SIM_TRACKING", "E_SIM_NEAR_MISS", "E_SIM_BATTERY"}


def test_video_frames_and_mp4(flyable, tmp_path):
    times, plan = tracks_m(flyable.baked, flyable.show)
    pos = np.c_[plan[:, :, :1], np.zeros(plan.shape[:2] + (1,)), plan[:, :, 1:]][:6]
    cols = baked_colors(flyable.baked, flyable.show.drones)[:6]
    im = next(frames(pos, cols, flyable.show.limits, 320, 180))
    assert im.size == (320, 180)
    out = write_mp4(tmp_path / "x.mp4", pos, cols, flyable.show.limits, width=320, height=180)
    assert out.exists() and out.stat().st_size > 1000


def test_apply_ops_set_add_remove_and_protected_paths():
    raw = {"steps": [{"id": "a"}, {"id": "b"}], "formations": {"h": {"size": 0.5}}, "limits": {"design_wind": 6}}
    out = agent.apply_ops(raw, [{"op": "set", "path": "/formations/h/size", "value": 0.7},
                                {"op": "add", "path": "/steps/1", "value": {"id": "x"}},
                                {"op": "remove", "path": "/steps/0"}])
    assert [s["id"] for s in out["steps"]] == ["x", "b"] and out["formations"]["h"]["size"] == 0.7
    assert raw["formations"]["h"]["size"] == 0.5  # atomic, on a copy
    with pytest.raises(ValueError, match="not editable"):
        agent.apply_ops(raw, [{"op": "set", "path": "/limits/design_wind", "value": 2}])
    with pytest.raises(ValueError, match="op 0"):
        agent.apply_ops(raw, [{"op": "set", "path": "/steps/9/x", "value": 1}])


class Scripted:
    """A fake model: replies in order, records what it was asked."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def complete(self, model, messages, **kw):
        self.seen.append(messages)
        return self.replies.pop(0), {"prompt_tokens": 10, "completion_tokens": 5}


def test_make_flyable_applies_the_editors_ops():
    show = load_show(EX / "heart-logo.show.json")
    fixed = json.loads((EX / "heart-logo-flyable.show.json").read_text())
    ops = [{"op": "set", "path": f"/steps/{i}/{k}", "value": fixed["steps"][i][k]}
           for i, k in ((0, "transition"), (1, "duration"), (2, "transition"), (3, "transition"))]
    ops.append({"op": "set", "path": "/formations/thanks/size", "value": 0.2})
    backend = Scripted(["not json", json.dumps({"ops": ops, "note": "applied hints"})])
    out = agent.make_flyable(show, LLM(backend), simulate=False)
    assert out.ok and len(out.rounds) == 1 and out.rounds[0].note == "applied hints"
    assert "E_GEOFENCE" in backend.seen[0][1]["content"]  # the hints reached the model
    assert "limits" not in backend.seen[0][1]["content"].split("Problems")[0]  # the script it sees has no limits


def test_plan_merges_sources_and_retries_invalid_scripts():
    base = load_show(EX / "heart-logo.show.json")
    good = {"title": "t", "formations": {"l": {"kind": "svg", "svg": "orbit"}},
            "steps": [{"kind": "form", "id": "a", "formation": "l"}], "limits": {"max_speed": 99}}
    backend = Scripted([json.dumps({"formations": {}, "steps": [{"kind": "form", "id": "a", "formation": "nope"}]}), json.dumps(good)])
    show = agent.plan("our logo", base, LLM(backend))
    assert show.sources.svgs["orbit"].startswith("<svg") and show.limits.max_speed == 8.0  # limits not the model's call
    assert "invalid" in backend.seen[1][-1]["content"]


@pytest.mark.skipif(not os.environ.get("RUN_LIVE"), reason="set RUN_LIVE=1 to call Token Factory")
@pytest.mark.live
def test_live_plan_and_repair():
    base = load_show(EX / "heart-logo.show.json")
    llm = LLM()
    show = agent.plan("a red heart that spins once, then our logo", base, llm)
    out = agent.make_flyable(show, llm, simulate=False, max_rounds=4)
    assert out.ok, [str(i) for i in out.checked.flight.issues]


def test_tweak_may_change_the_drone_count_but_repairs_may_not():
    show = load_show(EX / "heart-logo-flyable.show.json")
    backend = Scripted([json.dumps({"ops": [{"op": "set", "path": "/drones", "value": 150}], "note": "150 drones"})])
    new, ops, _ = agent.tweak(show, "use 150 drones", LLM(backend))
    assert new.drones == 150 and new.limits == show.limits
    with pytest.raises(ValueError, match="not editable"):
        agent.apply_ops(show.model_dump(mode="json"), [{"op": "set", "path": "/drones", "value": 150}])


def test_sweep_reports_progress(flyable):
    seen = []
    sweep(flyable.show, flyable.compiled, flyable.baked, winds=(0.0,), seeds=3, workers=1, on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 3), (2, 3), (3, 3)]
