from __future__ import annotations

import copy
from typing import Any

import pytest

BASE: dict[str, Any] = {
    "motion": "0.1",
    "canvas": {"width": 800, "height": 600, "fps": 60},
    "strokes": {"s1": {"points": [[300, 300], [400, 220], [520, 320]]}},
    "actors": [
        {"id": "a", "shape": {"kind": "rect", "w": 40, "h": 40}, "init": {"x": 300, "y": 300}},
        {"id": "b", "shape": {"kind": "ellipse", "rx": 20, "ry": 20}, "init": {"x": 550, "y": 300}},
        {"id": "line", "shape": {"kind": "stroke", "ref": "s1"}},
    ],
    "timeline": {"id": "root", "seq": []},
}


def make_doc(*blocks: dict, **overrides: Any) -> dict:
    doc = copy.deepcopy(BASE)
    doc["timeline"]["seq"] = list(blocks)
    for k, v in overrides.items():
        doc[k] = v
    return doc


@pytest.fixture
def doc_factory():
    return make_doc
