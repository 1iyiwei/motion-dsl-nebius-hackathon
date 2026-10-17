import math

import numpy as np
import pytest

from motionlang.color import mix, nearest_name, parse_color
from motionlang.ease import EASES, resolve_ease
from motionlang.paths import Polyline, catmull_rom, flatten_svg_path, resolve_path


@pytest.mark.parametrize("name", sorted(EASES))
def test_eases_hit_endpoints(name):
    f = EASES[name]
    assert f(0.0) == pytest.approx(0.0, abs=1e-9)
    assert f(1.0) == pytest.approx(1.0, abs=1e-9)


def test_bezier_ease_and_errors():
    f = resolve_ease({"bezier": [0.25, 0.1, 0.25, 1.0]})
    assert f(0) == 0 and f(1) == 1 and 0.5 < f(0.5) < 1
    with pytest.raises(ValueError, match="unknown ease"):
        resolve_ease("easeInOutCubic")
    with pytest.raises(ValueError):
        resolve_ease({"bezier": [2, 0, 0, 1]})


def test_polyline_arc_length():
    p = Polyline([[0, 0], [100, 0], [100, 100]])
    assert p.length == 200
    assert p.point_at(0.25) == (50, 0)
    assert p.point_at(0.75) == (100, 50)
    assert p.angle_at(0.0) == pytest.approx(0)
    assert p.angle_at(1.0) == pytest.approx(90)


def test_angle_unwraps_through_spiral():
    th = np.linspace(0, 4 * math.pi, 400)
    p = Polyline(np.stack([np.cos(th) * 100, np.sin(th) * 100], axis=1))
    assert p.angle_at(1.0) - p.angle_at(0.0) == pytest.approx(720, abs=5)


def test_svg_flatten():
    pts = flatten_svg_path("M 0 0 L 10 0 l 0 10 H 0 Z")
    assert pts[0].tolist() == [0, 0] and pts[-1].tolist() == [0, 0]
    curve = flatten_svg_path("M0,0 C 0,-50 100,-50 100,0")
    assert curve[-1].tolist() == pytest.approx([100, 0])
    assert curve[:, 1].min() < -30
    with pytest.raises(ValueError, match="arc"):
        flatten_svg_path("M0 0 A 10 10 0 0 1 20 0")


def test_catmull_rom_passes_through_points():
    pts = [(0, 0), (50, 80), (100, 0)]
    c = catmull_rom(pts)
    for q in pts:
        assert np.min(np.linalg.norm(c - np.array(q), axis=1)) < 1e-6


def test_resolve_path_errors():
    with pytest.raises(KeyError):
        resolve_path({"stroke": "nope"}, {})
    with pytest.raises(ValueError):
        resolve_path({"stroke": "s", "points": []}, {"s": [(0, 0)]})


def test_colors():
    assert parse_color("#fff") == (1, 1, 1)
    assert parse_color("Royal Blue") == parse_color("#4169e1")
    assert mix("#000000", "#ffffff", 0) == "#000000"
    assert mix("#000000", "#ffffff", 1) == "#ffffff"
    assert nearest_name("#4169e2") == "royalblue"
    with pytest.raises(ValueError):
        parse_color("blurple")
