"""Path geometry: polylines with arc-length parameterization, SVG path flattening, splines."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]

CURVE_SEGMENTS = 24  # samples per curve segment when flattening


@dataclass
class Polyline:
    """A flattened path, parameterized by arc length for constant-speed travel."""

    pts: np.ndarray  # (N, 2)

    def __post_init__(self) -> None:
        self.pts = np.asarray(self.pts, dtype=float).reshape(-1, 2)
        if len(self.pts) == 0:
            raise ValueError("path has no points")
        d = np.diff(self.pts, axis=0)
        seg = np.linalg.norm(d, axis=1)
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        # Unwrapped heading per segment, so turning through ±180° stays continuous.
        keep = seg > 1e-9
        heads = np.unwrap(np.arctan2(d[keep, 1], d[keep, 0])) if keep.any() else np.zeros(0)
        self._heads = np.degrees(heads)
        self._head_s = (self.cum[:-1][keep] + self.cum[1:][keep]) / 2  # heading sampled at segment midpoints

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    @property
    def start(self) -> Point:
        return float(self.pts[0, 0]), float(self.pts[0, 1])

    def _locate(self, u: float) -> tuple[int, float]:
        s = min(max(u, 0.0), 1.0) * self.length
        i = int(np.searchsorted(self.cum, s, side="right")) - 1
        i = min(max(i, 0), len(self.pts) - 2) if len(self.pts) > 1 else 0
        seg = self.cum[i + 1] - self.cum[i] if len(self.pts) > 1 else 0.0
        return i, (s - self.cum[i]) / seg if seg > 0 else 0.0

    def point_at(self, u: float) -> Point:
        """Point at fraction u of total arc length."""
        if len(self.pts) == 1 or self.length == 0:
            return self.start
        i, f = self._locate(u)
        p = self.pts[i] + (self.pts[i + 1] - self.pts[i]) * f
        return float(p[0]), float(p[1])

    def angle_at(self, u: float) -> float:
        """Unwrapped tangent direction in degrees (clockwise-positive in y-down space).

        Unwrapped means a spiral keeps increasing past 180° instead of jumping to -180°.
        """
        if len(self._heads) == 0:
            return 0.0
        s = min(max(u, 0.0), 1.0) * self.length
        return float(np.interp(s, self._head_s, self._heads))

    def translated(self, dx: float, dy: float) -> "Polyline":
        return Polyline(self.pts + np.array([dx, dy]))

    def reversed(self) -> "Polyline":
        return Polyline(self.pts[::-1].copy())

    def bbox(self) -> tuple[float, float, float, float]:
        mn, mx = self.pts.min(axis=0), self.pts.max(axis=0)
        return float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1])


def catmull_rom(points: list[Point], samples: int = 12) -> np.ndarray:
    """Centripetal Catmull-Rom through all points (keeps sketch strokes smooth without overshoot)."""
    p = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(p) < 3:
        return p
    ext = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    out = [p[0]]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]

        def tj(ti: float, a: np.ndarray, b: np.ndarray) -> float:
            return ti + max(float(np.linalg.norm(b - a)) ** 0.5, 1e-6)

        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        for t in np.linspace(t1, t2, samples + 1)[1:]:
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    return np.asarray(out)


def arc_points(center: Point, radius: float, start_deg: float, end_deg: float) -> np.ndarray:
    n = max(8, int(abs(end_deg - start_deg) / 360 * 96))
    th = np.radians(np.linspace(start_deg, end_deg, n + 1))
    return np.stack([center[0] + radius * np.cos(th), center[1] + radius * np.sin(th)], axis=1)


_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtZzAa]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "Z": 0}


def flatten_svg_path(d: str) -> np.ndarray:
    """Flatten an SVG path `d` string (M L H V C S Q T Z, absolute and relative) to points.

    Subpaths are concatenated in order. Elliptical arcs (A) are not supported in v0.1.
    """
    tokens = _TOKEN.findall(d)
    if not tokens:
        raise ValueError("empty SVG path")
    out: list[Point] = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    last_ctrl: Point | None = None
    last_cmd = ""
    i = 0
    cmd = ""

    def bez(pts: list[Point]) -> None:
        nonlocal cur
        for t in np.linspace(0, 1, CURVE_SEGMENTS + 1)[1:]:
            if len(pts) == 3:
                (x0, y0), (x1, y1), (x2, y2) = pts
                x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t * t * x2
                y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t * t * y2
            else:
                (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
                x = (1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t**3 * x3
                y = (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t**3 * y3
            out.append((float(x), float(y)))
        cur = pts[-1]

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Aa":
                raise ValueError("SVG arc commands (A) are not supported in v0.1; use C curves or an arc path")
            if cmd in "Zz":
                if out:
                    out.append(start)
                cur = start
                last_cmd, last_ctrl = "Z", None
                continue
        elif not cmd:
            raise ValueError("SVG path must start with a command")
        up = cmd.upper()
        n = _ARITY[up]
        arg_tokens = tokens[i : i + n]
        if len(arg_tokens) < n or any(t.isalpha() for t in arg_tokens):
            raise ValueError(f"SVG path command {cmd} needs {n} numbers")
        args = [float(v) for v in arg_tokens]
        i += n
        rel = cmd.islower()
        ox, oy = cur if rel else (0.0, 0.0)

        if up == "M":
            cur = (args[0] + ox, args[1] + oy)
            start = cur
            out.append(cur)
            cmd = "l" if rel else "L"  # implicit lineto after moveto
            last_ctrl = None
        elif up == "L":
            cur = (args[0] + ox, args[1] + oy)
            out.append(cur)
            last_ctrl = None
        elif up == "H":
            cur = (args[0] + ox, cur[1])
            out.append(cur)
            last_ctrl = None
        elif up == "V":
            cur = (cur[0], args[0] + (cur[1] if rel else 0.0))
            out.append(cur)
            last_ctrl = None
        elif up in "CS":
            if up == "C":
                c1 = (args[0] + ox, args[1] + oy)
                c2, end = (args[2] + ox, args[3] + oy), (args[4] + ox, args[5] + oy)
            else:
                c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_cmd in "CS" and last_ctrl else cur
                c2, end = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            if not out:
                out.append(cur)
            bez([cur, c1, c2, end])
            last_ctrl = c2
        elif up in "QT":
            if up == "Q":
                c1, end = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            else:
                c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_cmd in "QT" and last_ctrl else cur
                end = (args[0] + ox, args[1] + oy)
            if not out:
                out.append(cur)
            bez([cur, c1, end])
            last_ctrl = c1
        last_cmd = up
    return np.asarray(out, dtype=float)


def resolve_path(spec: dict, strokes: dict[str, "list[Point]"]) -> Polyline:
    """Resolve a path spec ({stroke}|{points}|{svg}|{arc}) to a Polyline. Raises ValueError/KeyError."""
    if not isinstance(spec, dict) or len(spec.keys() - {"smooth"}) != 1:
        raise ValueError('path must be exactly one of {"stroke"}, {"points"}, {"svg"}, {"arc"}')
    if "stroke" in spec:
        ref = spec["stroke"]
        if ref not in strokes:
            raise KeyError(f"stroke {ref!r} is not defined in document.strokes")
        pts = strokes[ref]
        return Polyline(catmull_rom(pts) if spec.get("smooth", True) else np.asarray(pts, dtype=float))
    if "points" in spec:
        pts = spec["points"]
        if len(pts) < 1:
            raise ValueError("points path needs at least one point")
        return Polyline(catmull_rom(pts) if spec.get("smooth", False) else np.asarray(pts, dtype=float))
    if "svg" in spec:
        return Polyline(flatten_svg_path(spec["svg"]))
    if "arc" in spec:
        a = spec["arc"]
        return Polyline(arc_points(tuple(a["center"]), float(a["radius"]), float(a.get("from", 0)), float(a.get("to", 360))))
    raise ValueError('path must be exactly one of {"stroke"}, {"points"}, {"svg"}, {"arc"}')
