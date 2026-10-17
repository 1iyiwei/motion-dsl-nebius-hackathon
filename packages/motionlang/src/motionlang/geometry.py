"""Actor geometry in local bounding-box coordinates, and 2D affine transforms."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .model import Actor, Document
from .paths import catmull_rom, flatten_svg_path

TEXT_ADVANCE = 0.6  # rough glyph width / font size, enough for bounds checks


@dataclass
class Geometry:
    """Shape geometry with its bounding box at the local origin: x ∈ [0, w], y ∈ [0, h]."""

    w: float
    h: float
    outline: np.ndarray  # (N, 2) points used for AABB tests; empty for groups
    origin: tuple[float, float]  # where the bbox top-left sits in canvas coords for drawn shapes, else None-ish
    drawn: bool  # True if the shape has an intrinsic canvas position (stroke/polygon/path)
    svg: dict[str, Any] = field(default_factory=dict)  # primitive for the preview player


def _points_geometry(pts: np.ndarray, closed: bool, kind: str) -> Geometry:
    mn = pts.min(axis=0)
    local = pts - mn
    w, h = (float(v) for v in np.maximum(local.max(axis=0), 1e-6))
    d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in local) + (" Z" if closed else "")
    return Geometry(w, h, local, (float(mn[0]), float(mn[1])), True, {"tag": "path", "d": d, "closed": closed, "kind": kind})


def actor_geometry(actor: Actor, doc: Document) -> Geometry:
    s = actor.shape
    k = s.kind
    if k == "rect":
        o = np.array([[0, 0], [s.w, 0], [s.w, s.h], [0, s.h]], dtype=float)
        return Geometry(s.w, s.h, o, (0, 0), False, {"tag": "rect", "w": s.w, "h": s.h, "r": s.r})
    if k == "ellipse":
        th = np.linspace(0, 2 * math.pi, 48, endpoint=False)
        o = np.stack([s.rx + s.rx * np.cos(th), s.ry + s.ry * np.sin(th)], axis=1)
        return Geometry(2 * s.rx, 2 * s.ry, o, (0, 0), False, {"tag": "ellipse", "rx": s.rx, "ry": s.ry})
    if k == "polygon":
        return _points_geometry(np.asarray(s.points, dtype=float), True, k)
    if k == "path":
        return _points_geometry(flatten_svg_path(s.d), s.closed, k)
    if k == "stroke":
        st = doc.strokes[s.ref]
        g = _points_geometry(catmull_rom(st.points), False, k)
        g.svg["width"] = st.width
        return g
    if k == "text":
        w, h = TEXT_ADVANCE * s.size * len(s.text), s.size
        o = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=float)
        return Geometry(w, h, o, (0, 0), False, {"tag": "text", "text": s.text, "size": s.size, "font": s.font, "weight": s.weight})
    if k == "image":
        o = np.array([[0, 0], [s.w, 0], [s.w, s.h], [0, s.h]], dtype=float)
        return Geometry(s.w, s.h, o, (0, 0), False, {"tag": "image", "url": s.url, "w": s.w, "h": s.h})
    return Geometry(0, 0, np.zeros((0, 2)), (0, 0), False, {"tag": "group"})


# ---- affine matrices as (a, b, c, d, e, f) = [[a c e], [b d f]] like SVG ----------------

Mat = tuple[float, float, float, float, float, float]
IDENTITY: Mat = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m: Mat, n: Mat) -> Mat:
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def local_matrix(x: float, y: float, rot_deg: float, sx: float, sy: float, px: float, py: float) -> Mat:
    """T(x, y) · R(rot) · S(sx, sy) · T(-px, -py): the pivot lands on (x, y)."""
    r = math.radians(rot_deg)
    cs, sn = math.cos(r), math.sin(r)
    a, b, c, d = cs * sx, sn * sx, -sn * sy, cs * sy
    return (a, b, c, d, x - (a * px + c * py), y - (b * px + d * py))


def camera_matrix(width: float, height: float, cx: float, cy: float, zoom: float, rot_deg: float) -> Mat:
    """World → screen: the camera's (x, y) appears at the canvas center."""
    r = math.radians(-rot_deg)
    cs, sn = math.cos(r) * zoom, math.sin(r) * zoom
    return (cs, sn, -sn, cs, width / 2 - (cs * cx - sn * cy), height / 2 - (sn * cx + cs * cy))


def apply(m: Mat, pts: np.ndarray) -> np.ndarray:
    a, b, c, d, e, f = m
    return np.stack([a * pts[:, 0] + c * pts[:, 1] + e, b * pts[:, 0] + d * pts[:, 1] + f], axis=1)
