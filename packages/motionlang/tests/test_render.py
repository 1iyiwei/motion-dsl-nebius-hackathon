"""Raster rendering (motionlang[render]): pixels match what the player draws."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from motionlang import check  # noqa: E402
from motionlang.cli import main  # noqa: E402
from motionlang.render import to_frames, to_gif  # noqa: E402

RED, WHITE = (255, 0, 0), (255, 255, 255)


def tracks(*actors: dict, blocks: list | None = None, assets: dict | None = None) -> dict:
    doc = {"motion": "0.1", "canvas": {"width": 200, "height": 100, "fps": 10, "background": "#ffffff"},
           "assets": assets or {}, "actors": list(actors),
           "timeline": {"id": "root", "seq": blocks or [{"id": "h", "verb": "hold", "duration": 1}]}}
    r = check(doc)
    assert r.baked is not None, r.report.summary()
    return r.baked.to_json()


def near(px, rgb, tol=12) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


def test_fill_position_and_opacity():
    T = tracks({"id": "box", "shape": {"kind": "rect", "w": 40, "h": 40}, "style": {"fill": "#ff0000"}, "init": {"x": 50, "y": 50}},
               {"id": "ghost", "shape": {"kind": "rect", "w": 40, "h": 40}, "style": {"fill": "#ff0000"}, "init": {"x": 150, "y": 50, "opacity": 0.5}})
    f = to_frames(T)[0]
    assert f.size == (200, 100)
    assert near(f.getpixel((50, 50)), RED) and near(f.getpixel((10, 10)), WHITE)
    assert near(f.getpixel((150, 50)), (255, 128, 128))  # half opacity over white


def test_reveal_clip_and_draw_on_trim():
    T = tracks({"id": "box", "shape": {"kind": "rect", "w": 100, "h": 40}, "style": {"fill": "#ff0000"}, "init": {"x": 100, "y": 30}},
               {"id": "line", "shape": {"kind": "path", "d": "M 20 80 L 180 80"}, "style": {"stroke": "#ff0000", "stroke_width": 6}},
               blocks=[{"id": "go", "par": [
                   {"id": "w", "verb": "reveal", "actor": "box", "direction": "left_to_right", "duration": 1, "ease": "linear"},
                   {"id": "d", "verb": "draw_on", "actor": "line", "duration": 1, "ease": "linear"}]}])
    half = to_frames(T)[5]  # t = 0.5 s
    assert near(half.getpixel((60, 30)), RED) and near(half.getpixel((140, 30)), WHITE)
    assert near(half.getpixel((60, 80)), RED) and near(half.getpixel((160, 80)), WHITE)


def test_holes_stay_empty():
    ring = {"kind": "path", "fill": "#ff0000", "d": "M 60 10 L 140 10 L 140 90 L 60 90 Z M 80 30 L 80 70 L 120 70 L 120 30 Z"}
    T = tracks({"id": "ring", "shape": {"kind": "asset", "ref": "ring"}}, assets={"ring": ring})
    f = to_frames(T)[0]
    assert near(f.getpixel((70, 50)), RED) and near(f.getpixel((100, 50)), WHITE)


def test_gif_size_fps_and_loop(tmp_path):
    T = tracks({"id": "box", "shape": {"kind": "rect", "w": 40, "h": 40}, "style": {"fill": "#ff0000"}, "init": {"x": 50, "y": 50}},
               blocks=[{"id": "m", "verb": "move", "actor": "box", "to": [150, 50], "duration": 2}])
    data = to_gif(T, width=100, fps=5)
    im = Image.open(io.BytesIO(data))
    assert im.format == "GIF" and im.size == (100, 50) and im.n_frames == 11 and im.info.get("loop") == 0
    assert "loop" not in Image.open(io.BytesIO(to_gif(T, width=100, fps=5, loop=False))).info


def test_cli_render(tmp_path):
    src = tmp_path / "x.motion.json"
    import json

    src.write_text(json.dumps({"motion": "0.1", "canvas": {"width": 200, "height": 100},
                               "actors": [{"id": "a", "shape": {"kind": "ellipse", "rx": 10, "ry": 10}, "init": {"x": 50, "y": 50}}],
                               "timeline": {"id": "root", "seq": [{"id": "m", "verb": "move", "actor": "a", "to": [150, 50]}]}}))
    out = tmp_path / "x.gif"
    assert main(["render", str(src), "--gif", str(out), "--width", "120", "--fps", "10"]) == 0
    assert Image.open(out).size == (120, 60)
