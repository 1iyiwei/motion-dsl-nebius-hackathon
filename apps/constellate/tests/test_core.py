import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import cKDTree

from constellate.compiler import PPM
from constellate.formations import launch_grid, sample
from constellate.pipeline import load_show, run
from constellate.show import Limits, Show

EX = Path(__file__).parent.parent / "src" / "constellate" / "examples"


def show(formations, steps, **kw):
    return Show.model_validate({"formations": formations, "steps": steps, "land": False, **kw})


@pytest.mark.parametrize("f", [
    {"kind": "preset", "shape": "heart"}, {"kind": "preset", "shape": "heart", "fill": "fill"},
    {"kind": "preset", "shape": "star"}, {"kind": "preset", "shape": "grid"}, {"kind": "preset", "shape": "spiral"},
    {"kind": "text", "text": "HI", "size": 0.4},
])
def test_formations_place_n_distinct_drones_in_their_box(f):
    s = show({"x": f}, [{"kind": "form", "id": "a", "formation": "x"}])
    out = sample(s.formations["x"], 80, s.limits, s.sources)
    assert out.points.shape == (80, 2)
    assert len(np.unique(out.points.round(6), axis=0)) == 80
    z = out.points[:, 1]
    size = s.formations["x"].size * s.limits.height
    assert z.max() - z.min() == pytest.approx(size, rel=0.02) or "spiral" in str(f)
    assert out.min_spacing > 0


def test_svg_formation_keeps_artwork_colours():
    s = load_show(EX / "heart-logo.show.json")
    out = sample(s.formations["logo"], 100, s.limits, s.sources)
    assert out.colors and {"#3fa9f5", "#ffd166"} <= set(out.colors)


def test_stroke_formation():
    pts = [(x, 10 * np.sin(x / 8)) for x in np.linspace(0, 60, 40)]
    s = show({"w": {"kind": "stroke", "stroke": "s1"}}, [{"kind": "form", "id": "a", "formation": "w"}],
             sources={"strokes": {"s1": pts}})
    out = sample(s.formations["w"], 50, s.limits, s.sources)
    d = cKDTree(out.points).query(out.points, k=2)[0][:, 1]
    assert d.max() < 2.5 * d.min()  # evenly spread along the line


def test_launch_grid_is_separated_and_inside():
    lim = Limits()
    g = launch_grid(100, lim)
    assert cKDTree(g).query(g, k=2)[0][:, 1].min() >= lim.min_separation
    assert g[:, 0].min() >= lim.margin and g[:, 0].max() <= lim.width - lim.margin


def test_compiled_document_is_valid_motionlang_and_matches_tracked_positions():
    c = run(load_show(EX / "heart-logo-flyable.show.json"))
    assert c.motion.ok, [str(i) for i in c.motion.errors]
    fin = np.array([c.baked.pos[f"d{i}"][-1] for i in range(c.show.drones)])
    want = np.c_[c.compiled.final[:, 0] * PPM, (c.show.limits.height - c.compiled.final[:, 1]) * PPM]
    assert np.abs(fin - want).max() < 1e-6
    assert c.flight.ok, [str(i) for i in c.flight.issues]


def test_unsafe_example_gets_issues_with_hints_and_one_repair_round_fixes_it():
    s = load_show(EX / "heart-logo.show.json")
    c = run(s)
    codes = {i.code for i in c.flight.issues}
    assert {"E_SPEED", "E_ACCEL", "E_GEOFENCE"} <= codes
    assert all(i.hint for i in c.flight.issues)
    fixed = json.loads((EX / "heart-logo-flyable.show.json").read_text())
    # the flyable example is exactly the hints applied
    for i in c.flight.issues:
        if i.code in ("E_SPEED", "E_ACCEL"):
            idx = int(i.pointer.split("/")[-1])
            new = float(i.hint.split(" to ")[-1].split(" ")[0])
            key = "transition" if s.steps[idx].kind == "form" else "duration"
            assert fixed["steps"][idx][key] == new
    assert fixed["formations"]["thanks"]["size"] == 0.2


def test_direct_transitions_between_spaced_formations_keep_separation():
    # CAPT: well-spaced start and goal, synchronized straight lines → no E_SEPARATION
    s = show({"g": {"kind": "preset", "shape": "grid", "size": 0.5},
              "h": {"kind": "preset", "shape": "heart", "fill": "fill"},
              "r": {"kind": "preset", "shape": "ring", "size": 0.95}},
             [{"kind": "form", "id": "a", "formation": "g", "transition": 14},
              {"kind": "form", "id": "b", "formation": "h", "transition": 12},
              {"kind": "form", "id": "c", "formation": "r", "transition": 12, "hold": 1}], drones=60)
    c = run(s)
    assert not [i for i in c.flight.issues if i.code == "E_SEPARATION"], [str(i) for i in c.flight.issues]


def test_dense_outline_reports_density_with_a_size_or_count_fix():
    s = show({"h": {"kind": "preset", "shape": "heart"}}, [{"kind": "form", "id": "a", "formation": "h", "transition": 12}])
    c = run(s)
    dens = [i for i in c.flight.issues if i.code == "E_DENSITY"]
    assert dens and dens[0].severity == "error" and ("size" in dens[0].hint or "drones" in dens[0].hint)


def test_spin_hint_names_the_duration_that_fits():
    s = show({"h": {"kind": "preset", "shape": "heart", "fill": "fill"}},
             [{"kind": "form", "id": "a", "formation": "h", "transition": 14}, {"kind": "spin", "id": "s", "turns": 0.5, "duration": 3}])
    c = run(s)
    sp = [i for i in c.flight.issues if i.block == "s" and i.code in ("E_SPEED", "E_ACCEL")]
    assert sp
    new = float(sp[0].hint.split(" to ")[-1].split(" ")[0])
    s2 = show({"h": {"kind": "preset", "shape": "heart", "fill": "fill"}},
              [{"kind": "form", "id": "a", "formation": "h", "transition": 14}, {"kind": "spin", "id": "s", "turns": 0.5, "duration": new}])
    c2 = run(s2)
    assert not [i for i in c2.flight.issues if i.block == "s"]


def test_show_rejects_unknown_formation():
    with pytest.raises(ValueError, match="unknown formation"):
        show({}, [{"kind": "form", "id": "a", "formation": "nope"}])
